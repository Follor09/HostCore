/**
 * PiCore Dashboard - what the logged-in user may do.
 * Elements with data-perm="<permission>" are hidden without that permission (the server
 * checks every request anyway - this only keeps the interface free of dead buttons).
 * Other scripts ask window.PiAuth.can('<permission>') or wait for PiAuth.ready.
 *
 * Also wraps fetch() for every page: when the server asks to confirm a critical change again
 * (403 with "reauth"), a dialog takes the password (and the 2FA code), confirms and repeats the
 * request; when the two-factor login still has to be set up ("setup_2fa"), it opens /2fa.
 */
(function () {
  'use strict';

  const state = { user: null, group: null, groupName: null, perms: new Set(), loaded: false, totp: false };
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
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
      state.totp = !!s.totp;
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

  // ---------------------------------------------------------------------------
  // Confirm again (password + code) and repeat the request
  // ---------------------------------------------------------------------------
  const origFetch = window.fetch.bind(window);
  let pending = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function confirmDialog(message) {
    if (pending) return pending;
    pending = new Promise(resolve => {
      const back = el('div', 'modal-backdrop reauth-modal');
      const card = el('form', 'modal-card');
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-modal', 'true');
      card.autocomplete = 'off';
      card.append(el('h3', '', t('Bestätigen')), el('p', '', t(message || 'Bitte zur Bestätigung das Passwort erneut eingeben.')));
      const pw = el('input');
      pw.type = 'password';
      pw.autocomplete = 'current-password';
      pw.placeholder = t('Passwort');
      pw.setAttribute('aria-label', t('Passwort'));
      card.appendChild(pw);
      const code = el('input');
      code.type = 'text';
      code.inputMode = 'numeric';
      code.autocomplete = 'one-time-code';
      code.placeholder = t('Code aus der App');
      code.setAttribute('aria-label', t('Code aus der App'));
      code.hidden = !state.totp;
      card.appendChild(code);
      const err = el('p', 'form-msg error');
      err.hidden = true;
      card.appendChild(err);
      const buttons = el('div', 'modal-buttons');
      const cancel = el('button', 'btn-cancel', t('Abbrechen'));
      cancel.type = 'button';
      const ok = el('button', 'btn-confirm', t('Bestätigen'));
      ok.type = 'submit';
      buttons.append(cancel, ok);
      card.appendChild(buttons);
      back.appendChild(card);
      document.body.appendChild(back);
      requestAnimationFrame(() => back.classList.add('open'));
      setTimeout(() => pw.focus(), 50);
      const done = result => {
        back.classList.remove('open');
        setTimeout(() => back.remove(), 250);
        document.removeEventListener('keydown', onKey);
        pending = null;
        resolve(result);
      };
      const onKey = e => { if (e.key === 'Escape') done(false); };
      document.addEventListener('keydown', onKey);
      cancel.addEventListener('click', () => done(false));
      card.addEventListener('submit', async e => {
        e.preventDefault();
        ok.disabled = true;
        err.hidden = true;
        try {
          const res = await origFetch('/api/auth/reauth', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw.value, code: code.value.trim() }) });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(d.detail || t('Bestätigung fehlgeschlagen.'));
          done(true);
        } catch (ex) {
          err.textContent = ex.message;
          err.hidden = false;
          ok.disabled = false;
          (state.totp && pw.value ? code : pw).focus();
        }
      });
    });
    return pending;
  }

  window.fetch = async function (input, init) {
    const res = await origFetch(input, init);
    if (res.status !== 403) return res;
    let data = null;
    try { data = await res.clone().json(); } catch (e) { return res; }
    if (data && data.setup_2fa && location.pathname !== '/2fa') {
      location.href = '/2fa';
      return res;
    }
    if (!data || !data.reauth) return res;
    if (!(await confirmDialog(data.detail))) return res;
    return origFetch(input, init);
  };

  // After a login with a recovery code: say how many are left
  try {
    const left = sessionStorage.getItem('recoveryLeft');
    if (left !== null) {
      sessionStorage.removeItem('recoveryLeft');
      window.addEventListener('load', () => {
        const box = document.getElementById('toast-container');
        const msg = `${t('Wiederherstellungscode benutzt – noch')} ${left}. ${t('Neue Codes unter Einstellungen → Konto.')}`;
        if (box) { const n = el('div', 'toast warning', msg); box.appendChild(n); setTimeout(() => n.remove(), 10000); }
      });
    }
  } catch (e) { /* storage blocked */ }

  window.PiAuth = { can, apply, ready, get user() { return state.user; }, get group() { return state.group; },
                    get groupName() { return state.groupName; }, get loaded() { return state.loaded; },
                    get totp() { return state.totp; }, confirm: confirmDialog };
})();
