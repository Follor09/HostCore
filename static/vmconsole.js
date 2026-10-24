/**
 * HostCore Dashboard - console of one VM (/vm-konsole?name=<vm>), like Cockpit's VM console.
 *
 * Grafisch: noVNC (static/vendor/novnc, MPL-2.0) through /ws/vm-console?kind=vnc, which the
 * dashboard connects to the VM's VNC port on 127.0.0.1 - the port is never opened to the network.
 * Seriell: xterm.js through /ws/vm-console?kind=serial (virsh console, same messages as the
 * web terminal). Every connection needs a one-time ticket (POST .../console, needs "VMs verwalten"
 * and - with re-authentication on - the password again).
 */
import RFB from './vendor/novnc/core/rfb.js';

const $ = id => document.getElementById(id);
const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
const name = new URLSearchParams(location.search).get('name') || '';
let kind = '';
let info = null;
let rfb = null, ws = null, term = null, fit = null, pinger = null;

function toast(message, type = 'info') {
  const n = document.createElement('div');
  n.className = `toast ${type}`;
  n.textContent = message;
  $('toast-container').appendChild(n);
  setTimeout(() => n.remove(), 5000);
}

function setStatus(level, text) {
  const s = $('con-status');
  s.className = `health-tag ${level}`;
  s.textContent = t(text);
}

async function ticket() {
  const res = await fetch(`/api/vms/vm/${encodeURIComponent(name)}/console`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; throw new Error('Nicht angemeldet.'); }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
  return json;
}

