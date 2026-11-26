/**
 * HostCore Dashboard - system page section "Externe Sicherung": restic backups of this server to Google Drive,
 * Dropbox, Nextcloud/WebDAV, S3 or a USB drive; states, restore, check; signing in to Google / Dropbox.
 */
(function () {
  'use strict';

  const U = window.OpsUI;
  if (!U) return;
  const { t, el, raw, toast, api, time, size, tag, button, confirmButton, run, can } = U;
  const $ = id => document.getElementById(id);
  const openTask = task => { if (window.PiTasks && task && task.id) window.PiTasks.open(task.id); };
  const DAYS = [['0', 'Mo'], ['1', 'Di'], ['2', 'Mi'], ['3', 'Do'], ['4', 'Fr'], ['5', 'Sa'], ['6', 'So']];
  const STATE = { ok: ['good', 'In Ordnung'], error: ['critical', 'Fehler'], overdue: ['warning', 'Überfällig'], running: ['warning', 'Läuft'], never: ['', 'Noch nie gelaufen'] };
  const HINT = {
    drive: 'Mit Google anmelden: Es öffnet sich die Google-Anmeldung. Danach landet der Browser auf einer Seite, die nicht lädt (127.0.0.1:53682) – deren Adresse kopieren und hier einfügen.',
    dropbox: 'Mit Dropbox anmelden: Es öffnet sich die Dropbox-Anmeldung. Danach landet der Browser auf einer Seite, die nicht lädt (127.0.0.1:53682) – deren Adresse kopieren und hier einfügen.',
    webdav: 'Bei Nextcloud: Adresse https://<cloud>/remote.php/dav/files/<benutzer> und ein App-Passwort (Einstellungen → Sicherheit), nicht das normale Passwort.',
    s3: 'Für Amazon S3, Backblaze B2, Hetzner Object Storage, Wasabi, MinIO … – der Bucket muss schon existieren.',
    local: 'Ein eingehängtes Laufwerk, z. B. /mnt/usb/hostcore. Am besten ein Laufwerk, das nicht ständig am Server hängt.'
  };
  let data = null;
  let fleet = null;          // all servers (main dashboard with agents only), else null
  let pollTimer = 0;

  // ---------------------------------------------------------------------------
  // List
  // ---------------------------------------------------------------------------
  function schedText(s) {
    if (!s || !s.enabled) return t('Nur von Hand');
    const days = s.days.length === 7 ? t('täglich') : s.days.map(d => t(DAYS[d][1])).join(', ');
    return `${days} · ${String(s.hour).padStart(2, '0')}:00`;
  }

  // Generations (grandfather-father-son): how many daily, weekly, monthly and yearly states stay
  const GENERATIONS = [['daily', 'Söhne', 'täglich'], ['weekly', 'Väter', 'wöchentlich'], ['monthly', 'Großväter', 'monatlich'], ['yearly', 'Urgroßväter', 'jährlich']];
  function keepText(k) {
    return GENERATIONS.filter(([key]) => k[key]).map(([key, gen, every]) => `${k[key]} ${t(gen)} (${t(every)})`).join(' · ');
  }

  const WEEKDAYS = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];
  function verifyText(j) {
    const v = j.verify || {};
    const plan = v.enabled ? `${t(v.every === 'monthly' ? 'monatlich, am ersten' : 'wöchentlich,')} ${t(WEEKDAYS[v.weekday] || '')} ${String(v.hour).padStart(2, '0')}:00` : t('aus');
    const last = j.last_check ? ` · ${t('zuletzt')} ${time(j.last_check)}: ${t(j.last_check_state === 'ok' ? 'in Ordnung' : 'Fehler')}${j.check_part ? ` (${t('Teil')} ${j.check_part}/${data.check_parts})` : ''}` : '';
    return plan + last;
  }

  // Durations as "1 Std. 05 Min." / "4 Min. 10 s" / "35 s"
  function dur(s) {
    if (s == null) return '';
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h) return `${h} ${t('Std.')} ${String(m).padStart(2, '0')} ${t('Min.')}`;
    if (m) return `${m} ${t('Min.')} ${String(sec).padStart(2, '0')} s`;
    return `${sec} s`;
  }

  // A running backup: phase, meter, how much is done, how long it has run and still runs
  function progressBox(p) {
    const box = el('div', 'os-progress');
    const head = el('div', 'os-progress-head');
    const pct = p.percent == null ? null : Math.min(100, Math.max(0, p.percent));
    head.append(el('span', 'os-progress-phase', t(p.label)), raw('strong', 'os-progress-pct', pct == null ? '' : `${Math.floor(pct)} %`));
    box.appendChild(head);
    const bar = el('div', 'progress-bar-bg os-progress-bar');
    const fill = el('div', `progress-bar-fill${pct == null ? ' os-indeterminate' : ''}`);
    fill.style.width = pct == null ? '100%' : `${pct}%`;
    fill.style.height = '100%';
    bar.appendChild(fill);
    box.appendChild(bar);
    const parts = [];
    if (p.phase === 'backup' && p.total_files) {
      parts.push(`${Number(p.files_done || 0).toLocaleString(U.LOCALE)} ${t('von')} ${Number(p.total_files).toLocaleString(U.LOCALE)} ${t('Dateien')}`);
      if (p.total_bytes) parts.push(`${size(p.bytes_done || 0)} ${t('von')} ${size(p.total_bytes)}`);
    }
    if (p.uploaded != null) parts.push(`${t('hochgeladen')} ${size(p.uploaded)}${p.upload_speed != null ? ` · ${size(p.upload_speed)}/s` : ''}`);
    if (p.errors) parts.push(`${p.errors} ${t('nicht lesbar')}`);
    if (parts.length) box.appendChild(raw('div', 'os-progress-line', parts.join(' · ')));
    const times = [`${t('läuft seit')} ${dur(p.elapsed)}`];
    if (p.remaining != null) times.push(`${t(p.estimate ? 'noch etwa' : 'noch')} ${dur(p.remaining)}${p.estimate ? ` (${t('geschätzt aus dem letzten Lauf')})` : ''}`);
    else if (!p.previous) times.push(t('Restzeit wird nach dem Durchsuchen der Ordner berechnet'));
    box.appendChild(raw('div', 'os-progress-line field-hint', times.join(' · ')));
    return box;
  }

  function render() {
    const list = $('os-list');
    list.innerHTML = '';
    $('os-summary').textContent = data.jobs.length ? [`${data.jobs.length} ${t(data.jobs.length === 1 ? 'Sicherung' : 'Sicherungen')}`, data.problems ? `${data.problems} ${t('mit Problem')}` : ''].filter(Boolean).join(' · ') : '';
    const inst = $('os-install');
    inst.innerHTML = '';
    const missing = Object.entries(data.tools).filter(([, ok]) => !ok).map(([n]) => n);
    if (missing.length) {
      inst.appendChild(el('span', 'field-hint', `${t('Noch nicht installiert')}: ${missing.join(', ')}`));
      inst.appendChild(button('restic und rclone installieren', 'btn-secondary', async () => {
        const r = await run(() => api('/api/offsite/install', {}));
        if (r) toast(t(r.message), 'success');
      }, 'users.manage'));
    }
    const others = fleet ? fleet.servers.filter(s => !s.local) : [];
    if (others.length) list.appendChild(el('h3', 'subhead os-server-head', `${t('Dieser Server')} · ${fleet.servers[0].name}`));
    if (!data.jobs.length) list.appendChild(el('p', 'field-hint', t('Noch keine externe Sicherung eingerichtet.')));
    data.jobs.forEach(j => list.appendChild(card(j)));
    others.forEach(s => list.appendChild(serverGroup(s)));
    clearTimeout(pollTimer);
    const running = data.jobs.some(j => j.state === 'running') || others.some(s => s.jobs.some(j => j.state === 'running'));
    if (running) pollTimer = setTimeout(load, others.length ? 5000 : 3000);
  }

  // Another server's backups (through its agent): status, progress, start - set up and changed over there
  function serverGroup(s) {
    const box = el('div', 'os-server');
    const head = el('div', 'os-server-head');
    head.append(raw('h3', 'subhead', s.name), s.online ? (s.error ? tag('warning', 'Hinweis') : tag('good', 'erreichbar')) : tag('critical', 'nicht erreichbar'));
    if (s.online && window.PiHosts) head.appendChild(button('Dort öffnen', 'text-btn', () => window.PiHosts.select(s.id)));
    box.appendChild(head);
    if (s.error) box.appendChild(el('p', 'field-hint warn', t(s.error)));
    else if (!s.jobs.length) box.appendChild(el('p', 'field-hint', t('Keine externe Sicherung – mit „Auf andere Server übertragen“ bei einer Sicherung oben einrichten.')));
    s.jobs.forEach(j => box.appendChild(card(j, s)));
    return box;
  }

  // Copy a backup of this server to others; the same name there is updated
  function copyPanel(j, c) {
    let box = c.querySelector('.os-copy');
    if (box) { box.remove(); return; }
    box = el('div', 'os-copy');
    const others = fleet.servers.filter(s => !s.local);
    box.appendChild(el('p', 'field-hint', t('Richtet diese Sicherung mit demselben Ziel, derselben Anmeldung, derselben Auswahl und demselben Passwort auch auf den gewählten Servern ein. Jeder Server sichert in seinen eigenen Ordner (HostCore/<servername>). Gibt es dort schon eine Sicherung mit diesem Namen, wird sie aktualisiert.')));
    const wrap = el('div', 'inc-targets pb-checks');
    others.forEach(s => {
      const l = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = s.id;
      i.disabled = !s.online || !!s.error;
      const has = s.jobs.some(x => x.name === j.name);
      l.append(i, ' ', raw('span', '', s.name), el('span', 'field-hint', has ? ` (${t('hat sie schon – wird aktualisiert')})` : !s.online ? ` (${t('nicht erreichbar')})` : s.error ? ` (${t('zu alt')})` : ''));
      wrap.appendChild(l);
    });
    box.appendChild(wrap);
    const act = el('div', 'form-actions');
    act.appendChild(button('Übertragen', 'btn-primary', async b => {
      const ids = [...wrap.querySelectorAll('input:checked')].map(i => i.value);
      if (!ids.length) { toast(t('Bitte mindestens einen Server wählen.'), 'error'); return; }
      b.disabled = true;
      const r = await run(() => api('/api/offsite/copy', { job: j.id, servers: ids }));
      b.disabled = false;
      if (!r) return;
      r.results.forEach(x => toast(`${x.name}: ${t(x.message)}`, x.ok ? 'success' : 'error'));
      if (r.results.some(x => x.ok)) await load();
    }, 'users.manage'));
    box.appendChild(act);
    c.appendChild(box);
  }

  function card(j, server) {
    const c = el('div', `backup-card ${j.state}`);
    const head = el('div', 'backup-head');
    const [cls, label] = STATE[j.state] || ['', j.state];
    head.append(raw('span', 'backup-name', j.name), el('span', 'backup-kind', t(j.provider)), tag(cls, label));
    c.appendChild(head);
    c.appendChild(raw('span', 'backup-target mono', j.where));
    if (j.progress) c.appendChild(progressBox(j.progress));
    const facts = el('dl', 'backup-facts');
    const add = (k, v, cls2) => { if (v === '' || v == null) return; facts.append(el('dt', '', t(k)), raw('dd', cls2 || '', v)); };
    add('Was', [...j.sources.map(s => t((data.sources[s] || {}).label || s)), ...(j.custom || [])].join(', '));
    if (j.target.kind === 'drive') add('Google-Kennung', j.target.client_id ? t('eigene Client-ID') : t('gemeinsame von rclone – wird oft gedrosselt, eigene Client-ID eintragen'), j.target.client_id ? '' : 'warn');
    add('Zeitplan', schedText(j.schedule));
    add('Aufbewahrung', keepText(j.keep || {}));
    add('Prüfung', verifyText(j), j.last_check_state === 'error' ? 'warn' : '');
    if (j.last_check_state === 'error' && j.last_check_error) add('Prüffehler', j.last_check_error, 'warn');
    add('Zuletzt erfolgreich', j.last_ok ? time(j.last_ok) : '');
    const s = j.summary || {};
    if (j.last_ok && s.files != null) add('Letzter Lauf', `${Number(s.files).toLocaleString(U.LOCALE)} ${t('Dateien')} · ${size(s.bytes)} · ${t('neu')} ${size(s.added)} · ${dur(s.total_seconds != null ? s.total_seconds : s.seconds)}${s.partial ? ` · ${t('einige Dateien nicht lesbar')}` : ''}`);
    if (j.snapshots != null) add('Stände', String(j.snapshots));
    if (j.last_state === 'error' && j.last_error) add('Fehler', j.last_error, 'warn');
    c.appendChild(facts);
    const act = el('div', 'docs-row-actions');
    if (server) {
      // another server: start and check from here, everything else over there ("Dort öffnen")
      const remote = action => async b => {
        b.disabled = true;
        await run(() => api(`/api/offsite/fleet/${server.id}/${j.id}/${action}`, {}), action === 'run' ? `Sicherung auf ${server.name} gestartet.` : `Prüfung auf ${server.name} gestartet.`);
        b.disabled = false;
        await load();
      };
      act.append(button('Jetzt sichern', 'text-btn', remote('run'), 'settings.manage'), button('Prüfen', 'text-btn', remote('check'), 'settings.manage'));
      c.appendChild(act);
      return c;
    }
    if (fleet && fleet.servers.length > 1) act.appendChild(button('Auf andere Server übertragen', 'text-btn', () => copyPanel(j, c), 'users.manage'));
    act.append(
      button('Jetzt sichern', 'text-btn', async b => {
        b.disabled = true;
        const task = await run(() => api(`/api/offsite/${j.id}/run`, {}), 'Sicherung gestartet.');
        openTask(task);
        await load();
      }, 'settings.manage'),
      button('Stände & Wiederherstellen', 'text-btn', () => showSnapshots(j, c), 'system.view'),
      button('Prüfen', 'text-btn', async () => openTask(await run(() => api(`/api/offsite/${j.id}/check`, {}), 'Prüfung gestartet.')), 'settings.manage'),
      button('Passwort anzeigen', 'text-btn', async b => {
        const r = await run(() => api(`/api/offsite/${j.id}/password`, {}));
        if (!r) return;
        const box = raw('code', 'mono os-password', r.password);
        b.replaceWith(box);
      }, 'users.manage'),
      button('Bearbeiten', 'text-btn', () => openForm(j), 'users.manage'),
      confirmButton('Entfernen', 'text-btn danger', async () => {
        const r = await run(() => api(`/api/offsite/${j.id}/delete`, {}), 'Entfernt – die Daten im Ziel bleiben erhalten. Wie man auch sie und den Zugriff entfernt: Anleitung, Abschnitt Rückbau.');
        if (r) { data = r; render(); }
      }, 'users.manage'));
    c.appendChild(act);
    return c;
  }

  async function showSnapshots(j, c) {
    let box = c.querySelector('.os-snapshots');
    if (box) { box.remove(); return; }
    box = el('div', 'os-snapshots');
    box.appendChild(el('p', 'field-hint', t('Lese die Stände aus dem Ziel …')));
    c.appendChild(box);
    let r;
    try { r = await api(`/api/offsite/${j.id}/snapshots`); } catch (e) { box.innerHTML = ''; box.appendChild(el('p', 'field-hint warn', t(e.message))); return; }
    box.innerHTML = '';
    if (!r.exists) { box.appendChild(el('p', 'field-hint', t('Das Ziel ist erreichbar, aber noch leer – die erste Sicherung legt das Repository an.'))); return; }
    const tbl = el('table', 'data-table');
    const rows = r.snapshots.slice().reverse().map(s => {
      const a = el('div', 'ops-actions');
      a.appendChild(button('Wiederherstellen', 'text-btn', () => restoreForm(j, s, a), 'users.manage'));
      const sm = s.summary || {};
      return [raw('code', 'mono', s.id), time(Date.parse(s.time) / 1000), sm.total_files_processed != null ? Number(sm.total_files_processed).toLocaleString(U.LOCALE) : '–',
        sm.total_bytes_processed != null ? size(sm.total_bytes_processed) : '–', a];
    });
    U.table(tbl, ['Stand', 'Zeitpunkt', 'Dateien', 'Größe', ''], rows, 'Noch keine Stände.');
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(tbl);
    box.appendChild(wrap);
  }

  function restoreForm(j, s, row) {
    row.innerHTML = '';
    const i = el('input');
    i.type = 'text';
    i.placeholder = t('nur dieser Pfad (leer = alles)');
    i.spellcheck = false;
    row.append(i, button('Wiederherstellen', 'text-btn', async () => {
      const task = await run(() => api(`/api/offsite/${j.id}/restore`, { snapshot: s.id, path: i.value.trim() }),
        `Wird nach ${data.restore_dir}/ wiederhergestellt – dort liegen die Dateien, nichts wird überschrieben.`);
      openTask(task);
    }, 'users.manage'));
    i.focus();
  }

  // ---------------------------------------------------------------------------
  // Form
  // ---------------------------------------------------------------------------
  function field(form, label, input, full) {
    const wrap = el('label', `login-field${full ? ' full' : ''}`);
    wrap.append(el('span', '', t(label)), input);
    form.appendChild(wrap);
    return wrap;
  }

  function input(type, value, attrs = {}) {
    const i = el(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') i.type = type;
    i.value = value == null ? '' : String(value);
    i.spellcheck = false;
    Object.entries(attrs).forEach(([k, v]) => { i[k] = v; });
    return i;
  }

  function checks(form, label, options, chosen) {
    const box = el('div', 'login-field full');
    box.appendChild(el('span', '', t(label)));
    const wrap = el('div', 'inc-targets pb-checks');
    const set = new Set((chosen || []).map(String));
    options.forEach(([val, lbl, title]) => {
      const l = el('label', 'toggle-label');
      if (title) l.title = t(title);
      const c = el('input');
      c.type = 'checkbox';
      c.value = val;
      c.checked = set.has(String(val));
      l.append(c, ' ', el('span', '', t(lbl)));
      wrap.appendChild(l);
    });
    box.appendChild(wrap);
    form.appendChild(box);
    return () => [...wrap.querySelectorAll('input:checked')].map(c => c.value);
  }

  function openForm(job) {
    const wrap = $('os-form-wrap');
    const v = job || { sources: ['docker', 'dashboard', 'system'], target: { kind: 'drive' }, keep: { daily: 7, weekly: 4, monthly: 6, yearly: 2 },
      schedule: { enabled: true, hour: 3, days: [0, 1, 2, 3, 4, 5, 6] }, verify: data.default_verify };
    const old = v.target || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(job ? 'Externe Sicherung bearbeiten' : 'Externe Sicherung einrichten')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';

    const name = input('text', v.name || '', { maxLength: 60, required: true, placeholder: t('z. B. Pi nach Google Drive') });
    field(form, 'Name', name);
    const kind = el('select');
    Object.entries(data.providers).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; kind.appendChild(o); });
    kind.value = old.kind || 'drive';
    field(form, 'Ziel', kind);

    // Provider fields, rebuilt when the provider changes
    const tbox = el('div', 'inline-grid full os-target');
    form.appendChild(tbox);
    const tget = {};
    let token = '';
    const buildTarget = () => {
      tbox.innerHTML = '';
      Object.keys(tget).forEach(k => delete tget[k]);
      const k = kind.value;
      const same = old.kind === k;
      const val = key => (same ? old[key] : '') || '';
      tbox.appendChild(el('p', 'field-hint full', t(HINT[k])));
      if (k === 'drive' || k === 'dropbox') {
        token = '';
        // Own Google client (recommended): its own quota instead of the one every rclone user shares
        let cid = null, csec = null;
        if (k === 'drive') {
          const own = el('details', 'db-details full');
          own.open = !!val('client_id');
          own.appendChild(el('summary', '', t('Eigene Google-Client-ID (empfohlen)')));
          const help = el('p', 'field-hint');
          help.append(t('Ohne eigene Client-ID teilt sich die Sicherung das Kontingent aller rclone-Nutzer – Google drosselt das oft, das Hochladen bleibt dann stehen. Eine eigene ist in fünf Minuten angelegt: '));
          const a = el('a', '', t('Anleitung von rclone'));
          a.href = 'https://rclone.org/drive/#making-your-own-client-id';
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          const b = el('a', '', t('Schritt für Schritt'));
          b.href = 'https://github.com/Follor09/HostCore/blob/main/docs/EXTERNE-SICHERUNG.md#google-drive-eigene-client-id';
          b.target = '_blank';
          b.rel = 'noopener noreferrer';
          help.append(a, ' · ', b, t('. Danach hier eintragen und neu verbinden.'));
          own.appendChild(help);
          const g = el('div', 'inline-grid');
          cid = input('text', val('client_id'), { placeholder: '123456789-abc….apps.googleusercontent.com' });
          csec = input('password', '', { autocomplete: 'new-password', placeholder: same && old.has_client_secret ? t('gespeichert – leer lassen zum Behalten') : 'GOCSPX-…' });
          field(g, 'Client-ID', cid, true);
          field(g, 'Client-Secret', csec, true);
          own.appendChild(g);
          tbox.appendChild(own);
          tget.client_id = () => cid.value.trim();
          tget.client_secret = () => csec.value.trim();
        }
        const state = el('span', 'field-hint', same && old.has_token ? t('Verbunden – nur neu verbinden, wenn nötig.') : t('Noch nicht verbunden.'));
        const row = el('div', 'vm-manage-row full');
        const addr = input('text', '', { placeholder: 'http://127.0.0.1:53682/?state=…&code=…' });
        const addrWrap = el('label', 'login-field full');
        addrWrap.append(el('span', '', t('Adresse nach der Anmeldung')), addr);
        addrWrap.hidden = true;
        const finish = button('Anmeldung abschließen', 'btn-secondary', async b => {
          b.disabled = true;
          const r = await run(() => api('/api/offsite/auth/finish', { address: addr.value.trim() }), 'Verbunden.');
          b.disabled = false;
          if (r) { token = r.token; state.textContent = t('Verbunden.'); addrWrap.hidden = true; finish.hidden = true; }
        });
        finish.hidden = true;
        row.append(button(`Mit ${data.providers[k]} verbinden`, 'btn-secondary', async b => {
          b.disabled = true;
          const r = await run(() => api('/api/offsite/auth/start', { kind: k, client_id: cid ? cid.value.trim() : '', client_secret: csec ? csec.value.trim() : '',
            job: job ? job.id : '' }));
          b.disabled = false;
          if (!r) return;
          window.open(r.url, '_blank', 'noopener');
          addrWrap.hidden = false;
          finish.hidden = false;
          state.textContent = t('Im neuen Tab anmelden, dann die Adresse hier einfügen.');
          addr.focus();
        }), state);
        tbox.append(row, addrWrap);
        const fin = el('div', 'form-actions full');
        fin.appendChild(finish);
        tbox.appendChild(fin);
        const manual = el('details', 'db-details full');
        manual.appendChild(el('summary', '', t('Anmeldung von Hand einfügen')));
        const tok = input('textarea', '', { rows: 3, placeholder: `rclone authorize "${k}"  →  {"access_token": …}` });
        tok.addEventListener('input', () => { token = tok.value.trim(); });
        manual.append(el('p', 'field-hint', t(`Auf einem Rechner mit Browser und rclone: rclone authorize "${k}" ausführen und die ausgegebene Zeile in geschweiften Klammern hier einfügen.`)), tok);
        tbox.appendChild(manual);
        tget.token = () => token;
        const folder = input('text', val('folder'), { placeholder: 'HostCore/<server>' });
        field(tbox, 'Ordner im Ziel', folder);
        tget.folder = () => folder.value.trim();
        if (k === 'drive') {
          const rid = input('text', val('root_folder_id'), { placeholder: t('optional') });
          field(tbox, 'ID eines Drive-Ordners als Wurzel', rid);
          tget.root_folder_id = () => rid.value.trim();
        }
      } else if (k === 'webdav') {
        const url = input('url', val('url'), { placeholder: 'https://cloud.example.de/remote.php/dav/files/anna' });
        field(tbox, 'WebDAV-Adresse', url, true);
        const vendor = el('select');
        [['nextcloud', 'Nextcloud'], ['owncloud', 'ownCloud'], ['sharepoint', 'SharePoint'], ['other', 'Anderer']].forEach(([a, b]) => { const o = raw('option', '', b); o.value = a; vendor.appendChild(o); });
        vendor.value = val('vendor') || 'nextcloud';
        field(tbox, 'Anbieter', vendor);
        const user = input('text', val('user'), { autocomplete: 'off' });
        field(tbox, 'Benutzer', user);
        const pw = input('password', '', { autocomplete: 'new-password', placeholder: same && old.has_password ? t('gespeichert – leer lassen zum Behalten') : t('App-Passwort') });
        field(tbox, 'Passwort', pw);
        const folder = input('text', val('folder'), { placeholder: 'HostCore/<server>' });
        field(tbox, 'Ordner im Ziel', folder);
        Object.assign(tget, { url: () => url.value.trim(), vendor: () => vendor.value, user: () => user.value.trim(), password: () => pw.value, folder: () => folder.value.trim() });
      } else if (k === 's3') {
        const ep = input('url', val('endpoint'), { placeholder: 'https://s3.eu-central-003.backblazeb2.com' });
        field(tbox, 'Endpunkt', ep, true);
        const bucket = input('text', val('bucket'));
        field(tbox, 'Bucket', bucket);
        const folder = input('text', val('folder'), { placeholder: '<server>' });
        field(tbox, 'Ordner im Bucket', folder);
        const region = input('text', val('region'), { placeholder: t('optional, z. B. eu-central-1') });
        field(tbox, 'Region', region);
        const key = input('text', val('key_id'));
        field(tbox, 'Key-ID', key);
        const secret = input('password', '', { autocomplete: 'new-password', placeholder: same && old.has_secret ? t('gespeichert – leer lassen zum Behalten') : '' });
        field(tbox, 'Secret', secret);
        Object.assign(tget, { endpoint: () => ep.value.trim(), bucket: () => bucket.value.trim(), folder: () => folder.value.trim(), region: () => region.value.trim(),
          key_id: () => key.value.trim(), secret: () => secret.value });
      } else {
        const path = input('text', val('path'), { placeholder: '/mnt/usb/hostcore' });
        field(tbox, 'Zielordner', path, true);
        tget.path = () => path.value.trim();
      }
    };
    kind.addEventListener('change', buildTarget);
    buildTarget();

    const sources = checks(form, 'Was sichern', Object.entries(data.sources).map(([k, s]) => [k, s.label, s.desc]), v.sources);
    const custom = input('textarea', (v.custom || []).join('\n'), { rows: 2, placeholder: '/srv/daten\n/home/anna/fotos' });
    field(form, 'Eigene Ordner (je Zeile einer, unter /home, /srv, /opt, /mnt, /media, /data, /var/www)', custom, true);
    const exclude = input('textarea', (v.exclude || []).join('\n'), { rows: 2, placeholder: '*.tmp\n/var/log/journal' });
    field(form, 'Ausnahmen (Muster, je Zeile eines)', exclude, true);

    const s = v.schedule || {};
    const enabled = el('label', 'toggle-label full');
    const en = el('input');
    en.type = 'checkbox';
    en.checked = !!s.enabled;
    enabled.append(en, ' ', el('span', '', t('Automatisch nach Zeitplan sichern')));
    form.appendChild(enabled);
    const days = checks(form, 'Wochentage', DAYS, (s.days || []).map(String));
    const hour = input('number', s.hour ?? 3, { min: 0, max: 23 });
    field(form, 'Uhrzeit (Stunde)', hour);
    const k = v.keep || {};
    form.appendChild(el('p', 'field-hint full', t('Aufbewahrung nach dem Generationenprinzip: Jeder Lauf überträgt nur Änderungen, trotzdem ist jeder Stand vollständig wiederherstellbar – ein wöchentliches Vollbackup ist darum nicht nötig. Behalten wird der jeweils letzte Stand jedes Tages (Söhne), jeder Woche (Väter), jedes Monats (Großväter) und jedes Jahres (Urgroßväter). 7 / 4 / 6 / 2 heißt: eine Woche tageweise, einen Monat wochenweise, ein halbes Jahr monatsweise und zwei Jahre jahresweise. 0 = diese Generation nicht.')));
    const keepIn = {};
    GENERATIONS.forEach(([key, gen, every]) => {
      keepIn[key] = input('number', k[key] ?? 0, { min: 0, max: 1000 });
      field(form, `${t(gen)} – ${t(every)}e Stände`, keepIn[key]);
    });
    const vf = v.verify || {};
    const ven = el('label', 'toggle-label full');
    const venIn = el('input');
    venIn.type = 'checkbox';
    venIn.checked = !!vf.enabled;
    ven.append(venIn, ' ', el('span', '', t(`Automatisch prüfen – liest bei jedem Lauf ein anderes ${data.check_parts}tel der Daten aus dem Ziel zurück, nach ${data.check_parts} Prüfungen ist alles einmal gelesen`)));
    form.appendChild(ven);
    const every = el('select');
    [['weekly', 'Wöchentlich'], ['monthly', 'Monatlich (erster Wochentag im Monat)']].forEach(([a, b]) => { const o = el('option', '', t(b)); o.value = a; every.appendChild(o); });
    every.value = vf.every || 'weekly';
    field(form, 'Prüfen', every);
    const wday = el('select');
    WEEKDAYS.forEach((d, i) => { const o = el('option', '', t(d)); o.value = String(i); wday.appendChild(o); });
    wday.value = String(vf.weekday ?? 6);
    field(form, 'Am', wday);
    const vhour = input('number', vf.hour ?? 5, { min: 0, max: 23 });
    field(form, 'Um (Stunde)', vhour);
    const pw = input('password', '', { autocomplete: 'new-password', placeholder: job ? t('gespeichert – leer lassen zum Behalten') : t('leer = wird erzeugt') });
    field(form, 'Passwort der Sicherung (für ein vorhandenes Repository)', pw);

    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    const close = () => { wrap.hidden = true; wrap.innerHTML = ''; };
    actions.append(save, button('Abbrechen', 'btn-secondary', close));
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      const target = { kind: kind.value };
      Object.entries(tget).forEach(([key, g]) => { target[key] = g(); });
      if ((target.kind === 'drive' || target.kind === 'dropbox') && !target.token && job && old.kind === target.kind) delete target.token;
      const body = { name: name.value.trim(), target, sources: sources(), custom: custom.value, exclude: exclude.value,
        schedule: { enabled: en.checked, hour: Number(hour.value), days: days().map(Number) },
        keep: Object.fromEntries(GENERATIONS.map(([key]) => [key, Number(keepIn[key].value)])),
        verify: { enabled: venIn.checked, every: every.value, weekday: Number(wday.value), hour: Number(vhour.value) }, password: pw.value };
      try {
        data = await api(job ? `/api/offsite/${job.id}` : '/api/offsite', body);
        close();
        render();
        toast(t(job ? 'Gespeichert.' : 'Eingerichtet. Das Passwort der Sicherung jetzt über „Passwort anzeigen“ an einem sicheren Ort ablegen – ohne es lässt sich nichts wiederherstellen.'), 'success');
      } catch (err) { toast(t(err.message), 'error'); }
      save.disabled = false;
    });
    wrap.appendChild(form);
    setTimeout(() => name.focus(), 30);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function load() {
    try {
      const onRemote = window.PiHost && window.PiHost.isRemote();
      const [d, f] = await Promise.all([api('/api/offsite'), onRemote ? null : api('/api/offsite/fleet').catch(() => null)]);
      data = d;
      fleet = f && f.servers && f.servers.length > 1 ? f : null;
      if (fleet) fleet.servers[0].jobs = data.jobs;
      render();
    } catch (e) { /* no permission or offline */ }
  }

  async function init() {
    if (!$('os-list')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('os-add').addEventListener('click', () => { if (data) openForm(null); });
    if (!can('system.view')) return;
    await load();
  }

  init();
})();
