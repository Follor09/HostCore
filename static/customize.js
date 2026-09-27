/**
 * PiCore Dashboard - customizable layout
 * Hide cards and change their order (arrows or drag & drop), saved per browser.
 * Several dashboard pages ("tabs"): order and widths are shared, each tab has its own
 * set of hidden cards.
 * Cards only move within their own area (top row, metric grid, bottom row, page sections),
 * so the grid layouts keep working.
 */
(function () {
  'use strict';

  const KEY = 'dashLayout';
  const LABELS = {
    top: 'Obere Reihe', metrics: 'Messwerte', bottom: 'Untere Reihe',
    clock: 'Uhrzeit & Betriebszeit', weather: 'Wetter', services: 'Server-Dienste',
    cpu: 'CPU-Auslastung', temp: 'CPU-Temperatur', ram: 'Arbeitsspeicher', disk: 'Speicher & NVMe',
    network: 'Netzwerk', fan: 'Lüfter', power: 'Stromversorgung', updates: 'System-Updates', wol: 'Wake-on-LAN',
    notes: 'Notizen', events: 'Ereignisse', actions: 'Schnellaktionen', health: 'Web-Dienste', history: 'Verlauf', workloads: 'Docker & Kubernetes', storage: 'Laufwerke & Explorer', processes: 'Prozesse & Diagnose'
  };

  // The first page has no stored name until it's renamed: shown as "Übersicht" in the page language
  let layout = { order: {}, sizes: {}, tabs: [{ id: 'main', name: '', hidden: [] }], active: 'main' };
  // Which cards can change width: metric cards get wider, page sections get half width
  const SIZE_OPTIONS = { metrics: ['normal', 'wide'], main: ['full', 'half'] };
  const RESIZABLE_MAIN = ['history', 'workloads'];
  let editing = false;
  let dragged = null;

  const groups = () => [...document.querySelectorAll('[data-widget-group]')];
  const widgetsOf = group => [...group.children].filter(el => el.dataset && el.dataset.widget);
  const isContainer = el => el.hasAttribute('data-widget-group');

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (saved && typeof saved === 'object') {
        let tabs = Array.isArray(saved.tabs) ? saved.tabs.filter(t => t && t.id && (t.name || t.id === 'main')) : [];
        // Older layouts had one global "hidden" list: it becomes the first tab
        if (!tabs.length) tabs = [{ id: 'main', name: '', hidden: Array.isArray(saved.hidden) ? saved.hidden : [] }];
        tabs.forEach(t => { if (!Array.isArray(t.hidden)) t.hidden = []; });
        layout = { order: saved.order || {}, sizes: saved.sizes && typeof saved.sizes === 'object' ? saved.sizes : {},
                   tabs, active: tabs.some(t => t.id === saved.active) ? saved.active : tabs[0].id };
      }
    } catch (e) { /* storage unavailable: default layout */ }
    const fromHash = (location.hash.match(/^#tab-([\w-]+)$/) || [])[1];
    if (fromHash && layout.tabs.some(t => t.id === fromHash)) layout.active = fromHash;
  }

  const activeTab = () => layout.tabs.find(t => t.id === layout.active) || layout.tabs[0];
  const allWidgets = () => [...document.querySelectorAll('[data-widget]')].filter(w => !isContainer(w)).map(w => w.dataset.widget);

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(layout)); } catch (e) { /* ignore */ }
  }

  function snapshotOrder() {
    groups().forEach(g => { layout.order[g.dataset.widgetGroup] = widgetsOf(g).map(w => w.dataset.widget); });
  }

  // Apply saved order (unknown / new cards keep their place at the end) and visibility
  function apply() {
    groups().forEach(g => {
      const order = layout.order[g.dataset.widgetGroup];
      if (!Array.isArray(order)) return;
      const byId = new Map(widgetsOf(g).map(w => [w.dataset.widget, w]));
      const anchor = widgetsOf(g).slice(-1)[0];
      const tail = anchor ? anchor.nextSibling : null;
      order.forEach(id => { const w = byId.get(id); if (w) g.insertBefore(w, tail); });
      widgetsOf(g).filter(w => !order.includes(w.dataset.widget)).forEach(w => g.insertBefore(w, tail));
    });
    applySizes();
    refreshVisibility();
  }

  function sizeOptions(w) {
    const group = w.parentElement && w.parentElement.dataset.widgetGroup;
    if (group === 'metrics') return SIZE_OPTIONS.metrics;
    if (group === 'main' && RESIZABLE_MAIN.includes(w.dataset.widget)) return SIZE_OPTIONS.main;
    return null;
  }

  function applySizes() {
    document.querySelectorAll('[data-widget]').forEach(w => {
      const size = layout.sizes[w.dataset.widget];
      w.classList.toggle('size-wide', size === 'wide');
      w.classList.toggle('size-half', size === 'half');
    });
  }

  function toggleSize(w) {
    const opts = sizeOptions(w);
    if (!opts) return;
    const cur = layout.sizes[w.dataset.widget] || opts[0];
    const next = opts[(opts.indexOf(cur) + 1) % opts.length];
    if (next === opts[0]) delete layout.sizes[w.dataset.widget];
    else layout.sizes[w.dataset.widget] = next;
    save();
    applySizes();
    updateTools();
    window.dispatchEvent(new Event('resize'));
  }

  function refreshVisibility() {
    const hidden = new Set(activeTab().hidden);
    document.querySelectorAll('[data-widget]').forEach(w => {
      if (isContainer(w)) return;
      w.classList.toggle('widget-off', hidden.has(w.dataset.widget));
    });
    // Containers: hide when every card inside is hidden; grids adapt to the visible count
    groups().filter(g => g.dataset.widget).forEach(g => {
      const visible = widgetsOf(g).filter(w => !w.classList.contains('widget-off') && !w.classList.contains('cap-off')).length;
      g.classList.toggle('widget-off', visible === 0);
      g.dataset.visible = visible;
    });
    document.body.classList.toggle('layout-custom', activeTab().hidden.length > 0 || Object.keys(layout.order).length > 0);
    const empty = document.getElementById('dash-empty');
    const unsupported = new Set([...document.querySelectorAll('[data-widget].cap-off')].map(w => w.dataset.widget));
    if (empty) empty.hidden = editing || allWidgets().some(id => !hidden.has(id) && !unsupported.has(id));
    renderTabs();
    window.dispatchEvent(new Event('resize'));  // charts re-measure
  }

  function move(w, dir) {
    const siblings = widgetsOf(w.parentElement);
    const i = siblings.indexOf(w);
    const target = siblings[i + dir];
    if (!target) return;
    w.parentElement.insertBefore(w, dir < 0 ? target : target.nextSibling);
    snapshotOrder();
    save();
    updateTools();
    w.querySelector('.widget-tools [data-move="' + dir + '"]')?.focus();
  }

  function toggleHidden(w) {
    const id = w.dataset.widget;
    const tab = activeTab();
    tab.hidden = tab.hidden.includes(id) ? tab.hidden.filter(x => x !== id) : [...tab.hidden, id];
    save();
    refreshVisibility();
    updateTools();
  }

  // ---------------------------------------------------------------------------
  // Edit mode: a small toolbar on every card
  // ---------------------------------------------------------------------------
  const ICONS = {
    left: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    right: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
    up: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15l-6-6-6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
    eye: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    width: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5M3 12h18"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
  };

  function toolButton(icon, title, onClick, attrs = {}) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'widget-tool';
    b.innerHTML = icon;
    b.title = title;
    b.setAttribute('aria-label', title);
    Object.entries(attrs).forEach(([k, v]) => b.setAttribute(k, v));
    b.addEventListener('click', e => { e.stopPropagation(); e.preventDefault(); onClick(); });
    return b;
  }

  function addTools(w) {
    const bar = document.createElement('div');
    bar.className = 'widget-tools';
    bar.setAttribute('data-no-drag', '');
    // The page sections stack vertically; everything else flows in rows
    const vertical = w.parentElement.dataset.widgetGroup === 'main';
    const name = document.createElement('span');
    name.className = 'widget-tools-name';
    name.textContent = LABELS[w.dataset.widget] || w.dataset.widget;
    bar.append(
      name,
      toolButton(vertical ? ICONS.up : ICONS.left, vertical ? 'Nach oben' : 'Nach vorne', () => move(w, -1), { 'data-move': '-1' }),
      toolButton(vertical ? ICONS.down : ICONS.right, vertical ? 'Nach unten' : 'Nach hinten', () => move(w, 1), { 'data-move': '1' })
    );
    if (sizeOptions(w)) bar.append(toolButton(ICONS.width, 'Breite ändern', () => toggleSize(w), { 'data-size': '' }));
    if (!isContainer(w)) bar.append(toolButton(ICONS.eye, 'Ausblenden', () => toggleHidden(w), { 'data-hide': '' }));
    w.prepend(bar);

    w.draggable = true;
    w.addEventListener('dragstart', onDragStart);
    w.addEventListener('dragend', onDragEnd);
  }

  function updateTools() {
    document.querySelectorAll('[data-widget]').forEach(w => {
      const bar = w.querySelector(':scope > .widget-tools');
      if (!bar) return;
      const siblings = widgetsOf(w.parentElement);
      const i = siblings.indexOf(w);
      bar.querySelector('[data-move="-1"]').disabled = i === 0;
      bar.querySelector('[data-move="1"]').disabled = i === siblings.length - 1;
      const sizeBtn = bar.querySelector('[data-size]');
      if (sizeBtn) {
        const opts = sizeOptions(w);
        const cur = layout.sizes[w.dataset.widget] || opts[0];
        const label = { normal: 'Normal', wide: 'Breit', full: 'Volle Breite', half: 'Halbe Breite' }[cur];
        sizeBtn.title = `Breite: ${label}`;
        sizeBtn.setAttribute('aria-label', sizeBtn.title);
        sizeBtn.setAttribute('aria-pressed', String(cur !== opts[0]));
      }
      const hide = bar.querySelector('[data-hide]');
      if (hide) {
        const off = w.classList.contains('widget-off');
        hide.innerHTML = off ? ICONS.eyeOff : ICONS.eye;
        hide.title = off ? 'Einblenden' : 'Ausblenden';
        hide.setAttribute('aria-label', hide.title);
        hide.setAttribute('aria-pressed', String(off));
      }
    });
    const n = activeTab().hidden.length;
    document.getElementById('customize-count').textContent = n ? `${n} ausgeblendet` : 'Alles sichtbar';
  }

  // Drag & drop within the same area
  function onDragStart(e) {
    if (!editing || e.target !== e.currentTarget) return;
    dragged = e.currentTarget;
    dragged.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.dataset.widget);
  }

  function onDragEnd() {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    dragged = null;
    snapshotOrder();
    save();
    updateTools();
  }

  function onDragOver(e) {
    if (!dragged) return;
    const target = e.target.closest('[data-widget]');
    let over = target;
    // Climb to the card that sits in the same area as the dragged one
    while (over && over.parentElement !== dragged.parentElement) over = over.parentElement.closest('[data-widget]');
    if (!over) return;
    e.preventDefault();
    if (over === dragged) return;
    const r = over.getBoundingClientRect();
    const vertical = dragged.parentElement.dataset.widgetGroup === 'main';
    const after = vertical ? e.clientY > r.top + r.height / 2 : e.clientX > r.left + r.width / 2;
    dragged.parentElement.insertBefore(dragged, after ? over.nextSibling : over);
  }

  function setEditing(on) {
    editing = on;
    document.body.classList.toggle('customizing', on);
    renderTabs();
    const empty = document.getElementById('dash-empty');
    if (empty) empty.hidden = true;
    document.getElementById('customize-bar').hidden = !on;
    document.getElementById('btn-customize').setAttribute('aria-pressed', String(on));
    if (on) {
      document.querySelectorAll('[data-widget]').forEach(addTools);
      updateTools();
      document.getElementById('customize-done').focus();
    } else {
      document.querySelectorAll('.widget-tools').forEach(b => b.remove());
      refreshVisibility();
      document.querySelectorAll('[data-widget]').forEach(w => {
        w.draggable = false;
        w.removeEventListener('dragstart', onDragStart);
        w.removeEventListener('dragend', onDragEnd);
      });
    }
    window.dispatchEvent(new Event('resize'));
  }

  // ---------------------------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------------------------
  function switchTab(id) {
    if (!layout.tabs.some(t => t.id === id) || id === layout.active) return;
    layout.active = id;
    save();
    try { history.replaceState(null, '', id === layout.tabs[0].id ? location.pathname : `#tab-${id}`); } catch (e) { /* ignore */ }
    refreshVisibility();
    if (editing) updateTools();
    window.scrollTo({ top: 0 });
  }

  function addTab() {
    const name = (window.prompt(t('Name der neuen Seite'), '') || '').trim().slice(0, 30);
    if (!name) return;
    let id = name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'seite';
    while (layout.tabs.some(x => x.id === id)) id += '-2';
    // A new page starts empty: cards are switched on with the eye in edit mode
    layout.tabs.push({ id, name, hidden: allWidgets() });
    save();
    switchTab(id);
    if (!editing) setEditing(true);
  }

  function renameTab(tab) {
    const name = (window.prompt(t('Neuer Name'), tabName(tab)) || '').trim().slice(0, 30);
    if (!name) return;
    tab.name = name;
    save();
    renderTabs();
  }

  function deleteTab(tab) {
    if (layout.tabs.length < 2) return;
    if (!window.confirm(`${t('Seite löschen')}: ${tabName(tab)}?`)) return;
    layout.tabs = layout.tabs.filter(x => x !== tab);
    if (layout.active === tab.id) layout.active = layout.tabs[0].id;
    save();
    refreshVisibility();
    if (editing) updateTools();
  }

  function moveTab(tab, dir) {
    const i = layout.tabs.indexOf(tab), j = i + dir;
    if (j < 0 || j >= layout.tabs.length) return;
    layout.tabs.splice(i, 1);
    layout.tabs.splice(j, 0, tab);
    save();
    renderTabs();
  }

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const tabName = tab => tab.name || t('Übersicht');

  function renderTabs() {
    const bar = document.getElementById('dash-tabs');
    if (!bar) return;
    bar.innerHTML = '';
    bar.hidden = layout.tabs.length < 2 && !editing;
    document.body.classList.toggle('has-tabs', !bar.hidden);
    const list = document.createElement('div');
    list.className = 'dash-tab-list';
    list.setAttribute('role', 'tablist');
    layout.tabs.forEach((tab, i) => {
      const wrap = document.createElement('div');
      wrap.className = `dash-tab${tab.id === layout.active ? ' active' : ''}`;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dash-tab-btn';
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(tab.id === layout.active));
      b.textContent = tabName(tab);
      b.setAttribute('data-no-i18n', '');
      b.title = i < 9 ? `Alt+${i + 1}` : '';
      b.addEventListener('click', () => switchTab(tab.id));
      wrap.appendChild(b);
      if (editing && tab.id === layout.active) {
        const tools = document.createElement('span');
        tools.className = 'dash-tab-tools';
        const mk = (label, text, fn, disabled) => {
          const x = document.createElement('button');
          x.type = 'button';
          x.className = 'dash-tab-tool';
          x.textContent = text;
          x.title = t(label);
          x.setAttribute('aria-label', t(label));
          x.disabled = !!disabled;
          x.addEventListener('click', e => { e.stopPropagation(); fn(); });
          tools.appendChild(x);
        };
        mk('Nach links', '‹', () => moveTab(tab, -1), i === 0);
        mk('Nach rechts', '›', () => moveTab(tab, 1), i === layout.tabs.length - 1);
        mk('Umbenennen', '✎', () => renameTab(tab));
        if (layout.tabs.length > 1) mk('Seite löschen', '×', () => deleteTab(tab));
        wrap.appendChild(tools);
      }
      list.appendChild(wrap);
    });
    bar.appendChild(list);
    if (editing) {
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'dash-tab-add';
      add.textContent = '+ Neue Seite';
      add.addEventListener('click', addTab);
      bar.appendChild(add);
    }
  }

  function reset() {
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    location.reload();
  }

  function init() {
    if (!document.querySelector('[data-widget-group]')) return;
    // Remember the original order for "reset" by reloading without saved layout
    load();
    apply();

    const btn = document.getElementById('btn-customize');
    btn.addEventListener('click', () => setEditing(!editing));
    document.getElementById('customize-done').addEventListener('click', () => setEditing(false));
    document.getElementById('customize-reset').addEventListener('click', e => {
      const b = e.currentTarget;
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = 'Wirklich zurücksetzen?';
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = 'Zurücksetzen'; }, 4000);
        return;
      }
      reset();
    });
    document.addEventListener('dragover', onDragOver);
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && editing) setEditing(false);
      // Alt+1..9 switches dashboard pages
      if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-9]$/.test(e.code)) {
        const tab = layout.tabs[+e.code.slice(5) - 1];
        if (tab) { e.preventDefault(); switchTab(tab.id); }
      }
    });
    window.addEventListener('hashchange', () => {
      const id = (location.hash.match(/^#tab-([\w-]+)$/) || [])[1];
      if (id) switchTab(id);
    });
    const emptyEdit = document.getElementById('dash-empty-edit');
    if (emptyEdit) emptyEdit.addEventListener('click', () => setEditing(true));
    // Cards must not react to clicks while editing (e.g. the updates card opens a sheet)
    document.addEventListener('click', e => {
      if (editing && e.target.closest('[data-widget]') && !e.target.closest('.widget-tools, #customize-bar')) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);

    // Cards for hardware this Pi lacks disappear once caps.js knows; the grids recount
    if (window.PiCaps) window.PiCaps.ready.then(refreshVisibility);

    window.PiLayout = { edit: () => setEditing(true), reset, tabs: () => layout.tabs.map(x => ({ id: x.id, name: tabName(x) })), switchTab, addTab };
  }

  init();
})();
