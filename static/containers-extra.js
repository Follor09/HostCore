/**
 * PiCore Dashboard - container page sections "Volumes & Netzwerke", "Docker aufräumen" and "Stack-Vorlagen".
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
    setTimeout(() => n.remove(), 5000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function fmtBytes(b) {
    if (b == null) return '–';
    if (!b) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }

  function confirmClick(btn, question, action) {
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      btn.dataset.label = btn.textContent;
      btn.textContent = question;
      setTimeout(() => { if (btn.classList.contains('confirming')) { btn.classList.remove('confirming'); btn.textContent = btn.dataset.label; } }, 4000);
      return;
    }
    btn.classList.remove('confirming');
    btn.textContent = btn.dataset.label;
    action();
  }

  function table(tb, heads, empty, rows) {
    tb.innerHTML = '';
    const head = tb.createTHead().insertRow();
    heads.forEach(h => head.appendChild(el('th', '', t(h))));
    const body = tb.createTBody();
    if (!rows.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = heads.length;
      c.className = 'docs-empty';
      c.textContent = t(empty);
    }
    return body;
  }

  const chips = (cell, names) => {
    const box = el('div', 'fleet-pis');
    names.forEach(n => box.appendChild(raw('span', 'pg-chip', n)));
    if (!names.length) box.appendChild(el('span', 'field-hint', t('keine')));
    cell.appendChild(box);
  };

  // ---------------------------------------------------------------------------
  // Volumes & networks
  // ---------------------------------------------------------------------------
  async function loadVolumes() {
    const btn = $('dv-reload');
    btn.disabled = true;
    $('dv-summary').textContent = t('Lade…');
    let d;
    try { d = await api('/api/containers/docker/volumes'); } catch (err) { $('dv-summary').textContent = err.message; btn.disabled = false; return; }
    btn.disabled = false;
    btn.textContent = t('Neu laden');
    $('dv-summary').textContent = t(`${d.volumes.length} Volumes · ${fmtBytes(d.total_size)} · ${d.networks.length} Netzwerke`);
    const vb = table($('dv-volumes'), ['Volume', 'Größe', 'Stack', 'Genutzt von'], 'Keine Volumes.', d.volumes);
    d.volumes.forEach(v => {
      const r = vb.insertRow();
      const n = r.insertCell();
      n.className = 'wrap';
      n.appendChild(raw('span', 'mono', v.anonymous ? `${v.name.slice(0, 12)}…` : v.name));
      n.appendChild(el('span', 'lan-sub', [v.driver, v.anonymous ? t('anonym') : ''].filter(Boolean).join(' · ')));
      r.insertCell().appendChild(raw('span', 'mono', fmtBytes(v.size)));
      r.insertCell().appendChild(raw('span', '', v.stack || '–'));
      chips(r.insertCell(), v.containers);
      if (!v.containers.length) r.classList.add('dv-unused');
    });
    const nb = table($('dv-networks'), ['Netzwerk', 'Subnetz', 'Stack', 'Container'], 'Keine Netzwerke.', d.networks);
    d.networks.forEach(n => {
      const r = nb.insertRow();
      const c = r.insertCell();
      c.appendChild(raw('span', 'mono', n.name));
      c.appendChild(el('span', 'lan-sub', [n.driver, n.scope, n.internal ? t('intern') : '', n.builtin ? t('Standard') : ''].filter(Boolean).join(' · ')));
      const s = r.insertCell();
      s.appendChild(raw('span', 'mono', n.subnet || '–'));
      if (n.gateway) s.appendChild(raw('span', 'lan-sub', `GW ${n.gateway}`));
      r.insertCell().appendChild(raw('span', '', n.stack || '–'));
      chips(r.insertCell(), n.containers);
    });
  }

  // ---------------------------------------------------------------------------
  // Cleanup
  // ---------------------------------------------------------------------------
  const picks = { kinds: new Set(), volumes: new Set() };
  let usage = null;

  function renderCleanup() {
    const tiles = $('dc-tiles');
    tiles.innerHTML = '';
    const df = usage.df;
    [['Images', 'Images'], ['Containers', 'Container'], ['Local Volumes', 'Volumes'], ['Build Cache', 'Build-Cache']].forEach(([k, label]) => {
      const x = df[k];
      if (!x) return;
      const b = el('div', 'bench-value');
      b.append(el('span', 'label', t(label)), raw('span', 'value', fmtBytes(x.size)),
        el('span', 'bench-sub', t(`${x.count} gesamt · ${x.active} aktiv · ${fmtBytes(x.reclaimable)} frei machbar`)));
      tiles.appendChild(b);
    });
    const box = $('dc-groups');
    box.innerHTML = '';
    const group = (kind, title, desc, items, render, sizeOf) => {
      const sec = el('div', 'dc-group');
      const head = el('label', 'dc-head');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = picks.kinds.has(kind);
      cb.disabled = !items.length && kind !== 'build_cache' || !can('services.control');
      cb.addEventListener('change', () => { cb.checked ? picks.kinds.add(kind) : picks.kinds.delete(kind); update(); });
      const total = sizeOf != null ? sizeOf : items.reduce((s, i) => s + (i.size || 0), 0);
      head.append(cb, el('strong', '', t(title)), el('span', 'field-hint', t(`${items.length || (kind === 'build_cache' && total ? '–' : 0)} · ${fmtBytes(total)}`)));
      sec.appendChild(head);
      sec.appendChild(el('p', 'field-hint', t(desc)));
      if (items.length) {
        const list = el('div', 'dc-items');
        items.slice(0, 40).forEach(i => list.appendChild(render(i)));
        if (items.length > 40) list.appendChild(el('span', 'field-hint', t(`… und ${items.length - 40} weitere`)));
        sec.appendChild(list);
      }
      box.appendChild(sec);
    };
    const imgChip = i => raw('span', 'pg-chip', `${i.repo === '<none>' ? i.id : `${i.repo}:${i.tag}`} · ${fmtBytes(i.size)}`);
    group('dangling_images', 'Unbenannte Images', 'Alte Schichten ohne Namen, meist von Updates übrig – gefahrlos.', usage.dangling_images, imgChip);
    group('unused_images', 'Ungenutzte Images', 'Images, die kein Container verwendet (auch gestoppte zählen als verwendet). Werden bei Bedarf neu geladen.', usage.unused_images, imgChip);
    group('networks', 'Ungenutzte Netzwerke', 'Eigene Netzwerke ohne Container.', usage.unused_networks, n => raw('span', 'pg-chip', n.name), 0);
    group('build_cache', 'Build-Cache', 'Zwischenstände von docker build – gefahrlos.', [], null, usage.build_cache);
    // Volumes: one by one
    const sec = el('div', 'dc-group');
    sec.appendChild(el('strong', '', t('Volumes ohne Container')));
    sec.appendChild(el('p', 'field-hint', t('Einzeln auswählen. Achtung: die Daten im Volume sind danach gelöscht.')));
    if (!usage.unused_volumes.length) sec.appendChild(el('p', 'field-hint', t('Keine.')));
    usage.unused_volumes.forEach(v => {
      const l = el('label', 'toggle-label dc-vol');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = picks.volumes.has(v.name);
      cb.disabled = !can('services.control');
      cb.addEventListener('change', () => { cb.checked ? picks.volumes.add(v.name) : picks.volumes.delete(v.name); update(); });
      l.append(cb, raw('span', 'mono', ` ${v.name.length > 40 ? `${v.name.slice(0, 12)}…` : v.name}`), el('span', 'field-hint', ` ${fmtBytes(v.size)}`));
      sec.appendChild(l);
    });
    box.appendChild(sec);
    update();
  }

  function update() {
    const n = picks.kinds.size + picks.volumes.size;
    $('dc-run').disabled = !n;
    $('dc-hint').textContent = picks.volumes.size ? t(`${picks.volumes.size} Volumes werden mit ihren Daten gelöscht.`) : '';
  }

  async function loadCleanup() {
    const btn = $('dc-reload');
    btn.disabled = true;
    try { usage = await api('/api/containers/docker/disk'); renderCleanup(); }
    catch (err) { $('dc-groups').innerHTML = ''; $('dc-groups').appendChild(el('p', 'field-hint', err.message)); }
    finally { btn.disabled = false; }
  }

  // ---------------------------------------------------------------------------
  // Stack templates
  // ---------------------------------------------------------------------------
  let tpls = [];
  let hosts = null;

  async function loadHosts() {
    if (hosts) return hosts;
    try {
      const r = await api('/api/remotes/current');
      hosts = [{ id: '', name: r.local || t('Dieser Pi'), online: true }, ...r.hosts];
    } catch (e) { hosts = [{ id: '', name: t('Dieser Pi'), online: true }]; }
    return hosts;
  }

  function renderTemplates() {
    const list = $('tpl-list');
    list.innerHTML = '';
    if (!tpls.length) { list.appendChild(el('p', 'field-hint', t('Noch keine Vorlagen.'))); return; }
    tpls.forEach(tp => {
      const card = el('div', 'tpl-card');
      const head = el('div', 'backup-head');
      head.append(raw('span', 'backup-name', tp.name), raw('code', 'mono', tp.stack_name));
      card.appendChild(head);
      if (tp.description) card.appendChild(raw('span', 'field-hint', tp.description));
      const svc = el('div', 'fleet-pis');
      tp.services.forEach(s => svc.appendChild(raw('span', 'pg-chip', s)));
      card.appendChild(svc);
      const act = el('div', 'docs-row-actions tpl-actions');
      const mk = (label, cls, fn) => { const b = el('button', cls, t(label)); b.type = 'button'; b.addEventListener('click', () => fn(b)); return b; };
      if (can('apps.install')) {
        act.append(mk('Anlegen auf …', 'text-btn', () => deployForm(card, tp)), mk('Bearbeiten', 'text-btn', () => tplForm(tp)),
          mk('Löschen', 'text-btn danger', b => confirmClick(b, t('Sicher?'), async () => {
            try { tpls = (await api(`/api/stack-templates/${tp.id}/delete`, {})).templates; renderTemplates(); } catch (err) { toast(err.message, 'error'); }
          })));
      }
      card.appendChild(act);
      list.appendChild(card);
    });
  }

  function tplForm(tp, preset) {
    const wrap = $('tpl-form-wrap');
    const v = tp || preset || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(tp ? 'Vorlage bearbeiten' : 'Neue Vorlage')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const field = (label, input, full) => { const w = el('label', `login-field${full ? ' full' : ''}`); w.append(el('span', '', t(label)), input); form.appendChild(w); return input; };
    const inp = (val, ph) => { const i = el('input'); i.type = 'text'; i.value = val || ''; if (ph) i.placeholder = t(ph); return i; };
    const name = field('Name', inp(v.name, 'z. B. Uptime Kuma'));
    const stack = field('Stack-Name (Vorschlag)', inp(v.stack_name, 'uptime-kuma'));
    const desc = field('Beschreibung', inp(v.description), true);
    const compose = el('textarea', 'tpl-compose');
    compose.rows = 16;
    compose.spellcheck = false;
    compose.value = v.compose || 'services:\n  app:\n    image: \n    restart: unless-stopped\n';
    compose.setAttribute('data-no-i18n', '');
    field('docker-compose.yml', compose, true);
    const envHint = el('textarea');
    envHint.rows = 3;
    envHint.spellcheck = false;
    envHint.value = v.env_hint || '';
    envHint.placeholder = 'DB_PASSWORD=\nTZ=Europe/Berlin';
    field('Benötigte .env-Variablen (nur Namen/Beispiele, keine echten Passwörter)', envHint, true);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { wrap.hidden = true; wrap.innerHTML = ''; });
    actions.append(save, cancel);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        tpls = (await api(tp ? `/api/stack-templates/${tp.id}` : '/api/stack-templates',
          { name: name.value.trim(), stack_name: stack.value.trim(), description: desc.value, compose: compose.value, env_hint: envHint.value })).templates;
        wrap.hidden = true;
        wrap.innerHTML = '';
        renderTemplates();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function fromStack() {
    let stacks = [];
    try { stacks = (await api('/api/containers/overview')).stacks || []; } catch (e) { /* ignore */ }
    const wrap = $('tpl-form-wrap');
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t('Vorlage aus einem Stack dieses Pis')));
    if (!stacks.length) { wrap.appendChild(el('p', 'field-hint', t('Auf diesem Pi gibt es keine Compose-Stacks.'))); return; }
    const row = el('div', 'fleet-pending-actions');
    const sel = el('select');
    stacks.forEach(s => { const o = raw('option', '', s.name); o.value = s.name; sel.appendChild(o); });
    const go = el('button', 'btn-primary', t('Übernehmen'));
    go.type = 'button';
    go.addEventListener('click', async () => {
      try {
        const c = await api(`/api/containers/stacks/${encodeURIComponent(sel.value)}/compose`);
        tplForm(null, { name: c.name, stack_name: c.name, compose: c.compose, env_hint: c.has_env ? t('# Der Stack hat eine .env – Variablen hier notieren (ohne Werte)') : '' });
      } catch (err) { toast(err.message, 'error'); }
    });
    row.append(sel, go);
    wrap.appendChild(row);
  }

  async function deployForm(card, tp) {
    const old = card.querySelector('.tpl-deploy');
    if (old) { old.remove(); return; }
    const box = el('form', 'tpl-deploy');
    box.autocomplete = 'off';
    const hs = await loadHosts();
    const targets = el('div', 'fleet-pis');
    hs.forEach(h => {
      const l = el('label', 'toggle-label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = h.id;
      cb.disabled = h.online === false;
      l.append(cb, raw('span', '', ` ${h.name}`));
      targets.appendChild(l);
    });
    const name = el('input');
    name.type = 'text';
    name.value = tp.stack_name;
    name.spellcheck = false;
    const env = el('textarea');
    env.rows = 4;
    env.spellcheck = false;
    env.placeholder = tp.env_hint || 'KEY=value';
    env.setAttribute('data-no-i18n', '');
    const field = (label, input) => { const w = el('label', 'login-field full'); w.append(el('span', '', t(label)), input); box.appendChild(w); };
    box.appendChild(el('span', 'field-hint', t('Auf welchen Pis?')));
    box.appendChild(targets);
    field('Stack-Name', name);
    field('.env (optional – wird nur auf den Ziel-Pis gespeichert)', env);
    const go = el('button', 'btn-primary', t('Stack anlegen'));
    go.type = 'submit';
    box.appendChild(go);
    const out = el('div', 'fleet-results');
    box.appendChild(out);
    box.addEventListener('submit', async e => {
      e.preventDefault();
      const ids = [...targets.querySelectorAll('input:checked')].map(c => c.value);
      if (!ids.length) { toast(t('Mindestens einen Pi auswählen.'), 'error'); return; }
      go.disabled = true;
      try {
        const r = await api(`/api/stack-templates/${tp.id}/deploy`, { targets: ids, name: name.value.trim(), env: env.value });
        out.innerHTML = '';
        r.results.forEach(x => {
          const line = el('div', 'fleet-result');
          line.append(el('span', `health-tag ${x.ok ? 'good' : 'critical'}`, t(x.ok ? 'Gestartet' : 'Fehler')),
            raw('strong', '', (hs.find(h => h.id === x.id) || {}).name || x.id), el('span', 'lan-sub', t(x.message)));
          out.appendChild(line);
        });
        if (r.results.some(x => x.ok)) toast(t('Der Stack wird angelegt – Fortschritt unter Compose-Stacks.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
      finally { go.disabled = false; }
    });
    card.appendChild(box);
  }

  async function init() {
    if (!$('stack-vorlagen')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('dv-reload').addEventListener('click', loadVolumes);
    $('dc-reload').addEventListener('click', loadCleanup);
    $('dc-run').addEventListener('click', () => {
      const b = $('dc-run');
      confirmClick(b, t('Wirklich entfernen?'), async () => {
        b.disabled = true;
        try {
          const r = await api('/api/containers/docker/cleanup', { kinds: [...picks.kinds], volumes: [...picks.volumes] });
          picks.kinds.clear();
          picks.volumes.clear();
          const out = $('dc-results');
          out.innerHTML = '';
          r.results.forEach(x => {
            const line = el('div', 'fleet-result');
            line.append(el('span', `health-tag ${x.ok ? 'good' : 'critical'}`, t(x.ok ? 'Erledigt' : 'Fehler')), el('strong', '', t(x.label)),
              el('span', 'lan-sub', x.ok ? t(`${fmtBytes(x.freed)} frei`) : x.message));
            out.appendChild(line);
          });
          usage = r.usage;
          renderCleanup();
        } catch (err) { toast(err.message, 'error'); update(); }
      });
    });
    $('tpl-add').addEventListener('click', () => tplForm(null));
    $('tpl-from-stack').addEventListener('click', fromStack);
    try { tpls = (await api('/api/stack-templates')).templates; renderTemplates(); } catch (e) { /* no permission */ }
    // Heavy Docker queries only when their section is opened
    const lazy = (id, fn) => {
      let done = false;
      const io = new IntersectionObserver(entries => {
        if (!done && entries.some(e => e.isIntersecting)) { done = true; fn(); io.disconnect(); }
      });
      io.observe($(id));
    };
    lazy('docker-volumes', loadVolumes);
    lazy('docker-aufraeumen', loadCleanup);
  }

  init();
})();
