/**
 * HostCore Dashboard - settings section "Anmeldung per SSO": OpenID Connect provider, how provider
 * accounts become dashboard users, group mapping, local sign-in policy and linked accounts.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  let groups = [];

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
    const m = $('sso-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function groupSelect(cls, value, empty) {
    const sel = el('select', cls);
    if (empty) { const o = el('option', '', empty); o.value = ''; sel.appendChild(o); }
    groups.forEach(g => { const o = el('option', '', g.name); o.value = g.id; sel.appendChild(o); });
    sel.value = value || (empty ? '' : (groups.find(g => g.id === 'viewer') || groups[0] || {}).id || '');
    return sel;
  }

  function groupRow(claim, group) {
    const row = el('div', 'sso-group-row');
    const m = el('input', 'sso-claim');
    m.type = 'text';
    m.placeholder = 'picore-admins';
    m.value = claim || '';
    m.spellcheck = false;
    m.autocomplete = 'picore-sso-group';
    m.setAttribute('data-lpignore', 'true');
    const sel = groupSelect('sso-target', group);
    const rm = el('button', 'btn-secondary', '×');
    rm.type = 'button';
    rm.title = t('Entfernen');
    rm.addEventListener('click', () => row.remove());
    row.append(m, el('span', 'mcp-arrow', '→'), sel, rm);
    $('sso-groups').appendChild(row);
  }

  function fmt(ts) { return ts ? new Date(ts * 1000).toLocaleString(LOCALE) : '–'; }

  function show(st) {
    groups = st.groups || [];
    $('sso-enabled').checked = st.enabled;
    $('sso-issuer').value = st.issuer || '';
    $('sso-client').value = st.client_id || '';
    $('sso-secret').value = '';
    $('sso-secret').placeholder = st.has_secret ? t('gespeichert – leer lassen zum Behalten') : '';
    $('sso-label').value = st.label || '';
    $('sso-scopes').value = st.scopes || '';
    $('sso-claim').value = st.groups_claim || 'groups';
    const local = $('sso-local');
    local.innerHTML = '';
    Object.entries(st.local_login_options).forEach(([k, v]) => { const o = el('option', '', v); o.value = k; local.appendChild(o); });
    local.value = st.local_login;
    $('sso-match').checked = st.match_accounts;
    $('sso-create').checked = st.auto_create;
    const def = $('sso-default');
    def.replaceWith(Object.assign(groupSelect('', st.default_group, t('– keine (nur mit Gruppen-Zuordnung) –')), { id: 'sso-default' }));
    [...$('sso-default').options].forEach(o => { if (o.value === 'admin') o.remove(); });
    $('sso-restrict').checked = st.restrict_to_groups;
    $('sso-sync').checked = st.sync_groups;
    $('sso-2fa').checked = st.counts_as_2fa;
    $('sso-groups').innerHTML = '';
    st.group_map.forEach(m => groupRow(m.claim, m.group));
    syncCreate();

    const tag = $('sso-state');
    tag.className = `health-tag ${st.active ? 'good' : ''}`;
    tag.textContent = st.active ? t('Aktiv') : t('Aus');
    const info = $('sso-info');
    info.innerHTML = '';
    const dd = v => { const d = el('dd', '', v); d.setAttribute('data-no-i18n', ''); return d; };
    info.append(el('dt', '', t('Callback-URL für den Anbieter')), dd(st.callback_url));

    const table = $('sso-linked');
    table.innerHTML = '';
    if (!st.linked.length) {
      const tr = el('tr');
      tr.appendChild(el('td', 'empty', t('Noch kein Konto verknüpft – das passiert bei der ersten SSO-Anmeldung.')));
      table.appendChild(tr);
      return;
    }
    const head = el('tr');
    ['Benutzer', 'Kennung beim Anbieter', 'Verknüpft', 'Zuletzt', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    table.appendChild(head);
    st.linked.forEach(l => {
      const tr = el('tr');
      const name = el('td', '', l.user);
      if (l.sso_only) name.appendChild(el('span', 'health-tag', t('nur SSO')));
      const sub = el('td', 'mono', l.subject);
      sub.setAttribute('data-no-i18n', '');
      const act = el('td');
      const b = el('button', 'btn-secondary', t('Lösen'));
      b.type = 'button';
      b.title = l.sso_only ? t('Konto hat kein bekanntes Passwort – es wird dabei deaktiviert') : '';
      b.addEventListener('click', async () => {
        if (!b.classList.contains('confirming')) {
          b.classList.add('confirming');
          b.textContent = t('Sicher?');
          setTimeout(() => { b.classList.remove('confirming'); b.textContent = t('Lösen'); }, 4000);
          return;
        }
        try { show(await api('/api/settings/sso/unlink', { user: l.user })); msg(t('Verknüpfung gelöst.'), 'success'); } catch (e) { msg(e.message, 'error'); }
      });
      act.appendChild(b);
      tr.append(name, sub, el('td', '', fmt(l.since)), el('td', '', fmt(l.last)), act);
      table.appendChild(tr);
    });
  }

  function syncCreate() {
    document.querySelector('.sso-default').hidden = !$('sso-create').checked;
  }

  function form() {
    return {
      enabled: $('sso-enabled').checked, issuer: $('sso-issuer').value.trim(), client_id: $('sso-client').value.trim(),
      client_secret: $('sso-secret').value.trim(), label: $('sso-label').value.trim(), scopes: $('sso-scopes').value.trim(),
      groups_claim: $('sso-claim').value.trim(), local_login: $('sso-local').value, match_accounts: $('sso-match').checked,
      auto_create: $('sso-create').checked, default_group: $('sso-default').value, restrict_to_groups: $('sso-restrict').checked,
      sync_groups: $('sso-sync').checked, counts_as_2fa: $('sso-2fa').checked,
      group_map: [...document.querySelectorAll('.sso-group-row')].map(r => ({ claim: r.querySelector('.sso-claim').value.trim(), group: r.querySelector('.sso-target').value }))
        .filter(m => m.claim)
    };
  }

  async function init() {
    if (!$('sso-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('users.manage')) return;
    try { show(await api('/api/settings/sso')); } catch (e) { msg(e.message, 'error'); }
    $('sso-create').addEventListener('change', syncCreate);
    $('sso-group-add').addEventListener('click', () => groupRow('', ''));
    $('sso-test').addEventListener('click', async () => {
      msg(t('Teste…'));
      try {
        const r = await api('/api/settings/sso/test', form());
        msg(`${t('Anbieter erreichbar')}: ${r.issuer} · ${r.keys} ${t('Schlüssel')}${r.pkce ? ' · PKCE' : ''}${r.groups ? ` · ${t('Scope „groups“ verfügbar')}` : ''}`, 'success');
      } catch (e) { msg(e.message, 'error'); }
    });
    $('sso-form').addEventListener('submit', async e => {
      e.preventDefault();
      msg(t('Speichere…'));
      try {
        const st = await api('/api/settings/sso', form());
        show(st);
        msg(st.active ? t('Gespeichert – auf der Anmeldeseite erscheint jetzt der SSO-Knopf.') : t('Gespeichert – SSO ist aus.'), 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
  }

  init();
})();
