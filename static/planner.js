/**
 * HostCore Dashboard - planning cards: homelab kanban (card + full board with drag & drop)
 * and recurring reminders (card + sheet with form). Both are shared by every device.
 * Fires "pi:planner" with what's due.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('kanban-card') && !$('reminders-card')) return;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const state = { board: null, reminders: null, editing: null, dragId: null, reminderEdit: null };

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
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  function openSheet(id) {
    const m = $(id);
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
  }

  function closeSheet(id) {
    const m = $(id);
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }

  const fmtDate = iso => new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit' });
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

  function relDay(days, iso) {
    if (days < -1) return `seit ${-days} Tagen überfällig`;
    if (days === -1) return 'seit gestern überfällig';
    if (days === 0) return 'heute';
    if (days === 1) return 'morgen';
    if (days <= 6) return `in ${days} Tagen`;
    return fmtDate(iso);
  }

  function emit() {
    const r = state.reminders, b = state.board;
    window.dispatchEvent(new CustomEvent('pi:planner', { detail: {
      remindersDue: r ? r.items.filter(i => i.days <= 0) : [],
      overdue: r ? r.items.filter(i => i.days < 0).length : 0,
      kanbanDue: b ? b.cards.filter(c => c.due_state) : [],
      doing: b ? b.cards.filter(c => c.column === 'doing').length : 0
    } }));
  }

  // ---------------------------------------------------------------------------
  // Kanban
  // ---------------------------------------------------------------------------
  const COL_TAG = { idea: '', doing: 'warning', done: 'good' };

  function labelChip(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    const c = raw('span', 'kb-label', name);
    c.style.setProperty('--label-h', h);
    return c;
  }

  function dueChip(card) {
    if (!card.due) return null;
    const c = el('span', `kb-due ${card.due_state || ''}`, card.due_state === 'overdue' ? `${t('überfällig')} · ${fmtDate(card.due)}`
      : card.due_state === 'today' ? t('heute fällig') : fmtDate(card.due));
    return c;
  }

  function renderMini() {
    const b = state.board;
    const mini = $('kanban-mini');
    mini.innerHTML = '';
    const counts = el('div', 'kb-counts');
    b.columns.forEach(c => {
      const pill = el('span', `kb-count col-${c.id}`);
      pill.append(raw('b', '', String(c.count)), el('span', '', t(c.name)));
      counts.appendChild(pill);
    });
    mini.appendChild(counts);
    const doing = b.cards.filter(c => c.column === 'doing');
    const due = b.cards.filter(c => c.column !== 'doing' && c.due_state);
    const list = el('ul', 'kb-mini-list');
    [...due, ...doing].slice(0, 4).forEach(c => {
      const li = el('li');
      li.appendChild(el('span', `kb-dot col-${c.column}`));
      li.appendChild(raw('span', 'kb-mini-title', c.title));
      const chip = dueChip(c);
      if (chip) li.appendChild(chip);
      li.addEventListener('click', () => openBoard(c.id));
      list.appendChild(li);
    });
    if (!list.childElementCount) list.appendChild(el('li', 'kb-empty', b.cards.length ? t('Gerade nichts in Arbeit.') : t('Keine Aufgaben vorhanden.')));
    mini.appendChild(list);
    $('kanban-sub').textContent = t('Offen · In Arbeit · Erledigt');
  }

  function cardEl(c) {
    const card = el('div', `kb-card${c.due_state ? ` ${c.due_state}` : ''}`);
    card.dataset.id = c.id;
    const editable = can('notes.edit');
    if (state.editing === c.id && editable) return editorEl(c, card);
    card.draggable = editable;
    card.tabIndex = 0;
    card.appendChild(raw('div', 'kb-card-title', c.title));
    if (c.desc) card.appendChild(raw('div', 'kb-card-desc', c.desc));
    const meta = el('div', 'kb-card-meta');
    (c.labels || []).forEach(l => meta.appendChild(labelChip(l)));
    const chip = dueChip(c);
    if (chip) meta.appendChild(chip);
    if (meta.childElementCount) card.appendChild(meta);
    if (editable) {
      const moves = el('div', 'kb-moves');
      const cols = state.board.columns.map(x => x.id);
      const i = cols.indexOf(c.column);
      if (i > 0) moves.appendChild(moveBtn('←', `${t('Nach')} „${t(state.board.columns[i - 1].name)}“`, () => move(c.id, cols[i - 1])));
      if (i < cols.length - 1) moves.appendChild(moveBtn('→', `${t('Nach')} „${t(state.board.columns[i + 1].name)}“`, () => move(c.id, cols[i + 1])));
      card.appendChild(moves);
      card.addEventListener('click', e => { if (!e.target.closest('.kb-moves')) { state.editing = c.id; renderBoard(); } });
      card.addEventListener('keydown', e => { if (e.key === 'Enter') { state.editing = c.id; renderBoard(); } });
      card.addEventListener('dragstart', e => {
        state.dragId = c.id;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', c.id);
        requestAnimationFrame(() => card.classList.add('dragging'));
      });
      card.addEventListener('dragend', () => { state.dragId = null; card.classList.remove('dragging'); clearDrop(); });
    }
    return card;
  }

  function moveBtn(label, title, onClick) {
    const b = el('button', 'kb-move', label);
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', title);
    b.addEventListener('click', e => { e.stopPropagation(); onClick(); });
    return b;
  }

  function editorEl(c, card) {
    card.classList.add('editing');
    const form = el('form', 'kb-editor');
    const title = el('input');
    title.value = c.title;
    title.maxLength = 120;
    title.required = true;
    title.setAttribute('aria-label', t('Titel'));
    const desc = el('textarea');
    desc.value = c.desc || '';
    desc.rows = 3;
    desc.placeholder = t('Notizen, Links, Schritte …');
    const labels = el('input');
    labels.value = (c.labels || []).join(', ');
    labels.placeholder = t('Labels, mit Komma getrennt');
    const due = el('input');
    due.type = 'date';
    due.value = c.due || '';
    due.setAttribute('aria-label', t('Fällig am'));
    const row = el('div', 'kb-editor-row');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { state.editing = null; renderBoard(); });
    const del = el('button', 'btn-danger kb-delete', t('Löschen'));
    del.type = 'button';
    del.addEventListener('click', async () => {
      if (!del.classList.contains('confirming')) { del.classList.add('confirming'); del.textContent = t('Sicher?'); return; }
      try { setBoard(await api(`/api/kanban/cards/${c.id}/delete`, {})); state.editing = null; renderBoard(); } catch (err) { toast(err.message, 'error'); }
    });
    row.append(save, cancel, del);
    form.append(title, desc, labels, due, row);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      try {
        setBoard(await api(`/api/kanban/cards/${c.id}`, { title: title.value, desc: desc.value, labels: labels.value.split(','), due: due.value }));
        state.editing = null;
        renderBoard();
      } catch (err) { toast(err.message, 'error'); }
    });
    form.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); state.editing = null; renderBoard(); } });
    card.appendChild(form);
    setTimeout(() => title.focus(), 30);
    return card;
  }

  function clearDrop() {
    document.querySelectorAll('.kb-drop-line').forEach(x => x.remove());
    document.querySelectorAll('.kb-col.drag-over').forEach(x => x.classList.remove('drag-over'));
  }

  function dropIndex(list, y) {
    const cards = [...list.querySelectorAll('.kb-card:not(.dragging)')];
    const i = cards.findIndex(c => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; });
    return { index: i < 0 ? cards.length : i, before: i < 0 ? null : cards[i] };
  }

  function renderBoard() {
    const b = state.board;
    if (!b) return;
    const board = $('kanban-board');
    board.innerHTML = '';
    const editable = can('notes.edit');
    b.columns.forEach(col => {
      const colEl = el('section', `kb-col col-${col.id}`);
      const head = el('div', 'kb-col-head');
      head.append(el('span', 'kb-col-name', t(col.name)), raw('span', `health-tag ${COL_TAG[col.id]}`, String(col.count)));
      if (editable && col.id !== 'done') {
        const add = el('button', 'kb-add', '+');
        add.type = 'button';
        add.title = t('Karte hinzufügen');
        add.setAttribute('aria-label', t('Karte hinzufügen'));
        add.addEventListener('click', async () => {
          try {
            const res = await api('/api/kanban/cards', { title: t('Neue Karte'), column: col.id, top: true });
            setBoard(res);
            state.editing = res.cards.find(c => c.column === col.id).id;
            renderBoard();
          } catch (err) { toast(err.message, 'error'); }
        });
        head.appendChild(add);
      }
      if (editable && col.id === 'done' && col.count) {
        const clear = el('button', 'text-btn kb-clear', t('Aufräumen'));
        clear.type = 'button';
        clear.title = t('Alle fertigen Karten entfernen');
        clear.addEventListener('click', async () => {
          if (!clear.classList.contains('confirming')) { clear.classList.add('confirming'); clear.textContent = t('Sicher?'); return; }
          try { setBoard(await api('/api/kanban/clear-done', {})); renderBoard(); } catch (err) { toast(err.message, 'error'); }
        });
        head.appendChild(clear);
      }
      const list = el('div', 'kb-list');
      b.cards.filter(c => c.column === col.id).forEach(c => list.appendChild(cardEl(c)));
      if (!list.childElementCount) list.appendChild(el('p', 'kb-empty', col.id === 'done' ? t('Keine erledigten Aufgaben') : t('Leer')));
      if (editable) {
        list.addEventListener('dragover', e => {
          if (!state.dragId) return;
          e.preventDefault();
          clearDrop();
          colEl.classList.add('drag-over');
          const { before } = dropIndex(list, e.clientY);
          const line = el('div', 'kb-drop-line');
          list.insertBefore(line, before);
        });
        list.addEventListener('dragleave', e => { if (!list.contains(e.relatedTarget)) clearDrop(); });
        list.addEventListener('drop', e => {
          e.preventDefault();
          const id = state.dragId;
          const { index } = dropIndex(list, e.clientY);
          clearDrop();
          if (id) move(id, col.id, index);
        });
      }
      colEl.append(head, list);
      board.appendChild(colEl);
    });
    $('kanban-sheet-sub').textContent = editable ? t('Karten ziehen oder mit den Pfeilen verschieben · Klick zum Bearbeiten') : t('Nur ansehen');
  }

  async function move(id, column, index) {
    const before = state.board.cards.find(c => c.id === id);
    try {
      setBoard(await api(`/api/kanban/cards/${id}`, index === undefined ? { column } : { column, index }));
      renderBoard();
    } catch (err) { toast(err.message, 'error'); }
  }

  function setBoard(b) {
    state.board = b;
    renderMini();
    emit();
  }

  function openBoard(focusId) {
    if (!state.board) return;
    state.editing = null;
    renderBoard();
    openSheet('kanban-modal');
    if (focusId) setTimeout(() => document.querySelector(`.kb-card[data-id="${focusId}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 80);
  }

  async function loadBoard() {
    try {
      const res = await fetch('/api/kanban');
      if (res.status === 401) { $('kanban-card').classList.add('planner-locked'); $('kanban-sub').textContent = t('Nur nach der Anmeldung'); return; }
      if (res.ok) setBoard(await res.json());
    } catch (e) { /* offline */ }
  }

  // ---------------------------------------------------------------------------
  // Reminders
  // ---------------------------------------------------------------------------
  function reminderRow(it, full) {
    const li = el('li', `rem-item ${it.state}`);
    const editable = can('notes.edit');
    const check = el('button', 'rem-check');
    check.type = 'button';
    check.title = it.repeat === 'none' ? t('Erledigt') : t('Erledigt – kommt wieder');
    check.setAttribute('aria-label', check.title);
    check.disabled = !editable;
    check.addEventListener('click', async () => {
      li.classList.add('ticking');
      try {
        setReminders(await api(`/api/reminders/${it.id}/done`, {}));
        if (it.repeat !== 'none') toast(`${it.title}: ${t('nächster Termin')} ${fmtDate(state.reminders.items.find(x => x.id === it.id)?.due || it.due)}`, 'success');
      } catch (err) { li.classList.remove('ticking'); toast(err.message, 'error'); }
    });
    const text = el('div', 'rem-text');
    text.appendChild(raw('span', 'rem-title', it.title));
    const when = el('span', 'rem-when', relDay(it.days, it.due));
    if (it.repeat !== 'none') {
      const rep = el('span', 'rem-repeat', `↻ ${it.every > 1 ? `${it.every}× ` : ''}${t(it.repeat_label)}`);
      text.append(when, rep);
    } else text.appendChild(when);
    li.append(check, text);
    if (editable && it.days <= 0 && !full) {
      const sn = el('button', 'rem-snooze text-btn', t('Morgen'));
      sn.type = 'button';
      sn.title = t('Auf morgen verschieben');
      sn.setAttribute('aria-label', sn.title);
      sn.addEventListener('click', async () => {
        try { setReminders(await api(`/api/reminders/${it.id}/snooze?days=1`, {})); } catch (err) { toast(err.message, 'error'); }
      });
      li.appendChild(sn);
    }
    if (editable && full) {
      const edit = el('button', 'text-btn', t('Bearbeiten'));
      edit.type = 'button';
      edit.addEventListener('click', () => startEdit(it));
      const del = el('button', 'text-btn danger', t('Löschen'));
      del.type = 'button';
      del.addEventListener('click', async () => {
        if (!del.classList.contains('confirming')) { del.classList.add('confirming'); del.textContent = t('Sicher?'); return; }
        try { setReminders(await api(`/api/reminders/${it.id}/delete`, {})); } catch (err) { toast(err.message, 'error'); }
      });
      li.append(edit, del);
    }
    return li;
  }

  function renderReminders() {
    const r = state.reminders;
    const list = $('reminders-list');
    list.innerHTML = '';
    const near = r.items.filter(i => i.days <= 7).slice(0, 5);
    near.forEach(i => list.appendChild(reminderRow(i, false)));
    if (!near.length) {
      const next = r.items[0];
      list.appendChild(el('li', 'rem-empty', next ? `${t('Nichts fällig. Nächste')}: ${next.title} (${fmtDate(next.due)})` : t('Keine Erinnerungen angelegt.')));
    }
    const count = $('reminders-count');
    count.textContent = String(r.due);
    count.classList.toggle('rem-due', r.due > 0);
    count.classList.toggle('rem-overdue', r.items.some(i => i.days < 0));
    $('reminders-sub').textContent = r.due ? (r.due === 1 ? t('1 fällig') : `${r.due} ${t('fällig')}`) : t('Wiederkehrende Aufgaben');

    const full = $('reminders-full');
    full.innerHTML = '';
    r.items.forEach(i => full.appendChild(reminderRow(i, true)));
    if (!r.items.length) full.appendChild(el('li', 'rem-empty', t('Noch keine Erinnerungen.')));
    $('reminders-sheet-sub').textContent = `${r.items.length} ${t('Erinnerungen')} · ${r.done_total} ${t('erledigt')}`;
  }

  function setReminders(r) {
    state.reminders = r;
    renderReminders();
    emit();
  }

  function resetForm() {
    state.reminderEdit = null;
    $('reminder-form').reset();
    $('reminder-due').value = todayIso();
    $('reminder-repeat').value = 'monthly';
    $('reminder-every').value = '1';
    $('reminder-submit').textContent = t('Hinzufügen');
    $('reminder-cancel').hidden = true;
    syncEvery();
  }

  function startEdit(it) {
    state.reminderEdit = it.id;
    $('reminder-title').value = it.title;
    $('reminder-due').value = it.due;
    $('reminder-repeat').value = it.repeat;
    $('reminder-every').value = String(it.every || 1);
    $('reminder-submit').textContent = t('Speichern');
    $('reminder-cancel').hidden = false;
    syncEvery();
    $('reminder-title').focus();
  }

  function syncEvery() {
    const rep = $('reminder-repeat').value;
    const wrap = document.querySelector('.reminder-every');
    wrap.hidden = rep === 'none';
    wrap.querySelector('span').textContent = t({ daily: 'Alle … Tage', weekly: 'Alle … Wochen', monthly: 'Alle … Monate', yearly: 'Alle … Jahre' }[rep] || 'Alle');
  }

  function openReminders(add) {
    if (!state.reminders) return;
    resetForm();
    openSheet('reminders-modal');
    if (add && can('notes.edit')) setTimeout(() => $('reminder-title').focus(), 60);
  }

  async function loadReminders() {
    try {
      const res = await fetch('/api/reminders');
      if (res.status === 401) { $('reminders-card').classList.add('planner-locked'); $('reminders-sub').textContent = t('Nur nach der Anmeldung'); return; }
      if (res.ok) setReminders(await res.json());
    } catch (e) { /* offline */ }
  }

  // ---------------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------------
  $('kanban-open').addEventListener('click', () => openBoard());
  $('kanban-close').addEventListener('click', () => closeSheet('kanban-modal'));
  $('kanban-modal').addEventListener('click', e => { if (e.target.id === 'kanban-modal') closeSheet('kanban-modal'); });
  $('kanban-quick').addEventListener('submit', async e => {
    e.preventDefault();
    const input = $('kanban-quick-input');
    if (!input.value.trim()) return;
    try {
      setBoard(await api('/api/kanban/cards', { title: input.value, column: 'idea' }));
      input.value = '';
    } catch (err) { toast(err.message, 'error'); }
  });

  $('reminders-add').addEventListener('click', () => openReminders(true));
  $('reminders-all').addEventListener('click', () => openReminders(false));
  $('reminders-close').addEventListener('click', () => closeSheet('reminders-modal'));
  $('reminders-modal').addEventListener('click', e => { if (e.target.id === 'reminders-modal') closeSheet('reminders-modal'); });
  $('reminder-repeat').addEventListener('change', syncEvery);
  $('reminder-cancel').addEventListener('click', resetForm);
  $('reminder-form').addEventListener('submit', async e => {
    e.preventDefault();
    const body = { title: $('reminder-title').value, due: $('reminder-due').value, repeat: $('reminder-repeat').value, every: +$('reminder-every').value || 1 };
    try {
      setReminders(await api(state.reminderEdit ? `/api/reminders/${state.reminderEdit}` : '/api/reminders', body));
      toast(state.reminderEdit ? t('Erinnerung gespeichert.') : t('Erinnerung angelegt.'), 'success');
      resetForm();
    } catch (err) { toast(err.message, 'error'); }
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if ($('kanban-modal').classList.contains('open') && !state.editing) closeSheet('kanban-modal');
    if ($('reminders-modal').classList.contains('open')) closeSheet('reminders-modal');
  });

  window.PiPlanner = { openKanban: openBoard, openReminders, state };

  function loadAll() { loadBoard(); loadReminders(); }
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    loadAll();
  })();
  setInterval(() => { if (!document.hidden) loadAll(); }, 5 * 60 * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadAll(); });
})();
