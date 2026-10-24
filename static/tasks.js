/**
 * PiCore Dashboard - task log (like Proxmox): a small bar at the bottom of every page shows
 * running tasks; it opens the list of recent tasks with their log (live while running) and
 * lets you cancel one. Tasks run on the Pi on their own - closing the page changes nothing.
 * Other scripts can open a task with window.PiTasks.open(id); #aufgaben opens the list,
 * #aufgabe=<id> one task.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const LEVEL = { running: 'warning', ok: 'good', error: 'critical', timeout: 'critical', lost: 'critical', cancelled: '' };
  let data = null;
  let bar = null, modal = null;
  let filter = 'all';
  let openId = null, offset = 0, logTimer = null;
  let pollTimer = null;
  const seenRunning = new Set();

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

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

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  function fmtDur(s) {
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
    return `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`;
  }

  // ---------------------------------------------------------------------------
  // Bar at the bottom
  // ---------------------------------------------------------------------------
  function buildBar() {
    bar = el('button', 'task-bar');
    bar.type = 'button';
    bar.title = t('Aufgaben – laufen auf dem Pi weiter, auch wenn die Seite geschlossen ist');
    bar.addEventListener('click', () => show());
    document.body.appendChild(bar);
    document.body.classList.add('has-task-bar');
  }

  function renderBar() {
    if (!bar) buildBar();
    bar.innerHTML = '';
    const running = data.running;
    const failed = data.failed_24h;
    bar.className = `task-bar${running ? ' running' : failed ? ' failed' : ''}`;
    bar.append(el('span', 'task-bar-dot'), el('span', '', t('Aufgaben')));
    if (running) bar.appendChild(el('span', 'task-bar-count', t(running === 1 ? '1 läuft' : `${running} laufen`)));
    else if (failed) bar.appendChild(el('span', 'task-bar-count', t(failed === 1 ? '1 fehlgeschlagen' : `${failed} fehlgeschlagen`)));
  }

  // ---------------------------------------------------------------------------
  // List and log
  // ---------------------------------------------------------------------------
  function buildModal() {
    modal = el('div', 'modal-backdrop tasks-modal');
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
      <div class="modal-card sheet-card tasks-sheet" role="dialog" aria-modal="true" aria-labelledby="tasks-title">
        <div class="sheet-head">
          <div>
            <h3 id="tasks-title">Aufgaben</h3>
            <p class="card-subtitle" id="tasks-sub"></p>
          </div>
          <div class="sheet-head-actions">
            <button class="btn-secondary" id="tasks-back" type="button" hidden>← Alle Aufgaben</button>
            <button class="mini-icon-btn" id="tasks-close" title="Schließen (Esc)">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"></path></svg>
            </button>
          </div>
        </div>
        <div id="tasks-list-view">
          <div class="segmented" id="tasks-filter" role="radiogroup" aria-label="Filter">
            <button type="button" data-f="all" class="active">Alle</button>
            <button type="button" data-f="running">Laufend</button>
            <button type="button" data-f="failed">Fehlgeschlagen</button>
          </div>
          <div class="table-scroll tasks-table-wrap"><table class="data-table tasks-table" id="tasks-table"></table></div>
        </div>
        <div id="tasks-log-view" hidden>
          <div class="tasks-log-head" id="tasks-log-head"></div>
          <pre class="tasks-log" id="tasks-log"></pre>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const $ = id => modal.querySelector(`#${id}`);
    $('tasks-close').addEventListener('click', hide);
    $('tasks-back').addEventListener('click', () => showList());
    modal.addEventListener('click', e => { if (e.target === modal) hide(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal.classList.contains('open')) hide(); });
    modal.querySelectorAll('#tasks-filter button').forEach(b => b.addEventListener('click', () => {
      filter = b.dataset.f;
      modal.querySelectorAll('#tasks-filter button').forEach(x => x.classList.toggle('active', x === b));
      renderList();
    }));
  }

  function stateTag(task) {
    return el('span', `health-tag ${LEVEL[task.state] || ''}`, t(task.state_label));
  }

  function renderList() {
    if (!modal || !data) return;
    const tbl = modal.querySelector('#tasks-table');
    tbl.innerHTML = '';
    const head = tbl.createTHead().insertRow();
    ['Start', 'Dauer', 'Benutzer', 'Beschreibung', 'Status'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = tbl.createTBody();
    const items = data.tasks.filter(x => filter === 'all' || (filter === 'running' ? x.state === 'running' : ['error', 'timeout', 'lost'].includes(x.state)));
    items.forEach(x => {
      const r = body.insertRow();
      r.className = `task-row ${x.state}`;
      r.tabIndex = 0;
      r.insertCell().textContent = fmtTs(x.started);
      r.insertCell().textContent = fmtDur(x.duration);
      r.insertCell().appendChild(raw('span', '', x.user || '–'));
      r.insertCell().appendChild(raw('span', '', t(x.title)));
      r.insertCell().appendChild(stateTag(x));
      r.addEventListener('click', () => openTask(x.id));
      r.addEventListener('keydown', e => { if (e.key === 'Enter') openTask(x.id); });
    });
    if (!items.length) {
      const r = body.insertRow();
      const c = r.insertCell();
      c.colSpan = 5;
      c.className = 'field-hint';
      c.textContent = t(filter === 'all' ? 'Noch keine Aufgaben.' : 'Keine Aufgaben für diesen Filter.');
    }
    modal.querySelector('#tasks-sub').textContent = data.durable
      ? t('Laufen auf dem Pi weiter, auch wenn die Seite geschlossen wird oder das Dashboard neu startet.')
      : t('Laufen weiter, wenn die Seite geschlossen wird – ein Neustart des Dashboards bricht sie ab (Linger für den Benutzer fehlt).');
  }

  function showList() {
    openId = null;
    clearTimeout(logTimer);
    modal.querySelector('#tasks-list-view').hidden = false;
    modal.querySelector('#tasks-log-view').hidden = true;
    modal.querySelector('#tasks-back').hidden = true;
    modal.querySelector('#tasks-title').textContent = t('Aufgaben');
    renderList();
  }

  function renderLogHead(task) {
    const head = modal.querySelector('#tasks-log-head');
    head.innerHTML = '';
    const facts = el('dl', 'backup-facts');
    const add = (k, v) => { if (v) facts.append(el('dt', '', t(k)), raw('dd', '', v)); };
    add('Gestartet', fmtTs(task.started));
    add(task.state === 'running' ? 'Läuft seit' : 'Dauer', fmtDur(task.duration));
    add('Benutzer', task.user);
    if (task.exit != null && task.state !== 'ok') add('Exit-Code', String(task.exit));
    const top = el('div', 'tasks-log-top');
    top.append(stateTag(task), facts);
    if (task.state === 'running' && task.kind !== 'apt' && can('services.control')) {
      const stop = el('button', 'btn-secondary', t('Abbrechen'));
      stop.type = 'button';
      stop.addEventListener('click', async () => {
        if (!confirm(t('Aufgabe abbrechen?'))) return;
        try { await api(`/api/tasks/${task.id}/cancel`, {}); } catch (err) { toast(err.message, 'error'); }
      });
      top.appendChild(stop);
    }
    head.appendChild(top);
  }

  async function openTask(id) {
    if (!modal) buildModal();
    openId = id;
    offset = 0;
    modal.querySelector('#tasks-list-view').hidden = true;
    modal.querySelector('#tasks-log-view').hidden = false;
    modal.querySelector('#tasks-back').hidden = false;
    modal.querySelector('#tasks-log').textContent = '';
    if (!modal.classList.contains('open')) open();
    await pollLog();
  }

  async function pollLog() {
    clearTimeout(logTimer);
    if (!openId) return;
    const id = openId;
    let r;
    try { r = await api(`/api/tasks/${id}/log?offset=${offset}`); } catch (err) {
      modal.querySelector('#tasks-log').textContent = err.message;
      return;
    }
    if (id !== openId) return;
    const pre = modal.querySelector('#tasks-log');
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    if (r.truncated && !offset) pre.textContent = `… ${t('(Anfang gekürzt)')}\n`;
    pre.textContent += r.text;
    offset = r.offset;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    modal.querySelector('#tasks-title').textContent = t(r.task.title);
    modal.querySelector('#tasks-title').setAttribute('data-no-i18n', '');
    renderLogHead(r.task);
    if (r.task.state === 'running') logTimer = setTimeout(pollLog, 1500);
  }

  function open() {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }

  function show() {
    if (!modal) buildModal();
    showList();
    open();
    load();
  }

  function hide() {
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    openId = null;
    clearTimeout(logTimer);
    if (/^#aufgabe/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);
  }

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------
  async function load() {
    clearTimeout(pollTimer);
    try {
      data = await api('/api/tasks?limit=100');
    } catch (err) {
      if (err.status === 401 || err.status === 403) { if (bar) bar.remove(); return; }   // not signed in / no permission
      pollTimer = setTimeout(load, 30000);
      return;
    }
    // Tell when a task that was running ends (also ones started on another device)
    data.tasks.forEach(x => {
      if (x.state === 'running') seenRunning.add(x.id);
      else if (seenRunning.has(x.id)) {
        seenRunning.delete(x.id);
        toast(`${t(x.title)}: ${t(x.state_label)}`, x.state === 'ok' ? 'success' : x.state === 'cancelled' ? 'info' : 'error');
      }
    });
    renderBar();
    if (modal && modal.classList.contains('open') && !openId) renderList();
    pollTimer = setTimeout(load, document.hidden ? 60000 : data.running ? 3000 : 15000);
  }

  window.PiTasks = { open: openTask, refresh: load, show };

  (async () => {
    if (window.PiAuth) {
      await window.PiAuth.ready;
      if (!can('system.view')) return;
    }
    await load();
    // #aufgaben opens the list, #aufgabe=<id> one task (links from events and alarms)
    const fromHash = () => {
      const m = location.hash.match(/^#aufgabe=([0-9a-f]{16})$/);
      if (m) openTask(m[1]);
      else if (location.hash === '#aufgaben') show();
    };
    fromHash();
    window.addEventListener('hashchange', fromHash);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  })();
})();
