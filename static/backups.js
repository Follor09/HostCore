/**
 * HostCore Dashboard - system page section "Backups": watched restic / Borg repositories and backup folders.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const STATE = { ok: ['good', 'Aktuell'], overdue: ['warning', 'Überfällig'], error: ['critical', 'Fehler'], unknown: ['', 'Noch nicht geprüft'] };
  const TARGET = {
    restic: ['Repository', '/mnt/backup/restic, sftp:user@host:/repo, rest:https://…, s3:…'],
    borg: ['Repository', '/mnt/backup/borg oder ssh://user@host/./repo'],
    folder: ['Ordner', '/mnt/backup/dumps']
  };
  let data = null;

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

  function fmtBytes(b) {
    if (b == null) return '';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = b ? Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024))) : 0;
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }
  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtAge = h => h == null ? '' : h < 1 ? t('vor weniger als einer Stunde') : t(h < 48 ? `vor ${Math.round(h)} Std.` : `vor ${Math.round(h / 24)} Tagen`);

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function render() {
    const list = $('bk-list');
    list.innerHTML = '';
    $('bk-summary').textContent = data.jobs.length ? [t(`${data.jobs.length} überwacht`), data.problems ? t(`${data.problems} mit Problem`) : ''].filter(Boolean).join(' · ') : '';
    if (!data.jobs.length) list.appendChild(el('p', 'field-hint', t('Noch keine Backups überwacht.')));
    data.jobs.forEach(j => {
      const card = el('div', `backup-card ${j.state}`);
      const head = el('div', 'backup-head');
      const [cls, label] = STATE[j.state];
      head.append(raw('span', 'backup-name', j.name), el('span', 'backup-kind', t(data.kinds[j.kind] || j.kind)),
        el('span', `health-tag ${cls}`, t(label)));
      if (j.running) head.appendChild(el('span', 'field-hint', t('Prüfe…')));
      card.appendChild(head);
      card.appendChild(raw('span', 'backup-target mono', j.target + (j.pattern ? `  (${j.pattern})` : '')));
      const facts = el('dl', 'backup-facts');
      const add = (k, v, cls2) => {
        if (v === '' || v == null) return;
        const dd = raw('dd', cls2 || '', v);
        facts.append(el('dt', '', t(k)), dd);
      };
      add('Letztes Backup', j.last_backup ? `${fmtTs(j.last_backup)} (${fmtAge(j.age_hours)})` : (j.checked && !j.error ? t('keins gefunden') : ''));
      add('Höchstalter', t(`${j.max_age_hours} Std.`));
      add(j.kind === 'folder' ? 'Dateien' : j.kind === 'borg' ? 'Archive' : 'Snapshots', j.snapshots != null ? j.snapshots.toLocaleString(LOCALE) + (j.truncated ? '+' : '') : '');
      add('Größe', fmtBytes(j.size));
      if (j.latest && j.latest.id) add(j.kind === 'folder' ? 'Neueste Datei' : 'Neuester Stand', [j.latest.id, j.latest.host, (j.latest.paths || []).join(', ')].filter(Boolean).join(' · '));
      add('Geprüft', j.checked ? `${fmtTs(j.checked)}${j.duration != null ? ` · ${j.duration} s` : ''}` : '');
      if (j.error) add('Fehler', j.error, 'warn');
      if (j.env_keys && j.env_keys.length) add('Umgebung', j.env_keys.join(', '));
      if (j.notes) add('Notiz', j.notes);
      card.appendChild(facts);
      if (can('settings.manage')) {
        const act = el('div', 'docs-row-actions');
        act.append(button('Jetzt prüfen', 'text-btn', async b => {
          b.disabled = true;
          try { data = await api(`/api/backups/${j.id}/check`, {}); render(); } catch (err) { toast(err.message, 'error'); b.disabled = false; }
        }), button('Bearbeiten', 'text-btn', () => openForm(j)), button('Entfernen', 'text-btn danger', async b => {
          if (!b.classList.contains('confirming')) {
            b.classList.add('confirming');
            b.textContent = t('Sicher?');
            setTimeout(() => { b.classList.remove('confirming'); b.textContent = t('Entfernen'); }, 4000);
            return;
          }
          try { data = await api(`/api/backups/${j.id}/delete`, {}); render(); } catch (err) { toast(err.message, 'error'); }
        }));
        card.appendChild(act);
      }
      list.appendChild(card);
    });
    if (data.jobs.some(j => j.running)) setTimeout(load, 3000);
  }

  function field(form, label, input, full) {
    const wrap = el('label', `login-field${full ? ' full' : ''}`);
    wrap.appendChild(el('span', '', t(label)));
    wrap.appendChild(input);
    form.appendChild(wrap);
    return wrap;
  }

  function input(type, value, attrs = {}) {
    const i = el(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') i.type = type;
    i.value = value == null ? '' : String(value);
    Object.entries(attrs).forEach(([k, v]) => { i[k] = v; });
    return i;
  }

  function openForm(job) {
    const wrap = $('bk-form-wrap');
    const v = job || { kind: data.tools.restic ? 'restic' : data.tools.borg ? 'borg' : 'folder', max_age_hours: 26 };
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(job ? 'Backup bearbeiten' : 'Backup überwachen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const kind = el('select');
    Object.entries(data.kinds).forEach(([k, l]) => {
      const o = el('option', '', t(l) + (k !== 'folder' && !data.tools[k] ? ` (${t('nicht installiert')})` : ''));
      o.value = k;
      kind.appendChild(o);
    });
    kind.value = v.kind;
    if (job) kind.disabled = true;
    field(form, 'Art', kind);
    const name = input('text', v.name, { maxLength: 60, required: true, placeholder: t('z. B. Nextcloud nach NAS') });
    field(form, 'Name', name);
    const target = input('text', v.target, { maxLength: 500, required: true, spellcheck: false });
    const targetWrap = field(form, 'Repository', target, true);
    const pass = input('password', '', { autocomplete: 'new-password' });
    const passWrap = field(form, 'Passwort', pass);
    const pattern = input('text', v.pattern, { maxLength: 100, placeholder: '*.sql.gz', spellcheck: false });
    const patternWrap = field(form, 'Nur Dateien wie', pattern);
    const age = input('number', v.max_age_hours, { min: 1, max: 2160 });
    field(form, 'Warnen, wenn älter als (Std.)', age);
    const env = input('textarea', '', { rows: 3, spellcheck: false, placeholder: 'AWS_ACCESS_KEY_ID=…\nAWS_SECRET_ACCESS_KEY=…' });
    const envWrap = field(form, 'Umgebungsvariablen (für S3, B2, SSH …)', env, true);
    const notes = input('text', v.notes, { maxLength: 300 });
    field(form, 'Notiz', notes, true);
    const sync = () => {
      const k = kind.value;
      targetWrap.firstChild.textContent = t(TARGET[k][0]);
      target.placeholder = TARGET[k][1];
      passWrap.hidden = envWrap.hidden = k === 'folder';
      patternWrap.hidden = k !== 'folder';
      pass.placeholder = job && job.has_password ? t('gespeichert – leer lassen zum Behalten') : '';
      env.placeholder = job && job.env_keys && job.env_keys.length ? `${t('gespeichert')}: ${job.env_keys.join(', ')} – ${t('leer lassen zum Behalten')}` : 'AWS_ACCESS_KEY_ID=…\nAWS_SECRET_ACCESS_KEY=…';
    };
    kind.addEventListener('change', sync);
    sync();
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.append(save, button('Abbrechen', 'btn-secondary', close));
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      const body = { kind: kind.value, name: name.value.trim(), target: target.value.trim(), max_age_hours: Number(age.value),
        pattern: pattern.value.trim(), notes: notes.value.trim(), password: pass.value, env: env.value };
      try {
        data = await api(job ? `/api/backups/${job.id}` : '/api/backups', body);
        close();
        render();
        toast(t('Gespeichert – die erste Prüfung läuft.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    setTimeout(() => name.focus(), 30);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function close() {
    $('bk-form-wrap').hidden = true;
    $('bk-form-wrap').innerHTML = '';
  }

  async function load() {
    try { data = await api('/api/backups'); render(); } catch (e) { /* no permission or offline */ }
  }

  async function init() {
    if (!$('bk-list')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('bk-add').addEventListener('click', () => openForm(null));
    await load();
    if (location.hash === '#backups') $('backups').scrollIntoView();
  }

  init();
})();
