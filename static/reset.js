/**
 * HostCore Dashboard - page /passwort: set a new password with the single-use link from the e-mail.
 * The token is removed from the address bar right away (it stays out of history and screenshots).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const token = new URLSearchParams(location.search).get('token') || '';
  history.replaceState(null, '', '/passwort');

  function error(msg) {
    $('reset-error').textContent = msg;
    $('reset-error').hidden = !msg;
  }

  fetch(`/api/auth/reset?token=${encodeURIComponent(token)}`).then(r => r.json()).then(d => {
    if (!d.valid) {
      $('reset-sub').textContent = t('Der Link ist abgelaufen oder wurde schon benutzt. Auf der Anmeldeseite einen neuen anfordern.');
      return;
    }
    $('reset-sub').textContent = `${t('Für')} ${d.user}`;
    $('reset-fields').hidden = false;
    $('reset-pass').focus();
  }).catch(() => { $('reset-sub').textContent = t('Nicht erreichbar.'); });

  $('reset-form').addEventListener('submit', async e => {
    e.preventDefault();
    error('');
    const p1 = $('reset-pass').value, p2 = $('reset-pass2').value;
    if (p1 !== p2) return error(t('Die Passwörter stimmen nicht überein.'));
    $('reset-submit').disabled = true;
    try {
      const res = await fetch('/api/auth/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, password: p1 }) });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.detail || t('Speichern fehlgeschlagen.'));
      $('reset-fields').hidden = true;
      $('reset-sub').textContent = t(d.message || 'Neues Passwort gespeichert – jetzt anmelden.');
    } catch (err) {
      error(err.message);
      $('reset-submit').disabled = false;
    }
  });
})();
