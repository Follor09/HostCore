/**
 * PiCore Dashboard - app store on the containers page: recommended apps plus the
 * LinuxServer.io catalog with search, categories, sorting and logos; the install form
 * is built from each app's ports, folders, settings and devices, with a compose
 * preview and the live install output.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const PAGE = 24;
  const state = { data: null, group: '', shown: PAGE, form: null, timer: 0 };

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
    setTimeout(() => n.remove(), 4500);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  // Apps run on the Pi the dashboard shows (this one or the selected remote Pi)
  function appUrl(open) {
    if (!open || !open.port) return '';
    let host = location.hostname;
    const remote = window.PiHosts && window.PiHosts.current && window.PiHosts.current();
    if (remote && remote.url) { try { host = new URL(remote.url).hostname; } catch (e) { /* keep */ } }
    return `http://${host.includes(':') ? `[${host}]` : host}:${open.port}${open.path || ''}`;
  }

  // Logo from the Pi's cache, two-letter tile as fallback
  function appIcon(app) {
    let h = 0;
    for (const ch of app.name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    const box = raw('span', 'app-icon', app.name.replace(/[^A-Za-z0-9 -]/g, '').split(/[\s-]+/).map(w => w[0]).join('').slice(0, 2).toUpperCase());
    box.style.setProperty('--app-h', h);
    box.setAttribute('aria-hidden', 'true');
    if (app.logo) {
      const img = el('img');
      img.alt = '';
      img.decoding = 'async';
      img.src = `/api/appstore/logo/${encodeURIComponent(app.id)}`;
      img.addEventListener('load', () => { box.textContent = ''; box.classList.add('has-logo'); box.appendChild(img); });
    }
    return box;
  }

  const fmtPulls = n => n >= 1e6 ? `${(n / 1e6).toLocaleString(LOCALE, { maximumFractionDigits: 1 })} Mio.` : n >= 1e3 ? `${Math.round(n / 1e3)} Tsd.` : String(n);

  // ---------------------------------------------------------------------------
  // Catalog
  // ---------------------------------------------------------------------------
  function renderFilters() {
    const box = $('app-filters');
    box.innerHTML = '';
    [['', 'Alle'], ...state.data.groups.map(g => [g, g])].forEach(([id, label]) => {
      const b = el('button', state.group === id ? 'active' : '', t(label));
      b.type = 'button';
      b.addEventListener('click', () => { state.group = id; state.shown = PAGE; renderFilters(); renderGrid(); });
      box.appendChild(b);
    });
  }

  function filtered() {
    const q = $('app-q').value.trim().toLowerCase();
    const source = $('app-source').value;
    let apps = state.data.apps.filter(a => (!state.group || a.group === state.group)
      && (!source || (source === 'installed' ? a.installed.length : a.source === source))
      && (!q || [a.name, a.description, a.image, ...a.categories].some(v => (v || '').toLowerCase().includes(q))));
    const sort = $('app-sort').value;
    if (sort === 'name') apps = apps.slice().sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === 'updated') apps = apps.slice().sort((a, b) => (b.updated || '').localeCompare(a.updated || ''));
    else apps = apps.slice().sort((a, b) => (b.source === 'curated') - (a.source === 'curated') || (b.pulls || 0) - (a.pulls || 0));
    return apps;
  }

  const LEVEL = { active: ['good', 'Läuft'], warning: ['warning', 'Teilweise'], error: ['critical', 'Fehler'], inactive: ['', 'Gestoppt'], missing: ['', 'Entfernt'] };

  function appCard(a) {
    const card = el('div', 'app-card');
    const head = el('div', 'app-card-head');
    const title = el('div', 'app-card-title');
    title.appendChild(raw('b', '', a.name));
    const meta = [t(a.group), a.source === 'curated' ? t('Empfohlen') : 'LinuxServer.io'];
    if (a.pulls) meta.push(`${fmtPulls(a.pulls)} ${t('Downloads/Monat')}`);
    if (a.ram) meta.push(`RAM ${a.ram}`);
    title.appendChild(el('small', '', meta.join(' · ')));
    head.append(appIcon(a), title);
    card.appendChild(head);
    card.appendChild(raw('p', 'app-desc', a.description));
    const tags = el('div', 'app-tags');
    if (a.source === 'curated') tags.appendChild(el('span', 'health-tag good', t('Empfohlen')));
    if (a.advanced) tags.appendChild(el('span', 'health-tag warning', t('Erweiterte Rechte')));
    if (tags.childElementCount) card.appendChild(tags);

    if (a.installed.length) {
      const inst = el('div', 'app-installed');
      a.installed.forEach(i => {
        const [level, label] = LEVEL[i.level] || ['', i.level];
        inst.appendChild(raw('span', `health-tag ${level}`, `${i.name}: ${t(label)}`));
        const url = appUrl(i.open);
        if (url && i.level !== 'missing') {
          const link = el('a', 'text-btn', `${t('Öffnen')} ↗`);
          link.href = url;
          link.target = '_blank';
          link.rel = 'noopener';
          inst.appendChild(link);
        }
      });
      card.appendChild(inst);
    }
    const actions = el('div', 'app-actions');
    const btn = el('button', a.installed.length ? 'btn-secondary' : 'btn-primary', a.installed.length ? t('Noch einmal installieren') : t('Installieren'));
    btn.type = 'button';
    btn.disabled = !state.data.docker;
    btn.hidden = !can('apps.install');
    btn.addEventListener('click', () => openForm(a.id));
    actions.appendChild(btn);
    card.appendChild(actions);
    return card;
  }

  function renderGrid() {
    const grid = $('app-grid');
    grid.innerHTML = '';
    const apps = filtered();
    apps.slice(0, state.shown).forEach(a => grid.appendChild(appCard(a)));
    $('app-count').textContent = `${apps.length} ${apps.length === 1 ? t('App') : t('Apps')}`;
    $('app-more').hidden = apps.length <= state.shown;
    $('app-more').textContent = `${t('Mehr anzeigen')} (${apps.length - state.shown})`;
  }

  function renderCatalogState() {
    const c = state.data.catalog;
    const parts = [`${t('Architektur')} ${state.data.arch}`];
    if (c.loading) parts.push(t('Katalog wird geladen …'));
    else if (c.fetched) parts.push(`${c.count} ${t('Apps von LinuxServer.io')} · ${t('Stand')} ${new Date(c.fetched * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
    $('app-catalog-state').textContent = parts.join(' · ');
    $('app-hint').textContent = [
      state.data.docker ? '' : t('Docker ist nicht installiert – ohne Docker lassen sich keine Apps installieren.'),
      c.error ? t(c.error) : ''
    ].filter(Boolean).join(' ');
    $('app-dir').textContent = `${t('Apps landen in')} ${state.data.dir}`;
  }

  async function load() {
    try {
      state.data = await api('/api/appstore');
    } catch (e) {
      $('app-hint').textContent = e.message;
      return;
    }
    renderCatalogState();
    renderFilters();
    renderGrid();
    // First visit: the catalog is fetched in the background
    if (state.data.catalog.loading) setTimeout(load, 4000);
  }

  // ---------------------------------------------------------------------------
  // Install form
  // ---------------------------------------------------------------------------
  function field(label, input, hint, warn) {
    const wrap = el('label', 'login-field');
    wrap.append(raw('span', '', label), input);
    if (hint) wrap.appendChild(el('small', warn ? 'warn' : '', hint));
    return wrap;
  }

  function input(type, value, attrs = {}) {
    const i = el('input');
    i.type = type;
    i.value = value == null ? '' : String(value);
    Object.entries(attrs).forEach(([k, v]) => i.setAttribute(k, v));
    return i;
  }

  // A row with an on/off box for optional entries
  function optionalRow(enabled, content, onToggle) {
    const row = el('div', 'app-opt-row');
    const box = input('checkbox', '');
    box.checked = enabled;
    box.setAttribute('aria-label', t('Verwenden'));
    box.addEventListener('change', () => { content.classList.toggle('off', !box.checked); if (onToggle) onToggle(box.checked); });
    content.classList.toggle('off', !enabled);
    row.append(box, content);
    return { row, box };
  }

  function renderForm(f) {
    const form = $('app-form');
    form.innerHTML = '';
    if (f.advanced.length) {
      const warn = el('div', 'app-warning');
      warn.appendChild(el('b', '', t('Diese App braucht erweiterte Rechte:')));
      const ul = el('ul');
      f.advanced.forEach(a => ul.appendChild(el('li', '', t(a))));
      warn.append(ul, el('span', '', t('Nur installieren, wenn du der App vertraust.')));
      form.appendChild(warn);
    }
    const links = el('div', 'app-links');
    [[f.app.setup_url, 'Einrichtung'], [f.app.project_url, 'Projekt']].forEach(([url, label]) => {
      if (!url) return;
      const a = el('a', 'text-btn', `${t(label)} ↗`);
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
      links.appendChild(a);
    });
    if (links.childElementCount) form.appendChild(links);

    const g0 = el('div', 'app-form-group');
    g0.appendChild(field(t('Name des Stacks'), input('text', f.name, { pattern: '[a-z0-9][a-z0-9-]{0,39}', required: '', spellcheck: 'false', 'data-key': 'name' }), `${f.dir}/…`));
    form.appendChild(g0);

    if (f.ports.length && !f.host_network) {
      form.appendChild(el('h4', '', t('Ports')));
      const g = el('div', 'app-form-list');
      f.ports.forEach(p => {
        const i = input('number', p.value, { min: '1', max: '65535', 'data-port': p.key, 'aria-label': `Port ${p.container}` });
        const hint = p.in_use != null && p.value === p.default ? `${t('Belegt')}${p.in_use ? ` (${p.in_use})` : ''}` : '';
        const content = field(`${p.label || t('Port')} → ${p.container}/${p.proto === 'both' ? 'tcp+udp' : p.proto}`, i, hint, true);
        if (p.optional) {
          i.disabled = true;
          g.appendChild(optionalRow(false, content, on => { i.disabled = !on; }).row);
        } else {
          i.required = true;
          g.appendChild(content);
        }
      });
      form.appendChild(g);
    }
    if (f.host_network) form.appendChild(el('p', 'field-hint', t('Diese App nutzt das Netzwerk des Pi direkt (network_mode: host) – ihre Ports lassen sich nicht umlegen.')));

    if (f.folders.length) {
      form.appendChild(el('h4', '', t('Ordner')));
      form.appendChild(el('p', 'field-hint', t('„./name“ legt einen Unterordner im App-Verzeichnis an; ein absoluter Pfad bindet einen vorhandenen Ordner des Pi ein.')));
      const g = el('div', 'app-form-list');
      f.folders.forEach(fo => {
        const label = `${fo.label || fo.container} → ${fo.container}${fo.ro ? ` (${t('nur lesen')})` : ''}`;
        if (!fo.user) {
          const ro = input('text', fo.default, { readonly: '' });
          ro.setAttribute('data-no-i18n', '');
          g.appendChild(field(label, ro));
          return;
        }
        const i = input('text', fo.default, { spellcheck: 'false', 'data-folder': fo.key });
        const content = field(label, i);
        if (fo.optional) {
          i.disabled = true;
          g.appendChild(optionalRow(false, content, on => { i.disabled = !on; }).row);
        } else {
          i.required = true;
          g.appendChild(content);
        }
      });
      form.appendChild(g);
    }

    if (f.env.length) {
      form.appendChild(el('h4', '', t('Einstellungen')));
      const g = el('div', 'app-form-list');
      f.env.forEach(e => {
        const i = input(e.secret ? 'password' : 'text', e.default, { 'data-env': e.key, spellcheck: 'false' });
        if (e.example && e.optional) i.placeholder = e.example;
        if (e.secret) { i.required = true; i.minLength = 8; i.autocomplete = 'new-password'; }
        const label = e.label && e.label !== e.key ? `${e.key} – ${e.label}` : e.key;
        g.appendChild(field(label, i, e.optional ? t('optional – leer lassen, wenn nicht gebraucht') : ''));
      });
      form.appendChild(g);
    }

    if (f.devices.length) {
      form.appendChild(el('h4', '', t('Geräte')));
      const g = el('div', 'app-form-list');
      f.devices.forEach(d => {
        const content = el('div', 'app-device');
        content.append(raw('b', '', d.path), raw('small', '', d.label));
        const { row, box } = optionalRow(!d.optional, content);
        box.dataset.device = d.key;
        if (!d.optional) box.disabled = true;
        g.appendChild(row);
      });
      form.appendChild(g);
    }
  }

  function values() {
    const form = $('app-form');
    const v = { name: form.querySelector('[data-key=name]').value.trim().toLowerCase(), ports: {}, folders: {}, env: {}, devices: {} };
    form.querySelectorAll('[data-port]').forEach(i => { v.ports[i.dataset.port] = i.disabled ? null : +i.value; });
    form.querySelectorAll('[data-folder]').forEach(i => { v.folders[i.dataset.folder] = i.disabled ? '' : i.value; });
    form.querySelectorAll('[data-env]').forEach(i => { v.env[i.dataset.env] = i.value; });
    form.querySelectorAll('[data-device]').forEach(b => { v.devices[b.dataset.device] = b.checked; });
    return v;
  }

  async function openForm(appId) {
    let f;
    try { f = await api(`/api/appstore/${encodeURIComponent(appId)}/form`); } catch (e) { toast(e.message, 'error'); return; }
    state.form = f;
    const icon = appIcon({ id: f.app.id, name: f.app.name, logo: f.logo });
    icon.id = 'app-modal-icon';
    $('app-modal-icon').replaceWith(icon);
    $('app-modal-title').textContent = f.app.name;
    $('app-modal-sub').textContent = f.app.image;
    $('app-compose').hidden = true;
    $('app-job').hidden = true;
    $('app-form').hidden = false;
    $('app-preview').hidden = false;
    $('app-install').hidden = false;
    $('app-install').disabled = false;
    renderForm(f);
    const m = $('app-modal');
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
    setTimeout(() => $('app-form').querySelector('input:not([readonly])')?.focus(), 50);
  }

  function closeForm() {
    const m = $('app-modal');
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }

  async function preview() {
    const pre = $('app-compose');
    if (!pre.hidden) { pre.hidden = true; return; }
    try {
      const p = await api(`/api/appstore/${encodeURIComponent(state.form.app.id)}/preview`, values());
      pre.textContent = `# ${p.dir}/docker-compose.yml\n${p.compose}`;
      pre.hidden = false;
      pre.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) { toast(e.message, 'error'); }
  }

  async function install(e) {
    e.preventDefault();
    if (!$('app-form').reportValidity()) return;
    const btn = $('app-install');
    btn.disabled = true;
    try {
      const v = values();
      const job = await api(`/api/appstore/${encodeURIComponent(state.form.app.id)}/install`, v);
      $('app-form').hidden = true;
      $('app-compose').hidden = true;
      $('app-preview').hidden = true;
      btn.hidden = true;
      renderJob(job);
      poll(job.id, v);
    } catch (err) {
      toast(err.message, 'error');
      btn.disabled = false;
    }
  }

  function renderJob(job, url) {
    const box = $('app-job');
    box.hidden = false;
    box.className = `stack-job ${job.status}`;
    const head = $('app-job-head');
    head.innerHTML = '';
    const text = job.status === 'running' ? t('Wird installiert … (Images werden geladen, das kann einige Minuten dauern)')
      : job.status === 'ok' ? t('Fertig – die App läuft.') : t('Installation fehlgeschlagen.');
    head.appendChild(el('span', `health-tag ${job.status === 'ok' ? 'good' : job.status === 'error' ? 'critical' : 'warning'}`, text));
    if (url) {
      const a = el('a', 'btn-primary', `${t('Öffnen')} ↗`);
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
      head.appendChild(a);
    }
    const out = $('app-job-out');
    out.textContent = job.output || '…';
    out.scrollTop = out.scrollHeight;
  }

  function poll(id, v) {
    clearInterval(state.timer);
    state.timer = setInterval(async () => {
      try {
        const job = await api(`/api/containers/jobs/${id}`);
        if (job.status === 'running') { renderJob(job); return; }
        clearInterval(state.timer);
        await load();
        const app = state.data && state.data.apps.find(a => a.id === state.form.app.id);
        const inst = app && app.installed.find(i => i.name === v.name);
        renderJob(job, job.status === 'ok' && inst ? appUrl(inst.open) : '');
        toast(`${v.name}: ${job.status === 'ok' ? t('installiert') : t('Installation fehlgeschlagen')}`, job.status === 'ok' ? 'success' : 'error');
      } catch (e) { clearInterval(state.timer); }
    }, 1200);
  }

  // ---------------------------------------------------------------------------
  let searchTimer = 0;
  $('app-q').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.shown = PAGE; renderGrid(); }, 150); });
  ['app-source', 'app-sort'].forEach(id => $(id).addEventListener('change', () => { state.shown = PAGE; renderGrid(); }));
  $('app-toolbar').addEventListener('submit', e => e.preventDefault());
  $('app-more').addEventListener('click', () => { state.shown += PAGE * 2; renderGrid(); });
  $('app-refresh').addEventListener('click', async () => {
    const b = $('app-refresh');
    b.disabled = true;
    try {
      state.data = await api('/api/appstore/refresh', {});
      renderCatalogState();
      renderFilters();
      renderGrid();
      toast(t('Katalog aktualisiert.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
  });
  $('app-form').addEventListener('submit', install);
  $('app-preview').addEventListener('click', preview);
  $('app-modal-close').addEventListener('click', closeForm);
  $('app-modal').addEventListener('click', e => { if (e.target.id === 'app-modal') closeForm(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('app-modal').classList.contains('open')) closeForm(); });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    load();
  })();
})();
