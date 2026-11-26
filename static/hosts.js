/**
 * HostCore Dashboard - host switcher: this server or a remote server running the HostCore agent.
 * The choice is a cookie; the server then forwards all API calls, live data and the
 * terminal to that agent. A coloured bar shows while a remote server is selected.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let state = null;

  // ---------------------------------------------------------------------------
  // The server this page shows. The selection is one cookie for the whole browser: another tab may
  // switch it while this one still shows the old server. Every action and every live connection says
  // which server it is meant for; the server refuses when that is no longer the selected one, so a
  // restart, a terminal or a console never lands on a different machine than the page shows.
  // ---------------------------------------------------------------------------
  let pageHost = null;          // "local" or the remote's id, once known
  let changedShown = false;

  function hostChanged(detail) {
    if (changedShown) return;
    changedShown = true;
    window.alert(detail || t('Inzwischen ist ein anderer Server ausgewählt. Die Seite wird neu geladen.'));
    location.reload();
  }

  const prevFetch = window.fetch;
  window.fetch = async function (input, init) {
    if (pageHost) {
      const method = String((init && init.method) || (input && typeof input === 'object' && input.method) || 'GET').toUpperCase();
      let url = null;
      try { url = new URL(typeof input === 'string' ? input : (input && input.url) || String(input), location.href); } catch (e) { /* not a URL */ }
      if (url && url.origin === location.origin && url.pathname.startsWith('/api/') && method !== 'GET' && method !== 'HEAD') {
        const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
        headers.set('X-PiCore-Host', pageHost);
        init = { ...(init || {}), headers };
      }
    }
    const res = await prevFetch(input, init);
    if (res.status === 409) {
      try {
        const d = await res.clone().json();
        if (d && d.host_changed) hostChanged(d.detail);
      } catch (e) { /* not ours */ }
    }
    return res;
  };

  window.PiHost = {
    // Query parameter for WebSocket URLs ("&host=…"), empty while unknown
    param: () => (pageHost ? `host=${encodeURIComponent(pageHost)}` : ''),
    // "local" or the remote's id the page shows - for requests that don't go through fetch (XHR uploads)
    id: () => pageHost,
    // An answer said the selection changed elsewhere (409 with host_changed)
    changed: detail => hostChanged(detail),
    // Name of the server the page shows
    name: () => (state ? (state.current ? state.current.name : state.local) : ''),
    isRemote: () => !!(state && state.current),
    // A WebSocket closed with 4409: the selection changed elsewhere
    closed: code => { if (code === 4409) { hostChanged(); return true; } return false; }
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="8" rx="2"></rect><rect x="2" y="13" width="20" height="8" rx="2"></rect><path d="M6 7h.01M6 17h.01"></path></svg>';

  async function select(id) {
    try {
      await fetch('/api/remotes/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
    } catch (e) { /* reload anyway */ }
    location.reload();
  }

  function dot(online) {
    return el('span', `host-dot ${online === true ? 'on' : online === false ? 'off' : ''}`);
  }

  function buildSwitcher() {
    const right = document.querySelector('.header-right');
    if (!right) return;
    const wrap = el('div', 'host-switch');
    const btn = el('button', 'host-switch-btn');
    btn.type = 'button';
    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = ICON;
    const label = el('span', 'host-switch-name', state.current ? state.current.name : state.local);
    label.setAttribute('data-no-i18n', '');
    btn.appendChild(label);
    btn.title = t('Server wechseln');
    const menu = el('div', 'host-menu');
    menu.hidden = true;
    menu.setAttribute('role', 'menu');

    const item = (name, sub, online, active, onClick) => {
      const b = el('button', `host-item${active ? ' active' : ''}`);
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      const text = el('span', 'host-item-text');
      const n = el('span', 'host-item-name', name);
      n.setAttribute('data-no-i18n', '');
      text.appendChild(n);
      if (sub) { const s = el('span', 'host-item-sub', sub); s.setAttribute('data-no-i18n', ''); text.appendChild(s); }
      b.append(dot(online), text);
      if (active) b.appendChild(el('span', 'host-item-check', '✓'));
      b.addEventListener('click', onClick);
      return b;
    };
    menu.appendChild(el('div', 'host-menu-title', t('Dashboard zeigt')));
    menu.appendChild(item(state.local, t('Dieser Server'), true, !state.current, () => select('')));
    state.hosts.forEach(h => {
      const info = h.info || {};
      const sub = h.online === false ? t('nicht erreichbar') : [info.model, info.ip].filter(Boolean).join(' · ');
      menu.appendChild(item(h.name, sub, h.online, state.current && state.current.id === h.id, () => select(h.id)));
    });
    if (!window.PiAuth || window.PiAuth.can('users.manage')) {
      const manage = el('a', 'host-manage', t('Remote-Server verwalten'));
      manage.href = '/settings#remote';
      menu.appendChild(manage);
    }

    btn.addEventListener('click', e => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', e => { if (!wrap.contains(e.target)) { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') menu.hidden = true; });
    wrap.append(btn, menu);
    right.insertBefore(wrap, right.firstChild);
  }

  function buildBanner() {
    const cur = state.current;
    if (!cur) return;
    document.body.classList.add('remote-host');
    const info = cur.info || {};
    const bar = el('div', `remote-banner${cur.online === false ? ' offline' : ''}`);
    bar.setAttribute('role', 'status');
    const text = el('span', 'remote-banner-text');
    text.append(document.createTextNode(`${t('Remote-Server')}: `));
    const b = el('b', '', cur.name);
    b.setAttribute('data-no-i18n', '');
    text.appendChild(b);
    const detail = cur.online === false ? ` – ${t('nicht erreichbar')}` : [info.hostname, info.model].filter(Boolean).length ? ` · ${[info.hostname, info.model].filter(Boolean).join(' · ')}` : '';
    if (detail) { const d = el('span', 'remote-banner-detail', detail); d.setAttribute('data-no-i18n', ''); text.appendChild(d); }
    const back = el('button', 'remote-banner-back', `${t('Zurück zu')} ${state.local}`);
    back.type = 'button';
    back.setAttribute('data-no-i18n', '');
    back.addEventListener('click', () => select(''));
    bar.append(text, back);
    const header = document.querySelector('.app-header');
    if (header) header.after(bar); else document.body.prepend(bar);
    document.title = `${cur.name} · ${document.title}`;
  }

  async function init() {
    if (document.body.classList.contains('login-page')) return;
    if (window.PiAuth && window.PiAuth.ready) await window.PiAuth.ready;
    try {
      const res = await fetch('/api/remotes/current');
      if (!res.ok) return;
      state = await res.json();
    } catch (e) { return; }
    pageHost = state.current ? state.current.id : 'local';
    if (!state.hosts.length && !state.current) return;
    buildSwitcher();
    buildBanner();
    window.PiHosts = { current: () => state && state.current, select };
  }

  init();
})();
