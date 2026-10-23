/**
 * PiCore Dashboard - settings sections "Zwei-Faktor-Anmeldung" and "Sitzungen" (own account) and
 * "Sicherheit" (admins): checklist for running on the internet, proxy / 2FA policy / confirm
 * again / home-network-only areas, all sessions and security events.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function device(ua) {
    if (!ua) return '–';
    const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /OPR\//.test(ua) ? 'Opera'
      : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : /curl|python|httpx/i.test(ua) ? 'Skript' : 'Browser';
    const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows'
      : /Mac OS X/.test(ua) ? 'macOS' : /CrOS/.test(ua) ? 'ChromeOS' : /Linux/.test(ua) ? 'Linux' : '';
    return os ? `${browser} · ${os}` : browser;
  }

  // Password (+ code) asked inline before something sensitive
  function confirmForm(box, label, withCode, onSubmit) {
    const form = el('form', 'twofa-row');
    form.autocomplete = 'off';
    const pw = el('input');
    pw.type = 'password';
    pw.placeholder = t('Passwort');
    pw.autocomplete = 'current-password';
    form.appendChild(pw);
    let code = null;
    if (withCode) {
      code = el('input');
      code.type = 'text';
      code.inputMode = 'numeric';
      code.placeholder = t('Code aus der App');
      code.autocomplete = 'one-time-code';
      form.appendChild(code);
    }
    const go = el('button', 'btn-primary', t(label));
    go.type = 'submit';
    form.appendChild(go);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      go.disabled = true;
      try { await onSubmit(pw.value, code ? code.value.trim() : ''); } catch (err) { toast(err.message, 'error'); go.disabled = false; }
    });
    box.appendChild(form);
    setTimeout(() => pw.focus(), 30);
  }

  // ---------------------------------------------------------------------------
  // Own two-factor login
  // ---------------------------------------------------------------------------
  async function loadTwoFa() {
    const body = $('tf-body');
    let d;
    try { d = await api('/api/auth/2fa'); } catch (e) { return; }
    const tag = $('tf-state');
    tag.className = `health-tag ${d.enabled ? 'good' : d.required ? 'critical' : 'warning'}`;
    tag.textContent = t(d.enabled ? 'Aktiv' : d.required ? 'Pflicht – nicht eingerichtet' : 'Aus');
    body.innerHTML = '';
    if (!d.enabled) {
      body.appendChild(button('Einrichten', 'btn-primary', () => window.PiTwoFA.setup(body, { onDone: () => { loadTwoFa(); toast(t('Zwei-Faktor-Anmeldung ist aktiv.'), 'success'); } })));
      return;
    }
    body.appendChild(el('p', d.recovery_left <= 3 ? 'form-msg warning' : 'field-hint',
      t(`Noch ${d.recovery_left} Wiederherstellungscodes übrig.`)));
    const actions = el('div', 'twofa-row');
    const area = el('div');
    actions.appendChild(button('Neue Wiederherstellungscodes', 'btn-secondary', () => {
      area.innerHTML = '';
      confirmForm(area, 'Codes erzeugen', true, async (password, code) => {
        const r = await api('/api/auth/2fa/recovery', { password, code });
        window.PiTwoFA.showCodes(area, r.recovery_codes, loadTwoFa);
      });
    }));
    if (!d.required) {
      actions.appendChild(button('Abschalten', 'btn-secondary danger', () => {
        area.innerHTML = '';
        area.appendChild(el('p', 'field-hint', t('Zum Abschalten Passwort und aktuellen Code eingeben.')));
        confirmForm(area, 'Abschalten', true, async (password, code) => {
          await api('/api/auth/2fa/disable', { password, code });
          toast(t('Zwei-Faktor-Anmeldung abgeschaltet.'), 'success');
          loadTwoFa();
        });
      }));
    }
    body.append(actions, area);
  }

  // ---------------------------------------------------------------------------
  // Sessions (own / all)
  // ---------------------------------------------------------------------------
  function sessionTable(table, data, all, revokePath) {
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    [...(all ? ['Benutzer'] : []), 'Gerät', 'Adresse', 'Angemeldet', 'Zuletzt aktiv', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    if (!data.sessions.length) {
      const r = body.insertRow();
      const c = r.insertCell();
      c.colSpan = all ? 6 : 5;
      c.textContent = t('Keine Sitzungen.');
    }
    data.sessions.forEach(s => {
      const r = body.insertRow();
      if (all) r.insertCell().appendChild(raw('span', '', s.user));
      const dev = r.insertCell();
      dev.appendChild(raw('span', '', device(s.agent)));
      if (s.remember) dev.appendChild(el('span', 'ctr-sub', t('angemeldet bleiben')));
      const addr = r.insertCell();
      addr.className = 'mono';
      addr.appendChild(raw('span', '', `${s.ip || '–'}${s.country ? ` (${s.country})` : ''}`));
      if (s.internal) addr.appendChild(el('span', 'ctr-sub', t('Heimnetz / VPN')));
      r.insertCell().textContent = s.created ? fmtTs(s.created) : '';
      r.insertCell().textContent = s.last_seen ? fmtTs(s.last_seen) : '';
      const act = r.insertCell();
      act.className = 'row-action';
      if (s.current) act.appendChild(el('span', 'health-tag good', t('Diese Sitzung')));
      else act.appendChild(button('Beenden', 'text-btn danger', async b => {
        b.disabled = true;
        try { const d = await api(revokePath(s.id), {}); sessionTable(table, d, all, revokePath); toast(t('Sitzung beendet.'), 'success'); }
        catch (err) { toast(err.message, 'error'); b.disabled = false; }
      }));
    });
  }

  async function loadOwnSessions() {
    try {
      const d = await api('/api/auth/sessions');
      sessionTable($('ses-table'), d, false, id => `/api/auth/sessions/${id}/revoke`);
      $('ses-summary').textContent = d.legacy ? t('Diese Sitzung stammt von vor dem Update und erscheint nach der nächsten Anmeldung.') : '';
    } catch (e) { /* not logged in */ }
  }

  // ---------------------------------------------------------------------------
  // Security (admins)
  // ---------------------------------------------------------------------------
  const STATE = { ok: ['good', 'OK'], warning: ['warning', 'Offen'], info: ['', 'Hinweis'] };

  function permChecks(perms, selected, name) {
    const box = el('div', 'sec-perms');
    perms.forEach(p => {
      const l = el('label', 'toggle-label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.name = name;
      cb.value = p.id;
      cb.checked = selected.includes(p.id);
      l.append(cb, el('span', '', t(p.label)));
      box.appendChild(l);
    });
    return box;
  }

  function field(form, label, control, hint) {
    const w = el('label', 'login-field full');
    w.append(el('span', '', t(label)), control);
    if (hint) w.appendChild(el('span', 'field-hint', t(hint)));
    form.appendChild(w);
    return w;
  }

  function toggle(form, label, checked, name) {
    const l = el('label', 'toggle-label');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.name = name;
    cb.checked = checked;
    l.append(cb, el('span', '', t(label)));
    form.appendChild(l);
    return cb;
  }

  function renderSecurity(d) {
    $('sec-client').textContent = `${t('Deine Adresse')}: ${d.client.ip}${d.client.internal ? ` (${t('Heimnetz / VPN')})` : ''} · ${d.client.scheme.toUpperCase()}`;
    const list = $('sec-checklist');
    list.innerHTML = '';
    d.checklist.forEach(c => {
      const li = el('li', `st-item ${c.state === 'warning' ? 'warning' : ''}`);
      const head = el('div', 'st-head');
      const [cls, label] = STATE[c.state] || STATE.info;
      head.append(el('span', `health-tag ${cls}`, t(label)), el('span', 'st-title', t(c.title)));
      li.append(head, el('span', 'st-detail', t(c.detail)));
      list.appendChild(li);
    });

    const form = $('sec-form');
    form.innerHTML = '';
    form.appendChild(el('h3', 'subhead', t('Proxy & Adressen')));
    const proxy = el('select');
    Object.entries(d.proxy_modes).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; proxy.appendChild(o); });
    proxy.value = d.proxy;
    field(form, 'Das Dashboard läuft', proxy, 'Bei Cloudflare zählt die Adresse aus CF-Connecting-IP, bei einem eigenen Proxy X-Forwarded-For – beides nur, wenn die Anfrage von einer vertrauenswürdigen Proxy-Adresse kommt.');
    const trusted = el('textarea');
    trusted.rows = 3;
    trusted.value = (d.trusted || []).join('\n');
    trusted.spellcheck = false;
    const trustedWrap = field(form, 'Vertrauenswürdige Proxy-Adressen (eine pro Zeile)', trusted, 'Wo cloudflared bzw. der Proxy läuft: 127.0.0.1 auf dem Pi selbst, 172.16.0.0/12 in Docker, 10.42.0.0/16 in k3s.');
    const syncProxy = () => { trustedWrap.hidden = proxy.value === 'none'; };
    proxy.addEventListener('change', syncProxy);
    syncProxy();
    const rate = el('input');
    rate.type = 'number';
    rate.min = 0;
    rate.max = 10000;
    rate.value = d.anon_rate;
    field(form, 'Anfragen ohne Login pro Minute und Adresse (aus dem Internet)', rate, '0 = keine Begrenzung. Betrifft Login-Seite, Freigabe- und Austausch-Links und die Statusseite; Heimnetz und VPN sind ausgenommen.');
    const hsts = toggle(form, 'HSTS senden, wenn die Seite über HTTPS kommt (Browser bleiben dann bei HTTPS)', d.hsts, 'hsts');

    form.appendChild(el('h3', 'subhead', t('Anmeldung')));
    const twofa = el('select');
    Object.entries(d.twofa_policies).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; twofa.appendChild(o); });
    twofa.value = d.twofa;
    field(form, 'Zwei-Faktor-Anmeldung', twofa, 'Bei Pflicht müssen betroffene Benutzer sie bei der nächsten Anmeldung einrichten, bevor sie weiterkommen. Handy verloren: Benutzer → „Zwei-Faktor zurücksetzen“ oder auf dem Pi ./set-password.sh 2fa-reset <name>.');
    const notify = toggle(form, 'Ereignis (und E-Mail) bei Anmeldung von einer neuen Adresse außerhalb des Heimnetzes', d.notify_new_login, 'notify');

    form.appendChild(el('h3', 'subhead', t('Erneute Bestätigung')));
    const reauth = toggle(form, 'Kritische Änderungen verlangen das Passwort (und den Code) erneut', d.reauth, 'reauth');
    const minutes = el('input');
    minutes.type = 'number';
    minutes.min = 1;
    minutes.max = 120;
    minutes.value = d.reauth_minutes;
    field(form, 'Gültig für (Minuten)', minutes);
    form.appendChild(permChecks(d.permissions, d.reauth_perms, 'reauth_perms'));

    form.appendChild(el('h3', 'subhead', t('Nur aus Heimnetz / VPN')));
    const restrict = toggle(form, 'Diese Bereiche aus dem Internet sperren – auch für Administratoren', d.restrict, 'restrict');
    form.appendChild(permChecks(d.permissions, d.restrict_perms, 'restrict_perms'));
    const internal = el('textarea');
    internal.rows = 2;
    internal.value = (d.internal_networks || []).join('\n');
    internal.spellcheck = false;
    field(form, 'Weitere Netze, die als intern gelten (eine pro Zeile)', internal, 'Private Netze (192.168.x, 10.x, 172.16–31.x) und VPN-Adressen (100.64.0.0/10, z.B. NetBird, Tailscale) gelten immer als intern.');

    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.appendChild(save);
    form.appendChild(actions);
    form.onsubmit = async e => {
      e.preventDefault();
      save.disabled = true;
      const checked = name => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(i => i.value);
      try {
        const r = await api('/api/security', {
          proxy: proxy.value, trusted: trusted.value, anon_rate: Number(rate.value), hsts: hsts.checked, twofa: twofa.value,
          notify_new_login: notify.checked, reauth: reauth.checked, reauth_minutes: Number(minutes.value),
          reauth_perms: checked('reauth_perms'), restrict: restrict.checked, restrict_perms: checked('restrict_perms'),
          internal_networks: internal.value
        });
        renderSecurity(r);
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    };

    const ev = $('sec-events');
    ev.innerHTML = '';
    if (!d.events.length) ev.appendChild(el('li', 'field-hint', t('Keine Ereignisse.')));
    d.events.forEach(e => {
      const li = el('li', `event-item ${e.level}`);
      li.append(el('span', 'inc-when', fmtTs(e.ts)), el('span', '', t(e.title)), raw('span', 'field-hint', e.detail || ''));
      ev.appendChild(li);
    });
  }

  async function loadSecurity() {
    try { renderSecurity(await api('/api/security')); } catch (e) { return; }
    try {
      const d = await api('/api/security/sessions');
      sessionTable($('sec-sessions'), d, true, id => `/api/security/sessions/${id}/revoke`);
    } catch (e) { /* ignore */ }
  }

  async function init() {
    if (!$('tf-body')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    loadTwoFa();
    loadOwnSessions();
    if (can('users.manage')) loadSecurity();
  }

  init();
})();
