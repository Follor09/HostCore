/**
 * HostCore Dashboard - navigation bar on every page
 *
 * One bar at the top for everything that used to be unlabeled icons in the header:
 * - Übersicht (the start page; several boards are listed in its menu and switch in place on the dashboard)
 * - the pages; hovering a page opens a menu with its groups (or, on pages without groups, its sections)
 * - "Wissen & Nachweise" and own links (Einstellungen -> Eigene Links) as menus
 * - actions: the header's icon buttons are moved here (same elements, so their handlers keep working)
 * On phones the bar is a drawer behind a menu button.
 */
(function () {
  'use strict';

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
    vm: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/><rect x="6" y="7" width="5" height="6" rx="0.5"/><rect x="13" y="7" width="5" height="6" rx="0.5"/>',
    fleet: '<rect x="2" y="3" width="8" height="7" rx="1"/><rect x="14" y="3" width="8" height="7" rx="1"/><rect x="8" y="14" width="8" height="7" rx="1"/><path d="M6 10v2h12v-2M12 12v2"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
    toolbox: '<rect x="2" y="7" width="20" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M2 13h20"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    collapse: '<path d="M11 17l-5-5 5-5"/><path d="M18 17l-5-5 5-5"/>',
    menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    chevron: '<path d="M9 18l6-6-6-6"/>'
  };
  // [href, icon, label, short label, permission, capability, where]: "ops" = in the bar, "docs" = in the
  // "Wissen & Nachweise" menu, "right" = at the right end
  const PAGES = [
    ['/system', 'tools', 'System-Werkzeuge', 'System', 'system.view', '', 'ops'],
    ['/containers', 'box', 'Container', 'Container', 'system.view', '', 'ops'],
    ['/vms', 'vm', 'VMs', 'VMs', 'system.view', 'vms', 'ops'],
    ['/flotte', 'fleet', 'Flotte', 'Flotte', 'system.view', '', 'ops'],
    ['/dateien', 'folder', 'Dateien', 'Dateien', 'cloud', '', 'ops'],
    ['/terminal', 'terminal', 'Terminal', 'Terminal', 'terminal', '', 'ops'],
    ['/doku', 'book', 'Dokumentation', 'Dokumentation', '', '', 'docs'],
    ['/nachweise', 'shield', 'Compliance', 'Compliance', '', '', 'docs'],
    ['/wissen', 'wiki', 'Wissen', 'Wissen', '', '', 'docs'],
    ['/report', 'report', 'Wochenbericht', 'Wochenbericht', 'system.view', '', 'docs'],
    ['/werkzeuge', 'toolbox', 'IT-Toolbox', 'IT-Toolbox', '', '', 'docs'],
    ['/settings', 'settings', 'Einstellungen', 'Einstellungen', '', '', 'right']
  ];
  // The groups of the long pages (same ids as pagegroups.js), shown when hovering the page in the bar
  const GROUPS = {
    '/system': [['zustand', 'Zustand'], ['netzwerk', 'Netzwerk'], ['sicherheit', 'Sicherheit', 'security.view'], ['hardware', 'Hardware & Strom'],
      ['speicher', 'Speicher & Backups'], ['logs', 'Logs'], ['wartung', 'Pakete & Wartung'], ['software', 'Schwachstellen & Support']],
    '/containers': [['docker', 'Docker'], ['sicherheitscheck', 'Sicherheitscheck'], ['datenbanken', 'Datenbanken'], ['kubernetes', 'Kubernetes'],
      ['images', 'Image-Updates'], ['gitops', 'GitOps'], ['vorlagen', 'Stack-Vorlagen'], ['statistik', 'Statistik'], ['apps', 'App-Store']],
    '/settings': [['konto', 'Konto & Zugang'], ['dashboard', 'Dashboard'], ['ueberwachung', 'Überwachung'], ['verbindungen', 'Verbindungen'], ['system', 'System & Daten']],
    '/nachweise': [['grundschutz', 'Grundschutz-Check'], ['nis2', 'NIS-2-Meldefristen'], ['richtlinien', 'Richtlinien'], ['lieferkette', 'Dienstleister'],
      ['audits', 'Interne Audits'], ['datenschutz', 'Datenschutz'], ['nachweise', 'Nachweis-Paket']],
    '/doku': [['bestand', 'Bestand'], ['betrieb', 'Betrieb'], ['notfall', 'Notfallhandbuch'], ['vertraege', 'Verträge & Kosten'], ['aenderungen', 'Änderungen']]
  };
  const onDashboard = location.pathname === '/';
  const isCurrent = href => location.pathname === href || (href === '/doku' && location.pathname === '/docs');

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
    if (opts.cap) a.dataset.cap = opts.cap;   // caps.js hides it where the feature is off
    if (opts.title) a.title = opts.title;
    const ic = el('span', 'side-icon');
    ic.innerHTML = icon;
    const text = el('span', 'side-label', label);
    if (opts.raw) text.setAttribute('data-no-i18n', '');
    a.append(ic, text);
    return a;
  }

  // One entry of the bar: a link (or a plain label) with an optional menu that opens on hover / focus
  function navItem(trigger, opts = {}) {
    const wrap = el('div', `tn-item${opts.group ? ' tn-group' : ''}`);
    if (trigger.dataset.perm) wrap.dataset.perm = trigger.dataset.perm;
    if (trigger.dataset.cap) wrap.dataset.cap = trigger.dataset.cap;
    wrap.appendChild(trigger);
    const menu = el('div', 'tn-menu');
    menu.setAttribute('role', 'menu');
    wrap.appendChild(menu);
    wrap.menu = menu;
    return wrap;
  }

  function menuLink(href, label, opts = {}) {
    const a = el('a', `tn-menu-link${opts.cls ? ` ${opts.cls}` : ''}`, label);
    a.href = href;
    a.setAttribute('role', 'menuitem');
    if (opts.perm) a.dataset.perm = opts.perm;
    if (opts.cap) a.dataset.cap = opts.cap;
    if (opts.raw) a.setAttribute('data-no-i18n', '');
    return a;
  }

  function fillMenu(wrap) {
    const has = !!wrap.menu.querySelector('.tn-menu-link');
    wrap.classList.toggle('has-menu', has);
    const trig = wrap.firstElementChild;
    if (has) trig.setAttribute('aria-haspopup', 'true'); else trig.removeAttribute('aria-haspopup');
  }

  // ---------------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------------
  const side = el('nav', 'sidebar topnav');
  side.id = 'sidebar';
  side.setAttribute('aria-label', 'Navigation');

  const head = el('div', 'sidebar-head');
  const brand = el('a', 'sidebar-brand');
  brand.href = '/';
  brand.title = 'HostCore';
  brand.innerHTML = '<span class="sidebar-logo">H</span><span class="side-label">HOST<b>CORE</b></span>';
  brand.setAttribute('data-no-i18n', '');
  head.append(brand);

  const main = el('div', 'tn-main');
  const right = el('div', 'tn-right');
  side.append(head, main, right);

  // Boards: "Übersicht" goes to the start page; several boards are listed in its menu
  const boardsItem = navItem(item('a', svg(ICON.board), t('Übersicht'), { href: '/', cls: onDashboard ? 'active' : '' }));
  main.appendChild(boardsItem);

  function pageItem([href, icon, label, short, perm, cap]) {
    const current = isCurrent(href);
    const a = item('a', svg(ICON[icon]), t(short), { href, perm, cap, cls: current ? 'active' : '', title: t(label) });
    if (current) a.setAttribute('aria-current', 'page');
    const wrap = navItem(a);
    (GROUPS[href] || []).forEach(([id, glabel, gperm]) => wrap.menu.appendChild(menuLink(`${href}#g-${id}`, t(glabel), { perm: gperm })));
    // Pages without groups: their own sections, while the page is open
    const nav = current && !GROUPS[href] && document.querySelector('.settings-nav');
    if (nav) {
      [...nav.querySelectorAll('a')].forEach(orig => {
        const sub = menuLink(orig.getAttribute('href'), orig.textContent.trim());
        sub.addEventListener('click', e => { e.preventDefault(); orig.click(); closeDrawer(); });
        wrap.menu.appendChild(sub);
      });
    }
    fillMenu(wrap);
    return wrap;
  }

  PAGES.filter(p => p[6] === 'ops').forEach(p => main.appendChild(pageItem(p)));

  // Knowledge and evidence: one menu
  const docsPages = PAGES.filter(p => p[6] === 'docs');
  const docsTrigger = item('button', svg(ICON.book), t('Wissen & Nachweise'), { cls: docsPages.some(p => isCurrent(p[0])) ? 'active' : '' });
  const docsItem = navItem(docsTrigger, { group: true });
  docsPages.forEach(([href, , label, , perm, cap]) => docsItem.menu.appendChild(menuLink(href, t(label), { perm, cap, cls: isCurrent(href) ? 'active' : '' })));
  fillMenu(docsItem);
  main.appendChild(docsItem);

  // Own links: one menu, only when there are some
  const linksItem = navItem(item('button', svg(ICON.link), t('Links')), { group: true });
  linksItem.hidden = true;
  main.appendChild(linksItem);

  function renderBoards(list, activeId) {
    boardsItem.menu.innerHTML = '';
    if ((list || []).length > 1) {
      list.forEach((b, i) => {
        const a = menuLink(i === 0 ? '/' : `/#board-${b.id}`, b.name, { raw: true, cls: onDashboard && b.id === activeId ? 'active' : '' });
        if (i < 9) a.title = `${b.name} (Alt+${i + 1})`;
        if (onDashboard) {
          a.addEventListener('click', e => {
            if (!window.PiLayout) return;
            e.preventDefault();
            window.PiLayout.switchTab(b.id);
            closeDrawer();
          });
        }
        boardsItem.menu.appendChild(a);
      });
    }
    fillMenu(boardsItem);
  }

  if (onDashboard) {
    window.addEventListener('pi-boards-changed', e => renderBoards(e.detail.boards, e.detail.active));
  } else {
    fetch('/api/boards').then(r => (r.ok ? r.json() : null)).then(cfg => {
      if (cfg) renderBoards(cfg.boards.map(b => ({ id: b.id, name: b.name || t('Übersicht') })));
    }).catch(() => {});
  }

  fetch('/api/links').then(r => (r.ok ? r.json() : null)).then(data => {
    const links = (data && data.links) || [];
    links.forEach(l => {
      const a = menuLink(l.url, l.name, { raw: true });
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      linksItem.menu.appendChild(a);
    });
    linksItem.hidden = !links.length;
    fillMenu(linksItem);
  }).catch(() => {});

  // Right end: search, the header's small actions as icons, settings
  const actions = el('div', 'side-actions');
  const hr = header.querySelector('.header-right');
  if (hr) {
    [...hr.querySelectorAll(':scope > .icon-button')].forEach(btn => {
      const href = btn.getAttribute('href');
      if (href && PAGES.some(p => p[0] === href)) { btn.remove(); return; }
      const label = btn.getAttribute('title') || btn.getAttribute('aria-label') || '';
      btn.classList.remove('icon-button');
      btn.classList.add('side-item');
      const ic = el('span', 'side-icon');
      [...btn.childNodes].forEach(n => ic.appendChild(n));
      btn.append(ic, el('span', 'side-label', label));
      if (btn.id === 'btn-palette') {
        btn.appendChild(el('kbd', 'side-kbd', t('Strg+K')));
        btn.classList.add('side-search');
        right.appendChild(btn);
        return;
      }
      actions.appendChild(btn);
    });
  }
  actions.hidden = !actions.querySelector('.side-item');
  right.appendChild(actions);
  PAGES.filter(p => p[6] === 'right').forEach(p => right.appendChild(pageItem(p)));

  // Phones: the bar becomes a drawer behind a menu button
  const burger = el('button', 'icon-button sidebar-burger');
  burger.type = 'button';
  burger.title = t('Menü');
  burger.setAttribute('aria-label', t('Menü'));
  burger.innerHTML = svg(ICON.menu);
  const left = header.querySelector('.header-left');
  if (left) left.prepend(burger);
  const backdrop = el('div', 'sidebar-backdrop');

  document.body.prepend(side, backdrop);
  document.body.classList.add('has-sidebar', 'has-topnav');

  function closeDrawer() { document.body.classList.remove('sidebar-open'); }
  // A menu closes right after a click in it (hover would keep it open)
  side.addEventListener('click', e => {
    if (e.target.closest('.tn-menu-link')) {
      const it = e.target.closest('.tn-item');
      if (it) { it.classList.add('tn-closed'); setTimeout(() => it.classList.remove('tn-closed'), 400); }
      if (document.activeElement) document.activeElement.blur();
    }
    if (e.target.closest('a[href]:not([target])')) closeDrawer();
  });
  burger.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
  backdrop.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    closeDrawer();
    if (side.contains(document.activeElement)) document.activeElement.blur();
  });
})();

