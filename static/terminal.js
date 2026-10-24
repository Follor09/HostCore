/**
 * PiCore Dashboard - Web terminal (xterm.js <-> /ws/terminal)
 * Flow: password -> one-time ticket -> WebSocket -> login shell in a PTY.
 * With ?container=<name> the shell runs inside that Docker container (docker exec).
 *
 * Persistent sessions (tmux on the Pi): the server sends the session id and a resume token,
 * kept in sessionStorage for this tab. When the connection drops (network, dashboard restart)
 * the page reconnects by itself with the token - no password - and also after a reload.
 * Running sessions can be taken over (password) or ended from the unlock card.
 * The owner can share a session ("Teilen"): to watch (read-only) or to type along; who is
 * connected shows next to the status. Recorded sessions carry the "Aufzeichnung" tag.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let term = null, fit = null, ws = null;
  const container = (new URLSearchParams(location.search).get('container') || '').trim();
  const STORE = `picore-term:${container || 'pi'}`;
  let current = null;          // {id, resume} of the attached persistent session
  let role = 'owner', readOnly = false, viewersTimer = null;
  let persistent = false;
  let retry = { n: 0, timer: null, since: 0 };
  let pinger = null;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  const store = {
    get() { try { return JSON.parse(sessionStorage.getItem(STORE) || 'null'); } catch (e) { return null; } },
    set(v) { try { if (v) sessionStorage.setItem(STORE, JSON.stringify(v)); else sessionStorage.removeItem(STORE); } catch (e) { /* storage blocked */ } }
  };

  if (container) {
    document.title = `${container} | ${t('Container-Konsole')}`;
    $('term-title').textContent = `${t('Konsole')}: ${container}`;
    $('term-title').setAttribute('data-no-i18n', '');
    $('term-unlock-title').textContent = t('Container-Konsole öffnen');
    $('term-hint').textContent = t('Die Shell läuft im Container (docker exec) – bash, falls vorhanden, sonst sh. Nach 30 Min. ohne Eingabe wird sie geschlossen.');
    $('term-open').textContent = t('Konsole öffnen');
  }

  function setStatus(level, text) {
    const tag = $('term-status');
    tag.className = `health-tag ${level}`;
    tag.textContent = text;
  }

  function showUnlock(message) {
    clearTimeout(retry.timer);
    $('terminal').hidden = true;
    $('term-unlock').hidden = false;
    $('term-reconnect').hidden = true;
    $('term-kill').hidden = true;
    loadSessions();
    $('term-password').value = '';
    $('term-error').hidden = !message;
    $('term-error').textContent = message || '';
    $('term-password').focus();
  }

  function ensureTerminal() {
    if (term) return;
    term = new window.Terminal({
      cursorBlink: true,
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 14,
      scrollback: 5000,
      theme: {
        background: '#05070c', foreground: '#d7e0ea', cursor: '#00f0ff', selectionBackground: 'rgba(0, 240, 255, 0.25)',
        black: '#1b2130', red: '#ff5c7c', green: '#00ffa3', yellow: '#ffb703', blue: '#38bdf8', magenta: '#a855f7', cyan: '#00f0ff', white: '#d7e0ea'
      }
    });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open($('terminal'));
    term.onData(data => {
      if (!readOnly && ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data }));
    });
    const sendSize = () => {
      fit.fit();
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
    };
    new ResizeObserver(sendSize).observe($('terminal'));
  }

  // Saved commands (Dokumentation -> Befehle): inserted at the prompt, never sent with Enter
  let snippets = null;

  function drawSnippets() {
    const list = $('term-snippets-list');
    list.innerHTML = '';
    const q = $('term-snippets-q').value.trim().toLowerCase();
    const items = (snippets || []).filter(s => !q || [s.title, s.command, ...(s.tags || [])].some(v => (v || '').toLowerCase().includes(q)));
    items.slice(0, 50).forEach(s => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'term-snippet';
      b.setAttribute('data-no-i18n', '');
      const title = document.createElement('strong');
      title.textContent = s.title;
      const cmd = document.createElement('code');
      cmd.textContent = s.command.split('\n')[0];
      b.append(title, cmd);
      b.addEventListener('click', () => {
        toggleSnippets(false);
        if (term) { term.paste(s.command); term.focus(); }
      });
      list.appendChild(b);
    });
    if (!items.length) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = snippets && snippets.length ? t('Keine Treffer.') : t('Noch keine Befehle gespeichert.');
      list.appendChild(p);
    }
  }

  async function toggleSnippets(open) {
    $('term-snippets-menu').hidden = !open;
    $('term-snippets').setAttribute('aria-expanded', String(open));
    if (!open) return;
    if (!snippets) {
      try { snippets = (await (await fetch('/api/docs/snippets')).json()).items || []; } catch (e) { snippets = []; }
    }
    $('term-snippets-q').value = '';
    drawSnippets();
    $('term-snippets-q').focus();
  }

  function connect(ticket, again) {
    $('term-unlock').hidden = true;
    $('terminal').hidden = false;
    ensureTerminal();
    fit.fit();
    if (!again) term.reset();
    term.focus();
    setStatus('warning', again ? 'Verbinde neu…' : 'Verbinde…');

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/terminal?ticket=${encodeURIComponent(ticket)}&rows=${term.rows}&cols=${term.cols}`);
    let exit = null;
    ws.onopen = () => {
      setStatus('good', 'Verbunden');
      retry = { n: 0, timer: null, since: 0 };
      clearInterval(pinger);
      // Keeps proxies (Cloudflare, nginx) from closing a quiet connection
      pinger = setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);
    };
    ws.onmessage = e => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'output') term.write(msg.data);
      else if (msg.type === 'session') {
        persistent = true;
        current = { id: msg.id, resume: msg.resume };
        store.set(current);
        role = msg.role || 'owner';
        readOnly = !!msg.read_only;
        term.options.disableStdin = readOnly;
        setStatus('good', readOnly ? `${t('Zuschauen bei')} ${msg.owner}` : role === 'write' ? `${t('Mitschreiben bei')} ${msg.owner}` : 'Verbunden · läuft dauerhaft');
        $('term-rec').hidden = !msg.recorded;
        $('term-kill').hidden = role !== 'owner';
        $('term-share').hidden = role !== 'owner';
        if (role === 'owner') watchViewers();
      } else if (msg.type === 'exit') exit = msg;
    };
    ws.onclose = e => {
      clearInterval(pinger);
      clearInterval(viewersTimer);
      $('term-kill').hidden = true;
      $('term-share').hidden = true;
      $('term-viewers').hidden = true;
      toggleShare(false);
      if (e.code === 4401) { store.set(null); return showUnlock(t('Sitzung abgelaufen oder Ticket ungültig – bitte erneut anmelden.')); }
      if (exit) {
        // Ended on purpose: the shell is gone, or idle (then the password is needed again)
        store.set(null);
        current = null;
        setStatus('', 'Getrennt');
        term.write(`\r\n\x1b[33m── ${t(exit.reason)} ──\x1b[0m\r\n`);
        $('term-reconnect').hidden = false;
        return;
      }
      if (current) return scheduleResume();
      setStatus('', 'Getrennt');
      term.write(`\r\n\x1b[33m── ${t('Verbindung getrennt.')} ──\x1b[0m\r\n`);
      $('term-reconnect').hidden = false;
    };
  }

  // Dropped connection: try again with growing pauses (1 s … 15 s) for up to 15 minutes
  function scheduleResume() {
    if (!retry.since) {
      retry.since = Date.now();
      term.write(`\r\n\x1b[33m── ${t('Verbindung unterbrochen – die Sitzung läuft weiter, verbinde neu…')} ──\x1b[0m\r\n`);
    }
    if (Date.now() - retry.since > 15 * 60000) {
      setStatus('', 'Getrennt');
      $('term-reconnect').hidden = false;
      return;
    }
    const wait = Math.min(15000, 1000 * 2 ** retry.n);
    retry.n++;
    setStatus('warning', t('Verbindung unterbrochen – neuer Versuch…'));
    clearTimeout(retry.timer);
    retry.timer = setTimeout(resume, wait);
  }

  async function resume() {
    if (!current) return showUnlock();
    let res;
    try {
      res = await fetch('/api/terminal/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: current.id, token: current.resume }) });
    } catch (e) { return scheduleResume(); }     // still offline
    if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
    if (res.status === 403 || res.status === 404) {
      store.set(null);
      current = null;
      return showUnlock(t('Die Sitzung läuft nicht mehr.'));
    }
    if (!res.ok) return scheduleResume();       // e.g. dashboard still restarting (502)
    const d = await res.json();
    connect(d.ticket, true);
  }

  // Today only the time, otherwise date and time
  const fmtTs = ts => {
    const d = new Date(ts * 1000);
    const today = d.toDateString() === new Date().toDateString();
    return d.toLocaleString(LOCALE, today ? { hour: '2-digit', minute: '2-digit' } : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  async function loadSessions() {
    let d;
    try { d = await (await fetch('/api/terminal/sessions')).json(); } catch (e) { return; }
    persistent = !!d.persistent;
    $('term-hint').textContent = (persistent
      ? t(`Die Shell läuft als dein Benutzer – für Root-Befehle sudo verwenden. Sie läuft weiter, wenn die Verbindung abbricht oder die Seite geschlossen wird; nach ${d.detached_hours} Std. ohne Nutzung wird sie beendet.`)
      : t('Die Shell läuft als dein Benutzer – für Root-Befehle sudo verwenden. Nach 30 Min. ohne Eingabe wird sie geschlossen.'))
      + (persistent && d.recording ? ` ${t('Sitzungen werden aufgezeichnet.')}` : '');
    const box = $('term-sessions');
    const list = $('term-sessions-list');
    list.innerHTML = '';
    const items = (d.sessions || []).filter(x => (x.container || '') === container || !container);
    box.hidden = !items.length;
    items.forEach(x => {
      const row = document.createElement('div');
      row.className = 'term-session';
      const info = document.createElement('div');
      const name = document.createElement('b');
      name.textContent = (x.container ? `${t('Container')} ${x.container}` : t('Shell auf dem Pi'))
        + (x.role === 'view' || x.role === 'write' ? ` · ${t('geteilt von')} ${x.user}` : '');
      name.setAttribute('data-no-i18n', '');
      const sub = document.createElement('small');
      sub.className = 'field-hint';
      sub.textContent = [`${t('seit')} ${fmtTs(x.created)}`, `${t('zuletzt aktiv')} ${fmtTs(x.activity)}`,
        x.attached ? t('gerade verbunden') : '', x.user !== d.user ? x.user : ''].filter(Boolean).join(' · ');
      sub.title = x.client ? `${t('Geöffnet von')} ${x.client}` : '';
      info.append(name, sub);
      const take = document.createElement('button');
      take.type = 'button';
      take.className = 'btn-secondary';
      take.textContent = t(x.role === 'view' ? 'Zuschauen' : x.role === 'write' ? 'Beitreten' : 'Übernehmen');
      take.hidden = !['owner', 'view', 'write'].includes(x.role);
      take.addEventListener('click', () => unlock(null, x.id));
      const kill = document.createElement('button');
      kill.type = 'button';
      kill.className = 'text-btn';
      kill.textContent = t('Beenden');
      kill.hidden = !['owner', 'other'].includes(x.role);
      kill.addEventListener('click', async () => {
        if (!confirm(t('Diese Sitzung beenden? Laufende Programme darin werden abgebrochen.'))) return;
        await fetch(`/api/terminal/sessions/${encodeURIComponent(x.id)}/close`, { method: 'POST' });
        loadSessions();
      });
      row.append(info, take, kill);
      list.appendChild(row);
    });
  }

  // ---------------------------------------------------------------------------
  // Sharing (owner only)
  // ---------------------------------------------------------------------------
  const MODE = { view: 'Zuschauen', write: 'Mitschreiben' };

  async function mySession() {
    try {
      const d = await (await fetch('/api/terminal/sessions')).json();
      return (d.sessions || []).find(x => current && x.id === current.id);
    } catch (e) { return null; }
  }

  function showViewers(s) {
    const box = $('term-viewers');
    const others = (s && s.connected || []).filter(c => c.user !== s.user);
    box.hidden = !others.length;
    box.textContent = others.length ? `${t('Verbunden')}: ${others.map(c => `${c.user} (${t(MODE[c.mode])})`).join(', ')}` : '';
  }

  function watchViewers() {
    clearInterval(viewersTimer);
    const tick = async () => showViewers(await mySession());
    tick();
    viewersTimer = setInterval(() => { if (!document.hidden) tick(); }, 10000);
  }

  async function renderShare() {
    const s = await mySession();
    showViewers(s);
    const list = $('term-share-list');
    list.innerHTML = '';
    (s && s.shares || []).forEach(sh => {
      const row = document.createElement('div');
      row.className = 'term-share-row';
      const who = document.createElement('span');
      who.setAttribute('data-no-i18n', '');
      const live = (s.connected || []).some(c => c.user === sh.user);
      who.textContent = `${sh.user} · ${t(MODE[sh.mode])}${live ? ` · ${t('verbunden')}` : ''}`;
      const end = document.createElement('button');
      end.type = 'button';
      end.className = 'text-btn';
      end.textContent = t('Freigabe beenden');
      end.addEventListener('click', async () => {
        await fetch(`/api/terminal/sessions/${encodeURIComponent(current.id)}/unshare`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: sh.user }) });
        renderShare();
      });
      row.append(who, end);
      list.appendChild(row);
    });
  }

  async function toggleShare(open) {
    $('term-share-menu').hidden = !open;
    $('term-share').setAttribute('aria-expanded', String(open));
    if (!open) return;
    const sel = $('term-share-user');
    sel.innerHTML = '';
    let users = [];
    try { users = (await (await fetch('/api/terminal/share-users')).json()).users || []; } catch (e) { /* offline */ }
    users.forEach(u => { const o = document.createElement('option'); o.value = u; o.textContent = u; sel.appendChild(o); });
    $('term-share-add').disabled = !users.length;
    $('term-share-hint').textContent = users.length ? t('Die Person öffnet das Terminal und wählt die Sitzung unter „Laufende Sitzungen“.')
      : t('Es gibt keinen anderen Benutzer mit dem Recht „Web-Terminal“.');
    renderShare();
  }

  async function addShare() {
    const res = await fetch(`/api/terminal/sessions/${encodeURIComponent(current.id)}/share`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: $('term-share-user').value, mode: $('term-share-mode').value })
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { alert(d.detail || t('Teilen fehlgeschlagen.')); return; }
    renderShare();
  }

  async function unlock(e, session) {
    if (e) e.preventDefault();
    if (session && !$('term-password').value) {
      $('term-error').hidden = false;
      $('term-error').textContent = t('Zum Übernehmen bitte zuerst das Passwort eingeben.');
      $('term-password').focus();
      return;
    }
    const btn = $('term-open');
    btn.disabled = true;
    try {
      const res = await fetch('/api/terminal/ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: $('term-password').value, container, session: session || '' })
      });
      if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Terminal konnte nicht geöffnet werden.');
      connect(data.ticket);
    } catch (err) {
      $('term-error').hidden = false;
      $('term-error').textContent = err.message;
      $('term-password').value = '';
      $('term-password').focus();
    } finally {
      btn.disabled = false;
    }
  }

  async function init() {
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    if (!window.Terminal || !window.FitAddon) {
      showUnlock('xterm.js konnte nicht geladen werden.');
      return;
    }
    $('term-unlock').addEventListener('submit', unlock);
    $('term-reconnect').addEventListener('click', () => showUnlock());
    $('term-share').addEventListener('click', e => { e.stopPropagation(); toggleShare($('term-share-menu').hidden); });
    $('term-share-add').addEventListener('click', addShare);
    document.addEventListener('click', e => { if (!e.target.closest('.term-share-wrap')) toggleShare(false); });
    $('term-kill').addEventListener('click', async () => {
      if (!current || !confirm(t('Sitzung beenden? Laufende Programme darin werden abgebrochen.'))) return;
      await fetch(`/api/terminal/sessions/${encodeURIComponent(current.id)}/close`, { method: 'POST' });
    });
    window.addEventListener('online', () => { if (current && retry.since) { clearTimeout(retry.timer); resume(); } });
    $('term-snippets').hidden = false;
    $('term-snippets').addEventListener('click', e => { e.stopPropagation(); toggleSnippets($('term-snippets-menu').hidden); });
    $('term-snippets-q').addEventListener('input', drawSnippets);
    $('term-snippets-q').addEventListener('keydown', e => {
      if (e.key === 'Escape') toggleSnippets(false);
      if (e.key === 'Enter') { e.preventDefault(); $('term-snippets-list').querySelector('.term-snippet')?.click(); }
    });
    document.addEventListener('click', e => { if (!e.target.closest('.term-snippets-wrap')) toggleSnippets(false); });
    // After a reload this tab goes straight back into its session
    current = store.get();
    if (current) { ensureTerminal(); resume(); return; }
    showUnlock();
  }

  init();
})();
