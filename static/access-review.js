/**
 * PiCore Dashboard - settings section "Rechte-Review": accounts with critical rights, second factor
 * and last activity, findings with direct fixes, groups, links without login, MCP identities;
 * sign-off with note and the history of reviews (Markdown export).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ar-users')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const LEVEL = { critical: 'critical', warning: 'warning', info: '' };
  const PERM_SHORT = { 'users.manage': 'Benutzer', terminal: 'Terminal', 'firewall.manage': 'Firewall', 'system.power': 'Neustart',
    'updates.install': 'Updates', 'apps.install': 'Apps', 'changes.approve': 'Freigaben', 'files.share': 'Teilen' };
  let data = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtDate = ts => (ts ? new Date(ts * 1000).toLocaleDateString(LOCALE) : '–');
  const fmtTs = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');

  function confirmButton(label, cls, fn) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', async () => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t('Sicher?');
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      b.disabled = true;
      try { await fn(); await load(); } catch (err) { toast(err.message, 'error'); b.disabled = false; }
    });
    return b;
  }

  function header(table, cols) {
    const tr = el('tr');
    cols.forEach(c => tr.appendChild(el('th', '', t(c))));
    table.appendChild(tr);
  }

  function renderSummary() {
    const box = $('ar-summary');
    box.innerHTML = '';
    const tile = (label, value, level) => { const d = el('div', `hb-tile ${level || ''}`); d.append(raw('strong', '', value), el('span', '', t(label))); box.appendChild(d); };
    const c = data.counts;
    tile('Aktive Konten', `${c.active} / ${c.users}`);
    tile('Befunde', String(c.findings), c.critical ? 'critical' : c.findings ? 'warning' : 'good');
    tile('Links ohne Anmeldung', String(c.links), c.links ? 'warning' : '');
    tile('Letztes Review', data.last_review ? `${fmtDate(data.last_review.ts)}` : t('nie'), data.overdue ? 'warning' : 'good');
    const due = $('ar-due');
    due.className = `health-tag ${data.overdue ? 'warning' : 'good'}`;
    due.textContent = data.overdue ? t('Review fällig') : `${t('Nächstes Review')} ${fmtDate(data.due)}`;
    const gen = $('ar-general');
    gen.innerHTML = '';
    data.general.forEach(f => gen.appendChild(el('li', '', `${t(f.text)} – ${t(f.fix)}`)));
  }

  function chips(perms) {
    const td = el('td');
    if (!perms.length) { td.textContent = '–'; return td; }
    const box = el('div', 'ar-perms');
    perms.forEach(p => box.appendChild(el('span', 'pg-chip', t(PERM_SHORT[p] || p))));
    td.appendChild(box);
    return td;
  }

  function renderUsers() {
    const table = $('ar-users');
    table.innerHTML = '';
    header(table, ['Benutzer', 'Gruppe', 'Kritische Rechte', '2. Faktor', 'Zuletzt aktiv', 'Befunde']);
    data.users.forEach(u => {
      const tr = el('tr', u.disabled || u.expired ? 'ar-off' : '');
      const name = el('td');
      name.appendChild(raw('strong', '', u.username));
      if (u.disabled) name.appendChild(el('span', 'health-tag', t('deaktiviert')));
      if (u.expires && !u.expired) name.appendChild(el('span', 'health-tag', `${t('Gast bis')} ${fmtDate(u.expires)}`));
      if (u.expired) name.appendChild(el('span', 'health-tag', t('abgelaufen')));
      const crit = chips(u.critical);
      const factor = el('td');
      factor.appendChild(u.factor ? el('span', 'health-tag good', u.factor) : el('span', `health-tag ${u.critical.length ? 'warning' : ''}`, t('keiner')));
      const findTd = el('td');
      const find = el('div', 'ar-findings');
      findTd.appendChild(find);
      if (!u.findings.length) find.appendChild(el('span', 'field-hint', t('Keine')));
      u.findings.forEach(f => {
        const row = el('div', `ar-finding ${LEVEL[f.level]}`);
        row.append(el('span', `health-tag ${LEVEL[f.level]}`, t(f.level === 'critical' ? 'Kritisch' : f.level === 'warning' ? 'Warnung' : 'Hinweis')), el('span', '', t(f.text)));
        row.title = t(f.fix);
        if (f.action === 'disable') row.appendChild(confirmButton('Deaktivieren', 'text-btn danger', () => api(`/api/users/${encodeURIComponent(u.username)}/update`, { disabled: true })));
        if (f.action === 'delete') row.appendChild(confirmButton('Löschen', 'text-btn danger', () => api(`/api/users/${encodeURIComponent(u.username)}/delete`, {})));
        find.appendChild(row);
      });
      tr.append(name, raw('td', '', u.group_name), crit, factor, el('td', '', u.last_activity ? fmtTs(u.last_activity) : t('nie')), findTd);
      table.appendChild(tr);
    });
  }

  function renderGroups() {
    const table = $('ar-groups');
    table.innerHTML = '';
    header(table, ['Gruppe', 'Mitglieder', 'Rechte', 'Kritische Rechte']);
    data.groups.forEach(g => {
      const crit = chips(g.critical);
      const tr = el('tr');
      tr.append(raw('td', '', g.name), el('td', '', String(g.members)), el('td', '', String(g.perms)), crit);
      table.appendChild(tr);
    });
  }

  function renderLinks() {
    const table = $('ar-links');
    table.innerHTML = '';
    if (!data.links.length && !data.mcp.length) {
      const tr = el('tr');
      tr.appendChild(el('td', 'empty', t('Keine aktiven Links und keine MCP-Identitäten.')));
      table.appendChild(tr);
      return;
    }
    header(table, ['Art', 'Ziel', 'Erstellt von', 'Gültig bis', '']);
    data.links.forEach(l => {
      const tr = el('tr');
      const act = el('td');
      act.appendChild(confirmButton('Beenden', 'text-btn danger', () => api(l.delete, {})));
      tr.append(el('td', '', t(l.kind)), raw('td', '', l.what), raw('td', '', l.user || '–'), el('td', '', fmtTs(l.expires)), act);
      table.appendChild(tr);
    });
    data.mcp.forEach(m => {
      const tr = el('tr');
      tr.append(el('td', '', t('MCP-Identität')), raw('td', '', m.identity), raw('td', '', `→ ${m.user}`), el('td', '', '–'), el('td'));
      table.appendChild(tr);
    });
  }

  function renderHistory() {
    const s = data.settings;
    $('ar-interval').value = s.interval_days;
    $('ar-inactive').value = s.inactive_days;
    $('ar-password').value = s.password_days;
    const table = $('ar-history');
    table.innerHTML = '';
    if (!data.reviews.length) {
      const tr = el('tr');
      tr.appendChild(el('td', 'empty', t('Noch kein Review abgezeichnet.')));
      table.appendChild(tr);
      return;
    }
    header(table, ['Datum', 'Von', 'Offene Befunde', 'Notiz', 'Fingerabdruck', '']);
    data.reviews.forEach(r => {
      const tr = el('tr');
      const md = el('a', 'text-btn', 'Markdown');
      md.href = `/api/access-review/${r.id}/markdown`;
      const act = el('td');
      act.appendChild(md);
      tr.append(el('td', '', fmtTs(r.ts)), raw('td', '', r.user), el('td', '', String(r.open_findings)), raw('td', 'ar-note', r.note || '–'),
        raw('td', 'mono', r.fingerprint.slice(0, 16)), act);
      table.appendChild(tr);
    });
  }

  function render() {
    renderSummary();
    renderUsers();
    renderGroups();
    renderLinks();
    renderHistory();
  }

  async function load() {
    try { data = await api('/api/access-review'); render(); } catch (e) { /* no permission */ }
  }

  $('ar-sign').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      data = await api('/api/access-review/sign-off', { note: $('ar-note').value });
      $('ar-note').value = '';
      render();
      toast(t('Review abgezeichnet.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
  });
  $('ar-settings').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      data = await api('/api/access-review/settings', { interval_days: $('ar-interval').value, inactive_days: $('ar-inactive').value, password_days: $('ar-password').value });
      render();
      toast(t('Gespeichert.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('users.manage')) return;
    load();
  })();
})();
