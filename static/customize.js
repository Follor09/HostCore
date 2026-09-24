/**
 * PiCore Dashboard - customizable layout
 * Hide cards and change their order (arrows or drag & drop), saved per browser.
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
    history: 'Verlauf', workloads: 'Docker & Kubernetes', storage: 'Laufwerke & Explorer', processes: 'Prozesse & Diagnose'
  };

  let layout = { order: {}, hidden: [] };
  let editing = false;
  let dragged = null;

  const groups = () => [...document.querySelectorAll('[data-widget-group]')];
  const widgetsOf = group => [...group.children].filter(el => el.dataset && el.dataset.widget);
  const isContainer = el => el.hasAttribute('data-widget-group');

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (saved && typeof saved === 'object') {
        layout = { order: saved.order || {}, hidden: Array.isArray(saved.hidden) ? saved.hidden : [] };
      }
    } catch (e) { /* storage unavailable: default layout */ }
  }

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
    refreshVisibility();
  }

  function refreshVisibility() {
    const hidden = new Set(layout.hidden);
    document.querySelectorAll('[data-widget]').forEach(w => {
      if (isContainer(w)) return;
      w.classList.toggle('widget-off', hidden.has(w.dataset.widget));
    });
    // Containers: hide when every card inside is hidden; grids adapt to the visible count
    groups().filter(g => g.dataset.widget).forEach(g => {
      const visible = widgetsOf(g).filter(w => !w.classList.contains('widget-off')).length;
      g.classList.toggle('widget-off', visible === 0);
      g.dataset.visible = visible;
    });
    document.body.classList.toggle('layout-custom', layout.hidden.length > 0 || Object.keys(layout.order).length > 0);
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
    layout.hidden = layout.hidden.includes(id) ? layout.hidden.filter(x => x !== id) : [...layout.hidden, id];
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
      const hide = bar.querySelector('[data-hide]');
      if (hide) {
        const off = w.classList.contains('widget-off');
        hide.innerHTML = off ? ICONS.eyeOff : ICONS.eye;
        hide.title = off ? 'Einblenden' : 'Ausblenden';
        hide.setAttribute('aria-label', hide.title);
        hide.setAttribute('aria-pressed', String(off));
      }
    });
    const n = layout.hidden.length;
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
    document.getElementById('customize-bar').hidden = !on;
    document.getElementById('btn-customize').setAttribute('aria-pressed', String(on));
    if (on) {
      document.querySelectorAll('[data-widget]').forEach(addTools);
      updateTools();
      document.getElementById('customize-done').focus();
    } else {
      document.querySelectorAll('.widget-tools').forEach(b => b.remove());
      document.querySelectorAll('[data-widget]').forEach(w => {
        w.draggable = false;
        w.removeEventListener('dragstart', onDragStart);
        w.removeEventListener('dragend', onDragEnd);
      });
    }
    window.dispatchEvent(new Event('resize'));
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
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && editing) setEditing(false); });
    // Cards must not react to clicks while editing (e.g. the updates card opens a sheet)
    document.addEventListener('click', e => {
      if (editing && e.target.closest('[data-widget]') && !e.target.closest('.widget-tools, #customize-bar')) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);

    window.PiLayout = { edit: () => setEditing(true), reset };
  }

  init();
})();
