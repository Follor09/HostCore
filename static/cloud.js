/**
 * HostCore Dashboard - files page (/dateien): own cloud like Nextcloud.
 * Spaces "Meine Dateien" / "Gemeinsam" (admins also every user's), recent files, favorites,
 * share links, uploads from exchange links and doc attachments, recycle bin.
 * Grid or list, drag & drop (files and folders), multi-select, preview with arrows.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);

  const ICONS = {
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
    video: '<rect x="2" y="5" width="15" height="14" rx="2"/><path d="M17 10l5-3v10l-5-3"/>',
    audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    pdf: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 15h1.5a1.5 1.5 0 0 0 0-3H8v5M13 12v5h1a2.5 2.5 0 0 0 0-5z"/>',
    text: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/>',
    archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
    office: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 12l1.5 6L12 13l2.5 5L16 12"/>',
    more: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>'
  };
  const svg = name => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ICONS.file}</svg>`;
  document.querySelectorAll('.cloud-ico[data-icon]').forEach(e => { e.innerHTML = svg(e.dataset.icon); });

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };
  function button(label, cls, onClick) {
    const b = el('button', cls, label);
    b.type = 'button';
    if (onClick) b.addEventListener('click', onClick);
    return b;
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
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail ? t(d.detail) : `${t('Fehler')} ${res.status}`);
    return d;
  }

  function fmtBytes(b) {
    if (b === null || b === undefined) return '';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = b ? Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024))) : 0;
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }
  const fmtTs = ts => {
    const d = new Date(ts * 1000);
    const today = d.toDateString() === new Date().toDateString();
    return d.toLocaleString(LOCALE, today ? { hour: '2-digit', minute: '2-digit' } : { day: '2-digit', month: '2-digit', year: 'numeric' });
  };
  const q = obj => new URLSearchParams(obj).toString();
  const fileUrl = (space, path, inline) => `/api/cloud/file?${q({ space, path, ...(inline ? { inline: 1 } : {}) })}`;
  const KIND_ICON = { folder: 'folder', image: 'image', video: 'video', audio: 'audio', pdf: 'pdf', text: 'text', archive: 'archive', office: 'office' };
  const PREVIEWABLE = new Set(['image', 'video', 'audio', 'pdf', 'text']);

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  const state = {
    view: 'me',            // me | shared | user:<n> | recent | favorites | shares | uploads | trash | search
    path: '',
    entries: [],
    selected: new Set(),
    layout: localStorage.getItem('cloud-layout') || 'grid',
    info: null,
    query: ''
  };
  const isSpace = v => v === 'me' || v === 'shared' || v.startsWith('user:');
  const spaceLabel = s => s === 'me' ? t('Meine Dateien') : s === 'shared' ? t('Gemeinsam') : s.startsWith('user:') ? s.slice(5) : s;

  function setHash() {
    const h = isSpace(state.view) ? `${state.view}${state.path ? `/${state.path}` : ''}` : state.view;
    history.replaceState(null, '', `#${encodeURI(h)}`);
  }
  function fromHash() {
    const h = decodeURI(location.hash.slice(1));
    if (!h) return;
    const [first, ...rest] = h.split('/');
    state.view = first || 'me';
    state.path = isSpace(state.view) ? rest.join('/') : '';
  }

  // ---------------------------------------------------------------------------
  // Overview: storage meter, admins' user list
  // ---------------------------------------------------------------------------
  async function loadInfo() {
    try { state.info = await api('/api/cloud'); } catch (e) { return; }
    const i = state.info;
    const fill = $('cloud-usage-fill');
    if (i.quota) {
      const pct = Math.min(100, i.own / i.quota * 100);
      fill.style.width = `${pct}%`;
      fill.classList.toggle('warn', pct > 90);
      $('cloud-usage-text').textContent = `${fmtBytes(i.own)} ${t('von')} ${fmtBytes(i.quota)} ${t('belegt')}`;
    } else {
      const total = i.own + i.free;
      fill.style.width = `${total ? Math.min(100, i.own / total * 100) : 0}%`;
      $('cloud-usage-text').textContent = `${fmtBytes(i.own)} ${t('belegt')} · ${fmtBytes(i.free)} ${t('frei')}`;
    }
    const users = i.users || [];
    $('cloud-users').hidden = !users.length;
    const list = $('cloud-users-list');
    list.innerHTML = '';
    users.forEach(u => {
      const b = button('', state.view === `user:${u.name}` ? 'active' : '', () => go(`user:${u.name}`, ''));
      b.dataset.view = `user:${u.name}`;
      b.innerHTML = `<span class="cloud-ico">${svg('user')}</span>`;
      b.appendChild(raw('span', 'cloud-user-name', u.name));
      b.appendChild(el('small', 'field-hint', fmtBytes(u.size)));
      list.appendChild(b);
    });
  }

  // ---------------------------------------------------------------------------
  // Navigation
  // ---------------------------------------------------------------------------
  function go(view, path = '') {
    state.view = view;
    state.path = path;
    state.selected.clear();
    if (view !== 'search') { state.query = ''; $('cloud-q').value = ''; }
    setHash();
    document.querySelectorAll('#cloud-nav button, #cloud-users-list button').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    load();
  }

  $('cloud-nav').addEventListener('click', e => {
    const b = e.target.closest('button[data-view]');
    if (b) go(b.dataset.view, '');
  });

  function crumbs() {
    const nav = $('cloud-crumbs');
    nav.innerHTML = '';
    const titles = { recent: 'Zuletzt geändert', favorites: 'Favoriten', shares: 'Freigaben', uploads: 'Andere Uploads', trash: 'Papierkorb' };
    if (state.view === 'search') {
      nav.appendChild(raw('span', 'cloud-crumb current', `${t('Suche')}: „${state.query}“`));
      return;
    }
    if (!isSpace(state.view)) {
      nav.appendChild(el('span', 'cloud-crumb current', t(titles[state.view] || '')));
      return;
    }
    const root = button(spaceLabel(state.view), 'cloud-crumb', () => go(state.view, ''));
    root.setAttribute('data-no-i18n', '');
    nav.appendChild(root);
    let acc = '';
    state.path.split('/').filter(Boolean).forEach(part => {
      acc = acc ? `${acc}/${part}` : part;
      const target = acc;
      nav.appendChild(el('span', 'cloud-crumb-sep', '/'));
      const b = button(part, 'cloud-crumb', () => go(state.view, target));
      b.setAttribute('data-no-i18n', '');
      nav.appendChild(b);
    });
    nav.lastChild.classList.add('current');
  }

  // ---------------------------------------------------------------------------
  // Loading and rendering
  // ---------------------------------------------------------------------------
  let loadSeq = 0;

  async function load() {
    const seq = ++loadSeq;
    crumbs();
    const editable = isSpace(state.view);
    $('cloud-actions').hidden = !editable;
    document.querySelector('.cloud-view-toggle').hidden = ['shares', 'uploads', 'trash'].includes(state.view);
    $('cloud-info').hidden = true;
    const box = $('cloud-content');
    box.className = `cloud-content ${state.layout}`;
    try {
      let d;
      if (isSpace(state.view)) {
        d = await api(`/api/cloud/list?${q({ space: state.view, path: state.path })}`);
        if (seq !== loadSeq) return;
        state.entries = d.entries.map(x => ({ ...x, space: state.view }));
        if (d.truncated) info(t('Nur die ersten 5000 Einträge werden angezeigt.'));
        renderEntries(state.entries, t(state.path ? 'Dieser Ordner ist leer.' : 'Noch keine Dateien – hierher ziehen oder „Hochladen“ wählen.'));
      } else if (state.view === 'recent') {
        d = await api('/api/cloud/recent');
        if (seq !== loadSeq) return;
        state.entries = d.items;
        renderEntries(state.entries, t('Noch keine Dateien.'), true);
      } else if (state.view === 'favorites') {
        d = await api('/api/cloud/favorites');
        if (seq !== loadSeq) return;
        state.entries = d.items;
        renderEntries(state.entries, t('Noch keine Favoriten – über das Menü einer Datei mit dem Stern markieren.'), true);
      } else if (state.view === 'search') {
        d = await api(`/api/cloud/search?${q({ q: state.query })}`);
        if (seq !== loadSeq) return;
        state.entries = d.results;
        if (d.truncated) info(t('Nur die ersten 300 Treffer werden angezeigt.'));
        renderEntries(state.entries, t('Nichts gefunden.'), true);
      } else if (state.view === 'shares') {
        d = await api('/api/cloud/shares');
        if (seq !== loadSeq) return;
        renderShares(d.shares);
      } else if (state.view === 'uploads') {
        d = await api('/api/cloud/uploads');
        if (seq !== loadSeq) return;
        renderUploads(d);
      } else if (state.view === 'trash') {
        d = await api('/api/cloud/trash');
        if (seq !== loadSeq) return;
        renderTrash(d);
      }
    } catch (err) {
      if (seq !== loadSeq) return;
      box.innerHTML = '';
      box.appendChild(el('p', 'field-hint cloud-empty', err.message));
    }
    updateSelbar();
  }

  function info(text) {
    $('cloud-info').textContent = text;
    $('cloud-info').hidden = false;
  }

  function thumb(x) {
    const wrap = el('div', 'cloud-thumb');
    if (x.kind === 'image' && x.size < 25 * 1024 * 1024) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = '';
      img.src = `${fileUrl(x.space, x.path, true)}&v=${x.mtime}`;
      img.addEventListener('error', () => { wrap.innerHTML = svg('image'); });
      wrap.appendChild(img);
    } else {
      wrap.innerHTML = svg(KIND_ICON[x.kind] || 'file');
      wrap.classList.add(`kind-${x.kind}`);
    }
    return wrap;
  }

  function renderEntries(entries, emptyText, showSpace) {
    const box = $('cloud-content');
    box.innerHTML = '';
    if (!entries.length) {
      box.appendChild(el('p', 'field-hint cloud-empty', emptyText));
      return;
    }
    if (state.layout === 'list') {
      const head = el('div', 'cloud-row cloud-row-head');
      head.append(el('span', ''), el('span', '', t('Name')), el('span', '', t(showSpace ? 'Ort' : 'Größe')), el('span', '', t('Geändert')), el('span', ''));
      box.appendChild(head);
    }
    entries.forEach((x, idx) => {
      const id = `${x.space}\u0000${x.path}`;
      const item = el('div', state.layout === 'list' ? 'cloud-row' : 'cloud-tile');
      item.tabIndex = 0;
      item.dataset.idx = idx;
      item.classList.toggle('selected', state.selected.has(id));
      if (x.type === 'dir') item.classList.add('is-dir');
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.className = 'cloud-check';
      check.checked = state.selected.has(id);
      check.setAttribute('aria-label', t('Auswählen'));
      check.addEventListener('click', e => { e.stopPropagation(); toggleSelect(id, check.checked, item); });
      const name = raw('span', 'cloud-name', x.name);
      name.title = x.name;
      const sub = showSpace
        ? `${spaceLabel(x.space)}${x.path.includes('/') ? ` / ${x.path.split('/').slice(0, -1).join('/')}` : ''}`
        : (x.type === 'dir' ? '' : fmtBytes(x.size));
      const more = button('', 'mini-icon-btn cloud-more', e => { e.stopPropagation(); openMenu(x, more); });
      more.innerHTML = svg('more');
      more.title = t('Aktionen');
      more.setAttribute('aria-label', t('Aktionen'));
      if (state.layout === 'list') {
        const icon = el('span', 'cloud-row-icon');
        icon.appendChild(thumb(x));
        const nameCell = el('span', 'cloud-row-name');
        nameCell.append(check, icon, name);
        if (x.fav) nameCell.appendChild(el('span', 'cloud-fav', '★'));
        item.append(nameCell, raw('span', 'field-hint', sub), el('span', 'field-hint', fmtTs(x.mtime)), more);
      } else {
        item.append(check, thumb(x));
        const label = el('div', 'cloud-tile-label');
        label.appendChild(name);
        label.appendChild(raw('small', 'field-hint', sub || fmtTs(x.mtime)));
        item.appendChild(label);
        if (x.fav) item.appendChild(el('span', 'cloud-fav', '★'));
        item.appendChild(more);
      }
      item.addEventListener('click', e => {
        if (e.ctrlKey || e.metaKey || e.shiftKey) { toggleSelect(id, !state.selected.has(id), item); return; }
        open(x, idx);
      });
      item.addEventListener('keydown', e => {
        if (e.key === 'Enter') open(x, idx);
        if (e.key === ' ') { e.preventDefault(); toggleSelect(id, !state.selected.has(id), item); }
        if (e.key === 'Delete') remove([x]);
      });
      item.addEventListener('contextmenu', e => { e.preventDefault(); openMenu(x, null, e.clientX, e.clientY); });
      if (isSpace(state.view)) {
        item.draggable = true;
        item.addEventListener('dragstart', e => {
          if (!state.selected.has(id)) { state.selected.clear(); state.selected.add(id); }
          e.dataTransfer.setData('application/x-cloud', JSON.stringify(selectedEntries().map(s => s.path)));
          e.dataTransfer.effectAllowed = 'move';
        });
        if (x.type === 'dir') {
          item.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('application/x-cloud')) { e.preventDefault(); item.classList.add('drop-target'); } });
          item.addEventListener('dragleave', () => item.classList.remove('drop-target'));
          item.addEventListener('drop', async e => {
            item.classList.remove('drop-target');
            const data = e.dataTransfer.getData('application/x-cloud');
            if (!data) return;
            e.preventDefault();
            e.stopPropagation();
            const paths = JSON.parse(data).filter(p => p !== x.path);
            if (!paths.length) return;
            try { await api('/api/cloud/transfer', { space: state.view, paths, target_space: state.view, target: x.path, mode: 'move' }); toast(t('Verschoben.'), 'success'); } catch (err) { toast(err.message, 'error'); }
            state.selected.clear();
            load();
          });
        }
      }
      box.appendChild(item);
    });
  }

  function open(x, idx) {
    if (x.type === 'dir') { go(x.space, x.path); return; }
    if (PREVIEWABLE.has(x.kind)) preview(idx);
    else location.href = fileUrl(x.space, x.path);
  }

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------
  function toggleSelect(id, on, item) {
    if (on) state.selected.add(id); else state.selected.delete(id);
    if (item) {
      item.classList.toggle('selected', on);
      const c = item.querySelector('.cloud-check');
      if (c) c.checked = on;
    }
    updateSelbar();
  }
  function selectedEntries() {
    return state.entries.filter(x => state.selected.has(`${x.space}\u0000${x.path}`));
  }
  function updateSelbar() {
    const n = state.selected.size;
    $('cloud-selbar').hidden = !n;
    $('cloud-sel-count').textContent = `${n} ${t(n === 1 ? 'ausgewählt' : 'ausgewählt')}`;
    const sameSpace = new Set(selectedEntries().map(x => x.space)).size <= 1;
    ['cloud-sel-zip', 'cloud-sel-move', 'cloud-sel-copy', 'cloud-sel-delete'].forEach(id => { $(id).disabled = !sameSpace; });
  }
  $('cloud-sel-clear').addEventListener('click', () => { state.selected.clear(); load(); });
  $('cloud-sel-zip').addEventListener('click', () => zip(selectedEntries()));
  $('cloud-sel-move').addEventListener('click', () => moveDialog(selectedEntries(), 'move'));
  $('cloud-sel-copy').addEventListener('click', () => moveDialog(selectedEntries(), 'copy'));
  $('cloud-sel-delete').addEventListener('click', () => remove(selectedEntries()));
  document.addEventListener('keydown', e => {
    if (e.target.closest('input, textarea, select') || document.querySelector('.modal-backdrop.open')) return;
    if ((e.ctrlKey || e.metaKey) && e.key === 'a' && state.entries.length) {
      e.preventDefault();
      state.entries.forEach(x => state.selected.add(`${x.space}\u0000${x.path}`));
      load();
    }
    if (e.key === 'Escape' && state.selected.size) { state.selected.clear(); load(); }
  });

  // ---------------------------------------------------------------------------
  // Context menu
  // ---------------------------------------------------------------------------
  const menu = $('cloud-menu');
  function closeMenu() { menu.hidden = true; }
  document.addEventListener('click', e => { if (!e.target.closest('#cloud-menu')) closeMenu(); });
  window.addEventListener('scroll', closeMenu, true);

  function openMenu(x, anchor, cx, cy) {
    menu.innerHTML = '';
    const add = (label, fn, cls = '') => {
      const b = button(t(label), `cloud-menu-item ${cls}`, () => { closeMenu(); fn(); });
      b.setAttribute('role', 'menuitem');
      menu.appendChild(b);
    };
    if (x.type === 'dir') add('Öffnen', () => go(x.space, x.path));
    else if (PREVIEWABLE.has(x.kind)) add('Vorschau', () => preview(state.entries.indexOf(x)));
    if (x.type === 'dir') add('Als ZIP herunterladen', () => zip([x]));
    else add('Herunterladen', () => { location.href = fileUrl(x.space, x.path); });
    add('Teilen', () => shareDialog(x));
    add(x.fav ? 'Aus Favoriten entfernen' : 'Zu Favoriten', () => favorite(x, !x.fav));
    if (!isSpace(state.view)) add('Im Ordner zeigen', () => go(x.space, x.path.split('/').slice(0, -1).join('/')));
    add('Umbenennen', () => renameEntry(x));
    add('Verschieben', () => moveDialog([x], 'move'));
    add('Kopieren', () => moveDialog([x], 'copy'));
    add('Löschen', () => remove([x]), 'danger');
    menu.hidden = false;
    const r = anchor ? anchor.getBoundingClientRect() : { left: cx, bottom: cy, right: cx };
    const w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, (anchor ? r.right - w : r.left)))}px`;
    menu.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, r.bottom + 4))}px`;
    const first = menu.querySelector('button');
    if (first) first.focus();
  }
  menu.addEventListener('keydown', e => {
    const items = [...menu.querySelectorAll('button')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    if (e.key === 'Escape') closeMenu();
  });

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------
  async function favorite(x, on) {
    try { await api('/api/cloud/favorite', { space: x.space, path: x.path, on }); x.fav = on; load(); } catch (err) { toast(err.message, 'error'); }
  }

  async function renameEntry(x) {
    const name = prompt(t('Neuer Name:'), x.name);
    if (!name || name.trim() === x.name) return;
    try { await api('/api/cloud/rename', { space: x.space, path: x.path, name: name.trim() }); load(); } catch (err) { toast(err.message, 'error'); }
  }

  async function remove(list) {
    if (!list.length) return;
    const msg = list.length === 1 ? `„${list[0].name}“ ${t('in den Papierkorb verschieben?')}` : `${list.length} ${t('Einträge in den Papierkorb verschieben?')}`;
    if (!confirm(msg)) return;
    try {
      await api('/api/cloud/delete', { space: list[0].space, paths: list.map(x => x.path) });
      toast(t('In den Papierkorb verschoben.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
    state.selected.clear();
    load();
    loadInfo();
  }

  async function zip(list) {
    if (!list.length) return;
    if (list.length === 1 && list[0].type === 'file') { location.href = fileUrl(list[0].space, list[0].path); return; }
    try {
      const r = await api('/api/cloud/zip', { space: list[0].space, paths: list.map(x => x.path) });
      location.href = `/api/cloud/zip/${r.ticket}`;
    } catch (err) { toast(err.message, 'error'); }
  }

  $('cloud-new-folder').addEventListener('click', async () => {
    const name = prompt(t('Name des neuen Ordners:'));
    if (!name || !name.trim()) return;
    try { await api('/api/cloud/folder', { space: state.view, path: state.path, name: name.trim() }); load(); } catch (err) { toast(err.message, 'error'); }
  });

  $('cloud-view-grid').addEventListener('click', () => { state.layout = 'grid'; localStorage.setItem('cloud-layout', 'grid'); load(); });
  $('cloud-view-list').addEventListener('click', () => { state.layout = 'list'; localStorage.setItem('cloud-layout', 'list'); load(); });

  $('cloud-search').addEventListener('submit', e => {
    e.preventDefault();
    const v = $('cloud-q').value.trim();
    if (v.length < 2) { toast(t('Mindestens 2 Zeichen eingeben.'), 'error'); return; }
    state.query = v;
    go('search');
  });
  $('cloud-q').addEventListener('search', () => { if (!$('cloud-q').value && state.view === 'search') go('me'); });

  // ---------------------------------------------------------------------------
  // Dialogs (share, move, settings)
  // ---------------------------------------------------------------------------
  function openModal(id) { $(id).classList.add('open'); }
  function closeModal(id) { $(id).classList.remove('open'); }
  document.querySelectorAll('.modal-backdrop').forEach(m => {
    m.addEventListener('click', e => { if (e.target === m || e.target.closest('[data-close]')) m.classList.remove('open'); });
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const m = document.querySelector('.modal-backdrop.open');
    if (m) m.classList.remove('open');
  });

  let shareTarget = null;
  function shareDialog(x) {
    shareTarget = x;
    $('cloud-share-what').textContent = `${spaceLabel(x.space)} / ${x.path}`;
    $('cloud-share-result').hidden = true;
    $('cloud-share-pw-note').hidden = true;
    $('cloud-share-max').value = '0';
    $('cloud-share-pw-on').checked = false;
    $('cloud-share-pw').value = '';
    $('cloud-share-pw-box').hidden = true;
    const m = (state.info && state.info.mail) || {};
    const ready = m.smtp && m.email;
    $('cloud-share-notify').checked = false;
    $('cloud-share-notify').disabled = !ready;
    $('cloud-share-notify-hint').hidden = false;
    $('cloud-share-notify-hint').textContent = ready ? `${t('Geht an')} ${m.email}`
      : !m.smtp ? t('Dafür muss ein Administrator unter Einstellungen → E-Mail einen Mailserver eintragen.')
        : t('Dafür unter Einstellungen → Konto eine E-Mail-Adresse hinterlegen.');
    openModal('cloud-share');
  }
  function randomPassword() {
    const chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const buf = new Uint32Array(12);
    crypto.getRandomValues(buf);
    return [...buf].map(n => chars[n % chars.length]).join('');
  }
  $('cloud-share-pw-on').addEventListener('change', () => {
    const on = $('cloud-share-pw-on').checked;
    $('cloud-share-pw-box').hidden = !on;
    if (on && !$('cloud-share-pw').value) $('cloud-share-pw').value = randomPassword();
    if (on) $('cloud-share-pw').select();
  });
  $('cloud-share-pw-gen').addEventListener('click', () => { $('cloud-share-pw').value = randomPassword(); $('cloud-share-pw').select(); });
  $('cloud-share-create').addEventListener('click', async () => {
    if (!shareTarget) return;
    const password = $('cloud-share-pw-on').checked ? $('cloud-share-pw').value : '';
    if ($('cloud-share-pw-on').checked && password.length < 4) { toast(t('Passwort: mindestens 4 Zeichen.'), 'error'); return; }
    try {
      const r = await api('/api/cloud/shares', { space: shareTarget.space, path: shareTarget.path, hours: Number($('cloud-share-hours').value),
        max_downloads: Number($('cloud-share-max').value), password, notify: $('cloud-share-notify').checked });
      $('cloud-share-url').value = `${location.origin}/s/c/${r.token}`;
      $('cloud-share-result').hidden = false;
      $('cloud-share-pw-note').hidden = !password;
      if (password) $('cloud-share-pw-note').textContent = `${t('Passwort')}: ${password} – ${t('am besten auf anderem Weg mitteilen als den Link.')}`;
      $('cloud-share-url').select();
    } catch (err) { toast(err.message, 'error'); }
  });
  $('cloud-share-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('cloud-share-url').value); toast(t('Link kopiert.'), 'success'); } catch (e) { $('cloud-share-url').select(); }
  });

  const move = { list: [], mode: 'move', space: 'me', path: '' };
  function moveDialog(list, mode) {
    if (!list.length) return;
    move.list = list;
    move.mode = mode;
    move.space = isSpace(state.view) ? state.view : list[0].space;
    move.path = isSpace(state.view) ? state.path : '';
    $('cloud-move-title').textContent = t(mode === 'copy' ? 'Kopieren nach' : 'Verschieben nach');
    $('cloud-move-go').textContent = t(mode === 'copy' ? 'Hierher kopieren' : 'Hierher verschieben');
    const sel = $('cloud-move-space');
    sel.innerHTML = '';
    const spaces = ['me', 'shared', ...((state.info && state.info.users) || []).map(u => `user:${u.name}`)];
    spaces.forEach(s => { const o = el('option', '', spaceLabel(s)); o.value = s; o.setAttribute('data-no-i18n', ''); sel.appendChild(o); });
    sel.value = move.space;
    openModal('cloud-move');
    loadMoveList();
  }
  $('cloud-move-space').addEventListener('change', () => { move.space = $('cloud-move-space').value; move.path = ''; loadMoveList(); });
  async function loadMoveList() {
    const crumbsBox = $('cloud-move-crumbs');
    crumbsBox.innerHTML = '';
    const root = button(spaceLabel(move.space), 'cloud-crumb', () => { move.path = ''; loadMoveList(); });
    root.setAttribute('data-no-i18n', '');
    crumbsBox.appendChild(root);
    let acc = '';
    move.path.split('/').filter(Boolean).forEach(part => {
      acc = acc ? `${acc}/${part}` : part;
      const target = acc;
      crumbsBox.appendChild(el('span', 'cloud-crumb-sep', '/'));
      const b = button(part, 'cloud-crumb', () => { move.path = target; loadMoveList(); });
      b.setAttribute('data-no-i18n', '');
      crumbsBox.appendChild(b);
    });
    const list = $('cloud-move-list');
    list.innerHTML = '';
    try {
      const d = await api(`/api/cloud/list?${q({ space: move.space, path: move.path })}`);
      const moving = new Set(move.list.filter(x => x.space === move.space).map(x => x.path));
      const dirs = d.entries.filter(x => x.type === 'dir' && !moving.has(x.path));
      if (!dirs.length) list.appendChild(el('p', 'field-hint', t('Keine Unterordner.')));
      dirs.forEach(x => {
        const b = button('', 'cloud-move-item', () => { move.path = x.path; loadMoveList(); });
        b.innerHTML = svg('folder');
        b.appendChild(raw('span', '', x.name));
        list.appendChild(b);
      });
    } catch (err) { list.appendChild(el('p', 'field-hint', err.message)); }
  }
  $('cloud-move-go').addEventListener('click', async () => {
    try {
      const r = await api('/api/cloud/transfer', { space: move.list[0].space, paths: move.list.map(x => x.path), target_space: move.space, target: move.path, mode: move.mode });
      toast(`${r.count} ${t(move.mode === 'copy' ? 'kopiert.' : 'verschoben.')}`, 'success');
      closeModal('cloud-move');
      state.selected.clear();
      load();
      loadInfo();
    } catch (err) { toast(err.message, 'error'); }
  });

  $('cloud-settings-open').addEventListener('click', () => {
    const i = state.info;
    if (!i || !i.config) return;
    $('cloud-set-root').value = i.config.root || '';
    $('cloud-set-root').placeholder = i.default_root;
    $('cloud-set-root-hint').textContent = `${t('Leer = Standard')}: ${i.default_root}`;
    $('cloud-set-quota').value = i.config.quota_mb;
    $('cloud-set-trash').value = i.config.trash_days;
    openModal('cloud-settings');
  });
  $('cloud-settings-form').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('/api/settings/cloud', { root: $('cloud-set-root').value.trim(), quota_mb: Number($('cloud-set-quota').value || 0), trash_days: Number($('cloud-set-trash').value || 30) });
      toast(t('Gespeichert.'), 'success');
      closeModal('cloud-settings');
      loadInfo();
      load();
    } catch (err) { toast(err.message, 'error'); }
  });

  // ---------------------------------------------------------------------------
  // Preview
  // ---------------------------------------------------------------------------
  let previewIdx = -1;
  function previewList() { return state.entries.filter(x => x.type === 'file' && PREVIEWABLE.has(x.kind)); }

  async function preview(idx) {
    const x = state.entries[idx];
    if (!x) return;
    previewIdx = idx;
    const body = $('cloud-preview-body');
    body.innerHTML = '';
    $('cloud-preview-name').textContent = x.name;
    $('cloud-preview-sub').textContent = `${spaceLabel(x.space)} · ${fmtBytes(x.size)} · ${fmtTs(x.mtime)}`;
    $('cloud-preview-download').href = fileUrl(x.space, x.path);
    const list = previewList();
    const pos = list.indexOf(x);
    $('cloud-preview-prev').disabled = pos <= 0;
    $('cloud-preview-next').disabled = pos < 0 || pos >= list.length - 1;
    openModal('cloud-preview');
    const src = fileUrl(x.space, x.path, true);
    if (x.kind === 'image') {
      const img = document.createElement('img');
      img.src = src;
      img.alt = x.name;
      body.appendChild(img);
    } else if (x.kind === 'video' || x.kind === 'audio') {
      const m = document.createElement(x.kind);
      m.src = src;
      m.controls = true;
      m.autoplay = true;
      body.appendChild(m);
    } else if (x.kind === 'pdf') {
      const f = document.createElement('iframe');
      f.src = src;
      f.title = x.name;
      body.appendChild(f);
      const a = el('a', 'text-btn', t('In neuem Tab öffnen'));
      a.href = src;
      a.target = '_blank';
      a.rel = 'noopener';
      body.appendChild(a);
    } else if (x.kind === 'text') {
      try {
        const d = await api(`/api/cloud/text?${q({ space: x.space, path: x.path })}`);
        const pre = raw('pre', 'cloud-text', d.content);
        body.appendChild(pre);
        if (d.truncated) body.appendChild(el('p', 'field-hint', t('Nur der Anfang der Datei wird angezeigt.')));
      } catch (err) { body.appendChild(el('p', 'field-hint', err.message)); }
    }
  }
  function step(dir) {
    const list = previewList();
    const pos = list.indexOf(state.entries[previewIdx]);
    const next = list[pos + dir];
    if (next) preview(state.entries.indexOf(next));
  }
  function closePreview() {
    $('cloud-preview-body').innerHTML = '';   // stops video/audio
    closeModal('cloud-preview');
  }
  $('cloud-preview-prev').addEventListener('click', () => step(-1));
  $('cloud-preview-next').addEventListener('click', () => step(1));
  $('cloud-preview-close').addEventListener('click', closePreview);
  $('cloud-preview').addEventListener('click', e => { if (e.target === $('cloud-preview')) closePreview(); });
  $('cloud-preview-share').addEventListener('click', () => { const x = state.entries[previewIdx]; closePreview(); if (x) shareDialog(x); });
  document.addEventListener('keydown', e => {
    if (!$('cloud-preview').classList.contains('open')) return;
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
    if (e.key === 'Escape') closePreview();
  });

  // ---------------------------------------------------------------------------
  // Other views: share links, uploads elsewhere, recycle bin
  // ---------------------------------------------------------------------------
  function table(headers) {
    const tbl = el('table', 'data-table');
    const head = tbl.createTHead().insertRow();
    headers.forEach(h => head.appendChild(el('th', '', t(h))));
    return { tbl, body: tbl.createTBody() };
  }

  const fmtFull = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  function shortAgent(ua) {
    if (!ua) return '–';
    const browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome'
      : /Safari\//.test(ua) ? 'Safari' : /curl|wget|python|httpx|Go-http/i.test(ua) ? ua.split('/')[0] : '';
    const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '';
    return [browser, os].filter(Boolean).join(' · ') || ua.slice(0, 60);
  }

  function renderShares(shares) {
    const box = $('cloud-content');
    box.className = 'cloud-content plain';
    box.innerHTML = '';
    if (!shares.length) { box.appendChild(el('p', 'field-hint cloud-empty', t('Keine aktiven Freigabe-Links.'))); return; }
    const { tbl, body } = table(['Datei', 'Ort', 'Gültig bis', 'Downloads', 'Schutz', '']);
    shares.forEach(s => {
      const row = body.insertRow();
      row.insertCell().appendChild(raw('span', '', `${s.type === 'dir' ? '📁 ' : ''}${s.name}`));
      const where = row.insertCell();
      where.appendChild(raw('span', 'field-hint', `${spaceLabel(s.space)} / ${s.path}`));
      if (s.user && state.info && s.user !== state.info.user) where.appendChild(raw('div', 'field-hint', `${t('von')} ${s.user}`));
      row.insertCell().textContent = new Date(s.expires * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      const dl = row.insertCell();
      dl.textContent = s.max_downloads ? `${s.downloads || 0} / ${s.max_downloads}` : `${s.downloads || 0}`;
      if (s.max_downloads && s.downloads >= s.max_downloads) dl.appendChild(el('div', 'field-hint', t('aufgebraucht')));
      const prot = row.insertCell();
      if (s.password) prot.appendChild(el('span', 'health-tag', t('Passwort')));
      if (s.notify) prot.appendChild(el('span', 'health-tag', t('Mail')));
      if (!s.password && !s.notify) prot.appendChild(el('span', 'field-hint', '–'));
      const act = row.insertCell();
      act.className = 'row-action';
      const logRow = body.insertRow();
      logRow.className = 'cloud-log-row';
      logRow.hidden = true;
      const cell = logRow.insertCell();
      cell.colSpan = 6;
      const fails = (s.log || []).filter(x => !x.ok).length;
      const logBtn = button(`${t('Protokoll')} (${(s.log || []).length})`, 'text-btn', () => { logRow.hidden = !logRow.hidden; });
      if (fails) logBtn.title = `${fails} ${t('falsche Passwort-Eingaben')}`;
      act.appendChild(logBtn);
      if (!(s.log || []).length) cell.appendChild(el('p', 'field-hint', t('Noch keine Abrufe.')));
      else {
        const list = el('div', 'cloud-log');
        s.log.forEach(x => {
          const line = el('div', `cloud-log-line${x.ok ? '' : ' fail'}`);
          line.appendChild(el('span', 'cloud-log-ts', fmtFull(x.ts)));
          line.appendChild(el('span', '', t(x.ok ? 'Heruntergeladen' : 'Falsches Passwort')));
          line.appendChild(raw('span', 'cloud-log-ip', x.ip || ''));
          const ag = raw('span', 'field-hint', shortAgent(x.agent));
          ag.title = x.agent || '';
          line.appendChild(ag);
          list.appendChild(line);
        });
        cell.appendChild(list);
      }
      act.appendChild(button(t('Link kopieren'), 'btn-secondary', async () => {
        const url = `${location.origin}/s/c/${s.token}`;
        try { await navigator.clipboard.writeText(url); toast(t('Link kopiert.'), 'success'); } catch (e) { prompt(t('Link:'), url); }
      }));
      act.appendChild(button(t('Beenden'), 'text-btn', async () => {
        if (!confirm(t('Diesen Link beenden? Er funktioniert danach nicht mehr.'))) return;
        try { await api(`/api/cloud/shares/${s.token}/delete`, {}); load(); } catch (err) { toast(err.message, 'error'); }
      }));
    });
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(tbl);
    box.appendChild(wrap);
  }

  function renderUploads(d) {
    const box = $('cloud-content');
    box.className = 'cloud-content plain';
    box.innerHTML = '';
    // Exchange links (public upload folders)
    const ex = el('section', 'cloud-section');
    const exHead = el('div', 'section-head');
    exHead.appendChild(el('h3', 'subhead', t('Briefkasten (Austausch-Links)')));
    if (can('files.share')) {
      const a = el('a', 'text-btn', t('Links verwalten →'));
      a.href = '/settings#freigaben';
      exHead.appendChild(a);
    }
    ex.appendChild(exHead);
    if (d.exchange === null) {
      ex.appendChild(el('p', 'field-hint', t('Nur mit dem Recht „Dateien teilen“ sichtbar.')));
    } else {
      const ups = [];
      d.exchange.forEach(l => (l.uploads || []).forEach(u => ups.push({ ...u, link: l })));
      ups.sort((a, b) => b.ts - a.ts);
      if (!ups.length) ex.appendChild(el('p', 'field-hint', t(d.exchange.length ? 'Über die aktiven Links wurde noch nichts hochgeladen.' : 'Keine aktiven Austausch-Links.')));
      else {
        const { tbl, body } = table(['Datei', 'Ordner', 'Hochgeladen', 'Größe', 'Von', '']);
        ups.forEach(u => {
          const row = body.insertRow();
          row.insertCell().appendChild(raw('span', '', u.name));
          const where = row.insertCell();
          where.appendChild(raw('span', 'field-hint', u.link.folder));
          if (u.link.note) where.appendChild(raw('div', 'field-hint', u.link.note));
          row.insertCell().textContent = fmtTs(u.ts);
          row.insertCell().textContent = fmtBytes(u.size);
          row.insertCell().appendChild(raw('span', 'field-hint', u.ip || ''));
          const act = row.insertCell();
          act.className = 'row-action';
          if (!u.exists) act.appendChild(el('span', 'field-hint', t('nicht mehr vorhanden')));
          else if (can('files.read')) {
            const a = el('a', 'btn-secondary', t('Herunterladen'));
            a.href = `/api/files/download?${q({ path: u.path })}`;
            act.appendChild(a);
          }
        });
        const wrap = el('div', 'table-scroll');
        wrap.appendChild(tbl);
        ex.appendChild(wrap);
      }
    }
    box.appendChild(ex);

    // Documentation attachments
    const at = el('section', 'cloud-section');
    const atHead = el('div', 'section-head');
    atHead.appendChild(el('h3', 'subhead', t('Anhänge der Dokumentation')));
    const docs = el('a', 'text-btn', t('Zur Dokumentation →'));
    docs.href = '/doku';
    atHead.appendChild(docs);
    at.appendChild(atHead);
    const KIND = { inventory: 'Inventar', contracts: 'Verträge', servicedocs: 'Steckbriefe', wiki: 'Wissen' };
    const items = d.attachments || [];
    if (!items.length) at.appendChild(el('p', 'field-hint', t('Noch keine Anhänge.')));
    else {
      const { tbl, body } = table(['Datei', 'Bereich', 'Hochgeladen', 'Größe', '']);
      items.forEach(a => {
        const row = body.insertRow();
        row.insertCell().appendChild(raw('span', '', a.name));
        const where = row.insertCell();
        where.appendChild(el('span', '', t(KIND[a.kind] || a.kind)));
        where.appendChild(raw('div', 'field-hint', a.item));
        row.insertCell().textContent = fmtTs(a.mtime);
        row.insertCell().textContent = fmtBytes(a.size);
        const act = row.insertCell();
        act.className = 'row-action';
        const base = `/api/docs/attachments/${encodeURIComponent(a.kind)}/${encodeURIComponent(a.item)}/file?${q({ name: a.name })}`;
        if (a.inline) {
          const v = el('a', 'text-btn', t('Ansehen'));
          v.href = base;
          v.target = '_blank';
          v.rel = 'noopener';
          act.appendChild(v);
        }
        const dl = el('a', 'btn-secondary', t('Herunterladen'));
        dl.href = `${base}&download=1`;
        act.appendChild(dl);
      });
      const wrap = el('div', 'table-scroll');
      wrap.appendChild(tbl);
      at.appendChild(wrap);
    }
    box.appendChild(at);
  }

  function renderTrash(d) {
    const box = $('cloud-content');
    box.className = 'cloud-content plain';
    box.innerHTML = '';
    const head = el('div', 'section-head');
    head.appendChild(el('p', 'field-hint', `${t('Gelöschte Dateien bleiben')} ${d.days} ${t('Tage im Papierkorb und werden dann endgültig entfernt.')}${d.items.length ? ` ${fmtBytes(d.size)}.` : ''}`));
    if (d.items.length) head.appendChild(button(t('Papierkorb leeren'), 'btn-secondary', async () => {
      if (!confirm(t('Alles im Papierkorb endgültig löschen?'))) return;
      try { await api('/api/cloud/trash/empty', {}); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    box.appendChild(head);
    if (!d.items.length) { box.appendChild(el('p', 'field-hint cloud-empty', t('Der Papierkorb ist leer.'))); return; }
    const { tbl, body } = table(['Name', 'Ursprünglich', 'Gelöscht', 'Größe', '']);
    d.items.forEach(it => {
      const row = body.insertRow();
      row.insertCell().appendChild(raw('span', '', `${it.type === 'dir' ? '📁 ' : ''}${it.name}`));
      row.insertCell().appendChild(raw('span', 'field-hint', `${spaceLabel(it.space)} / ${it.path}`));
      const del = row.insertCell();
      del.textContent = fmtTs(it.deleted);
      if (it.by) del.appendChild(raw('div', 'field-hint', it.by));
      row.insertCell().textContent = fmtBytes(it.size);
      const act = row.insertCell();
      act.className = 'row-action';
      act.appendChild(button(t('Wiederherstellen'), 'btn-secondary', async () => {
        try { await api('/api/cloud/trash/restore', { space: it.space, id: it.id }); toast(t('Wiederhergestellt.'), 'success'); load(); loadInfo(); } catch (err) { toast(err.message, 'error'); }
      }));
      act.appendChild(button(t('Endgültig löschen'), 'text-btn', async () => {
        if (!confirm(`„${it.name}“ ${t('endgültig löschen?')}`)) return;
        try { await api('/api/cloud/trash/delete', { space: it.space, id: it.id }); load(); } catch (err) { toast(err.message, 'error'); }
      }));
    });
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(tbl);
    box.appendChild(wrap);
  }

  // ---------------------------------------------------------------------------
  // Upload: buttons, drag & drop of files and whole folders, progress panel
  // ---------------------------------------------------------------------------
  const queue = [];
  let running = 0;
  const MAX_PARALLEL = 3;

  function enqueue(file, sub) {
    if (!isSpace(state.view)) return;
    const job = { file, sub: sub || '', space: state.view, path: state.path, row: null, xhr: null, done: false };
    const row = el('div', 'cloud-up');
    const name = raw('span', 'cloud-up-name', `${job.sub ? `${job.sub}/` : ''}${file.name}`);
    const bar = el('div', 'cloud-up-bar');
    bar.appendChild(el('span', ''));
    const status = el('span', 'field-hint cloud-up-status', t('wartet'));
    const cancel = button('✕', 'mini-icon-btn', () => { if (job.xhr) job.xhr.abort(); else { job.done = true; status.textContent = t('abgebrochen'); } });
    cancel.title = t('Abbrechen');
    row.append(name, bar, status, cancel);
    job.row = row;
    job.status = status;
    job.bar = bar.firstChild;
    job.cancel = cancel;
    $('cloud-uploads-list').appendChild(row);
    $('cloud-uploads').hidden = false;
    queue.push(job);
    pump();
  }

  function pump() {
    while (running < MAX_PARALLEL && queue.length) {
      const job = queue.shift();
      if (job.done) continue;
      running++;
      upload(job, false).finally(() => { running--; pump(); if (!running && !queue.length) { load(); loadInfo(); updateTitle(); } });
    }
    updateTitle();
  }

  function updateTitle() {
    const rows = $('cloud-uploads-list').children.length;
    const busy = running + queue.length;
    $('cloud-uploads-title').textContent = busy ? `${t('Hochladen')}: ${busy} ${t('offen')}` : `${rows} ${t('fertig')}`;
  }

  function upload(job, overwrite) {
    return new Promise(resolve => {
      const xhr = new XMLHttpRequest();
      job.xhr = xhr;
      const params = { space: job.space, path: job.path, name: job.file.name, overwrite: overwrite ? 1 : 0 };
      if (job.sub) params.sub = job.sub;
      xhr.open('PUT', `/api/cloud/upload?${q(params)}`);
      xhr.upload.onprogress = e => {
        if (!e.lengthComputable) return;
        const pct = Math.round(e.loaded / e.total * 100);
        job.bar.style.width = `${pct}%`;
        job.status.textContent = `${pct} %`;
      };
      xhr.onload = async () => {
        let d = {};
        try { d = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        if (xhr.status === 409 && !overwrite) {
          if (confirm(`„${job.file.name}“ ${t('gibt es dort schon. Überschreiben?')}`)) { resolve(await upload(job, true)); return; }
          job.status.textContent = t('übersprungen');
        } else if (xhr.status >= 200 && xhr.status < 300) {
          job.bar.style.width = '100%';
          job.status.textContent = fmtBytes(d.size);
          job.row.classList.add('ok');
        } else {
          job.status.textContent = d.detail ? t(d.detail) : `${t('Fehler')} ${xhr.status}`;
          job.row.classList.add('err');
        }
        job.done = true;
        job.cancel.hidden = true;
        resolve();
      };
      xhr.onerror = () => { job.status.textContent = t('Verbindung unterbrochen'); job.row.classList.add('err'); job.done = true; resolve(); };
      xhr.onabort = () => { job.status.textContent = t('abgebrochen'); job.done = true; job.cancel.hidden = true; resolve(); };
      job.status.textContent = '0 %';
      xhr.send(job.file);
    });
  }

  $('cloud-file-input').addEventListener('change', e => { [...e.target.files].forEach(f => enqueue(f)); e.target.value = ''; });
  $('cloud-dir-input').addEventListener('change', e => {
    [...e.target.files].forEach(f => {
      const parts = (f.webkitRelativePath || f.name).split('/');
      enqueue(f, parts.slice(0, -1).join('/'));
    });
    e.target.value = '';
  });
  $('cloud-uploads-close').addEventListener('click', () => {
    if (running || queue.length) { toast(t('Es laufen noch Uploads.'), 'error'); return; }
    $('cloud-uploads-list').innerHTML = '';
    $('cloud-uploads').hidden = true;
  });

  // Dropped folders: walk them with the entries API
  function walkEntry(entry, prefix) {
    return new Promise(resolve => {
      if (entry.isFile) {
        entry.file(f => { enqueue(f, prefix); resolve(); }, () => resolve());
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const sub = prefix ? `${prefix}/${entry.name}` : entry.name;
        const all = [];
        const read = () => reader.readEntries(async batch => {
          if (!batch.length) { for (const e of all) await walkEntry(e, sub); resolve(); return; }
          all.push(...batch);
          read();
        }, () => resolve());
        read();
      } else resolve();
    });
  }

  const main = $('cloud-main');
  let dragDepth = 0;
  const isFileDrag = e => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
  main.addEventListener('dragenter', e => { if (!isFileDrag(e) || !isSpace(state.view)) return; dragDepth++; $('cloud-drop').hidden = false; });
  main.addEventListener('dragleave', e => { if (!isFileDrag(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('cloud-drop').hidden = true; });
  main.addEventListener('dragover', e => { if (isFileDrag(e) && isSpace(state.view)) e.preventDefault(); });
  main.addEventListener('drop', async e => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $('cloud-drop').hidden = true;
    if (!isSpace(state.view)) return;
    const items = [...(e.dataTransfer.items || [])];
    const entries = items.map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (entries.length) { for (const en of entries) await walkEntry(en, ''); } else [...e.dataTransfer.files].forEach(f => enqueue(f));
  });
  window.addEventListener('beforeunload', e => { if (running || queue.length) { e.preventDefault(); e.returnValue = ''; } });

  // ---------------------------------------------------------------------------
  // Start
  // ---------------------------------------------------------------------------
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!can('cloud')) {
      $('cloud-content').appendChild(el('p', 'field-hint cloud-empty', t('Für die Dateien-Seite fehlt das Recht „Eigene Dateien (Cloud)“.')));
      return;
    }
    fromHash();
    if (state.view === 'search') state.view = 'me';
    await loadInfo();
    go(state.view, state.path);
  })();
})();
