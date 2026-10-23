/**
 * PiCore Dashboard - login page: username + password, then (if set up) the code of the
 * authenticator app or a recovery code. Kept out of the HTML so the page works with a
 * strict Content-Security-Policy (no inline scripts).
 */
(function () {
  'use strict';

  const form = document.getElementById('login-form');
  const user = document.getElementById('login-username');
  const pass = document.getElementById('login-password');
  const code = document.getElementById('login-code');
  const codeField = document.getElementById('login-code-field');
  const remember = document.getElementById('login-remember');
  const errorBox = document.getElementById('login-error');
  const notice = document.getElementById('login-notice');
  const submit = document.getElementById('login-submit');
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let needCode = false;

  // Where to go after signing in: the page that sent us here (only paths of this dashboard)
  function nextTarget() {
    const next = new URLSearchParams(location.search).get('next') || '/';
    if (!next.startsWith('/') || next.startsWith('//') || next.includes('\\') || next.startsWith('/login')) return '/';
    return next + location.hash;
  }

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = !msg;
  }

  fetch('/api/auth/status').then(r => r.json()).then(status => {
    if (!status.configured) {
      notice.hidden = false;
      notice.textContent = t('Noch kein Passwort gesetzt. Auf dem Pi per SSH ausführen:') + ' ';
      const c = document.createElement('code');
      c.textContent = '~/pi-dashboard/set-password.sh';
      notice.appendChild(c);
      submit.disabled = true;
    }
    document.getElementById('login-public').hidden = !status.public_view;
  }).catch(() => {});

  // Language toggle: shows the other language
  const langBtn = document.getElementById('login-lang');
  const current = (window.I18N && window.I18N.lang) || 'de';
  langBtn.textContent = current === 'de' ? 'English' : 'Deutsch';
  langBtn.addEventListener('click', () => window.I18N && window.I18N.setLang(current === 'de' ? 'en' : 'de'));

  try { user.value = localStorage.getItem('loginUser') || ''; } catch (e) { /* ignore */ }
  (user.value ? pass : user).focus();

  function askCode() {
    needCode = true;
    codeField.hidden = false;
    user.readOnly = true;
    pass.readOnly = true;
    code.value = '';
    code.focus();
  }

  // Passkey: no username needed (the passkey knows its account)
  const pkBtn = document.getElementById('login-passkey');
  pkBtn.hidden = !(window.PiPasskey && window.PiPasskey.supported());
  pkBtn.addEventListener('click', async () => {
    showError('');
    pkBtn.disabled = true;
    try {
      const d = await window.PiPasskey.login(remember.checked);
      try { localStorage.setItem('loginUser', d.user); } catch (e) { /* ignore */ }
      location.replace(nextTarget());
    } catch (err) {
      showError(err.message);
      pkBtn.disabled = false;
    }
  });

  // Forgot password: always the same answer, whether the account exists or not
  const forgotBox = document.getElementById('forgot-box');
  document.getElementById('login-forgot').addEventListener('click', () => {
    forgotBox.hidden = !forgotBox.hidden;
    if (!forgotBox.hidden) {
      document.getElementById('forgot-login').value = user.value.trim();
      document.getElementById('forgot-login').focus();
    }
  });
  document.getElementById('forgot-send').addEventListener('click', async () => {
    const msg = document.getElementById('forgot-msg');
    const value = document.getElementById('forgot-login').value.trim();
    if (!value) { msg.textContent = t('Bitte Benutzername oder E-Mail-Adresse eingeben.'); return; }
    const btn = document.getElementById('forgot-send');
    btn.disabled = true;
    try {
      const res = await fetch('/api/auth/forgot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ login: value }) });
      const d = await res.json().catch(() => ({}));
      msg.textContent = d.message || d.detail || t('Anfrage fehlgeschlagen.');
    } catch (e) {
      msg.textContent = t('Anfrage fehlgeschlagen.');
    }
    setTimeout(() => { btn.disabled = false; }, 5000);
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    showError('');
    if (!user.value.trim() || !pass.value) return showError('Bitte Benutzername und Passwort eingeben.');
    if (needCode && !code.value.trim()) return showError('Bitte den Code aus der App eingeben.');

    submit.disabled = true;
    submit.textContent = 'Prüfe…';
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user.value.trim(), password: pass.value, remember: remember.checked, code: needCode ? code.value.trim() : '' })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Anmeldung fehlgeschlagen.');
      if (data.twofa) {
        submit.disabled = false;
        submit.textContent = 'Anmelden';
        askCode();
        return;
      }
      try { localStorage.setItem('loginUser', user.value.trim()); } catch (e) { /* ignore */ }
      if (data.recovery_left != null && data.recovery_left <= 3) {
        try { sessionStorage.setItem('recoveryLeft', String(data.recovery_left)); } catch (e) { /* ignore */ }
      }
      location.replace(nextTarget());
    } catch (err) {
      showError(err.message);
      if (needCode) {
        code.value = '';
        code.focus();
      } else {
        pass.value = '';
        pass.focus();
      }
      submit.disabled = false;
      submit.textContent = 'Anmelden';
    }
  });
})();
