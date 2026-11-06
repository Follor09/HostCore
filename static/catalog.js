/**
 * HostCore Dashboard - documentation section "Service-Katalog": standard requests as forms, with
 * approval, fulfilment and a timeline; catalog entries for those who may manage them.
 */
(function () {
  'use strict';

  const { t, el, raw, toast, api, time, tag, button, confirmButton, showForm, run, lines, ask } = window.OpsUI;
  const $ = id => document.getElementById(id);
  if (!$('sv-items')) return;
  const LEVEL = { eingereicht: 'warning', umsetzung: 'warning', erledigt: 'good', fehlgeschlagen: 'critical', abgelehnt: '', zurueckgezogen: '' };
  const open = new Set();
  let data = null;

  function fieldsText(fields) {
    return (fields || []).map(f => [f.key, f.label, f.type + (f.required ? '*' : ''), (f.options || []).join(',')].join(' | ')).join('\n');
  }

  function parseFields(text) {
    return lines(text).map(l => {
      const [key, label, type = 'text', options = ''] = l.split('|').map(x => x.trim());
      return { key, label, type: type.replace('*', ''), required: type.endsWith('*'), options: options.split(',').map(o => o.trim()).filter(Boolean) };
    });
  }

  function itemForm(item) {
    showForm($('sv-form'), item ? 'Eintrag bearbeiten' : 'Eintrag anlegen', [
      { key: 'title', label: 'Titel', full: true },
      { key: 'description', label: 'Beschreibung', type: 'textarea', rows: 2 },
      { key: 'category', label: 'Kategorie', value: 'Allgemein' },
      { key: 'kind', label: 'Umsetzung', type: 'select', options: Object.entries(data.kinds), value: 'manual' },
      { key: 'template', label: 'VM-Vorlage (bei VM aus Vorlage)', type: 'select', options: [['', '–'], ...(data.templates || []).map(x => [x.id, x.name])] },
      { key: 'action', label: 'Schnellaktion (bei Schnellaktion)', type: 'select', options: [['', '–'], ...(data.actions || []).map(x => [x.id, x.name])] },
      { key: 'fields', label: 'Formularfelder: Schlüssel | Bezeichnung | Typ (text, textarea, number, select; * = Pflicht) | Optionen', type: 'textarea', rows: 4, placeholder: 'grund | Wofür? | textarea*' },
      { key: 'approval', label: 'Freigabe nötig (automatische Umsetzung immer)', type: 'check', value: true },
      { key: 'active', label: 'Im Katalog sichtbar', type: 'check', value: true }
    ], item ? { ...item, fields: fieldsText(item.fields) } : {}, 'Speichern', async d => {
      data = await api(item ? `/api/service-catalog/items/${item.id}` : '/api/service-catalog/items', { ...d, fields: parseFields(d.fields) });
      render();
    });
  }

  function requestForm(item) {
    const spec = item.form.map(f => ({ key: f.key, label: f.required ? `${f.label} *` : f.label, type: f.type, options: (f.options || []).map(o => [o, o]), rows: 3, full: f.type === 'text' }));
    spec.push({ key: '__note', label: 'Anmerkung', type: 'textarea', rows: 2 });
    showForm($('sv-form'), item.title, spec, {}, 'Anfrage stellen', async d => {
      const note = d.__note;
      delete d.__note;
      data = await api(`/api/service-catalog/items/${item.id}/request`, { values: d, note });
      if (data.saved) open.add(data.saved);
      render();
      toast(t('Anfrage gestellt.'), 'success');
    });
  }

  function renderItems() {
    const box = $('sv-items');
    box.innerHTML = '';
    data.items.forEach(i => {
      const c = el('div', 'hb-tile');
      c.append(raw('strong', '', i.title), raw('span', '', i.description || i.category));
      const a = el('div', 'ops-actions');
      if (i.active !== false) a.appendChild(button('Anfragen', 'text-btn', () => requestForm(i)));
      if (data.can_manage) a.append(button('Bearbeiten', 'text-btn', () => itemForm(i)), confirmButton('Löschen', 'text-btn danger', async () => {
        const r = await run(() => api(`/api/service-catalog/items/${i.id}/delete`, {}));
        if (r) { data = r; render(); }
      }));
      c.appendChild(a);
      box.appendChild(c);
    });
    if (!data.items.length) box.appendChild(el('p', 'field-hint full', t(data.can_manage ? 'Noch keine Einträge. Lege den ersten an, etwa „Neue VM“ oder „Neuer Zugang“.' : 'Noch keine Einträge.')));
  }

  async function act(r, name, note = '') {
    const res = await run(() => api(`/api/service-catalog/requests/${r.id}/${name}`, { note }));
    if (res) { data = res; render(); }
  }

  function requestCard(r) {
    const c = el('div', `inc-card ${r.open ? 'open mittel' : 'resolved'}`);
    const head = el('div', 'inc-head');
    head.append(tag(LEVEL[r.status], r.status_label), raw('strong', 'inc-title', r.item_title), raw('span', 'field-hint', `${r.user} · ${time(r.created)}`),
      button(open.has(r.id) ? 'Weniger' : 'Details', 'text-btn', () => { open.has(r.id) ? open.delete(r.id) : open.add(r.id); render(); }));
    c.appendChild(head);
    if (open.has(r.id)) {
      Object.entries(r.values).filter(([, v]) => v).forEach(([k, v]) => { const d = el('div', 'chg-detail'); d.append(raw('strong', '', (r.labels || {})[k] || k), raw('p', 'inc-summary', v)); c.appendChild(d); });
      if (r.note) c.appendChild(raw('p', 'inc-summary', r.note));
      const tl = el('ol', 'inc-timeline');
      [...r.timeline].reverse().forEach(u => {
        const li = el('li');
        li.append(raw('span', 'inc-when', `${time(u.ts)} · ${u.user}`), raw('span', '', u.text));
        tl.appendChild(li);
      });
      c.appendChild(tl);
    }
    const row = el('div', 'docs-row-actions inc-row-actions');
    if (r.status === 'eingereicht' && data.can_approve) row.append(button('Freigeben', 'text-btn', () => act(r, 'approve')), button('Ablehnen', 'text-btn danger', () => ask(row, 'Grund der Ablehnung', 'Ablehnen', note => act(r, 'reject', note))));
    if (r.status === 'eingereicht') row.appendChild(button('Zurückziehen', 'text-btn', () => act(r, 'withdraw')));
    if (r.status === 'umsetzung' && (data.can_approve || data.can_manage) && !r.task_id) row.append(button('Erledigt', 'text-btn', () => act(r, 'complete')), button('Fehlgeschlagen', 'text-btn danger', () => ask(row, 'Grund', 'Speichern', note => act(r, 'fail', note))));
    if (row.childElementCount) c.appendChild(row);
    return c;
  }

  function render() {
    $('sv-summary').textContent = data.waiting ? `${data.waiting} ${t('warten auf Freigabe')}` : '';
    renderItems();
    const box = $('sv-requests');
    box.innerHTML = '';
    data.requests.forEach(r => box.appendChild(requestCard(r)));
    if (!data.requests.length) box.appendChild(el('p', 'field-hint', t('Keine Anfragen.')));
  }

  async function load() { if (window.PiAuth) await window.PiAuth.ready; try { data = await api('/api/service-catalog'); render(); } catch (e) { /* not signed in */ } }
  $('sv-add').addEventListener('click', () => itemForm(null));
  load();
})();
