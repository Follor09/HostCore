/**
 * PiCore Dashboard - settings section "NetBox": address, API token, TLS check; test and disconnect.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function msg(text, level) {
    const m = $('nb-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function show(st) {
    $('nb-remove').hidden = !st.configured;
    $('nb-current').textContent = st.configured
      ? `${t('Verbunden mit')} ${st.url}${st.version ? ` (NetBox ${st.version})` : ''} · Token ${st.token_hint}${st.verify ? '' : ` · ${t('Zertifikat wird nicht geprüft')}`}`
      : t('Noch nicht verbunden.');
    if (st.configured) {
      $('nb-url').value = st.url;
      $('nb-insecure').checked = !st.verify;
      $('nb-token').placeholder = t('leer = gespeicherter Token');
    } else {
      $('nb-token').placeholder = 'NetBox → Profil → API-Tokens';
    }
  }

  const form = () => ({ url: $('nb-url').value.trim(), token: $('nb-token').value.trim(), verify: !$('nb-insecure').checked });

  async function init() {
    if (!$('nb-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('settings.manage')) return;
    try { show(await api('/api/netbox/status')); } catch (e) { msg(e.message, 'error'); }

    $('nb-test').addEventListener('click', async () => {
      msg(t('Teste…'));
      try {
        const r = await api('/api/settings/netbox/test', form());
        msg(`NetBox ${r.version} · ${r.prefixes} ${t('Präfixe lesbar.')}`, 'success');
      } catch (e) { msg(e.message, 'error'); }
    });
    $('nb-form').addEventListener('submit', async e => {
      e.preventDefault();
      msg(t('Verbinde…'));
      try {
        const st = await api('/api/settings/netbox', form());
        $('nb-token').value = '';
        show(st);
        msg(t('Gespeichert – die Daten stehen unter System-Werkzeuge → NetBox.'), 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
    const del = $('nb-remove');
    del.addEventListener('click', async () => {
      if (!del.classList.contains('confirming')) {
        del.classList.add('confirming');
        del.textContent = t('Sicher?');
        setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Trennen'); }, 4000);
        return;
      }
      del.classList.remove('confirming');
      del.textContent = t('Trennen');
      try {
        show(await api('/api/settings/netbox/delete', {}));
        $('nb-form').reset();
        msg(t('NetBox getrennt, Token gelöscht.'), 'success');
      } catch (e) { msg(e.message, 'error'); }
    });
  }

  init();
})();
