/**
 * PiCore Dashboard - boards (Homarr-style start page)
 *
 * A board is a free grid (gridstack) of app tiles, widgets (search, embedded web page) and the
 * dashboard's own cards, grouped into sections (categories). Several boards, stored on the Pi
 * (see boards.py) and shared by every device; editing needs the "board.edit" permission.
 *
 * The cards are the existing elements of index.html: they wait in #card-pool and are moved into
 * the grid while their board is shown, so app.js keeps updating them by id as before.
 * Wide screens get the grid; narrower ones a simple flow layout (apps as tiles, cards stacked).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const CELL = 64;                 // row height in px
  const GRID_MIN_WIDTH = 1100;     // narrower: flow layout, no editing
  const STATUS_EVERY = 60000;
  const KEY_ACTIVE = 'board';
  const KEY_COLLAPSED = 'boardCollapsed';

  const CARD_LABELS = {
    clock: 'Uhrzeit & Betriebszeit', weather: 'Wetter', services: 'Server-Dienste', cpu: 'CPU-Auslastung',
    temp: 'CPU-Temperatur', ram: 'Arbeitsspeicher', disk: 'Speicher & NVMe', network: 'Netzwerk', fan: 'Lüfter',
    power: 'Stromversorgung', updates: 'System-Updates', wol: 'Wake-on-LAN', notes: 'Notizen', events: 'Ereignisse',
    actions: 'Schnellaktionen', health: 'Web-Dienste', reminders: 'Erinnerungen', kanban: 'Aufgaben', pi3d: 'Live-3D-Pi',
    history: 'Verlauf', workloads: 'Docker & Kubernetes', storage: 'Laufwerke & Explorer', processes: 'Prozesse & Diagnose'
  };
  const CARD_SIZES = {
    clock: [4, 6], weather: [4, 6], services: [4, 6], cpu: [3, 7], temp: [3, 7], ram: [3, 7], disk: [3, 7],
    network: [3, 6], fan: [3, 6], power: [3, 6], updates: [3, 6], wol: [3, 5], notes: [3, 5], events: [3, 5],
    actions: [3, 5], health: [3, 6], reminders: [3, 6], kanban: [3, 6], pi3d: [3, 6], history: [12, 8],
    workloads: [12, 5], storage: [6, 14], processes: [6, 14]
  };
  const ENGINE_LABELS = { duckduckgo: 'DuckDuckGo', google: 'Google', startpage: 'Startpage', bing: 'Bing', ecosia: 'Ecosia', custom: 'Eigene Adresse' };
  const ICONS = {
    edit: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    remove: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    up: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15l-6-6-6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
    search: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>',
    external: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/></svg>'
  };

  let cfg = null;              // {rev, boards, cards, engines}
  let activeId = null;
  let editing = false;
  let flow = false;            // narrow screen: flow layout
  const grids = new Map();     // section id -> GridStack
  let saveTimer = null;
  let saving = Promise.resolve();
  let statusTimer = null;
  let statusData = {};

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.detail || `Fehler ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const canEdit = () => !window.PiAuth || window.PiAuth.can('board.edit');
  const newId = () => Math.random().toString(16).slice(2, 12);
  const active = () => cfg.boards.find(b => b.id === activeId) || cfg.boards[0];
  const boardName = b => b.name || t('Übersicht');
  const safeUrl = u => (/^https?:\/\//i.test(u || '') ? u : '');
  const iconSrc = icon => (!icon ? '' : /^https?:\/\//i.test(icon) ? icon : `/api/boards/icon/${encodeURIComponent(icon)}`);
  const allApps = () => cfg.boards.flatMap(b => b.sections.flatMap(s => s.items.filter(i => i.kind === 'app').map(i => ({ ...i, board: b.id }))));

  function collapsedSet() {
    try { return new Set(JSON.parse(localStorage.getItem(KEY_COLLAPSED) || '[]')); } catch (e) { return new Set(); }
  }

  function setCollapsed(id, on) {
    const s = collapsedSet();
    if (on) s.add(id); else s.delete(id);
    try { localStorage.setItem(KEY_COLLAPSED, JSON.stringify([...s])); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------------
  // Cards from the pool
  // ---------------------------------------------------------------------------
  const pool = () => $('card-pool');
  const cardEl = id => document.querySelector(`[data-widget="${id}"]`);

  function cardAvailable(id) {
    const c = cardEl(id);
    return !!c && !c.classList.contains('cap-off') && !c.classList.contains('perm-denied');
  }

  function returnCards() {
    document.querySelectorAll('#board [data-widget]').forEach(c => {
      c.classList.remove('board-card');
      pool().appendChild(c);
    });
  }

  // ---------------------------------------------------------------------------
  // Item content
  // ---------------------------------------------------------------------------
  function appIcon(item) {
    const box = el('span', 'app-icon');
    const letter = () => {
      box.innerHTML = '';
      box.classList.add('letter');
      box.textContent = (item.name || '?').trim().charAt(0).toUpperCase();
    };
    const src = iconSrc(item.icon);
    if (!src) { letter(); return box; }
    const img = el('img');
    img.alt = '';
    img.loading = 'lazy';
    img.referrerPolicy = 'no-referrer';
    img.src = src;
    img.addEventListener('error', letter);
    box.appendChild(img);
    return box;
  }

  function appTile(item) {
    const a = el('a', 'app-tile glass-card');
    const href = safeUrl(item.url);
    if (href) a.href = href;
    if (item.new_tab) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    a.title = [item.name, item.description, href].filter(Boolean).join('\n');
    a.setAttribute('data-no-i18n', '');
    if (item.ping) {
      const dot = el('span', 'app-status');
      dot.dataset.appStatus = item.id;
      a.appendChild(dot);
    }
    a.append(appIcon(item), el('span', 'app-name', item.name));
    if (item.description) a.appendChild(el('span', 'app-desc', item.description));
    return a;
  }

  function searchWidget(item) {
    const form = el('form', 'board-search glass-card');
    form.setAttribute('role', 'search');
    const icon = el('span', 'board-search-icon');
    icon.innerHTML = ICONS.search;
    const input = el('input');
    input.type = 'search';
    input.autocomplete = 'off';
    input.spellcheck = false;
    const engine = item.engine === 'custom' ? item.custom_url : (cfg.engines[item.engine] || cfg.engines.duckduckgo);
    const engineName = item.engine === 'custom' ? 'Web' : ENGINE_LABELS[item.engine] || 'DuckDuckGo';
    input.placeholder = t(item.apps ? `Apps oder ${engineName} durchsuchen…` : `${engineName} durchsuchen…`);
    input.setAttribute('aria-label', input.placeholder);
    const results = el('div', 'board-search-results');
    results.hidden = true;
    let hits = [];
    let sel = -1;

    function draw() {
      results.innerHTML = '';
      results.hidden = !hits.length;
      hits.forEach((app, i) => {
        const r = el('a', `board-search-hit${i === sel ? ' active' : ''}`);
        r.href = safeUrl(app.url) || '#';
        if (app.new_tab) { r.target = '_blank'; r.rel = 'noopener noreferrer'; }
        r.setAttribute('data-no-i18n', '');
        r.append(appIcon(app), el('span', 'app-name', app.name), el('span', 'board-search-url', (app.url || '').replace(/^https?:\/\//, '')));
        results.appendChild(r);
      });
    }

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      hits = item.apps && q ? allApps().filter(a => [a.name, a.description, a.url].some(v => (v || '').toLowerCase().includes(q))).slice(0, 6) : [];
      sel = hits.length ? 0 : -1;
      draw();
    });
    input.addEventListener('keydown', e => {
      if (!hits.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(hits.length - 1, sel + 1); draw(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(-1, sel - 1); draw(); }
      if (e.key === 'Escape') { hits = []; draw(); }
    });
    input.addEventListener('blur', () => setTimeout(() => { results.hidden = true; }, 150));
    input.addEventListener('focus', () => { results.hidden = !hits.length; });
    form.addEventListener('submit', e => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q) return;
      const app = sel >= 0 ? hits[sel] : null;
      if (app && safeUrl(app.url)) {
        if (app.new_tab) window.open(app.url, '_blank', 'noopener');
        else location.href = app.url;
      } else if (engine) {
        window.open(engine.replace('%s', encodeURIComponent(q)), '_blank', 'noopener');
      }
      input.value = '';
      hits = [];
      draw();
    });
    form.append(icon, input, results);
    return form;
  }

  function frameWidget(item) {
    const box = el('div', 'board-frame glass-card');
    const head = el('div', 'board-frame-head');
    const title = el('span', 'widget-label', item.title || (item.url || '').replace(/^https?:\/\//, ''));
    title.setAttribute('data-no-i18n', '');
    const open = el('a', 'mini-icon-btn');
    open.href = safeUrl(item.url) || '#';
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    open.title = t('In neuem Tab öffnen');
    open.innerHTML = ICONS.external;
    head.append(title, open);
    const frame = el('iframe');
    frame.src = safeUrl(item.url) || 'about:blank';
    frame.loading = 'lazy';
    frame.referrerPolicy = 'no-referrer';
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups');
    frame.title = item.title || item.url;
    box.append(head, frame);
    return box;
  }

  function content(item) {
    if (item.kind === 'card') {
      const c = cardEl(item.card);
      c.classList.add('board-card');
      return c;
    }
    if (item.kind === 'app') return appTile(item);
    if (item.kind === 'search') return searchWidget(item);
    if (item.kind === 'iframe') return frameWidget(item);
    return null;
  }

  function renderable(item, used) {
    if (item.kind !== 'card') return true;
    if (used.has(item.card) || !cardAvailable(item.card)) return false;
    used.add(item.card);
    return true;
  }

  function itemTools(item, wrap) {
    const bar = el('div', 'board-item-tools');
    const label = el('span', 'board-item-label', item.kind === 'card' ? t(CARD_LABELS[item.card] || item.card)
      : item.kind === 'app' ? item.name : item.kind === 'search' ? t('Suchleiste') : (item.title || t('Webseite')));
    label.setAttribute('data-no-i18n', '');
    bar.appendChild(label);
    if (item.kind !== 'card') {
      const ed = el('button', 'board-tool');
      ed.type = 'button';
      ed.innerHTML = ICONS.edit;
      ed.title = t('Bearbeiten');
      ed.setAttribute('aria-label', ed.title);
      ed.addEventListener('click', e => { e.stopPropagation(); openItemDialog(item); });
      bar.appendChild(ed);
    }
    const rm = el('button', 'board-tool danger');
    rm.type = 'button';
    rm.innerHTML = ICONS.remove;
    rm.title = t('Entfernen');
    rm.setAttribute('aria-label', rm.title);
    rm.addEventListener('click', e => {
      e.stopPropagation();
      if (!rm.classList.contains('confirming')) {
        rm.classList.add('confirming');
        rm.title = t('Nochmal klicken zum Entfernen');
        setTimeout(() => rm.classList.remove('confirming'), 3000);
        return;
      }
      removeItem(item, wrap);
    });
    bar.appendChild(rm);
    return bar;
  }

  function gridItem(item) {
    const wrap = el('div', `grid-stack-item board-item kind-${item.kind}`);
    wrap.setAttribute('gs-id', item.id);
    ['x', 'y', 'w', 'h'].forEach(k => wrap.setAttribute(`gs-${k}`, item[k]));
    if (item.kind === 'card') { wrap.setAttribute('gs-min-w', 2); wrap.setAttribute('gs-min-h', 2); }
    const inner = el('div', 'grid-stack-item-content');
    inner.appendChild(content(item));
    if (editing) inner.append(el('div', 'board-item-shield'), itemTools(item, wrap));
    wrap.appendChild(inner);
    return wrap;
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function teardown() {
    returnCards();
    grids.forEach(g => g.destroy(false));
    grids.clear();
    $('board').innerHTML = '';
  }

  function sectionHead(section, index, board) {
    const head = el('div', 'board-section-head');
    const collapsed = !editing && collapsedSet().has(section.id);
    const toggle = el('button', 'board-section-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', String(!collapsed));
    const chev = el('span', 'board-section-chevron');
    chev.innerHTML = ICONS.chevron;
    const name = el('span', 'board-section-name', section.name);
    name.setAttribute('data-no-i18n', '');
    toggle.append(chev, name);
    toggle.addEventListener('click', () => {
      if (editing) return;
      setCollapsed(section.id, !collapsedSet().has(section.id));
      render();
    });
    head.appendChild(toggle);
    if (editing) {
      const tools = el('span', 'board-section-tools');
      const mk = (icon, label, fn, disabled) => {
        const b = el('button', 'board-tool');
        b.type = 'button';
        b.innerHTML = icon;
        b.title = t(label);
        b.setAttribute('aria-label', b.title);
        b.disabled = !!disabled;
        b.addEventListener('click', fn);
        tools.appendChild(b);
        return b;
      };
      mk(ICONS.up, 'Nach oben', () => moveSection(section, -1), index <= 1);
      mk(ICONS.down, 'Nach unten', () => moveSection(section, 1), index === board.sections.length - 1);
      mk(ICONS.edit, 'Umbenennen', () => {
        const n = (window.prompt(t('Name der Kategorie'), section.name) || '').trim().slice(0, 40);
        if (n) { section.name = n; save(); render(); }
      });
      const del = mk(ICONS.remove, 'Kategorie löschen', () => {
        const n = section.items.length;
        if (n && !window.confirm(`${t('Kategorie löschen')}: ${section.name} (${n} ${t('Elemente')})?`)) return;
        sync();
        board.sections = board.sections.filter(s => s !== section);
        save();
        render();
      });
      del.classList.add('danger');
      head.appendChild(tools);
    }
    return { head, collapsed };
  }

  function renderGrid(board) {
    const root = $('board');
    const used = new Set();
    board.sections.forEach((section, i) => {
      const sec = el('section', 'board-section');
      sec.dataset.section = section.id;
      let collapsed = false;
      if (i > 0) {
        const h = sectionHead(section, i, board);
        collapsed = h.collapsed;
        sec.appendChild(h.head);
      }
      root.appendChild(sec);
      if (collapsed) return;
      const gridEl = el('div', 'grid-stack');
      section.items.filter(it => renderable(it, used)).forEach(it => gridEl.appendChild(gridItem(it)));
      sec.appendChild(gridEl);
      const grid = GridStack.init({
        column: board.columns, cellHeight: CELL, margin: 6, float: false, animate: true,
        staticGrid: !editing, acceptWidgets: editing, minRow: editing ? 2 : 0,
        resizable: { handles: 'se' }, draggable: { cancel: '.board-item-tools' }
      }, gridEl);
      grid.on('change added removed', () => { if (editing) sync(); });
      grids.set(section.id, grid);
    });
  }

  function renderFlow(board) {
    const root = $('board');
    const used = new Set();
    board.sections.forEach((section, i) => {
      const sec = el('section', 'board-section flow');
      let collapsed = false;
      if (i > 0) {
        const h = sectionHead(section, i, board);
        collapsed = h.collapsed;
        sec.appendChild(h.head);
      }
      root.appendChild(sec);
      if (collapsed) return;
      const items = section.items.filter(it => renderable(it, used)).sort((a, b) => a.y - b.y || a.x - b.x);
      let apps = null;
      items.forEach(it => {
        if (it.kind === 'app') {
          if (!apps) { apps = el('div', 'flow-apps'); sec.appendChild(apps); }
          apps.appendChild(appTile(it));
          return;
        }
        apps = null;
        const box = el('div', `flow-item kind-${it.kind}${it.w >= board.columns / 2 ? ' wide' : ''}`);
        if (it.kind === 'iframe') box.style.height = `${it.h * CELL}px`;
        box.appendChild(content(it));
        sec.appendChild(box);
      });
    });
  }

  function applyBackground(board) {
    const bg = $('board-bg');
    const url = safeUrl(board.background && board.background.url);
    document.body.classList.toggle('has-board-bg', !!url);
    bg.style.backgroundImage = url ? `url("${encodeURI(url).replace(/"/g, '%22')}")` : '';
    bg.style.setProperty('--board-dim', String(((board.background && board.background.dim) || 0) / 100));
  }

  function updateEmpty() {
    const empty = $('dash-empty');
    const hasItems = !!document.querySelector('#board .board-item, #board .app-tile, #board .flow-item');
    empty.hidden = editing || hasItems;
  }

  function render() {
    if (!cfg) return;
    teardown();
    flow = window.innerWidth <= GRID_MIN_WIDTH;
    if (flow && editing) setEditing(false, true);
    const board = active();
    document.body.classList.toggle('board-editing', editing);
    document.body.classList.toggle('board-flow', flow);
    applyBackground(board);
    if (flow) renderFlow(board); else renderGrid(board);
    renderTabs();
    updateEmpty();
    paintStatus();
    window.dispatchEvent(new Event('resize'));  // charts re-measure
  }

  // ---------------------------------------------------------------------------
  // Editing: model sync and saving
  // ---------------------------------------------------------------------------
  // Positions from the grids back into the model; items that are not shown (hardware this Pi lacks,
  // no permission, collapsed) keep their place
  function sync() {
    if (flow || !grids.size) return;
    const board = active();
    const byId = new Map();
    board.sections.forEach(s => s.items.forEach(it => byId.set(it.id, it)));
    const shown = new Set();
    const next = new Map(board.sections.map(s => [s.id, []]));
    grids.forEach((grid, sid) => {
      grid.getGridItems().forEach(node => {
        const id = node.getAttribute('gs-id');
        const it = byId.get(id);
        const n = node.gridstackNode;
        if (!it || !n || !next.has(sid)) return;
        shown.add(id);
        next.get(sid).push({ ...it, x: n.x, y: n.y, w: n.w, h: n.h });
      });
    });
    board.sections.forEach(s => {
      if (!grids.has(s.id)) { next.set(s.id, s.items); return; }
      s.items.forEach(it => { if (!shown.has(it.id)) next.get(s.id).push(it); });
    });
    board.sections.forEach(s => { s.items = next.get(s.id); });
    save();
  }

  function setSaveState(text, level) {
    const n = $('customize-count');
    if (!n) return;
    n.textContent = text;
    n.className = level || '';
  }

  function save() {
    clearTimeout(saveTimer);
    setSaveState(t('Speichert…'));
    saveTimer = setTimeout(() => {
      saving = saving.then(async () => {
        try {
          const res = await api('/api/boards', { rev: cfg.rev, boards: cfg.boards });
          cfg.rev = res.rev;
          setSaveState(t('Gespeichert'), 'ok');
        } catch (err) {
          if (err.status === 409) {
            toast(t('Das Board wurde auf einem anderen Gerät geändert – der aktuelle Stand wird geladen.'), 'error');
            await load();
          } else {
            setSaveState(err.message, 'error');
            toast(err.message, 'error');
          }
        }
      });
    }, 500);
  }

  function removeItem(item, wrap) {
    active().sections.forEach(s => { s.items = s.items.filter(it => it.id !== item.id); });
    if (item.kind === 'card') {
      const c = cardEl(item.card);
      c.classList.remove('board-card');
      pool().appendChild(c);  // back to the pool before its grid element goes away
    }
    const grid = [...grids.values()].find(g => g.getGridItems().includes(wrap));
    if (grid) grid.removeWidget(wrap, true, false);
    sync();
    updateEmpty();
  }

  function moveSection(section, dir) {
    sync();
    const list = active().sections;
    const i = list.indexOf(section), j = i + dir;
    if (j < 1 || j >= list.length) return;  // the first (unnamed) section stays on top
    list.splice(i, 1);
    list.splice(j, 0, section);
    save();
    render();
  }

  function addItem(item, sectionId) {
    const board = active();
    const section = board.sections.find(s => s.id === sectionId) || board.sections[0];
    const grid = grids.get(section.id);
    item.id = newId();
    item.x = 0;
    item.y = 0;
    section.items.push(item);
    if (grid) {
      const node = gridItem(item);
      node.removeAttribute('gs-x');
      node.removeAttribute('gs-y');
      node.setAttribute('gs-auto-position', 'true');
      grid.el.appendChild(node);
      grid.makeWidget(node);
      sync();
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      save();
      render();
    }
    updateEmpty();
  }

  function setEditing(on, quiet) {
    if (on && !canEdit()) return;
    if (on && window.innerWidth <= GRID_MIN_WIDTH) {
      toast(t('Zum Bearbeiten das Fenster breiter machen (ab 1100 Pixel).'), 'error');
      return;
    }
    if (!on && editing) sync();
    editing = on;
    $('customize-bar').hidden = !on;
    $('btn-customize').setAttribute('aria-pressed', String(on));
    if (on) setSaveState(t('Gespeichert'), 'ok');
    if (!quiet) render();
  }

  // ---------------------------------------------------------------------------
  // Boards (tabs)
  // ---------------------------------------------------------------------------
  function switchBoard(id) {
    if (!cfg.boards.some(b => b.id === id)) return;
    if (editing) sync();
    activeId = id;
    try { localStorage.setItem(KEY_ACTIVE, id); } catch (e) { /* ignore */ }
    try { history.replaceState(null, '', id === cfg.boards[0].id ? location.pathname : `#board-${id}`); } catch (e) { /* ignore */ }
    render();
    startStatus();
    window.scrollTo({ top: 0 });
  }

  function addBoard() {
    const name = (window.prompt(t('Name des neuen Boards'), '') || '').trim().slice(0, 40);
    if (!name) return;
    sync();
    let id = name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 20) || 'board';
    while (cfg.boards.some(b => b.id === id)) id = `${id.slice(0, 20)}-${Math.floor(Math.random() * 90 + 10)}`;
    cfg.boards.push({ id, name, columns: 12, background: { url: '', dim: 40 }, sections: [{ id: newId(), name: '', collapsed: false, items: [] }] });
    save();
    switchBoard(id);
  }

  function moveBoard(board, dir) {
    sync();
    const i = cfg.boards.indexOf(board), j = i + dir;
    if (j < 0 || j >= cfg.boards.length) return;
    cfg.boards.splice(i, 1);
    cfg.boards.splice(j, 0, board);
    save();
    renderTabs();
  }

  function deleteBoard(board) {
    if (cfg.boards.length < 2) return;
    if (!window.confirm(`${t('Board löschen')}: ${boardName(board)}?`)) return;
    cfg.boards = cfg.boards.filter(b => b !== board);
    save();
    switchBoard(cfg.boards[0].id);
  }

  function renderTabs() {
    const bar = $('dash-tabs');
    bar.innerHTML = '';
    bar.hidden = cfg.boards.length < 2 && !editing;
    document.body.classList.toggle('has-tabs', !bar.hidden);
    const list = el('div', 'dash-tab-list');
    list.setAttribute('role', 'tablist');
    cfg.boards.forEach((b, i) => {
      const wrap = el('div', `dash-tab${b.id === active().id ? ' active' : ''}`);
      const btn = el('button', 'dash-tab-btn', boardName(b));
      btn.type = 'button';
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', String(b.id === active().id));
      btn.setAttribute('data-no-i18n', '');
      btn.title = i < 9 ? `Alt+${i + 1}` : '';
      btn.addEventListener('click', () => switchBoard(b.id));
      wrap.appendChild(btn);
      if (editing && b.id === active().id) {
        const tools = el('span', 'dash-tab-tools');
        const mk = (label, text, fn, disabled) => {
          const x = el('button', 'dash-tab-tool', text);
          x.type = 'button';
          x.title = t(label);
          x.setAttribute('aria-label', t(label));
          x.disabled = !!disabled;
          x.addEventListener('click', e => { e.stopPropagation(); fn(); });
          tools.appendChild(x);
        };
        mk('Nach links', '‹', () => moveBoard(b, -1), i === 0);
        mk('Nach rechts', '›', () => moveBoard(b, 1), i === cfg.boards.length - 1);
        mk('Board-Einstellungen', '✎', openBoardDialog);
        if (cfg.boards.length > 1) mk('Board löschen', '×', () => deleteBoard(b));
        wrap.appendChild(tools);
      }
      list.appendChild(wrap);
    });
    bar.appendChild(list);
    if (editing) {
      const add = el('button', 'dash-tab-add', t('+ Neues Board'));
      add.type = 'button';
      add.addEventListener('click', addBoard);
      bar.appendChild(add);
    }
  }

  // ---------------------------------------------------------------------------
  // App status
  // ---------------------------------------------------------------------------
  function paintStatus() {
    document.querySelectorAll('[data-app-status]').forEach(dot => {
      const s = statusData[dot.dataset.appStatus];
      dot.classList.toggle('up', !!(s && s.ok));
      dot.classList.toggle('down', !!(s && !s.ok));
      dot.title = !s ? t('Status wird geprüft…') : s.ok ? `${t('Erreichbar')}${s.ms != null ? ` · ${s.ms} ms` : ''}`
        : `${t('Nicht erreichbar')}${s.code ? ` (HTTP ${s.code})` : ''}`;
    });
  }

  async function pollStatus() {
    if (document.hidden) return;
    const board = active();
    if (!board.sections.some(s => s.items.some(it => it.kind === 'app' && it.ping))) return;
    try {
      statusData = (await api(`/api/boards/status?board=${encodeURIComponent(board.id)}`)).apps || {};
      paintStatus();
    } catch (e) { /* next round */ }
  }

  function startStatus() {
    clearInterval(statusTimer);
    statusData = {};
    paintStatus();
    pollStatus();
    statusTimer = setInterval(pollStatus, STATUS_EVERY);
  }

  // ---------------------------------------------------------------------------
  // Dialogs
  // ---------------------------------------------------------------------------
  function openModal(title, sub, body) {
    $('board-modal-title').textContent = title;
    $('board-modal-sub').textContent = sub || '';
    const box = $('board-modal-body');
    box.innerHTML = '';
    box.appendChild(body);
    $('board-modal').classList.add('open');
    $('board-modal').setAttribute('aria-hidden', 'false');
    const first = box.querySelector('input, select, button');
    if (first) setTimeout(() => first.focus(), 50);
  }

  function closeModal() {
    $('board-modal').classList.remove('open');
    $('board-modal').setAttribute('aria-hidden', 'true');
  }

  const modalOpen = () => $('board-modal').classList.contains('open');

  function field(label, input, hint) {
    const l = el('label', 'login-field');
    l.append(el('span', '', label), input);
    if (hint) l.appendChild(el('small', 'field-hint', hint));
    return l;
  }

  function input(value, opts = {}) {
    const i = el('input');
    i.type = opts.type || 'text';
    i.value = value == null ? '' : value;
    if (opts.placeholder) i.placeholder = opts.placeholder;
    if (opts.max) i.maxLength = opts.max;
    if (opts.required) i.required = true;
    i.spellcheck = false;
    return i;
  }

  function check(label, checked) {
    const l = el('label', 'toggle-label');
    const c = el('input');
    c.type = 'checkbox';
    c.checked = !!checked;
    l.append(c, document.createTextNode(` ${label}`));
    return [l, c];
  }

  function actions(submitLabel, extra) {
    const row = el('div', 'form-actions board-form-actions');
    const msg = el('span', 'form-msg');
    msg.setAttribute('role', 'status');
    row.appendChild(msg);
    if (extra) row.appendChild(extra);
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', closeModal);
    const ok = el('button', 'btn-primary', submitLabel);
    ok.type = 'submit';
    row.append(cancel, ok);
    return { row, msg };
  }

  // App form (add and edit)
  function appForm(item, onDone) {
    const form = el('form', 'board-form');
    form.autocomplete = 'off';
    const name = input(item.name, { max: 40, required: true, placeholder: t('z.B. Proxmox') });
    const url = input(item.url, { max: 500, required: true, placeholder: 'https://192.168.178.10:8006' });
    const desc = input(item.description, { max: 120, placeholder: t('optional') });
    const pingUrl = input(item.ping_url, { max: 500, placeholder: t('leer = Adresse oben') });
    const [pingLabel, ping] = check(t('Erreichbarkeit anzeigen (der Pi prüft jede Minute)'), item.ping !== false);
    const [tabLabel, newTab] = check(t('In neuem Tab öffnen'), item.new_tab !== false);

    // Icon picker: dashboard-icons by name, or an own image address
    let icon = item.icon || '';
    let iconChosen = !!icon;
    const preview = el('span', 'board-icon-preview');
    const iconQ = input(/^https?:/i.test(icon) ? icon : icon.replace(/\.(svg|png|webp)$/, ''), { max: 500, placeholder: t('Name suchen oder Bild-Adresse') });
    const results = el('div', 'board-icon-results');
    const drawPreview = () => {
      preview.innerHTML = '';
      preview.appendChild(appIcon({ name: name.value || '?', icon }));
    };
    let searchSeq = 0;
    async function searchIcons(q, auto) {
      const seq = ++searchSeq;
      if (!q || /^https?:\/\//i.test(q)) { results.innerHTML = ''; return; }
      try {
        const res = await api(`/api/boards/icons?q=${encodeURIComponent(q)}`);
        if (seq !== searchSeq) return;
        results.innerHTML = '';
        res.icons.forEach(ic => {
          const b = el('button', `board-icon-option${ic.file === icon ? ' active' : ''}`);
          b.type = 'button';
          b.title = ic.name;
          const img = el('img');
          img.alt = '';
          img.loading = 'lazy';
          img.src = iconSrc(ic.file);
          b.appendChild(img);
          b.addEventListener('click', () => {
            icon = ic.file;
            iconChosen = true;
            iconQ.value = ic.name;
            drawPreview();
            results.querySelectorAll('.active').forEach(x => x.classList.remove('active'));
            b.classList.add('active');
          });
          results.appendChild(b);
        });
        if (!res.icons.length) results.appendChild(el('span', 'field-hint', t('Kein Icon gefunden – Anfangsbuchstabe wird verwendet.')));
        if (auto && res.icons.length && !iconChosen) {
          const slug = q.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
          const exact = res.icons.find(ic => ic.name === slug || ic.name.replace(/-/g, '') === slug.replace(/-/g, ''));
          if (exact) { icon = exact.file; drawPreview(); results.querySelector(`[title="${exact.name}"]`)?.classList.add('active'); }
        }
      } catch (err) {
        results.innerHTML = '';
        results.appendChild(el('span', 'field-hint', err.message));
      }
    }
    let timer = null;
    name.addEventListener('input', () => {
      drawPreview();
      if (iconChosen) return;
      clearTimeout(timer);
      timer = setTimeout(() => { iconQ.value = name.value; searchIcons(name.value, true); }, 350);
    });
    iconQ.addEventListener('input', () => {
      clearTimeout(timer);
      const q = iconQ.value.trim();
      if (/^https?:\/\//i.test(q)) { icon = q; iconChosen = true; drawPreview(); results.innerHTML = ''; return; }
      if (!q) { icon = ''; iconChosen = false; drawPreview(); }
      timer = setTimeout(() => searchIcons(q, false), 300);
    });
    drawPreview();
    if (iconQ.value && !/^https?:/i.test(iconQ.value)) searchIcons(iconQ.value, false);

    const iconRow = el('div', 'board-icon-row');
    iconRow.append(preview, field(t('Icon'), iconQ, t('Aus der Sammlung „Dashboard Icons“ oder eine eigene Bild-Adresse')));
    const grid = el('div', 'board-form-grid');
    grid.append(field(t('Name'), name), field(t('Adresse'), url), field(t('Beschreibung'), desc), field(t('Status-Adresse'), pingUrl, t('Falls der Pi die App unter einer anderen Adresse erreicht')));
    const { row, msg } = actions(item.id ? t('Speichern') : t('Hinzufügen'));
    form.append(grid, iconRow, results, pingLabel, tabLabel, row);
    form.addEventListener('submit', e => {
      e.preventDefault();
      let u = url.value.trim();
      if (u && !/^https?:\/\//i.test(u)) u = `http://${u}`;
      let p = pingUrl.value.trim();
      if (p && !/^https?:\/\//i.test(p)) p = `http://${p}`;
      if (!safeUrl(u)) { msg.className = 'form-msg error'; msg.textContent = t('Adresse muss mit http:// oder https:// beginnen.'); return; }
      onDone({ ...item, kind: 'app', name: name.value.trim(), url: u, description: desc.value.trim(), icon,
               ping: ping.checked, ping_url: p, new_tab: newTab.checked });
    });
    return form;
  }

  function searchForm(item, onDone) {
    const form = el('form', 'board-form');
    const engine = el('select');
    Object.entries(ENGINE_LABELS).forEach(([k, v]) => { const o = el('option', '', v); o.value = k; engine.appendChild(o); });
    engine.value = item.engine || 'duckduckgo';
    const custom = input(item.custom_url, { max: 500, placeholder: 'https://search.example/?q=%s' });
    const customField = field(t('Eigene Such-Adresse'), custom, t('%s steht für den Suchbegriff'));
    const sync2 = () => { customField.hidden = engine.value !== 'custom'; };
    engine.addEventListener('change', sync2);
    sync2();
    const [appsLabel, apps] = check(t('Auch Apps durchsuchen (Enter öffnet den ersten Treffer)'), item.apps !== false);
    const { row } = actions(item.id ? t('Speichern') : t('Hinzufügen'));
    form.append(field(t('Suchmaschine'), engine), customField, appsLabel, row);
    form.addEventListener('submit', e => {
      e.preventDefault();
      onDone({ ...item, kind: 'search', engine: engine.value, custom_url: custom.value.trim(), apps: apps.checked });
    });
    return form;
  }

  function frameForm(item, onDone) {
    const form = el('form', 'board-form');
    const title = input(item.title, { max: 40, placeholder: t('optional') });
    const url = input(item.url, { max: 500, required: true, placeholder: 'http://192.168.178.20:3000/d/abc?kiosk' });
    const { row, msg } = actions(item.id ? t('Speichern') : t('Hinzufügen'));
    form.append(field(t('Titel'), title), field(t('Adresse'), url, t('Manche Seiten verbieten das Einbetten (X-Frame-Options) – dann bleibt das Feld leer.')), row);
    form.addEventListener('submit', e => {
      e.preventDefault();
      let u = url.value.trim();
      if (u && !/^https?:\/\//i.test(u)) u = `http://${u}`;
      if (!safeUrl(u)) { msg.className = 'form-msg error'; msg.textContent = t('Adresse muss mit http:// oder https:// beginnen.'); return; }
      onDone({ ...item, kind: 'iframe', title: title.value.trim(), url: u });
    });
    return form;
  }

  function openItemDialog(item) {
    const done = updated => {
      const board = active();
      board.sections.forEach(s => { s.items = s.items.map(it => (it.id === item.id ? { ...updated } : it)); });
      closeModal();
      sync();
      render();
    };
    const forms = { app: appForm, search: searchForm, iframe: frameForm };
    const titles = { app: 'App bearbeiten', search: 'Suchleiste bearbeiten', iframe: 'Webseite bearbeiten' };
    openModal(t(titles[item.kind]), '', forms[item.kind](item, done));
  }

  function openAddDialog() {
    sync();
    const board = active();
    const body = el('div', 'board-add');
    const target = el('select');
    board.sections.forEach((s, i) => { const o = el('option', '', i === 0 ? t('Ohne Kategorie (oben)') : s.name); o.value = s.id; target.appendChild(o); });
    const targetField = field(t('Einfügen in'), target);
    const tabs = el('div', 'segmented board-add-tabs');
    tabs.setAttribute('role', 'tablist');
    const pane = el('div', 'board-add-pane');
    const add = item => { closeModal(); addItem(item, target.value); };

    const panes = {
      app: () => appForm({ ping: true, new_tab: true, w: 1, h: 2 }, it => add({ ...it, w: 1, h: 2 })),
      widget: () => {
        const box = el('div', 'board-choice-list');
        const choice = (title, text, fn) => {
          const b = el('button', 'board-choice');
          b.type = 'button';
          b.append(el('strong', '', title), el('span', '', text));
          b.addEventListener('click', fn);
          box.appendChild(b);
        };
        choice(t('Suchleiste'), t('Websuche mit DuckDuckGo, Google & Co. – findet auch deine Apps.'), () => {
          pane.innerHTML = '';
          pane.appendChild(searchForm({ engine: 'duckduckgo', apps: true }, it => add({ ...it, w: 6, h: 1 })));
        });
        choice(t('Webseite einbetten'), t('Eine Seite aus dem Heimnetz direkt im Board, z.B. ein Grafana-Dashboard.'), () => {
          pane.innerHTML = '';
          pane.appendChild(frameForm({}, it => add({ ...it, w: 6, h: 6 })));
        });
        return box;
      },
      card: () => {
        const box = el('div', 'board-choice-list cards');
        const onBoard = new Set(board.sections.flatMap(s => s.items.filter(i => i.kind === 'card').map(i => i.card)));
        const free = cfg.cards.filter(c => !onBoard.has(c) && cardAvailable(c));
        free.forEach(c => {
          const b = el('button', 'board-choice');
          b.type = 'button';
          b.appendChild(el('strong', '', t(CARD_LABELS[c] || c)));
          b.addEventListener('click', () => {
            const [w, h] = CARD_SIZES[c] || [3, 5];
            add({ kind: 'card', card: c, w: Math.min(w, board.columns), h });
          });
          box.appendChild(b);
        });
        if (!free.length) box.appendChild(el('p', 'field-hint', t('Alle Karten sind schon auf diesem Board.')));
        return box;
      },
      section: () => {
        const form = el('form', 'board-form');
        const name = input('', { max: 40, required: true, placeholder: t('z.B. Medien, Netzwerk, Smart Home') });
        const { row } = actions(t('Anlegen'));
        form.append(field(t('Name der Kategorie'), name), row);
        form.addEventListener('submit', e => {
          e.preventDefault();
          board.sections.push({ id: newId(), name: name.value.trim(), collapsed: false, items: [] });
          closeModal();
          save();
          render();
        });
        return form;
      }
    };
    const labels = { app: 'App', widget: 'Widget', card: 'Karte', section: 'Kategorie' };
    const show = key => {
      tabs.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.tab === key));
      targetField.hidden = key === 'section';
      pane.innerHTML = '';
      pane.appendChild(panes[key]());
    };
    Object.entries(labels).forEach(([key, label]) => {
      const b = el('button', '', t(label));
      b.type = 'button';
      b.dataset.tab = key;
      b.addEventListener('click', () => show(key));
      tabs.appendChild(b);
    });
    body.append(tabs, targetField, pane);
    openModal(t('Hinzufügen'), t('Apps verlinken deine Dienste, Widgets und Karten zeigen Daten.'), body);
    show('app');
  }

  function openBoardDialog() {
    sync();
    const board = active();
    const form = el('form', 'board-form');
    const name = input(board.name, { max: 40, placeholder: t('Übersicht') });
    const cols = input(board.columns, { type: 'number' });
    cols.min = 4;
    cols.max = 24;
    const bgUrl = input(board.background.url, { max: 500, placeholder: 'https://…/hintergrund.jpg' });
    const dim = input(board.background.dim, { type: 'range' });
    dim.min = 0;
    dim.max = 90;
    dim.step = 5;
    const dimLabel = el('span', 'field-hint', `${board.background.dim} %`);
    dim.addEventListener('input', () => { dimLabel.textContent = `${dim.value} %`; });

    const reset = el('button', 'btn-danger', t('Alle Boards zurücksetzen'));
    reset.type = 'button';
    reset.addEventListener('click', async () => {
      if (!reset.classList.contains('confirming')) {
        reset.classList.add('confirming');
        reset.textContent = t('Wirklich alle zurücksetzen?');
        setTimeout(() => { reset.classList.remove('confirming'); reset.textContent = t('Alle Boards zurücksetzen'); }, 4000);
        return;
      }
      try {
        clearTimeout(saveTimer);
        const res = await api('/api/boards/reset', {});
        cfg = { ...cfg, ...res };
        activeId = cfg.boards[0].id;
        closeModal();
        render();
        startStatus();
        toast(t('Boards auf den Standard zurückgesetzt.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
    const { row } = actions(t('Speichern'), reset);
    const grid = el('div', 'board-form-grid');
    grid.append(field(t('Name'), name), field(t('Spalten'), cols, t('4 bis 24, Standard 12')),
      field(t('Hintergrundbild'), bgUrl, t('Adresse eines Bildes, leer = kein Bild')), field(t('Abdunkeln'), dim));
    grid.lastChild.appendChild(dimLabel);
    form.append(grid, row);
    form.addEventListener('submit', e => {
      e.preventDefault();
      let bg = bgUrl.value.trim();
      if (bg && !/^https?:\/\//i.test(bg)) bg = `https://${bg}`;
      board.name = name.value.trim().slice(0, 40);
      board.columns = Math.max(4, Math.min(24, parseInt(cols.value, 10) || 12));
      board.sections.forEach(s => s.items.forEach(it => { it.w = Math.min(it.w, board.columns); it.x = Math.min(it.x, board.columns - it.w); }));
      board.background = { url: bg, dim: parseInt(dim.value, 10) || 0 };
      closeModal();
      save();
      render();
    });
    openModal(t('Board-Einstellungen'), boardName(board), form);
  }

  // ---------------------------------------------------------------------------
  // Start
  // ---------------------------------------------------------------------------
  async function load() {
    cfg = await api('/api/boards');
    const fromHash = (location.hash.match(/^#board-([\w-]+)$/) || [])[1];
    let stored = null;
    try { stored = localStorage.getItem(KEY_ACTIVE); } catch (e) { /* ignore */ }
    activeId = [fromHash, activeId, stored].find(id => id && cfg.boards.some(b => b.id === id)) || cfg.boards[0].id;
    render();
    startStatus();
    // The first board's icons are being looked up on the Pi: show them as soon as they are there
    if (cfg.icons_pending) setTimeout(() => { if (!editing && !modalOpen()) load().catch(() => {}); }, 6000);
  }

  async function init() {
    if (!$('board') || typeof GridStack === 'undefined') return;
    await Promise.all([window.PiAuth ? window.PiAuth.ready : null, window.PiCaps ? window.PiCaps.ready : null]);
    try {
      await load();
    } catch (err) {
      // Without boards the cards are still shown as before, so the dashboard never ends up empty
      toast(`${t('Boards')}: ${err.message}`, 'error');
      $('board').appendChild(pool());
      pool().hidden = false;
      return;
    }

    $('btn-customize').addEventListener('click', () => setEditing(!editing));
    $('customize-done').addEventListener('click', () => setEditing(false));
    $('board-add').addEventListener('click', openAddDialog);
    $('board-settings').addEventListener('click', openBoardDialog);
    $('dash-empty-edit').addEventListener('click', () => { setEditing(true); if (editing) openAddDialog(); });
    $('board-modal-close').addEventListener('click', closeModal);
    $('board-modal').addEventListener('click', e => { if (e.target === $('board-modal')) closeModal(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        if (modalOpen()) { closeModal(); return; }
        if (editing) setEditing(false);
      }
      if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-9]$/.test(e.code)) {
        const b = cfg.boards[+e.code.slice(5) - 1];
        if (b) { e.preventDefault(); switchBoard(b.id); }
      }
    });
    window.addEventListener('hashchange', () => {
      const id = (location.hash.match(/^#board-([\w-]+)$/) || [])[1];
      if (id && id !== activeId) switchBoard(id);
    });
    let wasFlow = flow;
    let resizeTimer = null;
    window.addEventListener('resize', e => {
      if (!e.isTrusted) return;  // our own resize events for the charts
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const nowFlow = window.innerWidth <= GRID_MIN_WIDTH;
        if (nowFlow !== wasFlow) { wasFlow = nowFlow; render(); }
      }, 200);
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollStatus(); });
    // Links (header, palette) must not open while dragging in edit mode
    document.addEventListener('click', e => {
      if (editing && e.target.closest('#board .board-item') && !e.target.closest('.board-item-tools')) {
        e.preventDefault();
        e.stopPropagation();
      }
    }, true);

    window.PiLayout = {
      edit: () => setEditing(true),
      tabs: () => cfg.boards.map(b => ({ id: b.id, name: boardName(b) })),
      switchTab: switchBoard,
      addTab: addBoard,
      apps: () => allApps().map(a => ({ id: a.id, name: a.name, url: a.url, new_tab: a.new_tab, description: a.description }))
    };
    window.dispatchEvent(new Event('pi-boards-ready'));
  }

  init();
})();
