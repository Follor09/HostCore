/**
 * HostCore Dashboard - settings section "Terminal-Mitschnitte": switch recording on/off, keep
 * time, list of recorded terminal sessions and a player (xterm.js) with play/pause, seeking,
 * speed and shortened pauses. Admins only.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('rec-form')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  let data = null;
  let found = null;   // content search: {q, byId: {id: {count, hits}}} or null

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

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function fmtBytes(b) {
    const u = ['B', 'KB', 'MB', 'GB'];
    const i = b ? Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024))) : 0;
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }
  function fmtClock(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
  }

  // Snippet with the search term highlighted (text only, no HTML from the recording)
  function mark(text, q) {
    const span = raw('span', 'rec-hit-text');
    const low = text.toLowerCase(), needle = q.toLowerCase();
    let at = 0, i;
    while ((i = low.indexOf(needle, at)) >= 0) {
      span.appendChild(document.createTextNode(text.slice(at, i)));
      span.appendChild(raw('mark', '', text.slice(i, i + q.length)));
      at = i + q.length;
    }
    span.appendChild(document.createTextNode(text.slice(at)));
    return span;
  }

  function render() {
    $('rec-enabled').checked = data.config.enabled;
    $('rec-days').value = data.config.days;
    $('rec-unavailable').hidden = data.available;
    $('rec-total').textContent = data.recordings.length ? `${data.recordings.length} · ${fmtBytes(data.total)}` : '';
    const tbl = $('rec-table');
    tbl.innerHTML = '';
    if (!data.recordings.length) {
      tbl.appendChild(el('caption', 'field-hint rec-empty', t('Noch keine Mitschnitte.')));
      return;
    }
    // Typing filters by user, target, address and date; "Im Inhalt suchen" asks the server
    const words = $('rec-q').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const list = data.recordings.filter(r => {
      if (found) return !!found.byId[r.id];
      const hay = `${r.user} ${r.client || ''} ${r.container ? `container ${r.container}` : 'server'} ${fmtTs(r.started)}`.toLowerCase();
      return words.every(w => hay.includes(w));
    });
    $('rec-q-clear').hidden = !found && !words.length;
    const info = $('rec-search-info');
    info.hidden = !found && !words.length;
    info.textContent = found
      ? (list.length ? `${t('Treffer für')} „${found.q}“: ${list.length} ${t(list.length === 1 ? 'Mitschnitt' : 'Mitschnitte')}` : `${t('Kein Mitschnitt enthält')} „${found.q}“.`)
      : `${list.length} ${t('von')} ${data.recordings.length}`;
    if (!list.length) return;
    const head = tbl.createTHead().insertRow();
    ['Beginn', 'Benutzer', 'Ziel', 'Dauer', 'Größe', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = tbl.createTBody();
    list.forEach(r => {
      const hit = found && found.byId[r.id];
      const row = body.insertRow();
      row.insertCell().textContent = fmtTs(r.started);
      const who = row.insertCell();
      who.appendChild(raw('span', '', r.user));
      if (r.client) who.appendChild(raw('div', 'field-hint', r.client));
      const target = row.insertCell();
      target.appendChild(r.container ? raw('span', '', `${t('Container')} ${r.container}`) : el('span', '', t('Server')));
      if (r.running) target.appendChild(el('span', 'health-tag warning rec-live', t('läuft')));
      row.insertCell().textContent = fmtClock(r.duration);
      row.insertCell().textContent = fmtBytes(r.size);
      const act = row.insertCell();
      act.className = 'row-action';
      const play = el('button', 'btn-secondary', t('Abspielen'));
      play.type = 'button';
      play.addEventListener('click', () => openPlayer(r, hit ? hit.hits[0].at : null));
      act.appendChild(play);
      if (hit) {
        const sub = body.insertRow();
        sub.className = 'rec-hit-row';
        const cell = sub.insertCell();
        cell.colSpan = 6;
        hit.hits.forEach(h => {
          const b = el('button', 'rec-hit', '');
          b.type = 'button';
          b.title = t('Ab dieser Stelle abspielen');
          b.appendChild(el('span', 'rec-hit-time', fmtClock(h.at)));
          b.appendChild(mark(h.text, found.q));
          b.addEventListener('click', () => openPlayer(r, h.at));
          cell.appendChild(b);
        });
        if (hit.count > hit.hits.length) cell.appendChild(el('span', 'field-hint', `+ ${hit.count - hit.hits.length} ${t('weitere Treffer')}`));
      }
      if (r.running) {
        const kill = el('button', 'text-btn', t('Beenden'));
        kill.type = 'button';
        kill.title = t('Die laufende Sitzung beenden');
        kill.addEventListener('click', () => killSession(r.id, r.user));
        act.appendChild(kill);
      } else {
        const del = el('button', 'text-btn', t('Löschen'));
        del.type = 'button';
        del.addEventListener('click', async () => {
          if (!confirm(t('Mitschnitt löschen?'))) return;
          try { data = await api(`/api/terminal/recordings/${r.id}/delete`, {}); render(); } catch (err) { toast(err.message, 'error'); }
        });
        act.appendChild(del);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Running sessions (all users): end any of them
  // ---------------------------------------------------------------------------
  let live = null;

  async function killSession(id, who) {
    if (!confirm(`${t('Sitzung von')} ${who} ${t('jetzt beenden? Laufende Programme darin werden abgebrochen.')}`)) return;
    try {
      live = await api(`/api/terminal/admin/sessions/${encodeURIComponent(id)}/kill`, {});
      toast(t('Sitzung beendet.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
    renderLive();
    loadRecordings();
  }

  function renderLive() {
    const box = $('rec-sessions-box');
    box.hidden = !live || !live.persistent;
    if (box.hidden) return;
    const tbl = $('rec-sessions');
    tbl.innerHTML = '';
    if (!live.sessions.length) {
      tbl.appendChild(el('caption', 'field-hint rec-empty', t('Keine laufenden Sitzungen.')));
      return;
    }
    const head = tbl.createTHead().insertRow();
    ['Benutzer', 'Ziel', 'Seit', 'Zuletzt aktiv', 'Verbunden', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = tbl.createTBody();
    live.sessions.forEach(x => {
      const row = body.insertRow();
      const who = row.insertCell();
      who.appendChild(raw('span', '', x.user));
      if (x.client) who.appendChild(raw('div', 'field-hint', x.client));
      const target = row.insertCell();
      target.appendChild(x.container ? raw('span', '', `${t('Container')} ${x.container}`) : el('span', '', t('Server')));
      if (x.recorded) target.appendChild(el('span', 'health-tag rec-live', t('Aufzeichnung')));
      row.insertCell().textContent = fmtTs(x.created);
      row.insertCell().textContent = fmtTs(x.activity);
      const con = row.insertCell();
      const names = (x.connected || []).map(c => c.user + (c.mode === 'view' ? ` (${t('Zuschauen')})` : ''));
      con.appendChild(names.length ? raw('span', '', names.join(', ')) : el('span', 'field-hint', t(x.attached ? 'verbunden' : 'niemand')));
      if ((x.shares || []).length) con.appendChild(raw('div', 'field-hint', `${t('geteilt mit')} ${x.shares.map(sh => sh.user).join(', ')}`));
      const act = row.insertCell();
      act.className = 'row-action';
      const kill = el('button', 'btn-secondary', t('Beenden'));
      kill.type = 'button';
      kill.addEventListener('click', () => killSession(x.id, x.user));
      act.appendChild(kill);
    });
  }

  async function loadLive() {
    try { live = await api('/api/terminal/admin/sessions'); } catch (e) { live = null; }
    renderLive();
  }

  async function loadRecordings() {
    try { data = await api('/api/terminal/recordings'); render(); } catch (e) { /* offline */ }
  }

  $('rec-sessions-reload').addEventListener('click', () => { loadLive(); loadRecordings(); });

  // ---------------------------------------------------------------------------
  // Player
  // ---------------------------------------------------------------------------
  function loadXterm() {
    if (window.Terminal) return Promise.resolve();
    if (!document.querySelector('link[href*="xterm.css"]')) {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = '/static/vendor/xterm/xterm.css';
      document.head.appendChild(css);
    }
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/xterm/xterm.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error(t('xterm.js konnte nicht geladen werden.')));
      document.head.appendChild(s);
    });
  }

  let player = null;

  function buildPlayer() {
    const modal = el('div', 'modal-backdrop rec-modal');
    modal.innerHTML = `
      <div class="modal-card sheet-card rec-sheet" role="dialog" aria-modal="true" aria-labelledby="rec-title">
        <div class="sheet-head">
          <div><h3 id="rec-title">Mitschnitt</h3><p class="card-subtitle" id="rec-sub"></p></div>
          <button class="mini-icon-btn" id="rec-close" title="Schließen (Esc)">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"></path></svg>
          </button>
        </div>
        <div class="rec-screen" id="rec-screen"></div>
        <div class="rec-controls">
          <button class="btn-primary" id="rec-play" type="button">Abspielen</button>
          <span class="rec-time" id="rec-time">0:00 / 0:00</span>
          <input type="range" id="rec-seek" min="0" max="1000" value="0" aria-label="Position">
          <select id="rec-speed" aria-label="Geschwindigkeit">
            <option value="1">1×</option><option value="2">2×</option><option value="4">4×</option><option value="8">8×</option>
          </select>
          <label class="toggle-label"><input type="checkbox" id="rec-skip" checked> Pausen kürzen</label>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const q = id => modal.querySelector(`#${id}`);
    player = { modal, q, term: null, events: [], idx: 0, pos: 0, playing: false, last: 0, duration: 0 };
    const close = () => { pause(); modal.classList.remove('open'); };
    q('rec-close').addEventListener('click', close);
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    document.addEventListener('keydown', e => {
      if (!modal.classList.contains('open')) return;
      if (e.key === 'Escape') close();
      if (e.key === ' ' && e.target.tagName !== 'SELECT') { e.preventDefault(); toggle(); }
    });
    q('rec-play').addEventListener('click', toggle);
    q('rec-seek').addEventListener('input', () => seek(q('rec-seek').value / 1000 * player.duration));
    q('rec-skip').addEventListener('change', () => { const frac = player.duration ? player.pos / player.duration : 0; timeline(); seek(frac * player.duration); });
  }

  // Times with long pauses cut to 2 s (if chosen), so a session with breaks plays through quickly
  function timeline() {
    const cap = player.q('rec-skip').checked ? 2 : Infinity;
    let prev = 0, at = 0;
    player.events.forEach(ev => {
      at += Math.min(ev.raw - prev, cap);
      prev = ev.raw;
      ev.t = at;
    });
    player.duration = player.events.length ? player.events[player.events.length - 1].t : 0;
  }

  function showTime() {
    player.q('rec-time').textContent = `${fmtClock(player.pos)} / ${fmtClock(player.duration)}`;
    player.q('rec-seek').value = player.duration ? Math.round(player.pos / player.duration * 1000) : 0;
  }

  function seek(pos) {
    player.pos = Math.max(0, Math.min(pos, player.duration));
    player.term.reset();
    let chunk = '';
    player.idx = 0;
    while (player.idx < player.events.length && player.events[player.idx].t <= player.pos) chunk += player.events[player.idx++].d;
    player.term.write(chunk);
    showTime();
  }

  function frame(now) {
    if (!player.playing) return;
    const dt = (now - player.last) / 1000;
    player.last = now;
    player.pos += dt * Number(player.q('rec-speed').value);
    let chunk = '';
    while (player.idx < player.events.length && player.events[player.idx].t <= player.pos) chunk += player.events[player.idx++].d;
    if (chunk) player.term.write(chunk);
    if (player.pos >= player.duration) { player.pos = player.duration; pause(); }
    showTime();
    if (player.playing) requestAnimationFrame(frame);
  }

  function play() {
    if (player.pos >= player.duration) seek(0);
    player.playing = true;
    player.last = performance.now();
    player.q('rec-play').textContent = t('Pause');
    requestAnimationFrame(frame);
  }
  function pause() {
    if (!player) return;
    player.playing = false;
    player.q('rec-play').textContent = t('Abspielen');
  }
  const toggle = () => (player.playing ? pause() : play());

  // Timeline position of a time in the recording (pauses may be shortened); a little earlier
  // so the command leading up to a search hit is visible too
  function posOf(rawAt) {
    const ev = player.events.find(e => e.raw >= rawAt);
    return Math.max(0, (ev ? ev.t : player.duration) - 3);
  }

  async function openPlayer(r, startAt = null) {
    try { await loadXterm(); } catch (err) { toast(err.message, 'error'); return; }
    if (!player) buildPlayer();
    pause();
    const q = player.q;
    q('rec-title').textContent = r.container ? `${t('Container')} ${r.container}` : t('Shell auf dem Server');
    q('rec-title').setAttribute('data-no-i18n', '');
    q('rec-sub').textContent = `${r.user} · ${fmtTs(r.started)}${r.client ? ` · ${r.client}` : ''}`;
    q('rec-screen').innerHTML = '';
    player.modal.classList.add('open');
    let text;
    try {
      const res = await fetch(`/api/terminal/recordings/${r.id}`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
      text = await res.text();
    } catch (err) { q('rec-screen').textContent = err.message; return; }
    const lines = text.split('\n').filter(Boolean);
    let header = {};
    try { header = JSON.parse(lines.shift()); } catch (e) { /* broken header */ }
    player.events = [];
    lines.forEach(l => {
      try {
        const [time, kind, d] = JSON.parse(l);
        if (kind === 'o') player.events.push({ raw: time, d });
      } catch (e) { /* half-written last line */ }
    });
    player.term = new window.Terminal({
      cols: header.width || 100, rows: header.height || 30, disableStdin: true, cursorBlink: false, scrollback: 2000,
      fontFamily: "'IBM Plex Mono', ui-monospace, monospace", fontSize: 13,
      theme: { background: '#05070c', foreground: '#d7e0ea', cursor: '#00f0ff' }
    });
    player.term.open(q('rec-screen'));
    timeline();
    seek(startAt == null ? 0 : posOf(startAt));
    play();
  }

  // ---------------------------------------------------------------------------
  $('rec-q').addEventListener('input', () => { found = null; if (data) render(); });
  $('rec-q-clear').addEventListener('click', () => { $('rec-q').value = ''; found = null; render(); });
  $('rec-search').addEventListener('submit', async e => {
    e.preventDefault();
    const q = $('rec-q').value.trim();
    if (q.length < 2) { toast(t('Mindestens 2 Zeichen eingeben.'), 'error'); return; }
    const btn = e.submitter || $('rec-search').querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api(`/api/terminal/recordings/search?q=${encodeURIComponent(q)}`);
      found = { q: res.q, byId: Object.fromEntries(res.results.map(x => [x.id, x])) };
      render();
    } catch (err) { toast(err.message, 'error'); } finally { btn.disabled = false; }
  });

  $('rec-form').addEventListener('submit', async e => {
    e.preventDefault();
    try { data = await api('/api/settings/terminal-recording', { enabled: $('rec-enabled').checked, days: Number($('rec-days').value) }); render(); toast(t('Gespeichert.'), 'success'); } catch (err) { toast(err.message, 'error'); }
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!can('users.manage')) return;
    await Promise.all([loadRecordings(), loadLive()]);
  })();
})();
