/**
 * HostCore Dashboard - system tools section "Patch-Fristen": deadlines per severity for fixable
 * vulnerabilities, traffic light per finding, exceptions with reason and end date, statistics
 * (open, overdue, fixed in time, mean days to fix).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ps-table')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const STATE = { overdue: ['critical', 'Überfällig'], soon: ['warning', 'Bald fällig'], ok: ['good', 'In Frist'], exception: ['', 'Ausnahme'], accepted: ['', 'Akzeptiert'] };
  const SEV = { critical: 'critical', high: 'warning', medium: '', low: '' };
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
  const header = (table, cols) => { const tr = el('tr'); cols.forEach(c => tr.appendChild(el('th', '', t(c)))); table.appendChild(tr); };

  function exceptionForm(item, cell) {
    cell.innerHTML = '';
    const form = el('form', 'inc-update');
    const reason = el('input');
    reason.type = 'text';
    reason.placeholder = t('Begründung (z. B. kein Update vom Hersteller, Dienst nicht erreichbar)');
    reason.required = true;
    const until = el('input');
    until.type = 'date';
    const d = new Date();
    d.setDate(d.getDate() + 30);
    until.value = d.toISOString().slice(0, 10);
    const go = el('button', 'btn-secondary', t('Ausnahme'));
    go.type = 'submit';
    form.append(reason, until, go);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      try { data = await api('/api/patch-sla/exception', { id: item.id, reason: reason.value, until: until.value }); render(); toast(t('Ausnahme gespeichert.'), 'success'); }
      catch (err) { toast(err.message, 'error'); }
    });
    cell.appendChild(form);
    reason.focus();
  }

  function renderStats() {
    const table = $('ps-stats');
    table.innerHTML = '';
    header(table, ['Schwere', 'Frist', 'Offen', 'Überfällig', 'Behoben (12 Mon.)', 'In Frist', 'Ø Tage bis Behebung']);
    Object.entries(data.stats).forEach(([sev, s]) => {
      const tr = el('tr');
      const name = el('td');
      name.appendChild(el('span', `health-tag ${SEV[sev]}`, t(s.label)));
      tr.append(name, el('td', '', `${s.deadline} ${t('Tage')}`), el('td', '', String(s.open)),
        el('td', s.overdue ? 'ps-bad' : '', String(s.overdue)), el('td', '', String(s.closed)),
        el('td', '', s.closed ? `${s.in_time} (${Math.round(100 * s.in_time / s.closed)} %)` : '–'), el('td', '', s.mttr == null ? '–' : String(s.mttr)));
      table.appendChild(tr);
    });
  }

  function renderItems() {
    const table = $('ps-table');
    table.innerHTML = '';
    const all = $('ps-filter').value === 'all';
    const items = data.items.filter(i => all || ['overdue', 'soon', 'ok'].includes(i.state));
    if (!data.scanned) { const tr = el('tr'); tr.appendChild(el('td', 'empty', t('Noch kein Schwachstellen-Scan – siehe Abschnitt Schwachstellen.'))); table.appendChild(tr); return; }
    if (!items.length) { const tr = el('tr'); tr.appendChild(el('td', 'empty', t('Keine offenen Lücken mit verfügbarem Fix.'))); table.appendChild(tr); return; }
    header(table, ['Status', 'Kennung', 'Paket', 'Betrifft', 'Schwere', 'Seit', 'Fällig', '']);
    items.forEach(i => {
      const tr = el('tr');
      const [cls, label] = STATE[i.state];
      const st = el('td');
      st.appendChild(el('span', `health-tag ${cls}`, t(label)));
      if (i.exception) st.appendChild(el('div', 'field-hint', `${t('bis')} ${new Date(i.exception.until).toLocaleDateString(LOCALE)}: ${i.exception.reason}`));
      const due = el('td', i.state === 'overdue' ? 'ps-bad' : '', `${fmtDate(i.due)} (${i.days_left < 0 ? `${Math.abs(Math.round(i.days_left))} ${t('Tage drüber')}` : `${Math.round(i.days_left)} ${t('Tage')}`})`);
      const sev = el('td');
      sev.appendChild(el('span', `health-tag ${SEV[i.severity]}`, t(i.label)));
      const act = el('td');
      if (can('settings.manage')) {
        const b = el('button', 'text-btn', t(i.exception_active ? 'Ausnahme beenden' : 'Ausnahme'));
        b.type = 'button';
        b.addEventListener('click', async () => {
          if (i.exception_active) {
            try { data = await api('/api/patch-sla/exception', { id: i.id, remove: true }); render(); } catch (err) { toast(err.message, 'error'); }
          } else exceptionForm(i, act);
        });
        act.appendChild(b);
      }
      tr.append(st, raw('td', 'mono', i.id), raw('td', '', i.package), raw('td', '', i.target), sev, el('td', '', fmtDate(i.first_seen)), due, act);
      table.appendChild(tr);
    });
    if (data.truncated) { const tr = el('tr'); const td = el('td', 'field-hint', t('Nur die ersten 600 Einträge.')); td.colSpan = 8; tr.appendChild(td); table.appendChild(tr); }
  }

  function renderSettings() {
    const form = $('ps-settings');
    form.innerHTML = '';
    const inputs = {};
    Object.entries(data.stats).forEach(([sev, s]) => {
      const w = el('label', 'login-field');
      const i = el('input');
      i.type = 'number';
      i.min = 1;
      i.max = 730;
      i.value = data.settings.days[sev];
      inputs[sev] = i;
      w.append(el('span', '', `${t(s.label)} (${t('Tage')})`), i);
      form.appendChild(w);
    });
    const w = el('label', 'login-field');
    const unknown = el('select');
    Object.entries(data.labels).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; unknown.appendChild(o); });
    unknown.value = data.settings.unknown_as;
    w.append(el('span', '', t('Ohne Einstufung zählt als')), unknown);
    form.appendChild(w);
    if (can('settings.manage')) {
      const actions = el('div', 'form-actions');
      const save = el('button', 'btn-secondary', t('Speichern'));
      save.type = 'submit';
      actions.appendChild(save);
      form.appendChild(actions);
      form.onsubmit = async e => {
        e.preventDefault();
        try {
          data = await api('/api/patch-sla/settings', { days: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, Number(i.value)])), unknown_as: unknown.value });
          render();
          toast(t('Gespeichert.'), 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
    } else form.querySelectorAll('input, select').forEach(x => { x.disabled = true; });
    const table = $('ps-exceptions');
    table.innerHTML = '';
    if (!data.exceptions.length) { const tr = el('tr'); tr.appendChild(el('td', 'empty', t('Keine Ausnahmen.'))); table.appendChild(tr); return; }
    header(table, ['Kennung', 'Begründung', 'Bis', 'Von', '']);
    data.exceptions.forEach(x => {
      const tr = el('tr', x.active ? '' : 'ar-off');
      const act = el('td');
      if (can('settings.manage')) {
        const b = el('button', 'text-btn danger', t('Entfernen'));
        b.type = 'button';
        b.addEventListener('click', async () => { try { data = await api('/api/patch-sla/exception', { id: x.id, remove: true }); render(); } catch (err) { toast(err.message, 'error'); } });
        act.appendChild(b);
      }
      tr.append(raw('td', 'mono', x.id), raw('td', '', x.reason), el('td', '', new Date(x.until).toLocaleDateString(LOCALE) + (x.active ? '' : ` (${t('abgelaufen')})`)), raw('td', '', x.user), act);
      table.appendChild(tr);
    });
  }

  function render() {
    const c = data.counts;
    $('ps-scanned').textContent = data.compliance != null ? `${t('Einhaltung')} ${data.compliance} % · ${c.overdue} ${t('überfällig')}` : `${c.overdue} ${t('überfällig')}`;
    renderStats();
    renderItems();
    renderSettings();
  }

  $('ps-filter').addEventListener('change', () => data && renderItems());
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('system.view')) return;
    try { data = await api('/api/patch-sla'); render(); } catch (e) { /* no access */ }
  })();
})();
