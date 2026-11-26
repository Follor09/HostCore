/**
 * HostCore Dashboard - task log (like Proxmox): a panel docked to the bottom of every page.
 * Collapsed it is a slim strip with the running tasks; opened (click the strip) it shows the
 * recent tasks on the left and the selected task's log on the right - live while it runs,
 * with "Abbrechen". The height can be dragged; open/closed and height are remembered.
 * Tasks run on the server on their own - closing the page changes nothing.
 * Other scripts open a task with window.PiTasks.open(id); #aufgaben opens the panel,
 * #aufgabe=<id> one task.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const LEVEL = { running: 'warning', ok: 'good', error: 'critical', timeout: 'critical', lost: 'critical', cancelled: '' };
  const PREF = 'picore-tasks-panel';
  const MIN_H = 180;
  let data = null;
  let dock = null;
  let filter = 'all';
  let selected = null, offset = 0, logTimer = null;
  let pollTimer = null;
  let hints = null, alarms = null, hintSel = null, hintTimer = null, showAckedHints = false;
  const seenRunning = new Set();
  const initialHash = location.hash;   // read now: the page's own scripts may rewrite the address before load() is done
  const pref = (() => { try { return JSON.parse(localStorage.getItem(PREF) || '{}'); } catch (e) { return {}; } })();
  const savePref = () => { try { localStorage.setItem(PREF, JSON.stringify(pref)); } catch (e) { /* storage blocked */ } };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };
  const $ = id => dock.querySelector(`[data-id="${id}"]`);

  function toast(message, type = 'info') {
    const box = document.getElementById('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 5000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(d.detail || `Fehler ${res.status}`); err.status = res.status; throw err; }
    return d;
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const today = d.toDateString() === new Date().toDateString();
    return d.toLocaleString(LOCALE, today ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  function fmtDur(s) {
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
    return `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`;
  }
  const tag = task => el('span', `health-tag ${LEVEL[task.state] || ''}`, t(task.state_label));

  // ---------------------------------------------------------------------------
  // Panel
  // ---------------------------------------------------------------------------
  function build() {
    dock = el('section', 'task-dock');
    dock.setAttribute('aria-label', t('Aufgaben'));
    dock.innerHTML = `
      <div class="task-dock-grip" data-id="grip" title="Höhe ziehen"></div>
      <div class="task-dock-head" data-id="head">
        <button class="task-dock-toggle" data-id="toggle" type="button" aria-expanded="false">
          <span class="task-dock-dot"></span>
          <span class="task-dock-title">Aufgaben</span>
          <span class="task-dock-summary" data-id="summary"></span>
        </button>
        <button class="task-dock-hints" data-id="hints" type="button" hidden></button>
        <div class="segmented task-dock-filter" data-id="filter" role="radiogroup" aria-label="Filter">
          <button type="button" data-f="all" class="active">Alle</button>
          <button type="button" data-f="running">Laufend</button>
          <button type="button" data-f="failed">Fehlgeschlagen</button>
          <button type="button" data-f="hints">Hinweise</button>
        </div>
        <button class="task-dock-chevron" data-id="chevron" type="button" title="Auf- oder zuklappen">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15l-6-6-6 6"></path></svg>
        </button>
      </div>
      <div class="task-dock-body" data-id="body">
        <div class="task-dock-list" data-id="list" role="listbox" aria-label="Aufgaben"></div>
        <div class="task-dock-detail" data-id="detail">
          <div class="task-dock-detail-head" data-id="detail-head"></div>
          <pre class="task-dock-log" data-id="log"></pre>
        </div>
      </div>`;
    document.body.appendChild(dock);
    document.body.classList.add('has-task-dock');
    const toggle = () => setOpen(!dock.classList.contains('open'));
    $('toggle').addEventListener('click', toggle);
    $('chevron').addEventListener('click', toggle);
    dock.querySelectorAll('[data-id="filter"] button').forEach(b => b.addEventListener('click', () => {
      filter = b.dataset.f;
      dock.querySelectorAll('[data-id="filter"] button').forEach(x => x.classList.toggle('active', x === b));
      if (!dock.classList.contains('open')) setOpen(true);
      renderList();
    }));
    $('hints').addEventListener('click', () => showHints());
    dragHeight();
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && dock.classList.contains('open') && !e.target.closest('input, textarea, .xterm')) setOpen(false);
    });
    setHeight(pref.height || 300);
    setOpen(!!pref.open);
  }

  function setHeight(h) {
    const max = Math.max(MIN_H, window.innerHeight - 140);
    pref.height = Math.round(Math.min(max, Math.max(MIN_H, h)));
    document.documentElement.style.setProperty('--task-dock-h', `${pref.height}px`);
  }

  function dragHeight() {
    const grip = $('grip');
    grip.addEventListener('pointerdown', e => {
      if (!dock.classList.contains('open')) return;
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const startY = e.clientY, startH = pref.height;
      dock.classList.add('dragging');
      const move = ev => setHeight(startH + (startY - ev.clientY));
      const up = () => {
        dock.classList.remove('dragging');
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', up);
        savePref();
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up);
    });
  }

  function setOpen(open) {
    dock.classList.toggle('open', open);
    document.body.classList.toggle('task-dock-open', open);
    $('toggle').setAttribute('aria-expanded', String(open));
    pref.open = open;
    savePref();
    if (open) {
      renderList();
      if (filter === 'hints') renderList();
      else if (!selected && data && data.tasks.length) select((data.tasks.find(x => x.state === 'running') || data.tasks[0]).id);
      else if (selected) pollLog();
    } else {
      clearTimeout(logTimer);
      if (/^#aufgabe/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);
    }
  }

  function renderHead() {
    const running = data.running, failed = data.failed_24h;
    dock.classList.toggle('is-running', !!running);
    dock.classList.toggle('is-failed', !running && !!failed);
    const sum = $('summary');
    sum.innerHTML = '';
    const run = data.tasks.find(x => x.state === 'running');
    if (run) {
      sum.append(el('span', 'task-dock-count', t(running === 1 ? '1 läuft' : `${running} laufen`)), raw('span', 'task-dock-current', t(run.title)));
    } else if (failed) {
      sum.appendChild(el('span', 'task-dock-count', t(failed === 1 ? '1 fehlgeschlagen' : `${failed} fehlgeschlagen`)));
    } else if (data.tasks[0]) {
      sum.appendChild(raw('span', 'task-dock-current', `${t('Zuletzt')}: ${t(data.tasks[0].title)} · ${t(data.tasks[0].state_label)}`));
    }
  }

  // ---------------------------------------------------------------------------
  // Hints: the status bar's warnings and the open alarms, on every page - set to green from here
  // ---------------------------------------------------------------------------
  const ICON = {
    good: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    warning: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>'
  };
  const ACK_MODES = [['change', 'Bis sich etwas ändert'], ['24h', '24 Stunden'], ['7d', '7 Tage']];
  const HINT_LABEL = { critical: 'Kritisch', warning: 'Warnung', info: 'Hinweis' };
  const openAlarms = () => (alarms ? alarms.alerts.filter(a => a.status !== 'resolved') : []);

  function showHints() {
    filter = 'hints';
    dock.querySelectorAll('[data-id="filter"] button').forEach(x => x.classList.toggle('active', x.dataset.f === 'hints'));
    if (!dock.classList.contains('open')) setOpen(true); else renderList();
  }

  async function loadHints() {
    clearTimeout(hintTimer);
    const [s, a] = await Promise.all([api('/api/status-summary').catch(() => null), api('/api/alerts').catch(() => null)]);
    if (s) hints = s;
    if (a) alarms = a;
    renderHints();
    hintTimer = setTimeout(loadHints, document.hidden ? 120000 : 30000);
  }

  function renderHints() {
    if (!dock || !hints) return;
    // the status bar counts the open alarms as one hint already ("1 Alarm nicht quittiert")
    const n = hints.items.length;
    const b = $('hints');
    b.hidden = false;
    b.className = `task-dock-hints ${n ? hints.level : 'good'}`;
    b.innerHTML = n ? ICON.warning : ICON.good;
    b.appendChild(el('span', '', n ? t(n === 1 ? '1 Hinweis' : `${n} Hinweise`) : t('alles grün')));
    b.title = n ? hints.items.map(i => i.text).join('\n') : t('Keine offenen Hinweise');
    if (filter === 'hints') renderList();
  }

  function hintRow(cls, label, text, sel, onClick) {
    const row = el('button', `task-dock-item hint ${cls}${sel ? ' selected' : ''}`);
    row.type = 'button';
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(sel));
    const top = el('span', 'task-dock-item-top');
    top.append(raw('span', 'task-dock-item-title', t(text)), el('span', `health-tag ${cls}`, t(label)));
    row.appendChild(top);
    row.addEventListener('click', onClick);
    return row;
  }

  function renderHintList() {
    const list = $('list');
    list.innerHTML = '';
    if (!hints) { list.appendChild(el('p', 'task-dock-empty', t('Lade …'))); return; }
    openAlarms().forEach(a => list.appendChild(hintRow(a.status === 'open' ? 'critical' : 'warning', a.status === 'open' ? 'Alarm' : 'Quittiert',
      a.title || a.subject, hintSel === `a:${a.id}`, () => { hintSel = `a:${a.id}`; renderHintList(); renderHintDetail(); })));
    hints.items.filter(i => !/Alarme? nicht quittiert$/.test(i.text) || !alarms).forEach(i => list.appendChild(hintRow(i.level, HINT_LABEL[i.level] || 'Hinweis', i.text,
      hintSel === `h:${i.key}`, () => { hintSel = `h:${i.key}`; renderHintList(); renderHintDetail(); })));
    const acked = hints.acked || [];
    if (acked.length) {
      const tog = el('button', 'task-dock-item hint-acked-toggle', `${acked.length} ${t('auf grün gesetzt')} ${showAckedHints ? '▴' : '▾'}`);
      tog.type = 'button';
      tog.addEventListener('click', () => { showAckedHints = !showAckedHints; renderHintList(); });
      list.appendChild(tog);
      if (showAckedHints) acked.forEach(i => list.appendChild(hintRow('', 'auf grün', i.text, hintSel === `g:${i.key}`,
        () => { hintSel = `g:${i.key}`; renderHintList(); renderHintDetail(); })));
    }
    if (!list.querySelector('.hint:not(.hint-acked-toggle)') && !acked.length) list.appendChild(el('p', 'task-dock-empty', t('Keine offenen Hinweise – alles grün.')));
    if (!hintSel || !findHint()) renderHintDetail();
  }

  function findHint() {
    if (!hintSel || !hints) return null;
    const [kind, id] = [hintSel.slice(0, 1), hintSel.slice(2)];
    if (kind === 'a') { const a = openAlarms().find(x => x.id === id); return a && { kind, a }; }
    const pool = kind === 'g' ? hints.acked || [] : hints.items;
    const i = pool.find(x => x.key === id);
    return i && { kind, i };
  }

  function actionButton(label, cls, fn) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { await fn(); } catch (err) { toast(err.message, 'error'); }
      b.disabled = false;
    });
    return b;
  }

  function goTo(link) {
    if (!link) return;
    location.href = link.startsWith('#') ? `/${link}` : link;
  }

  function renderHintDetail() {
    const head = $('detail-head');
    head.innerHTML = '';
    $('log').textContent = '';
    dock.classList.add('hints-mode');
    const h = findHint();
    if (!h) {
      head.appendChild(el('p', 'task-dock-empty', t('Einen Hinweis wählen: öffnen, wo er herkommt, oder auf grün setzen, wenn er gesehen und in Ordnung ist. Auf grün gesetzt zählt er nicht mehr – ändert sich etwas, erscheint er wieder.')));
      return;
    }
    const title = el('div', 'task-dock-detail-title');
    const actions = el('div', 'task-dock-actions');
    if (h.kind === 'a') {
      const a = h.a;
      title.append(raw('strong', '', a.title || a.subject), el('span', `health-tag ${a.status === 'open' ? 'critical' : 'warning'}`, t(a.status === 'open' ? 'Offen' : 'Quittiert')));
      if (a.status === 'open' && can('system.view')) actions.appendChild(actionButton('Quittieren', 'btn-secondary', async () => { await api(`/api/alerts/${a.id}/ack`, { note: '' }); toast(t('Quittiert.'), 'success'); await loadHints(); }));
      if (can('system.view')) actions.appendChild(actionButton('Erledigt', 'btn-primary', async () => { await api(`/api/alerts/${a.id}/resolve`, {}); hintSel = null; toast(t('Auf erledigt gesetzt.'), 'success'); await loadHints(); }));
      actions.appendChild(actionButton('Zu den Alarmen', 'btn-secondary', async () => goTo('/system#alarme')));
    } else if (h.kind === 'h') {
      const i = h.i;
      title.append(raw('strong', '', t(i.text)), el('span', `health-tag ${i.level}`, t(HINT_LABEL[i.level] || 'Hinweis')));
      if (i.link) actions.appendChild(actionButton('Öffnen', 'btn-secondary', async () => goTo(i.link)));
      if (can('system.view')) {
        actions.appendChild(el('span', 'task-dock-fact-k', t('Auf grün setzen:')));
        ACK_MODES.forEach(([mode, label]) => actions.appendChild(actionButton(label, 'btn-secondary', async () => {
          hints = await api('/api/status-summary/ack', { key: i.key, mode });
          hintSel = null;
          toast(t('Auf grün gesetzt.'), 'success');
          renderHints();
        })));
      }
    } else {
      const i = h.i;
      title.append(raw('strong', '', t(i.text)), el('span', 'health-tag good', t('auf grün')));
      const facts = el('div', 'task-dock-facts');
      const until = i.until ? new Date(i.until * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : t('bis sich etwas ändert');
      facts.append(raw('span', '', `${t('von')} ${i.acked_by || '–'} · ${t('am')} ${fmtTime(i.acked_at)} · ${t('gilt')} ${i.until ? `${t('bis')} ${until}` : until}`));
      head.append(title, facts);
      if (can('system.view')) actions.appendChild(actionButton('Wieder anzeigen', 'btn-secondary', async () => {
        hints = await api('/api/status-summary/unack', { key: i.key });
        hintSel = null;
        renderHints();
      }));
      head.appendChild(actions);
      return;
    }
    head.append(title, actions);
  }

  function renderList() {
    if (!data || !dock.classList.contains('open')) return;
    if (filter === 'hints') { renderHintList(); return; }
    dock.classList.remove('hints-mode');
    const list = $('list');
    list.innerHTML = '';
    const items = data.tasks.filter(x => filter === 'all' || (filter === 'running' ? x.state === 'running' : ['error', 'timeout', 'lost'].includes(x.state)));
    items.forEach(x => {
      const row = el('button', `task-dock-item ${x.state}${x.id === selected ? ' selected' : ''}`);
      row.type = 'button';
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(x.id === selected));
      const top = el('span', 'task-dock-item-top');
      top.append(raw('span', 'task-dock-item-title', t(x.title)), tag(x));
      const sub = el('span', 'task-dock-item-sub');
      sub.append(raw('span', '', fmtTime(x.started)), el('span', '', fmtDur(x.duration)), raw('span', '', x.user || '–'));
      row.append(top, sub);
      row.addEventListener('click', () => select(x.id));
      list.appendChild(row);
    });
    if (!items.length) list.appendChild(el('p', 'task-dock-empty', t(filter === 'all' ? 'Noch keine Aufgaben.' : 'Keine Aufgaben für diesen Filter.')));
    if (!selected) {
      $('detail-head').innerHTML = '';
      $('log').textContent = '';
      $('detail-head').appendChild(el('p', 'task-dock-empty', data.durable
        ? t('Laufen auf dem Server weiter, auch wenn die Seite geschlossen wird oder das Dashboard neu startet.')
        : t('Laufen weiter, wenn die Seite geschlossen wird – ein Neustart des Dashboards bricht sie ab (Linger für den Benutzer fehlt).')));
    }
  }

  function renderDetailHead(task) {
    const head = $('detail-head');
    head.innerHTML = '';
    const title = el('div', 'task-dock-detail-title');
    title.append(raw('strong', '', t(task.title)), tag(task));
    const facts = el('div', 'task-dock-facts');
    const fact = (k, v) => { if (v) { const s = el('span'); s.append(el('span', 'task-dock-fact-k', t(k)), raw('span', '', v)); facts.appendChild(s); } };
    fact('Gestartet', fmtTime(task.started));
    fact(task.state === 'running' ? 'Läuft seit' : 'Dauer', fmtDur(task.duration));
    fact('Benutzer', task.user);
    if (task.exit != null && task.state !== 'ok') fact('Exit-Code', String(task.exit));
    const actions = el('div', 'task-dock-actions');
    if (task.state === 'running' && task.kind !== 'apt' && can('services.control')) {
      const stop = el('button', 'btn-secondary', t('Abbrechen'));
      stop.type = 'button';
      stop.addEventListener('click', async () => {
        if (!confirm(t('Aufgabe abbrechen?'))) return;
        try { await api(`/api/tasks/${task.id}/cancel`, {}); pollLog(); } catch (err) { toast(err.message, 'error'); }
      });
      actions.appendChild(stop);
    }
    head.append(title, facts, actions);
  }

  function select(id) {
    if (filter === 'hints') {
      filter = 'all';
      dock.querySelectorAll('[data-id="filter"] button').forEach(x => x.classList.toggle('active', x.dataset.f === 'all'));
    }
    selected = id;
    offset = 0;
    $('log').textContent = '';
    dock.classList.add('show-detail');   // phones: the log replaces the list
    renderList();
    pollLog();
  }

  async function pollLog() {
    clearTimeout(logTimer);
    if (!selected || !dock.classList.contains('open') || filter === 'hints') return;
    const id = selected;
    let r;
    try { r = await api(`/api/tasks/${id}/log?offset=${offset}`); } catch (err) {
      $('log').textContent = err.message;
      return;
    }
    if (id !== selected) return;
    const pre = $('log');
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    if (r.truncated && !offset) pre.textContent = `… ${t('(Anfang gekürzt)')}\n`;
    pre.textContent += r.text;
    offset = r.offset;
    if (atBottom || offset === r.text.length) pre.scrollTop = pre.scrollHeight;
    renderDetailHead(r.task);
    if (r.task.state === 'running') logTimer = setTimeout(pollLog, 1500);
  }

  async function openTask(id) {
    if (!dock) return;
    if (!dock.classList.contains('open')) setOpen(true);
    select(id);
  }

  // Phones: back from the log to the list
  function backButton() {
    const back = el('button', 'task-dock-back', t('← Alle Aufgaben'));
    back.type = 'button';
    back.addEventListener('click', () => dock.classList.remove('show-detail'));
    $('detail').prepend(back);
  }

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------
  async function load() {
    clearTimeout(pollTimer);
    try {
      data = await api('/api/tasks?limit=100');
    } catch (err) {
      if (err.status === 401 || err.status === 403) { if (dock) { dock.remove(); document.body.classList.remove('has-task-dock', 'task-dock-open'); } return; }
      pollTimer = setTimeout(load, 30000);
      return;
    }
    if (!dock) { build(); backButton(); }
    // Tell when a task that was running ends (also ones started on another device)
    data.tasks.forEach(x => {
      if (x.state === 'running') seenRunning.add(x.id);
      else if (seenRunning.has(x.id)) {
        seenRunning.delete(x.id);
        toast(`${t(x.title)}: ${t(x.state_label)}`, x.state === 'ok' ? 'success' : x.state === 'cancelled' ? 'info' : 'error');
      }
    });
    renderHead();
    renderList();
    pollTimer = setTimeout(load, document.hidden ? 60000 : data.running ? 3000 : 15000);
  }

  window.PiTasks = { open: openTask, refresh: load, show: () => dock && setOpen(true) };

  (async () => {
    if (window.PiAuth) {
      await window.PiAuth.ready;
      if (!can('system.view')) return;
    }
    await load();
    if (dock) loadHints();
    const fromHash = hash => {
      const m = hash.match(/^#aufgabe=([0-9a-f]{16})$/);
      if (m) openTask(m[1]);
      else if (hash === '#aufgaben' && dock) setOpen(true);
      else if (hash === '#hinweise' && dock) showHints();
    };
    fromHash(initialHash);
    window.addEventListener('hashchange', () => fromHash(location.hash));
    window.addEventListener('resize', () => { if (dock) setHeight(pref.height); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { load(); if (dock) loadHints(); } });
  })();
})();
