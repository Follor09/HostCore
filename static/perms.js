/**
 * PiCore Dashboard - what the logged-in user may do.
 * Elements with data-perm="<permission>" are hidden without that permission (the server
 * checks every request anyway - this only keeps the interface free of dead buttons).
 * Other scripts ask window.PiAuth.can('<permission>') or wait for PiAuth.ready.
 */
(function () {
  'use strict';

  const state = { user: null, group: null, groupName: null, perms: new Set(), loaded: false };
  let resolveReady;
  const ready = new Promise(r => { resolveReady = r; });

  function can(perm) {
    if (!perm) return true;
    if (perm === 'login') return !!state.user;
    return state.perms.has(perm);
  }

  // Hide what the user can't use; "a|b" means any of them
  function apply(root = document) {
    root.querySelectorAll('[data-perm]').forEach(el => {
      const ok = el.dataset.perm.split('|').some(p => can(p.trim()));
      el.classList.toggle('perm-denied', !ok);
    });
  }

  fetch('/api/auth/status')
    .then(r => (r.ok ? r.json() : {}))
    .catch(() => ({}))
    .then(s => {
      state.user = s.user || null;
      state.group = s.group || null;
      state.groupName = s.group_name || null;
      state.perms = new Set(s.permissions || []);
      state.loaded = true;
      const doIt = () => {
        apply();
        document.documentElement.classList.add('perms-ready');
        // Elements added later (live updates) are checked as they appear
        new MutationObserver(muts => muts.forEach(m => m.addedNodes.forEach(n => {
          if (n.nodeType === 1) {
            if (n.matches && n.matches('[data-perm]')) apply(n.parentNode || document);
            else if (n.querySelector && n.querySelector('[data-perm]')) apply(n);
          }
        }))).observe(document.body, { childList: true, subtree: true });
        resolveReady(state);
      };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', doIt);
      else doIt();
    });

  window.PiAuth = { can, apply, ready, get user() { return state.user; }, get group() { return state.group; },
                    get groupName() { return state.groupName; }, get loaded() { return state.loaded; } };
})();