/**
 * MCP approvals on every page: a bar at the bottom as soon as Claude waits for a confirmation
 * (approve / deny right there, details under Einstellungen -> MCP-Aktivität).
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let bar = null;
  let seen = '';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function decide(id, decision) {
    try {
      const res = await fetch(`/api/mcp/approvals/${id}/${decision}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      render(await res.json());
    } catch (e) { /* next poll */ }
  }

  function render(d) {
    const open = (d && d.items || []).filter(a => a.status === 'pending');
    if (!open.length || location.hash === '#mcp-aktivitaet') {
      if (bar) bar.hidden = true;
      return;
    }
    if (!bar) {
      bar = el('div', 'mcp-approval-bar');
      bar.setAttribute('role', 'alert');
      document.body.appendChild(bar);
    }
    const a = open[0];
    const key = open.map(x => x.id).join(',');
    bar.hidden = false;
    if (key === seen) return;
    seen = key;
    bar.innerHTML = '';
    const text = el('div', 'mcp-approval-text');
    text.appendChild(el('strong', '', t('Claude wartet auf Freigabe')));
    const what = el('span', '', `${a.tool} · ${a.summary}`);
    what.setAttribute('data-no-i18n', '');
    text.appendChild(what);
    if (open.length > 1) text.appendChild(el('span', 'field-hint', `+${open.length - 1} ${t('weitere')}`));
    const ok = el('button', 'btn-primary', t('Freigeben'));
    const no = el('button', 'btn-danger', t('Ablehnen'));
    const more = el('a', 'btn-secondary', t('Details'));
    more.href = '/settings#mcp-aktivitaet';
    [ok, no].forEach(b => { b.type = 'button'; });
    ok.addEventListener('click', () => { ok.disabled = true; decide(a.id, 'approve'); });
    no.addEventListener('click', () => { no.disabled = true; decide(a.id, 'deny'); });
    const actions = el('div', 'mcp-approval-actions');
    actions.append(ok, no, more);
    bar.append(text, actions);
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const res = await fetch('/api/mcp/approvals');
      if (!res.ok) return;
      const d = await res.json();
      if (!d.enabled && !d.pending) return;
      render(d);
    } catch (e) { /* offline */ }
  }

  async function start() {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.user) return;
    poll();
    setInterval(() => { if (!document.hidden) poll(); }, 10000);
    window.addEventListener('hashchange', poll);
  }

  start();
})();
