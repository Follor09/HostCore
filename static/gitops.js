/**
 * PiCore Dashboard - container page section "GitOps": compose stacks from Git repositories.
 * Create (clone), check for new commits, look at the diff, deploy (as a task), settings,
 * deploy webhook, drift and deployment history.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('go-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const STATE = {
    synced: ['good', 'Synchron'], behind: ['warning', 'Neue Commits'], drift: ['warning', 'Weicht ab'],
    error: ['critical', 'Fehler'], deploying: ['', 'Rollt aus…'], new: ['', 'Noch nicht ausgerollt']
  };
  const open = new Set();
  let data = null;
  let timer = null;

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
    setTimeout(() => n.remove(), 5000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–');
  const short = sha => (sha ? sha.slice(0, 8) : '–');

  function confirmButton(label, cls, action) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', async () => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t('Sicher?');
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = t(label);
      b.disabled = true;
      try { await action(); } catch (err) { toast(err.message, 'error'); }
      b.disabled = false;
    });
    return b;
  }

  // ---------------------------------------------------------------------------
  // Unified diff with coloured lines
  // ---------------------------------------------------------------------------
  function diffView(text) {
    const pre = raw('pre', 'go-diff');
    text.split('\n').forEach(line => {
      const cls = line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ') ? 'file'
        : line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
      const span = el('span', cls, line);
      pre.append(span, '\n');
    });
    return pre;
  }

  async function showDiff(s, box) {
    box.innerHTML = '';
    box.appendChild(el('p', 'field-hint', t('Lade…')));
    try {
      const d = await api(`/api/gitops/${s.name}/diff`);
      box.innerHTML = '';
      if (d.first) { box.appendChild(el('p', 'field-hint', t('Noch nichts ausgerollt – beim ersten Ausrollen wird der Stand des Repositorys übernommen.'))); return; }
      if (d.diff) {
        box.appendChild(el('h4', 'go-sub', `${t('Änderungen')} ${short(d.base)} → ${short(d.target)}`));
        if (d.stat) box.appendChild(raw('pre', 'go-stat', d.stat.trim()));
        box.appendChild(diffView(d.diff));
        if (d.truncated) box.appendChild(el('p', 'field-hint', t('Gekürzt – das vollständige Diff steht im Repository.')));
      } else {
        box.appendChild(el('p', 'field-hint', t('Keine neuen Änderungen im Ordner des Stacks.')));
      }
      if (d.local) {
        box.appendChild(el('h4', 'go-sub warn', t('Auf dem Pi geändert (wird beim Ausrollen überschrieben)')));
        box.appendChild(diffView(d.local));
      }
    } catch (err) { box.innerHTML = ''; box.appendChild(el('p', 'form-msg error', err.message)); }
  }

  // ---------------------------------------------------------------------------
  // Card per stack
  // ---------------------------------------------------------------------------
  function driftList(s) {
    const d = s.drift || {};
    const items = [];
    (s.local_changes || []).forEach(f => items.push(`${t('Datei geändert')}: ${f}`));
    (d.missing || []).forEach(x => items.push(`${t('Dienst fehlt')}: ${x}`));
    (d.stopped || []).forEach(x => items.push(`${t('Dienst gestoppt')}: ${x}`));
    (d.image || []).forEach(x => items.push(`${x.service}: ${t('läuft mit')} ${x.have || '?'} ${t('statt')} ${x.want}`));
    if (d.error) items.push(d.error);
    return items;
  }

  function card(s) {
    const [cls, label] = STATE[s.state] || ['', s.state];
    const c = el('div', `backup-card go-card${s.state === 'error' ? ' error' : s.state === 'drift' ? ' overdue' : ''}`);
    const head = el('div', 'backup-head');
    head.append(raw('span', 'backup-name', s.name), el('span', `health-tag ${cls}`, s.state === 'behind' ? `${s.behind} ${t(s.behind === 1 ? 'neuer Commit' : 'neue Commits')}` : t(label)));
    if (s.auto) head.appendChild(el('span', 'backup-kind', t('Automatisch')));
    c.appendChild(head);
    c.appendChild(raw('span', 'backup-target', `${s.url} · ${s.branch}${s.path ? ` · ${s.path}` : ''} · ${s.file}`));

    const facts = el('dl', 'backup-facts');
    const fact = (k, v, warn) => { const dd = raw('dd', warn ? 'warn' : '', v); facts.append(el('dt', '', t(k)), dd); };
    fact('Ausgerollt', s.deployed ? `${short(s.deployed)} · ${fmtTs(s.deployed_at)}${s.deployed_by ? ` · ${s.deployed_by}` : ''}` : t('noch nicht'));
    fact('Repository', s.remote ? short(s.remote) : '–');
    fact('Geprüft', `${fmtTs(s.checked)} · ${t('alle')} ${s.interval} ${t('Min.')}`);
    if (s.error) fact('Fehler', s.error, true);
    c.appendChild(facts);

    const drift = driftList(s);
    if (drift.length) {
      const box = el('div', 'db-problem warning');
      box.appendChild(el('strong', '', t('Abweichung vom Repository')));
      const ul = el('ul', 'go-drift');
      drift.forEach(x => ul.appendChild(raw('li', '', x)));
      box.appendChild(ul);
      c.appendChild(box);
    }

    if ((s.pending || []).length) {
      const det = el('details', 'db-details');
      det.open = open.has(`p:${s.name}`) || s.pending.length <= 3;
      det.addEventListener('toggle', () => (det.open ? open.add(`p:${s.name}`) : open.delete(`p:${s.name}`)));
      det.appendChild(el('summary', '', `${s.pending.length} ${t(s.pending.length === 1 ? 'Commit zum Ausrollen' : 'Commits zum Ausrollen')}`));
      const ul = el('ul', 'go-commits');
      s.pending.forEach(cm => {
        const li = el('li');
        li.append(raw('code', '', short(cm.sha)), raw('span', 'go-subject', cm.subject), raw('span', 'go-meta', `${cm.author} · ${fmtTs(cm.ts)}`));
        ul.appendChild(li);
      });
      det.appendChild(ul);
      c.appendChild(det);
    }

    if ((s.history || []).length) {
      const det = el('details', 'db-details');
      det.open = open.has(`h:${s.name}`);
      det.addEventListener('toggle', () => (det.open ? open.add(`h:${s.name}`) : open.delete(`h:${s.name}`)));
      det.appendChild(el('summary', '', t('Verlauf')));
      const ul = el('ul', 'go-commits');
      s.history.forEach(h => {
        const li = el('li');
        li.append(el('span', `health-tag ${h.state === 'ok' ? 'good' : 'critical'}`, t(h.state === 'ok' ? 'OK' : 'Fehler')),
          raw('code', '', short(h.commit)), raw('span', 'go-subject', h.subject || ''), raw('span', 'go-meta', `${h.user || ''} · ${fmtTs(h.ts)}`));
        ul.appendChild(li);
      });
      det.appendChild(ul);
      c.appendChild(det);
    }

    const diffBox = el('div', 'go-diff-box');
    diffBox.hidden = true;
    const extra = el('div', 'go-extra');
    extra.hidden = true;

    const actions = el('div', 'docs-row-actions');
    const btn = (label, fn, cls = 'text-btn') => { const b = el('button', cls, t(label)); b.type = 'button'; b.addEventListener('click', fn); actions.appendChild(b); return b; };
    const check = btn('Prüfen', async () => {
      check.disabled = true;
      try { replace(await api(`/api/gitops/${s.name}/check`, {})); } catch (err) { toast(err.message, 'error'); }
      check.disabled = false;
    });
    btn('Änderungen', () => {
      diffBox.hidden = !diffBox.hidden;
      if (!diffBox.hidden) showDiff(s, diffBox);
    });
    if (can('apps.install')) {
      // One inline panel at a time; the same button again closes it
      const panel = (kind, fn) => {
        if (!extra.hidden && extra.dataset.kind === kind) { extra.hidden = true; return; }
        extra.hidden = false;
        fn(s, extra);
      };
      btn('Einstellungen', () => panel('settings', settingsForm));
      btn('Webhook', () => panel('hook', hookBox));
      btn('Entfernen', () => panel('remove', removeBox), 'text-btn danger');
    }
    if (can('services.control') && s.state !== 'deploying' && s.remote && (s.state !== 'synced' || !s.deployed)) {
      const label = s.deployed ? (s.behind ? `${short(s.remote)} ausrollen` : 'Erneut ausrollen') : 'Jetzt ausrollen';
      actions.appendChild(confirmButton(label, 'btn-primary go-deploy', async () => {
        const r = await api(`/api/gitops/${s.name}/deploy`, { commit: s.remote });
        toast(t('Ausrollen gestartet – Fortschritt in den Aufgaben.'), 'success');
        replace(r);
      }));
    }
    c.append(actions, extra, diffBox);
    return c;
  }

  // ---------------------------------------------------------------------------
  // Inline boxes: settings, webhook, remove
  // ---------------------------------------------------------------------------
  function intervalSelect(value) {
    const sel = el('select');
    (data.intervals || [5]).forEach(m => { const o = el('option', '', m < 60 ? `${m} ${t('Min.')}` : `${m / 60} ${t('Std.')}`); o.value = m; sel.appendChild(o); });
    sel.value = String(value || 5);
    return sel;
  }

  function field(box, label, input, full) {
    const w = el('label', `login-field${full ? ' full' : ''}`);
    w.append(el('span', '', t(label)), input);
    box.appendChild(w);
    return input;
  }

  function toggle(box, label, checked) {
    const w = el('label', 'toggle-label full');
    const i = el('input');
    i.type = 'checkbox';
    i.checked = !!checked;
    w.append(i, ` ${t(label)}`);
    box.appendChild(w);
    return i;
  }

  function input(value, ph, type = 'text') {
    const i = el('input');
    i.type = type;
    i.value = value || '';
    if (ph) i.placeholder = ph;
    i.spellcheck = false;
    i.autocomplete = type === 'password' ? 'new-password' : 'off';
    i.setAttribute('data-lpignore', 'true');
    return i;
  }

  function envArea() {
    const a = el('textarea', 'go-env');
    a.rows = 4;
    a.spellcheck = false;
    a.placeholder = 'DB_PASSWORD=…\nTZ=Europe/Berlin';
    a.setAttribute('data-no-i18n', '');
    return a;
  }

  async function settingsForm(s, box) {
    box.dataset.kind = 'settings';
    box.innerHTML = '';
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const branch = field(form, 'Branch', input(s.branch));
    const interval = field(form, 'Prüfen alle', intervalSelect(s.interval));
    const token = field(form, 'Token (leer = unverändert)', input('', s.has_token ? t('gespeichert') : t('nur für private Repositorys'), 'password'));
    const auto = toggle(form, 'Neue Commits automatisch ausrollen', s.auto);
    const rmToken = s.has_token ? toggle(form, 'Gespeicherten Token entfernen', false) : null;
    const env = field(form, 'Lokale Variablen (.env, bleibt auf dem Pi)', envArea(), true);
    try { env.value = (await api(`/api/gitops/${s.name}/env`)).env; } catch (e) { /* no permission */ }
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.appendChild(save);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        replace(await api(`/api/gitops/${s.name}/update`, { branch: branch.value.trim(), interval: Number(interval.value), token: token.value,
          remove_token: rmToken ? rmToken.checked : false, auto: auto.checked, env: env.value }));
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    box.appendChild(form);
  }

  function hookBox(s, box) {
    box.dataset.kind = 'hook';
    box.innerHTML = '';
    const url = `${location.origin}${s.hook_path}`;
    box.appendChild(el('p', 'field-hint', t('Diese Adresse beim Repository als Webhook (Push-Ereignis, POST) eintragen – dann prüft der Pi sofort statt erst nach dem Intervall. Ausgerollt wird nur, wenn „automatisch“ eingeschaltet ist. Der Pi muss dafür von außen erreichbar sein (z.B. Cloudflare Tunnel).')));
    const row = el('div', 'go-hook');
    const code = raw('code', '', url);
    const copy = el('button', 'btn-secondary', t('Kopieren'));
    copy.type = 'button';
    copy.addEventListener('click', () => navigator.clipboard.writeText(url).then(() => toast(t('Kopiert.'), 'success'), () => {}));
    row.append(code, copy, confirmButton('Neue Adresse', 'btn-secondary', async () => {
      replace(await api(`/api/gitops/${s.name}/hook`, {}));
      toast(t('Neue Webhook-Adresse – die alte gilt nicht mehr.'), 'success');
    }));
    box.appendChild(row);
  }

  function removeBox(s, box) {
    box.dataset.kind = 'remove';
    box.innerHTML = '';
    box.appendChild(el('p', 'field-hint', t('Beendet die Verwaltung aus Git. Ohne Häkchen laufen die Container weiter und der Ordner bleibt erhalten.')));
    const wrap = el('div', 'inline-grid');
    const stop = toggle(wrap, 'Stack stoppen und entfernen (docker compose down, Volumes bleiben)', false);
    const del = toggle(wrap, `Ordner ${s.repo_dir} löschen (auch Daten, die der Stack dort ablegt)`, false);
    box.appendChild(wrap);
    box.appendChild(confirmButton('GitOps beenden', 'btn-danger', async () => {
      data = await api(`/api/gitops/${s.name}/delete`, { stop: stop.checked, delete_files: del.checked });
      render();
      toast(t('Entfernt.'), 'success');
    }));
  }

  // ---------------------------------------------------------------------------
  // New stack from Git
  // ---------------------------------------------------------------------------
  function createForm() {
    const wrap = $('go-form-wrap');
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t('Stack aus Git anlegen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const name = field(form, 'Stack-Name', input('', 'uptime-kuma'));
    const url = field(form, 'Repository', input('', 'https://github.com/user/homelab.git'));
    const branch = field(form, 'Branch', input('main'));
    const path = field(form, 'Unterordner (leer = oberste Ebene)', input('', 'stacks/uptime-kuma'));
    const file = field(form, 'Compose-Datei (leer = automatisch)', input('', 'compose.yaml'));
    const token = field(form, 'Token (nur für private Repositorys über https)', input('', '', 'password'));
    const interval = field(form, 'Prüfen alle', intervalSelect(5));
    const env = field(form, 'Lokale Variablen (.env, bleibt auf dem Pi)', envArea(), true);
    const auto = toggle(form, 'Neue Commits automatisch ausrollen', false);
    const adopt = toggle(form, 'Vorhandenen Stack mit diesem Namen übernehmen', false);
    const now = can('services.control') ? toggle(form, 'Danach sofort ausrollen', true) : null;
    form.appendChild(el('p', 'field-hint full', t('Per SSH (git@…) nutzt der Pi die Schlüssel des Dashboard-Benutzers. Geklont wird nach ~/picore-apps/<Stack-Name>.')));
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Klonen & anlegen'));
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { wrap.hidden = true; wrap.innerHTML = ''; });
    actions.append(save, cancel);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      save.textContent = t('Klone…');
      try {
        const s = await api('/api/gitops', { name: name.value.trim(), url: url.value.trim(), branch: branch.value.trim(), path: path.value.trim(),
          file: file.value.trim(), token: token.value.trim(), interval: Number(interval.value), env: env.value, auto: auto.checked, adopt: adopt.checked });
        wrap.hidden = true;
        wrap.innerHTML = '';
        if (now && now.checked) {
          await api(`/api/gitops/${s.name}/deploy`, { commit: s.remote });
          toast(t('Angelegt – Ausrollen läuft als Aufgabe.'), 'success');
        } else {
          toast(t('Angelegt.'), 'success');
        }
        await load();
      } catch (err) {
        toast(err.message, 'error');
        save.disabled = false;
        save.textContent = t('Klonen & anlegen');
      }
    });
    wrap.appendChild(form);
    name.focus();
  }

  // ---------------------------------------------------------------------------
  function replace(s) {
    if (!data || !s || !s.name) return;
    const i = data.stacks.findIndex(x => x.name === s.name);
    if (i >= 0) data.stacks[i] = s;
    render();
  }

  function render() {
    const list = $('go-list');
    list.innerHTML = '';
    const st = data.stacks;
    const deploying = st.some(s => s.state === 'deploying');
    $('go-summary').textContent = st.length ? `${st.filter(s => s.state === 'synced').length} / ${st.length} ${t('synchron')}` : '';
    if (!data.git) list.appendChild(el('p', 'form-msg error', t('git ist auf diesem Pi nicht installiert (sudo apt install git).')));
    if (!st.length) list.appendChild(el('p', 'field-hint', t('Noch kein Stack aus Git. „Stack aus Git“ klont ein Repository und rollt den Ordner mit der Compose-Datei aus.')));
    st.forEach(s => list.appendChild(card(s)));
    clearTimeout(timer);
    if (deploying) timer = setTimeout(load, 4000);
  }

  async function load() {
    try { data = await api('/api/gitops'); render(); } catch (e) { /* no permission or remote Pi without GitOps */ }
  }

  $('go-add').addEventListener('click', createForm);
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    load();
  })();
})();
