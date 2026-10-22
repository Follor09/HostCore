/**
 * PiCore Dashboard - documentation page: hardware inventory, operations journal,
 * runbooks (checklists with recorded runs) and service docs ("Steckbriefe").
 */
(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const state = { ct: null, sn: null, inv: null, journal: null, runbooks: null, sd: null, editInv: null, editJr: null, editRb: null, editSd: null, openRun: null };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (res.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const fmtDay = iso => iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' }) : '–';
  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtMoney = v => v == null ? '–' : v.toLocaleString(LOCALE, { style: 'currency', currency: 'EUR' });
  const toLocalInput = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

  function button(label, cls, onClick, perm) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    if (perm) { b.dataset.perm = perm; if (!can(perm)) b.hidden = true; }
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function confirmButton(label, cls, action, perm) {
    return button(label, cls, b => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t('Sicher?');
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      action(b);
    }, perm);
  }

  // Small form builder: [{key, label, type, value, options, required, full, max}]
  function buildForm(fields, onSubmit, onCancel, submitLabel) {
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const inputs = {};
    fields.forEach(f => {
      const wrap = el('label', `login-field${f.full ? ' full' : ''}`);
      wrap.appendChild(el('span', '', t(f.label)));
      let input;
      if (f.type === 'select') {
        input = el('select');
        f.options.forEach(([v, l, rawLabel]) => {
          const o = el('option', '', rawLabel ? l : t(l));
          o.value = v;
          if (rawLabel) o.setAttribute('data-no-i18n', '');
          input.appendChild(o);
        });
      } else if (f.type === 'textarea') {
        input = el('textarea');
        input.rows = f.rows || 3;
      } else {
        input = el('input');
        input.type = f.type || 'text';
        if (f.step) input.step = f.step;
      }
      if (f.max) input.maxLength = f.max;
      if (f.placeholder) input.placeholder = t(f.placeholder);
      if (f.required) input.required = true;
      input.value = f.value == null ? '' : String(f.value);
      inputs[f.key] = input;
      wrap.appendChild(input);
      form.appendChild(wrap);
    });
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t(submitLabel || 'Speichern'));
    save.type = 'submit';
    actions.append(save, button('Abbrechen', 'btn-secondary', onCancel));
    form.appendChild(actions);
    form.addEventListener('submit', e => {
      e.preventDefault();
      const values = {};
      Object.entries(inputs).forEach(([k, i]) => { values[k] = i.value; });
      onSubmit(values, save);
    });
    form.addEventListener('keydown', e => { if (e.key === 'Escape') onCancel(); });
    setTimeout(() => form.querySelector('input, textarea')?.focus(), 30);
    return form;
  }

  // ---------------------------------------------------------------------------
  // Attachments (invoices, manuals, photos …) for inventory, contracts and service docs
  // ---------------------------------------------------------------------------
  const attCounts = { inventory: {}, contracts: {}, servicedocs: {} };
  const attKey = id => String(id).replace(/:/g, '_');
  const fmtSize = b => (b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.ceil(b / 1024))} KB`);

  async function loadAttCounts() {
    await Promise.all(Object.keys(attCounts).map(async k => {
      try { attCounts[k] = (await api(`/api/docs/attachments/${k}`)).counts || {}; } catch (e) { /* ignore */ }
    }));
  }

  function attButton(kind, id, panelHost) {
    const n = attCounts[kind][attKey(id)] || 0;
    return button(n ? `Anhänge (${n})` : 'Anhänge', 'text-btn', () => toggleAttachments(kind, id, panelHost()));
  }

  async function toggleAttachments(kind, id, host) {
    const existing = host.querySelector('.att-panel');
    if (existing) { existing.remove(); return; }
    const panel = el('div', 'att-panel');
    host.appendChild(panel);
    const base = `/api/docs/attachments/${kind}/${encodeURIComponent(id)}`;
    const draw = files => {
      panel.innerHTML = '';
      attCounts[kind][attKey(id)] = files.length;
      if (!files.length) panel.appendChild(el('p', 'field-hint', t('Noch keine Anhänge.')));
      files.forEach(f => {
        const row = el('div', 'att-row');
        const a = raw('a', 'att-name', f.name);
        a.href = `${base}/file?name=${encodeURIComponent(f.name)}`;
        a.target = '_blank';
        a.rel = 'noopener';
        row.append(a, el('span', 'att-meta', `${fmtSize(f.size)} · ${fmtTs(f.mtime)}`));
        const dl = el('a', 'text-btn', t('Herunterladen'));
        dl.href = `${base}/file?name=${encodeURIComponent(f.name)}&download=1`;
        row.appendChild(dl);
        row.appendChild(confirmButton('Löschen', 'text-btn danger', async () => {
          try { draw((await api(`${base}/delete?name=${encodeURIComponent(f.name)}`, {})).files); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
        panel.appendChild(row);
      });
      if (can('notes.edit')) {
        const label = el('label', 'att-upload');
        const input = el('input');
        input.type = 'file';
        input.multiple = true;
        input.hidden = true;
        label.append(input, el('span', 'btn-secondary', t('Dateien anhängen')), el('span', 'field-hint', t('PDF, Bilder, Text, ZIP – höchstens 25 MB')));
        input.addEventListener('change', async () => {
          for (const file of input.files) {
            try {
              const res = await fetch(`${base}?name=${encodeURIComponent(file.name)}`, { method: 'PUT', body: file });
              const data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
              draw(data.files);
            } catch (err) { toast(`${file.name}: ${err.message}`, 'error'); }
          }
          input.value = '';
        });
        panel.appendChild(label);
      }
    };
    try { draw((await api(base)).files); } catch (err) { panel.appendChild(el('p', 'field-hint', err.message)); }
  }

  // Table rows get the panel in an extra row below them
  function rowPanelHost(row, cols) {
    return () => {
      let next = row.nextElementSibling;
      if (!next || !next.classList.contains('att-host')) {
        next = document.createElement('tr');
        next.className = 'att-host';
        const c = next.insertCell();
        c.colSpan = cols;
        row.after(next);
      }
      const cell = next.cells[0];
      setTimeout(() => { if (!cell.querySelector('.att-panel')) next.remove(); }, 0);
      return cell;
    };
  }

  // ---------------------------------------------------------------------------
  // Inventory
  // ---------------------------------------------------------------------------
  const WARRANTY = { ok: 'good', soon: 'warning', expired: '' };

  function renderInventory() {
    const d = state.inv;
    const cat = $('inv-cat');
    if (cat.options.length === 1) d.categories.forEach(c => { const o = el('option', '', t(c)); o.value = c; cat.appendChild(o); });
    $('inv-summary').textContent = `${d.items.length} ${t('Geräte')} · ${fmtMoney(d.total_price)}${d.warranty_soon ? ` · ${d.warranty_soon} ${t('Garantie läuft bald ab')}` : ''}`;
    const q = $('inv-q').value.trim().toLowerCase();
    const items = d.items.filter(i => (!cat.value || i.category === cat.value)
      && (!q || [i.name, i.manufacturer, i.model, i.serial, i.location, i.notes].some(v => (v || '').toLowerCase().includes(q))));
    const table = $('inv-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Gerät', 'Kategorie', 'Standort', 'Gekauft', 'Garantie', 'Netzwerk', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    if (!items.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 7;
      c.className = 'docs-empty';
      c.textContent = d.items.length ? t('Keine Treffer.') : t('Noch keine Geräte erfasst.');
    }
    items.forEach(i => {
      const row = body.insertRow();
      const name = row.insertCell();
      name.appendChild(raw('b', '', i.name));
      const sub = [i.manufacturer, i.model].filter(Boolean).join(' ');
      if (sub || i.serial) name.appendChild(raw('div', 'field-hint', [sub, i.serial && `S/N ${i.serial}`].filter(Boolean).join(' · ')));
      if (i.notes) name.title = i.notes;
      row.insertCell().textContent = t(i.category);
      row.insertCell().appendChild(raw('span', '', i.location || '–'));
      row.insertCell().textContent = [i.purchased && fmtDay(i.purchased), i.price != null && fmtMoney(i.price)].filter(Boolean).join(' · ') || '–';
      const w = row.insertCell();
      if (i.warranty_until) {
        const label = i.warranty === 'expired' ? `${t('abgelaufen')} ${fmtDay(i.warranty_until)}`
          : i.warranty === 'soon' ? `${t('noch')} ${i.warranty_days} ${t('Tage')}` : `${t('bis')} ${fmtDay(i.warranty_until)}`;
        w.appendChild(el('span', `health-tag ${WARRANTY[i.warranty]}`, label));
      } else w.textContent = '–';
      const net = row.insertCell();
      if (i.device) {
        net.appendChild(el('span', `health-tag ${i.device.online ? 'good' : ''}`, i.device.online ? t('Online') : t('Offline')));
        if (i.device.ip) net.appendChild(raw('div', 'field-hint', i.device.ip));
      } else net.textContent = '–';
      const act = el('div', 'docs-row-actions');
      row.insertCell().appendChild(act);
      act.append(attButton('inventory', i.id, rowPanelHost(row, 7)), button('Bearbeiten', 'text-btn', () => openInvForm(i), 'notes.edit'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          try { state.inv = await api(`/api/docs/inventory/${i.id}/delete`, {}); renderInventory(); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
    });
  }

  function openInvForm(item) {
    const d = state.inv;
    const wrap = $('inv-form-wrap');
    const v = item || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', item ? t('Gerät bearbeiten') : t('Neues Gerät')));
    wrap.appendChild(buildForm([
      { key: 'name', label: 'Name', value: v.name, required: true, max: 80, placeholder: 'z. B. Raspberry Pi 5 (Server)' },
      { key: 'category', label: 'Kategorie', type: 'select', value: v.category || 'Raspberry Pi', options: d.categories.map(c => [c, c]) },
      { key: 'manufacturer', label: 'Hersteller', value: v.manufacturer, max: 60 },
      { key: 'model', label: 'Modell', value: v.model, max: 80 },
      { key: 'serial', label: 'Seriennummer', value: v.serial, max: 80 },
      { key: 'location', label: 'Standort', value: v.location, max: 60, placeholder: 'z. B. Rack, Büro' },
      { key: 'purchased', label: 'Kaufdatum', type: 'date', value: v.purchased },
      { key: 'price', label: 'Preis (€)', type: 'number', step: '0.01', value: v.price },
      { key: 'warranty_until', label: 'Garantie bis', type: 'date', value: v.warranty_until },
      { key: 'mac', label: 'Gerät im Netzwerk', type: 'select', value: v.mac || '',
        options: [['', '– keins –'], ...d.lan_devices.map(x => [x.mac, `${x.name}${x.ip ? ` (${x.ip})` : ''}`, true])] },
      { key: 'notes', label: 'Notizen', type: 'textarea', value: v.notes, full: true, max: 1000 }
    ], async (values, btn) => {
      btn.disabled = true;
      try {
        state.inv = await api(item ? `/api/docs/inventory/${item.id}` : '/api/docs/inventory', values);
        closeInvForm();
        renderInventory();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    }, closeInvForm));
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeInvForm() {
    $('inv-form-wrap').hidden = true;
    $('inv-form-wrap').innerHTML = '';
  }

  // ---------------------------------------------------------------------------
  // Journal
  // ---------------------------------------------------------------------------
  const JR_LEVEL = { 'Störung': 'critical', 'Wartung': 'warning', 'Änderung': '', 'Installation': 'good', 'Notiz': '' };

  function renderJournal() {
    const d = state.journal;
    [$('jr-category'), $('jr-filter-cat')].forEach(sel => {
      if (sel.dataset.filled) return;
      d.categories.forEach(c => { const o = el('option', '', t(c)); o.value = c; sel.appendChild(o); });
      sel.dataset.filled = '1';
    });
    $('jr-summary').textContent = `${d.total} ${d.total === 1 ? t('Eintrag') : t('Einträge')}`;
    const list = $('jr-list');
    list.innerHTML = '';
    if (!d.entries.length) list.appendChild(el('li', 'docs-empty', t('Keine Einträge.')));
    d.entries.forEach(e => {
      const li = el('li', 'journal-item');
      const meta = el('div', 'journal-meta');
      meta.append(el('span', 'journal-time', fmtTs(e.ts)), el('span', `health-tag ${JR_LEVEL[e.category] || ''}`, t(e.category)));
      if (e.ref) meta.appendChild(raw('span', 'journal-ref', e.ref));
      if (e.user) meta.appendChild(raw('span', 'journal-user', e.user));
      const body = el('div', 'journal-body');
      body.appendChild(raw('b', '', e.title));
      if (e.text) body.appendChild(raw('p', 'journal-text', e.text));
      const act = el('div', 'journal-actions');
      act.append(button('Bearbeiten', 'text-btn', () => editJournal(e), 'notes.edit'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          try { await api(`/api/docs/journal/${e.id}/delete`, {}); loadJournal(); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
      li.append(meta, body, act);
      list.appendChild(li);
    });
  }

  function resetJournalForm() {
    state.editJr = null;
    $('jr-form').reset();
    $('jr-ts').value = toLocalInput(new Date());
    $('jr-category').value = 'Änderung';
    $('jr-submit').textContent = t('Eintragen');
    $('jr-cancel').hidden = true;
  }

  function editJournal(e) {
    state.editJr = e.id;
    $('jr-title').value = e.title;
    $('jr-category').value = e.category;
    $('jr-ts').value = toLocalInput(new Date(e.ts * 1000));
    $('jr-ref').value = e.ref || '';
    $('jr-text').value = e.text || '';
    $('jr-submit').textContent = t('Speichern');
    $('jr-cancel').hidden = false;
    $('jr-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    $('jr-title').focus();
  }

  async function loadJournal() {
    try {
      const qs = new URLSearchParams({ q: $('jr-q').value.trim(), category: $('jr-filter-cat').value });
      state.journal = await api(`/api/docs/journal?${qs}`);
      renderJournal();
    } catch (err) { toast(err.message, 'error'); }
  }

  // ---------------------------------------------------------------------------
  // Runbooks
  // ---------------------------------------------------------------------------
  function renderRunbooks() {
    const list = $('rb-list');
    list.innerHTML = '';
    const items = state.runbooks.runbooks;
    if (!items.length) list.appendChild(el('p', 'field-hint', t('Noch keine Runbooks. Typische Kandidaten: Pi neu aufsetzen, Backup wiederherstellen, Zertifikat erneuern.')));
    items.forEach(rb => list.appendChild(runbookCard(rb)));
  }

  function runbookCard(rb) {
    const card = el('div', `runbook-card${rb.running ? ' running' : ''}`);
    card.dataset.id = rb.id;
    const head = el('div', 'runbook-head');
    const title = el('div');
    title.appendChild(raw('b', 'runbook-title', rb.title));
    const meta = [`${rb.steps.length} ${t('Schritte')}`, rb.last_done ? `${t('zuletzt')} ${fmtTs(rb.last_done)}` : t('noch nie ausgeführt')];
    title.appendChild(el('div', 'field-hint', meta.join(' · ')));
    head.appendChild(title);
    if (rb.running) head.appendChild(el('span', 'health-tag warning', t('Läuft')));
    card.appendChild(head);
    if (rb.description) card.appendChild(raw('p', 'runbook-desc', rb.description));

    const open = state.openRun === rb.id || !!rb.running;
    if (rb.running && open) card.appendChild(runPanel(rb));
    else {
      const ol = el('ol', 'runbook-steps');
      rb.steps.slice(0, 4).forEach(s => ol.appendChild(raw('li', '', s.text)));
      if (rb.steps.length > 4) ol.appendChild(el('li', 'field-hint', `… ${rb.steps.length - 4} ${t('weitere')}`));
      card.appendChild(ol);
    }

    const act = el('div', 'runbook-actions');
    if (!rb.running) act.appendChild(button('Durchlauf starten', 'btn-primary', () => runAction(rb.id, { action: 'start' }), 'notes.edit'));
    act.append(button('Bearbeiten', 'btn-secondary', () => openRbForm(rb), 'notes.edit'),
      confirmButton('Löschen', 'btn-danger', async () => {
        try { state.runbooks = await api(`/api/docs/runbooks/${rb.id}/delete`, {}); renderRunbooks(); } catch (err) { toast(err.message, 'error'); }
      }, 'notes.edit'));
    card.appendChild(act);

    const runs = (rb.runs || []).filter(r => r.status !== 'running').slice(-5).reverse();
    if (runs.length) {
      const hist = el('details', 'runbook-history');
      hist.appendChild(el('summary', '', `${t('Letzte Durchläufe')} (${runs.length})`));
      const ul = el('ul');
      runs.forEach(r => {
        const done = Object.keys(r.checked || {}).length;
        const li = el('li');
        li.append(el('span', `health-tag ${r.status === 'done' ? 'good' : ''}`, r.status === 'done' ? t('Abgeschlossen') : t('Abgebrochen')),
          raw('span', '', ` ${fmtTs(r.finished || r.started)} · ${r.finished_by || r.user || ''} · ${done}/${r.total || rb.steps.length}`));
        if (r.note) li.appendChild(raw('div', 'field-hint', r.note));
        ul.appendChild(li);
      });
      hist.appendChild(ul);
      card.appendChild(hist);
    }
    return card;
  }

  function runPanel(rb) {
    const run = rb.running;
    const panel = el('div', 'run-panel');
    const done = rb.steps.filter(s => run.checked[s.id]).length;
    const progress = el('div', 'run-progress');
    const bar = el('i');
    bar.style.width = `${(done / rb.steps.length) * 100}%`;
    progress.appendChild(bar);
    panel.append(el('div', 'field-hint', `${t('Gestartet')} ${fmtTs(run.started)} ${t('von')} ${run.user} · ${done}/${rb.steps.length} ${t('erledigt')}`), progress);
    const ol = el('ol', 'run-steps');
    rb.steps.forEach(s => {
      const li = el('li', run.checked[s.id] ? 'done' : '');
      const label = el('label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = !!run.checked[s.id];
      cb.disabled = !can('notes.edit');
      cb.addEventListener('change', () => runAction(rb.id, { action: 'check', step: s.id, checked: cb.checked }));
      label.append(cb, raw('span', '', s.text));
      li.appendChild(label);
      if (run.checked[s.id]) li.appendChild(raw('span', 'field-hint run-who', `${run.checked[s.id].user} · ${new Date(run.checked[s.id].ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })}`));
      ol.appendChild(li);
    });
    panel.appendChild(ol);
    const note = el('textarea', 'run-note');
    note.rows = 2;
    note.placeholder = t('Notiz zum Durchlauf (optional)');
    panel.appendChild(note);
    const act = el('div', 'runbook-actions');
    act.append(button(done < rb.steps.length ? 'Abschließen (unvollständig)' : 'Abschließen', 'btn-primary', () => runAction(rb.id, { action: 'finish', note: note.value }, true), 'notes.edit'),
      confirmButton('Abbrechen', 'btn-secondary', () => runAction(rb.id, { action: 'abort', note: note.value }), 'notes.edit'));
    panel.appendChild(act);
    return panel;
  }

  async function runAction(id, body, finished) {
    try {
      state.runbooks = await api(`/api/docs/runbooks/${id}/run`, body);
      state.openRun = body.action === 'start' ? id : state.openRun;
      renderRunbooks();
      if (finished) { toast(t('Durchlauf abgeschlossen und im Betriebstagebuch vermerkt.'), 'success'); loadJournal(); }
    } catch (err) { toast(err.message, 'error'); loadRunbooks(); }
  }

  function openRbForm(rb) {
    const wrap = $('rb-form-wrap');
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', rb ? t('Runbook bearbeiten') : t('Neues Runbook')));
    wrap.appendChild(buildForm([
      { key: 'title', label: 'Titel', value: rb && rb.title, required: true, max: 100, placeholder: 'z. B. Pi neu aufsetzen' },
      { key: 'description', label: 'Beschreibung', value: rb && rb.description, max: 1000 },
      { key: 'steps', label: 'Schritte – einer pro Zeile', type: 'textarea', rows: 8, full: true, required: true,
        value: rb ? rb.steps.map(s => s.text).join('\n') : '' }
    ], async (values, btn) => {
      btn.disabled = true;
      try {
        state.runbooks = await api(rb ? `/api/docs/runbooks/${rb.id}` : '/api/docs/runbooks', values);
        closeRbForm();
        renderRunbooks();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    }, closeRbForm));
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeRbForm() {
    $('rb-form-wrap').hidden = true;
    $('rb-form-wrap').innerHTML = '';
  }

  async function loadRunbooks() {
    try { state.runbooks = await api('/api/docs/runbooks'); renderRunbooks(); } catch (err) { toast(err.message, 'error'); }
  }

  // ---------------------------------------------------------------------------
  // Service docs
  // ---------------------------------------------------------------------------
  const KIND = { service: 'Dienst', health: 'Web-Dienst', stack: 'Compose-Stack', custom: 'Eigener Eintrag' };
  const LINK_FIELDS = ['url', 'docs_url'];

  function renderServiceDocs() {
    const d = state.sd;
    const documented = new Set(d.docs.map(x => x.target));
    const sel = $('sd-new-target');
    sel.innerHTML = '';
    d.available.filter(a => !documented.has(a.target)).forEach(a => {
      const o = el('option', '', `${a.name} (${t(KIND[a.kind])})`);
      o.value = a.target;
      o.setAttribute('data-no-i18n', '');
      sel.appendChild(o);
    });
    const custom = el('option', '', t('Eigener Eintrag …'));
    custom.value = '';
    sel.appendChild(custom);

    const list = $('sd-list');
    list.innerHTML = '';
    if (!d.docs.length) list.appendChild(el('p', 'field-hint', t('Noch keine Steckbriefe.')));
    d.docs.forEach(doc => {
      const card = el('div', `servicedoc-card${doc.present ? '' : ' missing'}`);
      card.id = `sd-${doc.target.replace(/[^\w-]/g, '_')}`;
      const head = el('div', 'runbook-head');
      const title = el('div');
      title.append(raw('b', 'runbook-title', doc.name), el('div', 'field-hint', t(KIND[doc.target.split(':')[0]] || '') + (doc.present ? '' : ` · ${t('nicht mehr vorhanden')}`)));
      head.appendChild(title);
      card.appendChild(head);
      const dl = el('dl', 'servicedoc-fields');
      d.fields.forEach(f => {
        if (!doc[f.key]) return;
        dl.appendChild(el('dt', '', t(f.label)));
        const dd = el('dd');
        if (LINK_FIELDS.includes(f.key)) {
          const a = raw('a', '', doc[f.key]);
          a.href = doc[f.key];
          a.target = '_blank';
          a.rel = 'noopener';
          dd.appendChild(a);
        } else dd.appendChild(raw('span', '', doc[f.key]));
        dl.appendChild(dd);
      });
      card.appendChild(dl);
      if (doc.updated) card.appendChild(el('div', 'field-hint', `${t('Stand')} ${fmtTs(doc.updated)}${doc.updated_by ? ` · ${doc.updated_by}` : ''}`));
      const act = el('div', 'runbook-actions');
      act.append(attButton('servicedocs', doc.target, () => card), button('Bearbeiten', 'btn-secondary', () => openSdForm(doc.target, doc), 'notes.edit'),
        confirmButton('Löschen', 'btn-danger', async () => {
          try { state.sd = await api('/api/docs/services/delete', { target: doc.target }); renderServiceDocs(); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
      card.appendChild(act);
      list.appendChild(card);
    });
  }

  function openSdForm(target, doc) {
    const d = state.sd;
    const avail = d.available.find(a => a.target === target);
    const v = doc || { name: avail ? avail.name : (target || '').split(':').slice(1).join(':') };
    const wrap = $('sd-form-wrap');
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', doc ? `${t('Steckbrief bearbeiten')}: ${doc.name}` : t('Neuer Steckbrief')));
    const fields = [{ key: 'name', label: 'Name', value: v.name, required: true, max: 80 }];
    const types = { purpose: 'textarea', notes: 'textarea' };
    const hints = { url: 'https://…', docs_url: 'https://…', ports: 'z. B. 8096/tcp', data: 'z. B. ~/picore-apps/jellyfin', backup: 'z. B. nachts per restic auf das NAS' };
    d.fields.forEach(f => fields.push({ key: f.key, label: f.label, value: v[f.key], type: types[f.key], placeholder: hints[f.key],
                                        full: f.key === 'notes' || f.key === 'purpose', rows: 2 }));
    wrap.appendChild(buildForm(fields, async (values, btn) => {
      btn.disabled = true;
      try {
        state.sd = await api('/api/docs/services', { ...values, target: target || '' });
        closeSdForm();
        renderServiceDocs();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    }, closeSdForm));
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeSdForm() {
    $('sd-form-wrap').hidden = true;
    $('sd-form-wrap').innerHTML = '';
  }

  // ---------------------------------------------------------------------------
  // Contracts & subscriptions
  // ---------------------------------------------------------------------------
  const CT_STATE = { ok: ['good', ''], notice: ['warning', 'Kündigungsfrist naht'], missed: ['critical', 'Frist verpasst'], ended: ['', 'Beendet'] };

  function renderContracts() {
    const d = state.ct;
    const cat = $('ct-cat');
    if (cat.options.length === 1) d.categories.forEach(c => { const o = el('option', '', t(c)); o.value = c; cat.appendChild(o); });
    $('ct-summary').textContent = `${fmtMoney(d.monthly_total)} ${t('pro Monat')} · ${fmtMoney(d.yearly_total)} ${t('pro Jahr')}${d.notice_soon ? ` · ${d.notice_soon} ${t('Fristen in 30 Tagen')}` : ''}`;
    const q = $('ct-q').value.trim().toLowerCase();
    const items = d.items.filter(c => (!cat.value || c.category === cat.value)
      && (!q || [c.name, c.provider, c.notes, c.category].some(v => (v || '').toLowerCase().includes(q))));
    const table = $('ct-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Vertrag', 'Kosten', 'Läuft bis', 'Kündigen bis', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    if (!items.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 5;
      c.className = 'docs-empty';
      c.textContent = d.items.length ? t('Keine Treffer.') : t('Noch keine Verträge erfasst.');
    }
    items.forEach(c => {
      const row = body.insertRow();
      const name = row.insertCell();
      name.className = 'wrap';
      const title = c.link ? el('a', 'lan-name', c.name) : raw('span', 'lan-name', c.name);
      if (c.link) { title.href = c.link; title.target = '_blank'; title.rel = 'noopener noreferrer'; title.setAttribute('data-no-i18n', ''); }
      name.append(title, raw('span', 'lan-sub', [c.provider, t(c.category)].filter(Boolean).join(' · ')));
      if (c.notes) name.appendChild(raw('span', 'lan-sub', c.notes));
      const cost = row.insertCell();
      cost.append(el('span', '', c.cost == null ? '–' : fmtMoney(c.cost)), el('span', 'lan-sub', t(c.interval_label)));
      const end = row.insertCell();
      end.append(el('span', '', fmtDay(c.next_end)), el('span', 'lan-sub', c.next_end ? t(c.auto_renew ? 'verlängert sich' : 'endet') : ''));
      const cancel = row.insertCell();
      const [cls, label] = CT_STATE[c.state];
      if (c.cancel_by && c.state !== 'ended') {
        cancel.appendChild(el('span', '', fmtDay(c.cancel_by)));
        if (c.notice_days) cancel.appendChild(el('span', 'lan-sub', `${c.notice_days} ${t('Tage Frist')}`));
        if (label) { cancel.append(' '); cancel.appendChild(el('span', `health-tag ${cls}`, t(label))); }
      } else cancel.appendChild(label ? el('span', `health-tag ${cls}`, t(label)) : el('span', '', '–'));
      const act = el('div', 'docs-row-actions');
      row.insertCell().appendChild(act);
      act.append(attButton('contracts', c.id, rowPanelHost(row, 5)), button('Bearbeiten', 'text-btn', () => openCtForm(c), 'notes.edit'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          try { state.ct = await api(`/api/docs/contracts/${c.id}/delete`, {}); renderContracts(); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
    });
  }

  function openCtForm(item) {
    const d = state.ct;
    const wrap = $('ct-form-wrap');
    const v = item || { interval: 'yearly', auto_renew: true, category: 'Domain' };
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', item ? t('Vertrag bearbeiten') : t('Neuer Vertrag')));
    wrap.appendChild(buildForm([
      { key: 'name', label: 'Name', value: v.name, required: true, max: 80, placeholder: 'z. B. example.de' },
      { key: 'provider', label: 'Anbieter', value: v.provider, max: 80, placeholder: 'z. B. INWX, Hetzner' },
      { key: 'category', label: 'Kategorie', type: 'select', value: v.category, options: d.categories.map(c => [c, c]) },
      { key: 'cost', label: 'Kosten (€)', type: 'number', step: '0.01', value: v.cost },
      { key: 'interval', label: 'Zahlung', type: 'select', value: v.interval, options: Object.entries(d.intervals) },
      { key: 'start', label: 'Beginn', type: 'date', value: v.start },
      { key: 'end', label: 'Läuft bis / nächste Verlängerung', type: 'date', value: v.end },
      { key: 'notice_days', label: 'Kündigungsfrist (Tage)', type: 'number', value: v.notice_days || 0 },
      { key: 'auto_renew', label: 'Danach', type: 'select', value: v.auto_renew === false ? '0' : '1',
        options: [['1', 'Verlängert sich automatisch'], ['0', 'Endet']] },
      { key: 'link', label: 'Link (Kundenkonto)', value: v.link, max: 300, placeholder: 'https://…' },
      { key: 'notes', label: 'Notizen', type: 'textarea', value: v.notes, full: true, max: 1000 }
    ], async (values, btn) => {
      btn.disabled = true;
      try {
        state.ct = await api(item ? `/api/docs/contracts/${item.id}` : '/api/docs/contracts', { ...values, auto_renew: values.auto_renew === '1' });
        closeCtForm();
        renderContracts();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    }, closeCtForm));
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeCtForm() {
    $('ct-form-wrap').hidden = true;
    $('ct-form-wrap').innerHTML = '';
  }

  // ---------------------------------------------------------------------------
  // Command snippets
  // ---------------------------------------------------------------------------
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // Plain http (no secure context): the old way
      const ta = el('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(t('Kopiert.'), 'success');
  }

  function renderSnippets() {
    const d = state.sn;
    const tagSel = $('sn-tag');
    const cur = tagSel.value;
    tagSel.innerHTML = '';
    [['', t('Alle Tags')], ...d.tags.map(x => [x, x])].forEach(([v, l]) => { const o = el('option', '', l); o.value = v; tagSel.appendChild(o); });
    tagSel.value = d.tags.includes(cur) ? cur : '';
    const q = $('sn-q').value.trim().toLowerCase();
    const list = $('sn-list');
    list.innerHTML = '';
    const items = d.items.filter(s => (!tagSel.value || (s.tags || []).includes(tagSel.value))
      && (!q || [s.title, s.command, s.description, ...(s.tags || [])].some(v => (v || '').toLowerCase().includes(q))));
    if (!items.length) list.appendChild(el('p', 'docs-empty', d.items.length ? t('Keine Treffer.') : t('Noch keine Befehle gespeichert.')));
    items.forEach(s => {
      const card = el('div', 'snippet-card');
      const head = el('div', 'snippet-head');
      head.appendChild(raw('strong', '', s.title));
      (s.tags || []).forEach(tag => head.appendChild(raw('span', 'health-tag', tag)));
      card.appendChild(head);
      if (s.description) card.appendChild(raw('p', 'field-hint', s.description));
      card.appendChild(raw('pre', 'snippet-code', s.command));
      const act = el('div', 'docs-row-actions');
      act.append(button('Kopieren', 'text-btn', () => copyText(s.command)),
        button('Bearbeiten', 'text-btn', () => openSnForm(s), 'notes.edit'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          try { state.sn = await api(`/api/docs/snippets/${s.id}/delete`, {}); renderSnippets(); } catch (err) { toast(err.message, 'error'); }
        }, 'notes.edit'));
      card.appendChild(act);
      list.appendChild(card);
    });
  }

  function openSnForm(item) {
    const wrap = $('sn-form-wrap');
    const v = item || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', item ? t('Befehl bearbeiten') : t('Neuer Befehl')));
    wrap.appendChild(buildForm([
      { key: 'title', label: 'Titel', value: v.title, required: true, max: 100, placeholder: 'z. B. Docker aufräumen' },
      { key: 'tags', label: 'Tags (mit Komma)', value: (v.tags || []).join(', '), max: 200, placeholder: 'docker, wartung' },
      { key: 'command', label: 'Befehl', type: 'textarea', rows: 4, value: v.command, full: true, required: true, max: 4000, placeholder: 'docker system prune -af --volumes' },
      { key: 'description', label: 'Beschreibung', type: 'textarea', rows: 2, value: v.description, full: true, max: 500 }
    ], async (values, btn) => {
      btn.disabled = true;
      try {
        state.sn = await api(item ? `/api/docs/snippets/${item.id}` : '/api/docs/snippets', values);
        closeSnForm();
        renderSnippets();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    }, closeSnForm));
    wrap.querySelector('textarea').classList.add('mono-input');
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function closeSnForm() {
    $('sn-form-wrap').hidden = true;
    $('sn-form-wrap').innerHTML = '';
  }

  // ---------------------------------------------------------------------------
  // Homelab costs
  // ---------------------------------------------------------------------------
  function renderCosts(d) {
    const tiles = $('cost-tiles');
    tiles.innerHTML = '';
    const tile = (label, value, sub) => {
      const box = el('div', 'nb-stat');
      box.append(el('span', 'nb-stat-value', value), el('span', 'nb-stat-label', t(label)));
      if (sub) box.appendChild(el('span', 'nb-stat-label', sub));
      tiles.appendChild(box);
    };
    tile('Gesamt pro Monat', fmtMoney(d.total.monthly), `${fmtMoney(d.total.yearly)} ${t('pro Jahr')}`);
    tile('Strom pro Monat', d.power.available ? fmtMoney(d.power.month) : '–',
      d.power.available ? `${t('hochgerechnet')} · Ø ${String(d.power.avg_w).replace('.', LOCALE.startsWith('de') ? ',' : '.')} W` : t('nur mit Messung (Pi 5)'));
    tile('Verträge & Abos', fmtMoney(d.contracts.monthly), `${d.contracts.count} ${t('aktiv')} · ${fmtMoney(d.contracts.yearly)} ${t('pro Jahr')}`);
    tile('Hardware pro Monat', fmtMoney(d.hardware.monthly_depreciation), t('auf 3 Jahre verteilt'));
    tile('Hardware gesamt', fmtMoney(d.hardware.total), `${fmtMoney(d.hardware.this_year)} ${t('dieses Jahr')}`);

    const chart = $('cost-chart');
    chart.innerHTML = '';
    const max = Math.max(1, ...d.history.map(h => h.power + h.contracts + h.hardware));
    d.history.forEach(h => {
      const col = el('div', 'cost-col');
      const total = h.power + h.contracts + h.hardware;
      col.title = `${h.month}: ${fmtMoney(total)}\n${t('Strom')} ${fmtMoney(h.power)} · ${t('Verträge')} ${fmtMoney(h.contracts)} · ${t('Hardware')} ${fmtMoney(h.hardware)}`;
      const stack = el('div', 'cost-stack');
      [['hardware', h.hardware], ['contracts', h.contracts], ['power', h.power]].forEach(([k, v]) => {
        if (!v) return;
        const seg = el('div', `cost-seg c-${k}`);
        seg.style.height = `${(v / max) * 100}%`;
        stack.appendChild(seg);
      });
      const [y, m] = h.month.split('-');
      col.append(el('span', 'cost-total', total ? fmtMoney(total).replace(/,00\s?€|\.00/, '') : ''), stack,
        el('span', 'cost-month', new Date(+y, +m - 1, 1).toLocaleDateString(LOCALE, { month: 'short' })));
      chart.appendChild(col);
    });

    const det = $('cost-details');
    det.innerHTML = '';
    const table = el('table', 'data-table');
    const head = table.createTHead().insertRow();
    ['Posten', 'pro Monat', 'pro Jahr'].forEach(x => head.appendChild(el('th', '', t(x))));
    const body = table.createTBody();
    const row = (label, m, raw) => {
      const r = body.insertRow();
      const c = r.insertCell();
      c.textContent = raw ? label : t(label);
      if (raw) c.setAttribute('data-no-i18n', '');
      r.insertCell().textContent = m == null ? '–' : fmtMoney(m);
      r.insertCell().textContent = m == null ? '–' : fmtMoney(m * 12);
    };
    row('Strom', d.power.available ? d.power.month : null);
    Object.entries(d.contracts.by_category).forEach(([cat, v]) => row(`${t('Verträge')}: ${t(cat)}`, v, true));
    row('Hardware (abgeschrieben)', d.hardware.monthly_depreciation);
    const sum = body.insertRow();
    sum.className = 'cost-sum';
    sum.insertCell().textContent = t('Gesamt');
    sum.insertCell().textContent = fmtMoney(d.total.monthly);
    sum.insertCell().textContent = fmtMoney(d.total.yearly);
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(table);
    det.appendChild(wrap);
    const years = Object.entries(d.hardware.by_year);
    if (years.length) det.appendChild(el('p', 'field-hint', `${t('Hardware-Käufe')}: ${years.map(([y, v]) => `${y} ${fmtMoney(v)}`).join(' · ')}`));
  }

  // #steckbrief=stack:jellyfin from the containers page: show it, or start one
  function followHash() {
    const m = location.hash.match(/^#steckbrief=(.+)$/);
    if (!m || !state.sd) return;
    const target = decodeURIComponent(m[1]);
    const doc = state.sd.docs.find(x => x.target === target);
    if (doc) {
      const card = document.getElementById(`sd-${target.replace(/[^\w-]/g, '_')}`);
      card?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card?.classList.add('flash');
    } else if (can('notes.edit')) {
      $('steckbriefe').scrollIntoView({ behavior: 'smooth' });
      openSdForm(target, null);
    }
  }

  // ---------------------------------------------------------------------------
  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }

    $('inv-add').addEventListener('click', () => openInvForm(null));
    $('inv-q').addEventListener('input', renderInventory);
    $('inv-cat').addEventListener('change', renderInventory);
    $('inv-filters').addEventListener('submit', e => e.preventDefault());

    resetJournalForm();
    $('jr-form').addEventListener('submit', async e => {
      e.preventDefault();
      const body = { title: $('jr-title').value, category: $('jr-category').value, ref: $('jr-ref').value, text: $('jr-text').value,
                     ts: $('jr-ts').value ? Math.round(new Date($('jr-ts').value).getTime() / 1000) : undefined };
      try {
        await api(state.editJr ? `/api/docs/journal/${state.editJr}` : '/api/docs/journal', body);
        resetJournalForm();
        loadJournal();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
    $('jr-cancel').addEventListener('click', resetJournalForm);
    let timer = 0;
    $('jr-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(loadJournal, 250); });
    $('jr-filter-cat').addEventListener('change', loadJournal);
    $('jr-filters').addEventListener('submit', e => e.preventDefault());

    $('rb-add').addEventListener('click', () => openRbForm(null));
    $('ct-add').addEventListener('click', () => openCtForm(null));
    $('ct-q').addEventListener('input', () => state.ct && renderContracts());
    $('ct-cat').addEventListener('change', () => state.ct && renderContracts());
    $('ct-filters').addEventListener('submit', e => e.preventDefault());
    $('sn-add').addEventListener('click', () => openSnForm(null));
    $('sn-q').addEventListener('input', () => state.sn && renderSnippets());
    $('sn-tag').addEventListener('change', () => state.sn && renderSnippets());
    $('sn-filters').addEventListener('submit', e => e.preventDefault());
    $('sd-add').addEventListener('click', () => openSdForm($('sd-new-target').value, null));
    window.addEventListener('hashchange', followHash);

    // Highlight the section in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    await loadAttCounts();
    try { state.inv = await api('/api/docs/inventory'); renderInventory(); } catch (err) { toast(err.message, 'error'); }
    loadJournal();
    loadRunbooks();
    try { state.sd = await api('/api/docs/services'); renderServiceDocs(); followHash(); } catch (err) { toast(err.message, 'error'); }
    try { state.ct = await api('/api/docs/contracts'); renderContracts(); } catch (err) { toast(err.message, 'error'); }
    try { state.sn = await api('/api/docs/snippets'); renderSnippets(); } catch (err) { toast(err.message, 'error'); }
    try { renderCosts(await api('/api/costs')); } catch (err) { toast(err.message, 'error'); }
    // Deep links from the search (#vertraege, #befehle) arrive before these sections are filled
    if (/^#(vertraege|befehle|kosten)$/.test(location.hash)) document.querySelector(location.hash)?.scrollIntoView();
  }

  init();
})();
