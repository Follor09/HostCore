/**
 * PiCore Dashboard - bottom navigation on phones (hidden by CSS on wider screens).
 * Replaces the header's page icons there, so the header can shrink to one row.
 */
(function () {
  'use strict';

  const ICON = {
    home: '<path d="M3 11l9-8 9 8"></path><path d="M5 10v10h5v-6h4v6h5V10"></path>',
    tools: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>',
    search: '<circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.35-4.35"></path>',
    terminal: '<polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line>',
    settings: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>'
  };
  const ITEMS = [
    ['/', 'home', 'Dashboard', ''],
    ['/system', 'tools', 'System', 'system.view'],
    [null, 'search', 'Suche', ''],
    ['/terminal', 'terminal', 'Terminal', 'terminal'],
    ['/settings', 'settings', 'Einstellungen', '']
  ];

  const nav = document.createElement('nav');
  nav.className = 'mobile-nav';
  nav.setAttribute('aria-label', 'Hauptnavigation');
  ITEMS.forEach(([href, icon, label, perm]) => {
    const el = document.createElement(href ? 'a' : 'button');
    el.className = 'mobile-nav-item';
    if (perm) el.dataset.perm = perm;
    if (href) {
      el.href = href;
      if (location.pathname === href) {
        el.classList.add('active');
        el.setAttribute('aria-current', 'page');
      }
    } else {
      el.type = 'button';
      el.addEventListener('click', () => window.PiPalette && window.PiPalette.open());
    }
    el.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[icon]}</svg>`;
    const span = document.createElement('span');
    span.textContent = label;
    el.appendChild(span);
    nav.appendChild(el);
  });
  document.body.appendChild(nav);
  document.body.classList.add('has-mobile-nav');
})();
