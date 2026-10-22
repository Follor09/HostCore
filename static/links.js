/**
 * PiCore Dashboard - your own links (Home Assistant, router, ...) in the header.
 * Managed under Einstellungen -> Eigene Links, stored on the Pi (same on every device).
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let links = [];

  function hostOf(url) {
    try { return new URL(url).host; } catch (e) { return url; }
  }

  function build() {
    const right = document.querySelector('.app-header .header-right');
    if (!right || !links.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'links-menu-wrap';
    const btn = document.createElement('button');
    btn.className = 'icon-button links-button';
    btn.id = 'btn-links';
    btn.type = 'button';
    btn.title = t('Eigene Links');
    btn.setAttribute('aria-label', t('Eigene Links'));
    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>';
    const menu = document.createElement('div');
    menu.className = 'links-menu';
    menu.hidden = true;
    menu.setAttribute('role', 'menu');
    links.forEach(l => {
      const a = document.createElement('a');
      a.href = l.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.setAttribute('role', 'menuitem');
      a.setAttribute('data-no-i18n', '');
      const badge = document.createElement('span');
      badge.className = 'link-badge';
      badge.textContent = (l.name.trim()[0] || '?').toUpperCase();
      const text = document.createElement('span');
      text.className = 'link-text';
      const name = document.createElement('b');
      name.textContent = l.name;
      const host = document.createElement('small');
      host.textContent = hostOf(l.url);
      text.append(name, host);
      a.append(badge, text);
      menu.appendChild(a);
    });
    const manage = document.createElement('a');
    manage.href = '/settings#links';
    manage.className = 'links-manage';
    manage.textContent = t('Links verwalten →');
    menu.appendChild(manage);

    const toggle = open => {
      menu.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      if (!open) return;
      // Open towards the side with room: the header wraps, so the button can be anywhere
      menu.style.left = '0';
      menu.style.right = 'auto';
      const r = menu.getBoundingClientRect();
      if (r.right > window.innerWidth - 8) {
        menu.style.left = 'auto';
        menu.style.right = '0';
      }
    };
    btn.addEventListener('click', e => { e.stopPropagation(); toggle(menu.hidden); });
    document.addEventListener('click', e => { if (!wrap.contains(e.target)) toggle(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') toggle(false); });
    wrap.append(btn, menu);
    // Right after the search button (or first in the row)
    const anchor = right.querySelector('#btn-palette');
    if (anchor) anchor.after(wrap); else right.prepend(wrap);
  }

  fetch('/api/links')
    .then(r => (r.ok ? r.json() : { links: [] }))
    .then(data => { links = data.links || []; if (!document.body.classList.contains('has-sidebar')) build(); })
    .catch(() => {});

  window.PiLinks = { list: () => links };
})();
