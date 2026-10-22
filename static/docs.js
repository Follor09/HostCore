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
  const state = { inv: null, journal: null, runbooks: null, sd: null, editInv: null, editJr: null, editRb: null, editSd: null, openRun: null };

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
      act.append(button('Bearbeiten', 'text-btn', () => openInvForm(i), 'notes.edit'),
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
      act.append(button('Bearbeiten', 'btn-secondary', () => openSdForm(doc.target, doc), 'notes.edit'),
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
    $('sd-add').addEventListener('click', () => openSdForm($('sd-new-target').value, null));
    window.addEventListener('hashchange', followHash);

    // Highlight the section in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    try { state.inv = await api('/api/docs/inventory'); renderInventory(); } catch (err) { toast(err.message, 'error'); }
    loadJournal();
    loadRunbooks();
    try { state.sd = await api('/api/docs/services'); renderServiceDocs(); followHash(); } catch (err) { toast(err.message, 'error'); }
  }

  init();
})();
