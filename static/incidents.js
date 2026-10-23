/**
 * PiCore Dashboard - documentation section "Störungen": incidents with severity, affected services,
 * status and a timeline of updates.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const SEV = { gering: '', mittel: 'warning', schwer: 'critical' };
  const open = new Set();
  let data = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtDur = s => (s < 3600 ? `${Math.max(1, Math.round(s / 60))} Min.` : s < 86400 ? `${Math.floor(s / 3600)} Std. ${Math.round((s % 3600) / 60)} Min.` : `${Math.round(s / 86400)} Tage`);
  const toLocal = ts => { const d = new Date(ts * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
  const targetName = tg => (data.targets.find(x => x.target === tg) || {}).name || tg;

  function button(label, cls, fn) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => fn(b));
    return b;
  }

  function render() {
    const list = $('inc-list');
    list.innerHTML = '';
    $('inc-summary').textContent = data.open ? t(`${data.open} offen`) : '';
    const items = data.incidents.filter(i => $('inc-filter').value !== 'open' || i.open);
    if (!items.length) list.appendChild(el('p', 'docs-empty', t(data.incidents.length ? 'Keine offenen Störungen.' : 'Noch keine Störungen erfasst.')));
    items.forEach(i => {
      const card = el('div', `inc-card ${i.open ? 'open' : 'resolved'} ${i.severity}`);
      const head = el('div', 'inc-head');
      head.append(el('span', `health-tag ${i.open ? SEV[i.severity] || '' : 'good'}`, t(i.open ? i.severity_label : 'Behoben')), raw('strong', 'inc-title', i.title));
      if (i.open) head.appendChild(el('span', 'health-tag', t(i.status_label)));
      if (i.public) head.appendChild(el('span', 'field-hint', t('öffentlich')));
      const toggle = button(open.has(i.id) ? 'Weniger' : 'Verlauf', 'text-btn', () => { open.has(i.id) ? open.delete(i.id) : open.add(i.id); render(); });
      head.appendChild(toggle);
      card.appendChild(head);
      card.appendChild(el('span', 'lan-sub', `${fmtTs(i.started)} · ${i.open ? t('seit') : t('Dauer')} ${t(fmtDur(i.duration))}${i.user ? ` · ${i.user}` : ''}`));
      if (i.services.length) {
        const svc = el('div', 'fleet-pis');
        i.services.forEach(s => svc.appendChild(raw('span', 'pg-chip', targetName(s))));
        card.appendChild(svc);
      }
      if (i.summary) card.appendChild(raw('p', 'inc-summary', i.summary));
      if (open.has(i.id) || i.open) {
        const tl = el('ol', 'inc-timeline');
        [...i.updates].reverse().forEach(u => {
          const li = el('li');
          li.append(el('span', 'inc-when', `${fmtTs(u.ts)} · ${t(data.statuses[u.status] || u.status)}${u.user ? ` · ${u.user}` : ''}`), raw('span', '', u.text));
          tl.appendChild(li);
        });
        card.appendChild(tl);
      }
      if (can('notes.edit')) {
        const act = el('div', 'inc-actions');
        if (i.open) {
          const form = el('form', 'inc-update');
          const status = el('select');
          Object.entries(data.statuses).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; status.appendChild(o); });
          status.value = i.status;
          const text = el('input');
          text.type = 'text';
          text.placeholder = t('Neuer Stand, z. B. „Ursache: volle Festplatte, wird bereinigt“');
          text.required = true;
          const go = el('button', 'btn-primary', t('Eintragen'));
          go.type = 'submit';
          form.append(status, text, go);
          form.addEventListener('submit', async e => {
            e.preventDefault();
            go.disabled = true;
            try { await api(`/api/incidents/${i.id}/update`, { status: status.value, text: text.value }); await load(); }
            catch (err) { toast(err.message, 'error'); go.disabled = false; }
          });
          act.appendChild(form);
        }
        const row = el('div', 'docs-row-actions inc-row-actions');
        row.append(button('Bearbeiten', 'text-btn', () => openForm(i)), button('Löschen', 'text-btn danger', b => {
          if (!b.classList.contains('confirming')) {
            b.classList.add('confirming');
            b.textContent = t('Sicher?');
            setTimeout(() => { b.classList.remove('confirming'); b.textContent = t('Löschen'); }, 4000);
            return;
          }
          api(`/api/incidents/${i.id}/delete`, {}).then(d => { data = d; render(); }).catch(err => toast(err.message, 'error'));
        }));
        act.appendChild(row);
        card.appendChild(act);
      }
      list.appendChild(card);
    });
  }

  function openForm(item) {
    const wrap = $('inc-form-wrap');
    const v = item || { severity: 'mittel', status: 'untersuchung', services: [], started: Math.floor(Date.now() / 1000) };
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(item ? 'Störung bearbeiten' : 'Störung erfassen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const field = (label, input, full) => { const w = el('label', `login-field${full ? ' full' : ''}`); w.append(el('span', '', t(label)), input); form.appendChild(w); return input; };
    const title = el('input');
    title.type = 'text';
    title.maxLength = 120;
    title.required = true;
    title.value = v.title || '';
    title.placeholder = t('z. B. Nextcloud nicht erreichbar');
    field('Titel', title, true);
    const sev = el('select');
    Object.entries(data.severities).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; sev.appendChild(o); });
    sev.value = v.severity;
    field('Schwere', sev);
    const started = el('input');
    started.type = 'datetime-local';
    started.value = toLocal(v.started);
    field('Beginn', started);
    let status = null, text = null;
    if (!item) {
      status = el('select');
      Object.entries(data.statuses).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; status.appendChild(o); });
      field('Status', status);
      text = el('input');
      text.type = 'text';
      text.placeholder = t('Was ist aufgefallen?');
      field('Erster Eintrag', text, true);
    }
    const svc = el('div', 'inc-targets');
    data.targets.forEach(tg => {
      const l = el('label', 'toggle-label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = tg.target;
      cb.checked = v.services.includes(tg.target);
      l.append(cb, raw('span', '', ` ${tg.name}`));
      svc.appendChild(l);
    });
    if (!data.targets.length) svc.appendChild(el('span', 'field-hint', t('Keine Dienste gefunden.')));
    const svcWrap = el('div', 'login-field full');
    svcWrap.append(el('span', '', t('Betroffen')), svc);
    form.appendChild(svcWrap);
    const summary = el('textarea');
    summary.rows = 3;
    summary.value = v.summary || '';
    field('Zusammenfassung / Ursache (intern)', summary, true);
    const pub = el('input');
    pub.type = 'checkbox';
    pub.checked = !!v.public;
    const pubLabel = el('label', 'toggle-label login-field full');
    pubLabel.append(pub, document.createTextNode(` ${t('Auf der öffentlichen Statusseite zeigen (Titel und Einträge, ohne Zusammenfassung und Namen)')}`));
    form.appendChild(pubLabel);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.append(save, button('Abbrechen', 'btn-secondary', () => { wrap.hidden = true; wrap.innerHTML = ''; }));
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      const body = { title: title.value.trim(), severity: sev.value, started: Math.floor(new Date(started.value).getTime() / 1000),
        services: [...svc.querySelectorAll('input:checked')].map(c => c.value), summary: summary.value, public: pub.checked };
      if (!item) Object.assign(body, { status: status.value, text: text.value });
      try {
        await api(item ? `/api/incidents/${item.id}` : '/api/incidents', body);
        wrap.hidden = true;
        wrap.innerHTML = '';
        await load();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    setTimeout(() => title.focus(), 30);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function load() {
    try { data = await api('/api/incidents'); render(); } catch (e) { /* not logged in */ }
  }

  async function init() {
    if (!$('stoerungen')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('inc-add').addEventListener('click', () => openForm(null));
    $('inc-filter').addEventListener('change', () => data && render());
    await load();
  }

  init();
})();
