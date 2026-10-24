/**
 * PiCore Dashboard - task log (like Proxmox): a panel docked to the bottom of every page.
 * Collapsed it is a slim strip with the running tasks; opened (click the strip) it shows the
 * recent tasks on the left and the selected task's log on the right - live while it runs,
 * with "Abbrechen". The height can be dragged; open/closed and height are remembered.
 * Tasks run on the Pi on their own - closing the page changes nothing.
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
        <div class="segmented task-dock-filter" data-id="filter" role="radiogroup" aria-label="Filter">
          <button type="button" data-f="all" class="active">Alle</button>
          <button type="button" data-f="running">Laufend</button>
          <button type="button" data-f="failed">Fehlgeschlagen</button>
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
      if (!selected && data && data.tasks.length) select((data.tasks.find(x => x.state === 'running') || data.tasks[0]).id);
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

  function renderList() {
    if (!data || !dock.classList.contains('open')) return;
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
        ? t('Laufen auf dem Pi weiter, auch wenn die Seite geschlossen wird oder das Dashboard neu startet.')
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
    selected = id;
    offset = 0;
    $('log').textContent = '';
    dock.classList.add('show-detail');   // phones: the log replaces the list
    renderList();
    pollLog();
  }

  async function pollLog() {
    clearTimeout(logTimer);
    if (!selected || !dock.classList.contains('open')) return;
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
    const fromHash = hash => {
      const m = hash.match(/^#aufgabe=([0-9a-f]{16})$/);
      if (m) openTask(m[1]);
      else if (hash === '#aufgaben' && dock) setOpen(true);
    };
    fromHash(initialHash);
    window.addEventListener('hashchange', () => fromHash(location.hash));
    window.addEventListener('resize', () => { if (dock) setHeight(pref.height); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  })();
})();
