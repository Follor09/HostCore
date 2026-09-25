/**
 * PiCore Dashboard - user editor and permission editor (settings page, admins only).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const state = { users: [], groups: [], permissions: [], me: null };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const box = $('toast-container');
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function button(label, cls, onClick) {
    const b = el('button', cls, label);
    b.type = 'button';
    if (onClick) b.addEventListener('click', onClick);
    return b;
  }

  // Destructive: second click within 4 s
  function confirmButton(label, confirmLabel, cls, action) {
    const b = button(label, cls);
    b.addEventListener('click', () => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = confirmLabel;
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = label; }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = label;
      action();
    });
    return b;
  }

  const groupName = id => (state.groups.find(g => g.id === id) || {}).name || id;

  async function run(promise, okMessage) {
    try {
      const data = await promise;
      if (data.users) state.users = data.users;
      if (data.groups) state.groups = data.groups;
      render();
      if (okMessage) toast(okMessage, 'success');
      return true;
    } catch (err) {
      toast(err.message, 'error');
      render();
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Users
  // ---------------------------------------------------------------------------
  function renderUsers() {
    const table = $('users-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Benutzer', 'Gruppe', 'Status', 'Angelegt', ''].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    state.users.forEach(u => {
      const self = u.username.toLowerCase() === (state.me || '').toLowerCase();
      const row = body.insertRow();
      const name = row.insertCell();
      const n = el('span', 'user-name-cell', u.username);
      n.setAttribute('data-no-i18n', '');
      name.appendChild(n);
      if (self) name.append(' ', el('span', 'health-tag good', 'Du'));

      const groupCell = row.insertCell();
      const select = el('select', 'inline-select');
      select.setAttribute('aria-label', `Gruppe von ${u.username}`);
      state.groups.forEach(g => {
        const o = el('option', '', g.name);
        o.value = g.id;
        if (!g.builtin) o.setAttribute('data-no-i18n', '');
        select.appendChild(o);
      });
      select.value = u.group;
      select.disabled = self;   // nobody removes their own admin rights by accident
      if (self) select.title = 'Die eigene Gruppe kann nur ein anderer Administrator ändern.';
      select.addEventListener('change', () =>
        run(api(`/api/users/${encodeURIComponent(u.username)}/update`, { group: select.value }), `${u.username}: ${groupName(select.value)}`));
      groupCell.appendChild(select);

      row.insertCell().appendChild(el('span', `health-tag ${u.disabled ? 'critical' : 'good'}`, u.disabled ? 'Deaktiviert' : 'Aktiv'));
      row.insertCell().textContent = u.created ? new Date(u.created * 1000).toLocaleDateString(LOCALE) : '–';

      const actions = row.insertCell();
      actions.className = 'row-action user-actions';
      actions.appendChild(button('Passwort', 'btn-secondary', () => openPasswordRow(row, u)));
      actions.appendChild(button('Abmelden', 'btn-secondary', () =>
        run(api(`/api/users/${encodeURIComponent(u.username)}/update`, { logout: true }), `${u.username} wurde auf allen Geräten abgemeldet.`)));
      if (!self) {
        actions.appendChild(button(u.disabled ? 'Aktivieren' : 'Deaktivieren', 'btn-secondary', () =>
          run(api(`/api/users/${encodeURIComponent(u.username)}/update`, { disabled: !u.disabled }),
            u.disabled ? `${u.username} aktiviert.` : `${u.username} deaktiviert und abgemeldet.`)));
        actions.appendChild(confirmButton('Löschen', 'Sicher?', 'btn-danger', () =>
          run(api(`/api/users/${encodeURIComponent(u.username)}/delete`, {}), `${u.username} gelöscht.`)));
      }
    });
    // group select for new users
    const sel = $('new-user-group');
    const keep = sel.value;
    sel.innerHTML = '';
    state.groups.forEach(g => {
      const o = el('option', '', g.name);
      o.value = g.id;
      sel.appendChild(o);
    });
    sel.value = state.groups.some(g => g.id === keep) ? keep : 'user';
  }

  function openPasswordRow(row, u) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains('password-row')) { next.remove(); return; }
    const tr = el('tr', 'password-row');
    const td = el('td');
    td.colSpan = 5;
    const form = el('form', 'inline-form password-form');
    const input = el('input');
    input.type = 'password';
    input.autocomplete = 'new-password';
    input.placeholder = t('Neues Passwort (mind. 8 Zeichen)');
    input.setAttribute('aria-label', `Neues Passwort für ${u.username}`);
    const save = el('button', 'btn-primary', 'Setzen');
    save.type = 'submit';
    const hint = el('span', 'field-hint', 'Meldet den Benutzer auf allen Geräten ab.');
    form.append(input, save, hint);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (await run(api(`/api/users/${encodeURIComponent(u.username)}/update`, { password: input.value }), `Neues Passwort für ${u.username} gesetzt.`)) tr.remove();
    });
    td.appendChild(form);
    tr.appendChild(td);
    row.after(tr);
    input.focus();
  }

  // ---------------------------------------------------------------------------
  // Permission matrix: rows = permissions, columns = groups
  // ---------------------------------------------------------------------------
  function saveGroup(g, perms) {
    return run(api(`/api/groups/${encodeURIComponent(g.id)}/update`, { name: g.name, description: g.description, permissions: perms }));
  }

  function renderMatrix() {
    const table = $('perm-matrix');
    table.innerHTML = '';
    const head = table.createTHead();
    const hr = head.insertRow();
    hr.appendChild(el('th', 'perm-label-col', 'Recht'));
    state.groups.forEach(g => {
      const th = el('th', 'perm-group-col');
      const name = el('button', 'perm-group-name', g.name);
      name.type = 'button';
      if (!g.builtin) name.setAttribute('data-no-i18n', '');
      name.title = g.locked ? g.description : `${g.description || ''}\n${t('Klicken zum Umbenennen')}`.trim();
      if (!g.locked) {
        name.addEventListener('click', () => {
          const newName = window.prompt(t('Name der Gruppe'), g.name);
          if (newName === null || !newName.trim()) return;
          const desc = window.prompt(t('Beschreibung (optional)'), g.description || '');
          run(api(`/api/groups/${encodeURIComponent(g.id)}/update`, { name: newName.trim(), description: desc === null ? g.description : desc, permissions: g.perms }));
        });
      } else {
        name.disabled = true;
      }
      th.appendChild(name);
      th.appendChild(el('span', 'perm-members', `${g.members} ${g.members === 1 ? 'Mitglied' : 'Mitglieder'}`));
      hr.appendChild(th);
    });

    const body = table.createTBody();
    let category = null;
    state.permissions.forEach(p => {
      if (p.category !== category) {
        category = p.category;
        const cr = body.insertRow();
        cr.className = 'perm-category';
        const c = cr.insertCell();
        c.colSpan = state.groups.length + 1;
        c.textContent = category;
      }
      const row = body.insertRow();
      const label = row.insertCell();
      label.className = 'perm-label';
      label.append(el('span', 'perm-name', p.label), el('span', 'perm-desc', p.description));
      state.groups.forEach(g => {
        const cell = row.insertCell();
        cell.className = 'perm-cell';
        const box = el('input');
        box.type = 'checkbox';
        box.checked = g.perms.includes(p.id);
        box.disabled = g.locked;
        box.setAttribute('aria-label', `${g.name}: ${p.label}`);
        box.addEventListener('change', () => {
          const perms = box.checked ? [...g.perms, p.id] : g.perms.filter(x => x !== p.id);
          saveGroup(g, perms).then(ok => { if (ok) toast(`${g.name}: ${p.label} ${box.checked ? t('erlaubt') : t('entzogen')}`, 'success'); });
        });
        cell.appendChild(box);
      });
    });

    // Footer: reset built-in groups, delete custom ones
    const foot = table.createTFoot().insertRow();
    foot.appendChild(el('td'));
    state.groups.forEach(g => {
      const td = foot.insertCell();
      td.className = 'perm-cell';
      if (g.builtin && !g.locked) {
        td.appendChild(confirmButton('Zurücksetzen', 'Sicher?', 'btn-secondary', () =>
          run(api(`/api/groups/${encodeURIComponent(g.id)}/reset`, {}), `${g.name}: ${t('Standardrechte wiederhergestellt.')}`)));
      } else if (!g.builtin) {
        td.appendChild(confirmButton('Löschen', 'Sicher?', 'btn-danger', () =>
          run(api(`/api/groups/${encodeURIComponent(g.id)}/delete`, {}), `${g.name} gelöscht.`)));
      }
    });

    const copy = $('group-copy');
    const keep = copy.value;
    copy.innerHTML = '';
    copy.appendChild(Object.assign(el('option', '', t('– keine –')), { value: '' }));
    state.groups.forEach(g => copy.appendChild(Object.assign(el('option', '', g.name), { value: g.id })));
    copy.value = keep;
  }

  function render() {
    renderUsers();
    renderMatrix();
  }

  async function init() {
    if (!$('users-table') || !window.PiAuth) return;
    await window.PiAuth.ready;
    if (!window.PiAuth.can('users.manage')) return;
    try {
      const data = await api('/api/users');
      Object.assign(state, { users: data.users, groups: data.groups, permissions: data.permissions, me: data.me });
      render();
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
    $('user-form').addEventListener('submit', async e => {
      e.preventDefault();
      const ok = await run(api('/api/users', { username: $('new-user-name').value.trim(), password: $('new-user-password').value, group: $('new-user-group').value }),
        `${$('new-user-name').value.trim()} angelegt.`);
      if (ok) { $('new-user-name').value = ''; $('new-user-password').value = ''; }
    });
    $('group-form').addEventListener('submit', async e => {
      e.preventDefault();
      const from = state.groups.find(g => g.id === $('group-copy').value);
      const ok = await run(api('/api/groups', { name: $('group-name').value, description: $('group-desc').value, permissions: from ? from.perms : [] }),
        `${$('group-name').value.trim()} angelegt.`);
      if (ok) { $('group-name').value = ''; $('group-desc').value = ''; }
    });
    const logoutAll = $('logout-all');
    logoutAll.addEventListener('click', async () => {
      if (!logoutAll.classList.contains('confirming')) {
        logoutAll.classList.add('confirming');
        logoutAll.textContent = t('Wirklich alle abmelden?');
        setTimeout(() => { logoutAll.classList.remove('confirming'); logoutAll.textContent = t('Alle Benutzer abmelden'); }, 4000);
        return;
      }
      logoutAll.classList.remove('confirming');
      logoutAll.textContent = t('Alle Benutzer abmelden');
      try { toast((await api('/api/settings/logout-all', {})).message, 'success'); } catch (err) { toast(err.message, 'error'); }
    });
  }

  init();
})();