const wsUrl = (tk, k, extra = '') => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/vm-console?ticket=${encodeURIComponent(tk)}&kind=${k}${extra}`;

function disconnect() {
  clearInterval(pinger);
  if (rfb) { const r = rfb; rfb = null; try { r.disconnect(); } catch (e) { /* already closed */ } }
  if (ws) { const w = ws; ws = null; w.onclose = null; try { w.close(); } catch (e) { /* already closed */ } }
}

function showKind(k) {
  kind = k;
  document.querySelectorAll('#con-kind button').forEach(b => {
    b.classList.toggle('active', b.dataset.kind === k);
    b.disabled = !(info && info[b.dataset.kind]);
  });
  $('con-vnc').hidden = k !== 'vnc';
  $('con-serial').hidden = k !== 'serial';
  $('con-cad').hidden = k !== 'vnc';
  $('con-paste').hidden = k !== 'vnc';
  $('con-hint').textContent = k === 'serial'
    ? t('Serielle Konsole: Enter drücken, falls kein Login erscheint. Linux-Gäste brauchen dafür console=ttyS0 (Cloud-Images haben das).')
    : t('Tastatur und Maus gehen an die VM, solange das Bild den Fokus hat.');
}

async function connect(k) {
  disconnect();
  setStatus('warning', 'Verbindet …');
  $('con-reconnect').hidden = true;
  try {
    const tk = await ticket();
    info = { vnc: tk.vnc, serial: tk.serial };
    if (!info[k]) k = info.vnc ? 'vnc' : 'serial';
    if (!info[k]) throw new Error(t('Die VM hat weder eine grafische noch eine serielle Konsole.'));
    showKind(k);
    if (k === 'vnc') connectVnc(tk.ticket);
    else connectSerial(tk.ticket);
  } catch (err) {
    setStatus('critical', 'Getrennt');
    $('con-hint').textContent = t(err.message);
    $('con-reconnect').hidden = false;
  }
}

function connectVnc(tk) {
  const screen = $('con-vnc');
  screen.innerHTML = '';
  rfb = new RFB(screen, wsUrl(tk, 'vnc'), { shared: true });
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.background = '#05070c';
  const mine = rfb;
  rfb.addEventListener('connect', () => { if (rfb === mine) { setStatus('good', 'Verbunden'); rfb.focus(); } });
  rfb.addEventListener('disconnect', e => {
    if (rfb !== mine) return;
    rfb = null;
    setStatus('critical', e.detail && e.detail.clean ? 'Getrennt' : 'Verbindung verloren');
    $('con-reconnect').hidden = false;
  });
  rfb.addEventListener('credentialsrequired', () => toast(t('Die VM verlangt ein VNC-Passwort – das unterstützt die Konsole nicht.'), 'error'));
}

function ensureTerm() {
  if (term) return;
  term = new window.Terminal({
    cursorBlink: true, fontFamily: "'JetBrains Mono', monospace", fontSize: 14, scrollback: 5000,
    theme: {
      background: '#05070c', foreground: '#d7e0ea', cursor: '#00f0ff', selectionBackground: 'rgba(0, 240, 255, 0.25)',
      black: '#1b2130', red: '#ff5c7c', green: '#00ffa3', yellow: '#ffb703', blue: '#38bdf8', magenta: '#a855f7', cyan: '#00f0ff', white: '#d7e0ea'
    }
  });
  fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('con-serial'));
  term.onData(data => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data })); });
  new ResizeObserver(() => {
    if ($('con-serial').hidden) return;
    fit.fit();
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
  }).observe($('con-serial'));
}

function connectSerial(tk) {
  ensureTerm();
  fit.fit();
  term.reset();
  const sock = new WebSocket(wsUrl(tk, 'serial', `&rows=${term.rows}&cols=${term.cols}`));
  ws = sock;
  let reason = '';
  sock.onopen = () => {
    setStatus('good', 'Verbunden');
    term.focus();
    sock.send(JSON.stringify({ type: 'input', data: '\r' }));   // wakes up the login prompt
    pinger = setInterval(() => { if (sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify({ type: 'ping' })); }, 25000);
  };
  sock.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'output') term.write(msg.data);
    else if (msg.type === 'exit') reason = msg.reason || '';
  };
  sock.onclose = e => {
    if (ws !== sock) return;
    ws = null;
    clearInterval(pinger);
    setStatus('critical', 'Getrennt');
    $('con-hint').textContent = t(reason || e.reason || (e.code === 4401 ? 'Ticket abgelaufen – neu verbinden.' : 'Verbindung beendet.'));
    $('con-reconnect').hidden = false;
  };
}

// Typing a text into the VM (e.g. a long password at an installer): one key press per character
function typeText(text) {
  if (!rfb) return;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if (ch === '\n') { rfb.sendKey(0xff0d, 'Enter'); continue; }
    if (ch === '\t') { rfb.sendKey(0xff09, 'Tab'); continue; }
    if (cp < 0x20) continue;
    rfb.sendKey(cp < 0x100 ? cp : 0x01000000 + cp);
  }
  rfb.focus();
}

document.title = `${name} – ${t('Konsole')} | HostCore Server`;
$('con-title').textContent = name || t('Konsole');
document.querySelectorAll('#con-kind button').forEach(b => b.addEventListener('click', () => { if (b.dataset.kind !== kind) connect(b.dataset.kind); }));
$('con-reconnect').addEventListener('click', () => connect(kind || 'vnc'));
$('con-cad').addEventListener('click', () => { if (rfb) { rfb.sendCtrlAltDel(); rfb.focus(); } });
$('con-paste').addEventListener('click', () => {
  const text = window.prompt(t('Text, der in die VM getippt werden soll (US-Tastaturbelegung in der VM empfohlen):'));
  if (text) typeText(text);
});
$('con-full').addEventListener('click', () => {
  const target = document.querySelector('.vmconsole-wrap');
  if (document.fullscreenElement) document.exitFullscreen();
  else if (target.requestFullscreen) target.requestFullscreen();
});
window.addEventListener('beforeunload', disconnect);

if (!name) {
  setStatus('critical', 'Getrennt');
  $('con-hint').textContent = t('Keine VM angegeben.');
} else {
  connect('vnc');
}
