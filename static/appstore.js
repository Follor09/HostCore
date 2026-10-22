/**
 * PiCore Dashboard - app store on the containers page: catalog, install form with
 * port / folder / password fields, compose preview and the live install output.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const state = { data: null, category: '', form: null, job: null, timer: 0 };

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

  // ---------------------------------------------------------------------------
  // Catalog
  // ---------------------------------------------------------------------------
  function renderFilters() {
    const box = $('app-filters');
    box.innerHTML = '';
    [['', 'Alle'], ...state.data.categories.map(c => [c, c])].forEach(([id, label]) => {
      const b = el('button', state.category === id ? 'active' : '', t(label));
      b.type = 'button';
      b.addEventListener('click', () => { state.category = id; renderFilters(); renderGrid(); });
      box.appendChild(b);
    });
  }

  const LEVEL = { active: ['good', 'Läuft'], warning: ['warning', 'Teilweise'], error: ['critical', 'Fehler'], inactive: ['', 'Gestoppt'], missing: ['', 'Entfernt'] };

  function renderGrid() {
    const grid = $('app-grid');
    grid.innerHTML = '';
    state.data.apps.filter(a => !state.category || a.category === state.category).forEach(a => {
      const card = el('div', 'app-card');
      const head = el('div', 'app-card-head');
      const icon = raw('span', 'app-icon', a.icon);
      icon.setAttribute('aria-hidden', 'true');
      const title = el('div');
      title.append(raw('b', '', a.name), el('small', '', `${t(a.category)} · RAM ${a.ram}`));
      head.append(icon, title);
      card.append(head, el('p', 'app-desc', t(a.description)), raw('span', 'app-meta', a.image));

      if (a.installed.length) {
        const inst = el('div', 'app-installed');
        a.installed.forEach(i => {
          const [level, label] = LEVEL[i.level] || ['', i.level];
          const tag = el('span', `health-tag ${level}`, `${i.name}: ${t(label)}`);
          tag.setAttribute('data-no-i18n', '');
          inst.appendChild(tag);
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
      btn.dataset.perm = 'apps.install';
      btn.disabled = !state.data.docker;
      if (!can('apps.install')) btn.hidden = true;
      btn.addEventListener('click', () => openForm(a.id));
      actions.appendChild(btn);
      card.appendChild(actions);
      grid.appendChild(card);
    });
  }

  async function load() {
    try {
      state.data = await api('/api/appstore');
    } catch (e) {
      $('app-hint').textContent = e.message;
      return;
    }
    $('app-dir').textContent = state.data.dir;
    $('app-hint').textContent = state.data.docker ? '' : t('Docker ist nicht installiert – ohne Docker lassen sich keine Apps installieren.');
    renderFilters();
    renderGrid();
  }

  // ---------------------------------------------------------------------------
  // Install form
  // ---------------------------------------------------------------------------
  function field(label, input, hint, warn) {
    const wrap = el('label', 'login-field');
    wrap.appendChild(el('span', '', t(label)));
    wrap.appendChild(input);
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

  function renderForm(f) {
    const form = $('app-form');
    form.innerHTML = '';
    const name = input('text', f.name, { pattern: '[a-z0-9][a-z0-9-]{0,39}', required: '', spellcheck: 'false', 'data-key': 'name' });
    const g0 = el('div', 'app-form-group');
    g0.appendChild(field('Name des Stacks', name, `${f.dir}/…`));
    form.appendChild(g0);

    if (f.ports.length) {
      form.appendChild(el('h4', '', t('Ports')));
      const g = el('div', 'app-form-group');
      f.ports.forEach(p => {
        const i = input('number', p.value, { min: '1', max: '65535', required: '', 'data-port': p.key });
        const hint = p.in_use != null && p.value === p.default ? `${t('Belegt')}${p.in_use ? ` (${p.in_use})` : ''}` : '';
        g.appendChild(field(`${t(p.label)} → ${p.container}`, i, hint, true));
      });
      form.appendChild(g);
    }
    if (f.host_network) form.appendChild(el('p', 'field-hint', t('Diese App nutzt das Netzwerk des Pi direkt (network_mode: host) – ihre Ports lassen sich nicht umlegen.')));

    form.appendChild(el('h4', '', t('Ordner')));
    const gf = el('div', 'app-form-group');
    f.folders.forEach(fo => {
      if (fo.user) {
        gf.appendChild(field(`${t(fo.label)} → ${fo.container}${fo.ro ? ` (${t('nur lesen')})` : ''}`,
          input('text', fo.value, { required: '', spellcheck: 'false', 'data-folder': fo.key }), t('Ein vorhandener Ordner auf dem Pi')));
      } else {
        const ro = input('text', `${fo.value} → ${fo.container}`, { readonly: '' });
        ro.setAttribute('data-no-i18n', '');
        gf.appendChild(field(fo.label, ro));
      }
    });
    form.appendChild(gf);

    if (f.env.length) {
      form.appendChild(el('h4', '', t('Einstellungen')));
      const ge = el('div', 'app-form-group');
      f.env.forEach(e => {
        const i = input(e.secret ? 'password' : 'text', e.value, { 'data-env': e.key, spellcheck: 'false' });
        if (e.secret) { i.required = true; i.minLength = 8; i.autocomplete = 'new-password'; }
        ge.appendChild(field(e.label, i));
      });
      form.appendChild(ge);
    }
  }

  function values() {
    const form = $('app-form');
    const v = { name: form.querySelector('[data-key=name]').value.trim().toLowerCase(), ports: {}, folders: {}, env: {} };
    form.querySelectorAll('[data-port]').forEach(i => { v.ports[i.dataset.port] = +i.value; });
    form.querySelectorAll('[data-folder]').forEach(i => { v.folders[i.dataset.folder] = i.value; });
    form.querySelectorAll('[data-env]').forEach(i => { v.env[i.dataset.env] = i.value; });
    return v;
  }

  async function openForm(appId) {
    let f;
    try { f = await api(`/api/appstore/${appId}/form`); } catch (e) { toast(e.message, 'error'); return; }
    state.form = f;
    state.job = null;
    $('app-modal-icon').textContent = f.app.icon;
    $('app-modal-title').textContent = f.app.name;
    $('app-modal-sub').textContent = f.app.image;
    $('app-compose').hidden = true;
    $('app-job').hidden = true;
    $('app-form').hidden = false;
    $('app-preview').hidden = false;
    $('app-install').hidden = false;
    $('app-install').disabled = false;
    $('app-install').textContent = t('Installieren');
    renderForm(f);
    const m = $('app-modal');
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
    setTimeout(() => $('app-form').querySelector('input')?.focus(), 50);
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
      const p = await api(`/api/appstore/${state.form.app.id}/preview`, values());
      pre.textContent = `# ${p.dir}/docker-compose.yml\n${p.compose}`;
      pre.hidden = false;
    } catch (e) { toast(e.message, 'error'); }
  }

  async function install(e) {
    e.preventDefault();
    if (!$('app-form').reportValidity()) return;
    const btn = $('app-install');
    btn.disabled = true;
    try {
      const v = values();
      const job = await api(`/api/appstore/${state.form.app.id}/install`, v);
      state.job = job;
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
