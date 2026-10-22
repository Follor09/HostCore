/**
 * PiCore Dashboard - collapsible sidebar on every page
 *
 * One place for everything that used to be unlabeled icons in the header:
 * - Boards (the start page's boards; on the dashboard they switch in place)
 * - Pages with their names; the current page's sections (.settings-nav) are listed below it
 * - own links (Einstellungen -> Eigene Links)
 * - actions: the header's icon buttons are moved here (same elements, so their handlers keep working)
 * Expanded / collapsed (icons only) is remembered per browser; on phones it is a drawer.
 */
(function () {
  'use strict';

  const KEY = 'sidebar';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const svg = path => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  const ICON = {
    board: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    tools: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>',
    terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    wiki: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    fleet: '<rect x="2" y="3" width="8" height="7" rx="1"/><rect x="14" y="3" width="8" height="7" rx="1"/><rect x="8" y="14" width="8" height="7" rx="1"/><path d="M6 10v2h12v-2M12 12v2"/>',
    toolbox: '<rect x="2" y="7" width="20" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M2 13h20"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    collapse: '<path d="M11 17l-5-5 5-5"/><path d="M18 17l-5-5 5-5"/>',
    menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    chevron: '<path d="M9 18l6-6-6-6"/>'
  };
  const PAGES = [
    ['/system', 'tools', 'System-Werkzeuge', 'system.view'],
    ['/containers', 'box', 'Container', 'system.view'],
    ['/flotte', 'fleet', 'Flotte', 'system.view'],
    ['/doku', 'book', 'Dokumentation', ''],
    ['/wissen', 'wiki', 'Wissen', ''],
    ['/report', 'report', 'Wochenbericht', 'system.view'],
    ['/werkzeuge', 'toolbox', 'IT-Toolbox', ''],
    ['/terminal', 'terminal', 'Terminal', 'terminal'],
    ['/settings', 'settings', 'Einstellungen', '']
  ];
  const onDashboard = location.pathname === '/';

  const header = document.querySelector('.app-header');
  if (!header) return;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function item(tag, icon, label, opts = {}) {
    const a = el(tag, `side-item${opts.cls ? ` ${opts.cls}` : ''}`);
    if (tag === 'a') a.href = opts.href;
    else a.type = 'button';
    if (opts.perm) a.dataset.perm = opts.perm;
    a.title = label;
    const ic = el('span', 'side-icon');
    ic.innerHTML = icon;
    const text = el('span', 'side-label', label);
    if (opts.raw) text.setAttribute('data-no-i18n', '');
    a.append(ic, text);
    return a;
  }

  function group(title) {
    const g = el('div', 'side-group');
    if (title) g.appendChild(el('div', 'side-group-title', title));
    return g;
  }

  // ---------------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------------
  const side = el('aside', 'sidebar');
  side.id = 'sidebar';
  side.setAttribute('aria-label', 'Navigation');

  const head = el('div', 'sidebar-head');
  const brand = el('a', 'sidebar-brand');
  brand.href = '/';
  brand.title = 'PiCore';
  brand.innerHTML = '<span class="sidebar-logo">P</span><span class="side-label">PI<b>CORE</b></span>';
  brand.setAttribute('data-no-i18n', '');
  const toggle = el('button', 'sidebar-toggle');
  toggle.type = 'button';
  toggle.innerHTML = svg(ICON.collapse);
  head.append(brand, toggle);

  const scroll = el('nav', 'sidebar-scroll');
  const boardsGroup = group(t('Boards'));
  const pagesGroup = group(t('Seiten'));
  const linksGroup = group(t('Links'));
  const actionsGroup = group(t('Aktionen'));
  linksGroup.hidden = true;
  scroll.append(boardsGroup, pagesGroup, linksGroup, actionsGroup);
  side.append(head, scroll);

  // Pages, with the sections of the current page below it
  PAGES.forEach(([href, icon, label, perm]) => {
    const current = location.pathname === href || (href === '/doku' && location.pathname === '/docs');
    const a = item('a', svg(ICON[icon]), t(label), { href, perm, cls: current ? 'active' : '' });
    if (current) a.setAttribute('aria-current', 'page');
    pagesGroup.appendChild(a);
    const nav = current && document.querySelector('.settings-nav');
    if (!nav) return;
    const subs = el('div', 'side-subs');
    [...nav.querySelectorAll('a')].forEach(orig => {
      const sub = orig.cloneNode(true);
      sub.removeAttribute('id');
      const mirror = () => { sub.className = `side-sub ${orig.className}`.trim(); };
      mirror();
      new MutationObserver(mirror).observe(orig, { attributes: true, attributeFilter: ['class'] });
      // The page's own handlers (smooth scrolling, active state) stay on the original link
      sub.addEventListener('click', e => { e.preventDefault(); orig.click(); closeDrawer(); });
      subs.appendChild(sub);
    });
    pagesGroup.appendChild(subs);
    document.body.classList.add('sidebar-sections');
  });

  // Boards: switched in place on the dashboard, links to it elsewhere
  function renderBoards(list, activeId) {
    boardsGroup.querySelectorAll('.side-item').forEach(n => n.remove());
    (list || []).forEach((b, i) => {
      const href = i === 0 ? '/' : `/#board-${b.id}`;
      const a = item('a', svg(ICON.board), b.name, { href, raw: true, cls: onDashboard && b.id === activeId ? 'active' : '' });
      if (i < 9) a.title = `${b.name} (Alt+${i + 1})`;
      if (onDashboard) {
        a.addEventListener('click', e => {
          if (!window.PiLayout) return;
          e.preventDefault();
          window.PiLayout.switchTab(b.id);
          closeDrawer();
        });
      }
      boardsGroup.appendChild(a);
    });
  }

  if (onDashboard) {
    window.addEventListener('pi-boards-changed', e => renderBoards(e.detail.boards, e.detail.active));
  } else {
    fetch('/api/boards').then(r => (r.ok ? r.json() : null)).then(cfg => {
      if (cfg) renderBoards(cfg.boards.map(b => ({ id: b.id, name: b.name || t('Übersicht') })));
    }).catch(() => {});
  }

  // Own links
  fetch('/api/links').then(r => (r.ok ? r.json() : null)).then(data => {
    const links = (data && data.links) || [];
    links.forEach(l => {
      const a = item('a', '', l.name, { href: l.url, raw: true });
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.querySelector('.side-icon').textContent = (l.name.trim()[0] || '?').toUpperCase();
      a.querySelector('.side-icon').classList.add('letter');
      linksGroup.appendChild(a);
    });
    linksGroup.hidden = !links.length;
  }).catch(() => {});

  // Actions: the header's icon buttons move into the sidebar (page links are replaced by the list above)
  const right = header.querySelector('.header-right');
  if (right) {
    [...right.querySelectorAll(':scope > .icon-button')].forEach(btn => {
      const href = btn.getAttribute('href');
      if (href && PAGES.some(p => p[0] === href)) { btn.remove(); return; }
      const label = btn.getAttribute('title') || btn.getAttribute('aria-label') || '';
      btn.classList.remove('icon-button');
      btn.classList.add('side-item');
      const ic = el('span', 'side-icon');
      [...btn.childNodes].forEach(n => ic.appendChild(n));
      btn.append(ic, el('span', 'side-label', label));
      if (btn.id === 'btn-palette') btn.appendChild(el('kbd', 'side-kbd', t('Strg+K')));
      actionsGroup.appendChild(btn);
    });
  }
  actionsGroup.hidden = !actionsGroup.querySelector('.side-item');

  // Phone: a menu button opens the sidebar as a drawer
  const burger = el('button', 'icon-button sidebar-burger');
  burger.type = 'button';
  burger.title = t('Menü');
  burger.setAttribute('aria-label', t('Menü'));
  burger.innerHTML = svg(ICON.menu);
  const left = header.querySelector('.header-left');
  if (left) left.prepend(burger);
  const backdrop = el('div', 'sidebar-backdrop');

  document.body.prepend(side, backdrop);
  document.body.classList.add('has-sidebar');

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function setCollapsed(on, remember) {
    document.body.classList.toggle('sidebar-collapsed', on);
    toggle.title = on ? t('Seitenleiste ausklappen') : t('Seitenleiste einklappen');
    toggle.setAttribute('aria-label', toggle.title);
    toggle.setAttribute('aria-expanded', String(!on));
    if (remember) { try { localStorage.setItem(KEY, on ? 'collapsed' : 'expanded'); } catch (e) { /* ignore */ } }
    window.dispatchEvent(new Event('resize'));  // grids and charts re-measure
  }

  function closeDrawer() { document.body.classList.remove('sidebar-open'); }

  const pref = stored();
  setCollapsed(pref ? pref === 'collapsed' : window.innerWidth < 1280, false);
  toggle.addEventListener('click', () => setCollapsed(!document.body.classList.contains('sidebar-collapsed'), true));
  burger.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
  backdrop.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
  side.addEventListener('click', e => { if (e.target.closest('a.side-item[href]:not([target])')) closeDrawer(); });
})();
