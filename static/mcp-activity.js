/**
 * HostCore Dashboard - settings section "MCP-Aktivität": open approvals (approve / deny),
 * which tools need one, and the log of all MCP tool calls with filters.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const STATUS = { pending: ['warning', 'Wartet'], approved: ['good', 'Freigegeben'], denied: ['critical', 'Abgelehnt'],
    used: ['', 'Erledigt'], expired: ['', 'Abgelaufen'] };
  const APPROVAL = { required: 'Freigabe nötig', approved: 'freigegeben', denied: 'abgelehnt', waiting: 'wartete auf Freigabe' };
  let manager = false;
  let timer = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function data(tag, cls, text) {
    const e = el(tag, cls, text);
    e.setAttribute('data-no-i18n', '');
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function msg(text, level) {
    const m = $('mcpa-msg');
    if (!m) return;
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  const fmt = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });

  // ---------------------------------------------------------------- approvals
  function renderApprovals(d) {
    const box = $('mcpa-approvals');
    box.innerHTML = '';
    const tag = $('mcpa-pending');
    tag.hidden = !d.pending;
    tag.className = 'health-tag warning';
    tag.textContent = `${d.pending} ${t('offen')}`;
    if (!d.items.length) {
      box.appendChild(el('p', 'field-hint', d.enabled ? t('Keine Anfragen.') : t('Freigaben sind ausgeschaltet – Claude führt erlaubte Aktionen direkt aus.')));
      return;
    }
    d.items.forEach(a => {
      const row = el('div', `mcpa-item ${a.status}`);
      const main = el('div', 'workload-main');
      const head = el('div', 'mcpa-head');
      const [cls, label] = STATUS[a.status] || ['', a.status];
      head.append(el('span', `health-tag ${cls}`, t(label)), data('span', 'workload-name', a.tool), el('span', 'workload-sub', t(a.label)));
      main.appendChild(head);
      main.appendChild(data('code', 'mcpa-args', a.summary));
      const who = `${fmt(a.created)} · ${t('als')} ${a.user}${a.identity ? ` (${a.identity})` : ''}` +
        (a.decided_by ? ` · ${t(a.status === 'denied' ? 'abgelehnt von' : 'entschieden von')} ${a.decided_by}` : '');
      main.appendChild(data('span', 'workload-sub', who));
      row.appendChild(main);
      if (a.status === 'pending') {
        const actions = el('div', 'item-actions');
        const ok = el('button', 'btn-primary', t('Freigeben'));
        const no = el('button', 'btn-danger', t('Ablehnen'));
        [ok, no].forEach(b => { b.type = 'button'; });
        ok.addEventListener('click', () => decide(a.id, 'approve', ok));
        no.addEventListener('click', () => decide(a.id, 'deny', no));
        actions.append(ok, no);
        row.appendChild(actions);
      }
      box.appendChild(row);
    });
  }

  async function decide(id, decision, btn) {
    btn.disabled = true;
    try {
      renderApprovals(await api(`/api/mcp/approvals/${id}/${decision}`, {}));
      msg(decision === 'approve' ? t('Freigegeben – Claude führt die Aktion jetzt aus.') : t('Abgelehnt.'), 'success');
      if (manager) loadCalls();
    } catch (err) {
      msg(err.message, 'error');
      btn.disabled = false;
    }
  }

  async function loadApprovals() {
    try { renderApprovals(await api('/api/mcp/approvals')); } catch (e) { /* offline */ }
  }

  // ---------------------------------------------------------------- approval settings
  function renderConfig(st) {
    $('mcpa-enabled').checked = st.approval.enabled;
    $('mcpa-wait').value = st.approval.wait;
    const box = $('mcpa-tools');
    box.innerHTML = '';
    st.approval_tools.forEach(tool => {
      const l = el('label', 'toggle-label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = tool.name;
      cb.checked = st.approval.tools.includes(tool.name);
      l.append(cb, document.createTextNode(` ${t(tool.label)}`));
      const code = data('code', 'mcpa-tool-name', tool.name);
      l.appendChild(code);
      box.appendChild(l);
    });
  }

  // ---------------------------------------------------------------- call log
  function fillSelect(sel, values) {
    const keep = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    values.forEach(v => { const o = data('option', '', v); o.value = v; sel.appendChild(o); });
    if (values.includes(keep)) sel.value = keep;
  }

  async function loadCalls() {
    const p = new URLSearchParams({ tool: $('mcpa-tool').value, user: $('mcpa-user').value, status: $('mcpa-status').value,
      q: $('mcpa-q').value.trim(), limit: '200' });
    let d;
    try { d = await api(`/api/settings/mcp/calls?${p}`); } catch (e) { return; }
    fillSelect($('mcpa-tool'), d.tools);
    fillSelect($('mcpa-user'), d.users);
    const stats = $('mcpa-stats');
    stats.innerHTML = '';
    const tile = (label, value, cls) => {
      const b = el('div', 'bench-value');
      b.append(el('span', 'label', t(label)), data('span', `value ${cls || ''}`, value));
      stats.appendChild(b);
    };
    tile('Aufrufe (24 Std.)', String(d.last_24h.calls));
    tile('Fehler (24 Std.)', String(d.last_24h.errors), d.last_24h.errors ? 'warn' : '');
    if (d.last_24h.top.length) tile('Am häufigsten', d.last_24h.top.slice(0, 3).map(([n, c]) => `${n} (${c})`).join(', '));
    const table = $('mcpa-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Zeit', 'Werkzeug', 'Benutzer', 'Parameter', 'Ergebnis', 'Dauer'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    d.calls.forEach(c => {
      const r = body.insertRow();
      r.insertCell().appendChild(data('span', 'mono', fmt(c.ts)));
      r.insertCell().appendChild(data('code', '', c.tool));
      const u = r.insertCell();
      u.appendChild(data('span', '', c.user || '–'));
      if (c.identity) u.appendChild(data('span', 'ctr-sub', c.identity));
      const a = r.insertCell();
      a.className = 'mcpa-args-cell';
      a.appendChild(data('span', 'mono', c.args));
      const res = r.insertCell();
      res.appendChild(el('span', `health-tag ${c.status === 'ok' ? 'good' : 'critical'}`, t(c.status === 'ok' ? 'OK' : 'Fehler')));
      if (c.approval) res.appendChild(el('span', 'ctr-sub', t(APPROVAL[c.approval] || c.approval)));
      if (c.error) res.appendChild(data('span', 'ctr-sub', c.error));
      r.insertCell().appendChild(data('span', 'mono', c.ms != null ? `${c.ms} ms` : ''));
    });
    if (!d.calls.length) {
      const r = body.insertRow();
      const cell = r.insertCell();
      cell.colSpan = 6;
      cell.className = 'empty-cell';
      cell.textContent = t('Noch keine Aufrufe.');
    }
  }

  async function init() {
    if (!$('mcpa-approvals')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    manager = !window.PiAuth || window.PiAuth.can('users.manage');
    let first;
    try { first = await api('/api/mcp/approvals'); } catch (e) { return; }
    if (!first.enabled && !first.items.length && !manager) {
      $('mcp-aktivitaet').hidden = true;
      const link = document.querySelector('.settings-nav a[href="#mcp-aktivitaet"]');
      if (link) link.hidden = true;
      return;
    }
    renderApprovals(first);
    timer = setInterval(() => { if (!document.hidden) loadApprovals(); }, 5000);
    if (!manager) return;
    try { renderConfig(await api('/api/settings/mcp')); } catch (e) { msg(e.message, 'error'); }
    $('mcpa-form').addEventListener('submit', async e => {
      e.preventDefault();
      const tools = [...$('mcpa-tools').querySelectorAll('input:checked')].map(c => c.value);
      try {
        renderConfig(await api('/api/settings/mcp/approval', { enabled: $('mcpa-enabled').checked, wait: Number($('mcpa-wait').value), tools }));
        msg(t('Gespeichert.'), 'success');
        loadApprovals();
      } catch (err) { msg(err.message, 'error'); }
    });
    let debounce;
    $('mcpa-q').addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(loadCalls, 300); });
    ['mcpa-tool', 'mcpa-user', 'mcpa-status'].forEach(id => $(id).addEventListener('change', loadCalls));
    $('mcpa-filters').addEventListener('submit', e => { e.preventDefault(); loadCalls(); });
    loadCalls();
    setInterval(() => { if (!document.hidden) loadCalls(); }, 30000);
  }

  init();
})();
