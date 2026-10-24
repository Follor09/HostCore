/**
 * HostCore Dashboard - page /2fa: the forced first setup of the two-factor login.
 */
(function () {
  'use strict';

  document.getElementById('twofa-logout').addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    location.replace('/login');
  });
  fetch('/api/auth/2fa').then(r => (r.ok ? r.json() : Promise.reject(r))).then(d => {
    if (d.enabled) { location.replace('/'); return; }
    window.PiTwoFA.setup(document.getElementById('twofa-box'), { onDone: () => location.replace('/') });
  }).catch(() => location.replace('/login'));
})();
