/**
 * PiCore Dashboard - Web terminal (xterm.js <-> /ws/terminal)
 * Flow: password -> one-time ticket -> WebSocket -> login shell in a PTY.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  let term = null, fit = null, ws = null;

  function setStatus(level, text) {
    const tag = $('term-status');
    tag.className = `health-tag ${level}`;
    tag.textContent = text;
  }

  function showUnlock(message) {
    $('terminal').hidden = true;
    $('term-unlock').hidden = false;
    $('term-reconnect').hidden = true;
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
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data }));
    });
    const sendSize = () => {
      fit.fit();
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
    };
    new ResizeObserver(sendSize).observe($('terminal'));
  }

  function connect(ticket) {
    $('term-unlock').hidden = true;
    $('terminal').hidden = false;
    ensureTerminal();
    fit.fit();
    term.reset();
    term.focus();
    setStatus('warning', 'Verbinde…');

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/terminal?ticket=${encodeURIComponent(ticket)}&rows=${term.rows}&cols=${term.cols}`);
    let exitReason = null;
    ws.onopen = () => setStatus('good', 'Verbunden');
    ws.onmessage = e => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'output') term.write(msg.data);
      else if (msg.type === 'exit') exitReason = msg.reason;
    };
    ws.onclose = e => {
      if (e.code === 4401) return showUnlock('Sitzung abgelaufen oder Ticket ungültig – bitte erneut anmelden.');
      setStatus('', 'Getrennt');
      term.write(`\r\n\x1b[33m── ${exitReason || 'Verbindung getrennt.'} ──\x1b[0m\r\n`);
      $('term-reconnect').hidden = false;
    };
  }

  async function unlock(e) {
    e.preventDefault();
    const btn = $('term-open');
    btn.disabled = true;
    try {
      const res = await fetch('/api/terminal/ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: $('term-password').value })
      });
      if (res.status === 401) { location.href = '/login'; return; }
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
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    if (!window.Terminal || !window.FitAddon) {
      showUnlock('xterm.js konnte nicht geladen werden.');
      return;
    }
    $('term-unlock').addEventListener('submit', unlock);
    $('term-reconnect').addEventListener('click', () => showUnlock());
    showUnlock();
  }

  init();
})();
