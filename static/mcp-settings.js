/**
 * HostCore Dashboard - settings section "MCP-Server": Pocket ID connection and user mapping.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  let users = [];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function msg(text, level) {
    const m = $('mcp-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function userRow(match, user) {
    const row = el('div', 'mcp-user-row');
    const m = el('input');
    m.type = 'text';
    m.placeholder = 'niko@example.com';
    m.value = match || '';
    m.spellcheck = false;
    m.className = 'mcp-match';
    m.autocomplete = 'picore-identity';
    m.setAttribute('data-lpignore', 'true');
    const sel = el('select', 'mcp-user');
    users.forEach(u => { const o = el('option', '', u); o.value = u; sel.appendChild(o); });
    if (user) sel.value = user;
    const rm = el('button', 'btn-secondary', '×');
    rm.type = 'button';
    rm.title = t('Entfernen');
    rm.addEventListener('click', () => row.remove());
    row.append(m, el('span', 'mcp-arrow', '→'), sel, rm);
    $('mcp-users').appendChild(row);
  }

  function show(st) {
    $('mcp-enabled').checked = st.enabled;
    $('mcp-base').value = st.base_url || '';
    $('mcp-issuer').value = st.issuer || '';
    $('mcp-client').value = st.client_id || '';
    $('mcp-secret').value = '';
    $('mcp-secret').placeholder = st.has_secret ? t('gespeichert – leer lassen zum Behalten') : '';
    $('mcp-consent').checked = st.consent !== false;
    $('mcp-users').innerHTML = '';
    (st.users.length ? st.users : [{}]).forEach(u => userRow(u.match, u.user));
    const tag = $('mcp-state');
    tag.className = `health-tag ${st.running ? 'good' : st.error ? 'critical' : ''}`;
    tag.textContent = !st.available ? t('fastmcp fehlt') : st.running ? t('Läuft') : st.error ? t('Fehler') : t('Aus');
    const info = $('mcp-info');
    info.innerHTML = '';
    const add = (k, v) => { if (!v) return; const dd = el('dd', '', v); dd.setAttribute('data-no-i18n', ''); info.append(el('dt', '', t(k)), dd); };
    add('MCP-Adresse für Claude', st.mcp_url);
    add('Callback-URL für Pocket ID', st.callback_url);
    if (st.running) add('Läuft seit', new Date(st.started * 1000).toLocaleString(LOCALE));
    if (st.error) add('Fehler', st.error);
    if (!st.available) add('Hinweis', t('Das Paket fastmcp ist nicht installiert – es kommt mit dem nächsten Update (requirements.txt).'));
  }

  async function init() {
    if (!$('mcp-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('users.manage')) return;
    try { users = (await api('/api/users')).users.map(u => u.username); } catch (e) { users = []; }
    try { show(await api('/api/settings/mcp')); } catch (e) { msg(e.message, 'error'); }
    $('mcp-user-add').addEventListener('click', () => userRow('', users[0]));
    $('mcp-form').addEventListener('submit', async e => {
      e.preventDefault();
      msg(t('Speichere und starte neu…'));
      const mapping = [...document.querySelectorAll('.mcp-user-row')].map(r => ({ match: r.querySelector('.mcp-match').value.trim(), user: r.querySelector('.mcp-user').value }))
        .filter(m => m.match);
      try {
        const st = await api('/api/settings/mcp', { enabled: $('mcp-enabled').checked, base_url: $('mcp-base').value.trim(), issuer: $('mcp-issuer').value.trim(),
          client_id: $('mcp-client').value.trim(), client_secret: $('mcp-secret').value.trim(), consent: $('mcp-consent').checked, users: mapping });
        show(st);
        msg(st.error ? `${t('Gespeichert, aber der Start schlug fehl:')} ${st.error}` : st.running ? t('Gespeichert – der MCP-Server läuft.') : t('Gespeichert – der MCP-Server ist aus.'), st.error ? 'error' : 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
  }

  init();
})();
