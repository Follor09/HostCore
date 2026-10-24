"""
Web terminal: a real login shell in a pseudo-terminal, streamed over a WebSocket.

Persistent sessions (with tmux installed, see install.sh): the shell runs in its own tmux
server (tmux -L picore, started as a systemd user unit), the WebSocket only attaches to it.
A dropped connection, a closed tab or a dashboard restart leave the shell running; the page
reconnects by itself with the session's resume token (no password - the login cookie is still
required), and the session can be taken over from another device with the password. Sessions
nobody looked at for DETACHED_TTL end. Without tmux the shell ends with the connection.

Sharing: the owner can give another user (with the terminal permission) a session to watch
(read-only tmux client) or to type along; ending the share disconnects them at once. Each
session is recorded (recordings.py) unless that is switched off.

Safety:
- Opening a terminal needs the password again (not just the session cookie). That
  yields a one-time ticket valid for 60 s, which the WebSocket must present.
- The WebSocket also checks the session cookie and that the Origin is the dashboard
  itself (blocks cross-site WebSocket hijacking).
- The shell runs as the dashboard user; sudo prompts for a password as usual.
- At most MAX_SESSIONS at once; an idle connection closes after IDLE_TIMEOUT (the session
  itself stays, but coming back then needs the password again).
"""

import asyncio
import codecs
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import pwd
import re
import secrets
import shutil
import signal
import struct
import subprocess
import termios
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("pi_dashboard.terminal")

TICKET_TTL = 60
MAX_SESSIONS = 5
IDLE_TIMEOUT = 30 * 60
DETACHED_TTL = 24 * 3600
TMUX = shutil.which("tmux")
SOCKET = "picore"
CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terminal-tmux.conf")
SESSION_RE = re.compile(r"^picore-[0-9a-f]{12}$")

_tickets: Dict[str, tuple] = {}   # ticket -> (username, expires, container, session, read_only)
_sessions: Dict[int, dict] = {}   # pid -> info (connections without tmux)
_attached: Dict[str, Dict[int, dict]] = {}   # session -> {pid of the tmux client: {user, mode, client, proc}}
MODES = {"view": "Zuschauen", "write": "Mitschreiben"}


class TerminalError(Exception):
    pass


CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def issue_ticket(username: str, container: str = "", session: str = "", read_only: bool = False) -> str:
    """container: open a shell inside this running Docker container instead of on the Pi.
    session: attach to this existing (persistent) session instead of starting a new one;
    read_only: only watch it (a session shared for viewing)."""
    now = time.time()
    for t, entry in list(_tickets.items()):
        if entry[1] < now:
            _tickets.pop(t, None)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (username, now + TICKET_TTL, container, session, read_only)
    return ticket


def redeem_ticket(ticket: Optional[str], username: str) -> Optional[Tuple[str, str, bool]]:
    """One-time use: a ticket is consumed even if it turns out to be invalid.
    Returns (container ("" = the Pi), session ("" = a new one), read_only) or None."""
    entry = _tickets.pop(ticket or "", None)
    if not entry or entry[0] != username or entry[1] < time.time():
        return None
    return entry[2], entry[3], entry[4]


def check_container(name: str) -> str:
    """The container must exist and run - a shell in it is opened with docker exec."""
    if not CONTAINER_RE.match(name or ""):
        raise ValueError("Ungültiger Container-Name.")
    res = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        raise ValueError(f"Container {name} nicht gefunden.")
    if res.stdout.strip() != "true":
        raise ValueError(f"Container {name} läuft nicht.")
    return name


def session_count() -> int:
    return len(_sessions) + (len(sessions()) if TMUX else 0)


# ---------------------------------------------------------------------------
# Persistent sessions (tmux)
# ---------------------------------------------------------------------------
def persistent() -> bool:
    return bool(TMUX)


def _tmux(args: List[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run([TMUX, "-L", SOCKET, "-f", CONF, *args], capture_output=True, text=True, timeout=timeout,
                          env=_shell_env(), errors="replace")


def _no_server(res: subprocess.CompletedProcess) -> bool:
    return res.returncode != 0 and bool(re.search(r"no server running|error connecting|No such file", res.stderr, re.I))


def _ensure_server() -> None:
    """Start the tmux server as a systemd user unit, so it is not part of the dashboard service
    and survives its restarts. Without user systemd, new-session starts it the usual way."""
    if not _no_server(_tmux(["list-sessions"])):
        return
    import tasks
    if not tasks.user_systemd():
        return
    cmd = ["systemd-run", "--user", "--unit", "picore-terminals", "--collect", "--quiet",
           f"--working-directory={pwd.getpwuid(os.getuid()).pw_dir}"]
    cmd += [f"--setenv={k}={v}" for k, v in _shell_env().items()]
    cmd += [TMUX, "-L", SOCKET, "-f", CONF, "-D"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=15, env=tasks._systemd_env())
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not start the terminal server as a user unit: {e}")
        return
    for _ in range(30):
        if not _no_server(_tmux(["list-sessions"])):
            return
        time.sleep(0.1)


FIELDS = ["session_name", "session_created", "session_attached", "session_activity", "@picore_user", "@picore_container", "@picore_client",
          "@picore_share", "@picore_recorded"]


def _parse_share(text: str) -> Dict[str, str]:
    """"bob:view,anna:write" -> {"bob": "view", "anna": "write"}"""
    out = {}
    for part in (text or "").split(","):
        user, _, mode = part.partition(":")
        if user and mode in MODES:
            out[user] = mode
    return out


def sessions(user: Optional[str] = None) -> List[Dict[str, Any]]:
    """Running persistent sessions (of one user - own ones and the ones shared with them - or all).
    "role": owner, view or write - for the user asked about."""
    if not TMUX:
        return []
    try:
        res = _tmux(["list-sessions", "-F", "\t".join("#{%s}" % f for f in FIELDS)])
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != len(FIELDS) or not SESSION_RE.match(parts[0]):
            continue
        name, created, attached, activity, owner, container, client, share, recorded = parts
        shared = _parse_share(share)
        if user is not None and owner != user and user not in shared:
            continue
        role = "owner" if user is None or owner == user else shared[user]
        connected = [{"user": c["user"], "mode": c["mode"]} for c in (_attached.get(name) or {}).values()]
        out.append({"id": name, "created": int(created or 0), "attached": int(attached or 0), "activity": int(activity or 0),
                    "user": owner, "container": container, "client": client, "role": role, "recorded": recorded == "1",
                    "shares": [{"user": u, "mode": m} for u, m in sorted(shared.items())] if role == "owner" else [],
                    "connected": connected if role == "owner" else []})
    out.sort(key=lambda s: -s["activity"])
    return out


def session(sid: str) -> Optional[Dict[str, Any]]:
    return next((s for s in sessions() if s["id"] == sid), None) if SESSION_RE.match(sid or "") else None


def _resume_option(user: str) -> str:
    """One resume token per user and session - a viewer reconnecting does not log out the owner."""
    return "@picore_resume_" + hashlib.sha256(user.encode()).hexdigest()[:12]


def _set_resume(sid: str, user: str) -> str:
    token = secrets.token_urlsafe(32)
    _tmux(["set-option", "-t", sid, _resume_option(user), hashlib.sha256(token.encode()).hexdigest()])
    return token


def access(sid: str, user: str) -> Optional[str]:
    """The user's role in a session: owner / write / view - or None."""
    s = next((x for x in sessions(user) if x["id"] == sid), None) if SESSION_RE.match(sid or "") else None
    return s["role"] if s else None


def check_resume(sid: str, user: str, token: str) -> bool:
    if not token or not access(sid, user):
        return False
    res = _tmux(["show-options", "-v", "-t", sid, _resume_option(user)])
    stored = res.stdout.strip()
    return bool(stored) and hmac.compare_digest(stored, hashlib.sha256(token.encode()).hexdigest())


def share(sid: str, owner: str, user: str, mode: str) -> None:
    s = session(sid)
    if not s or s["user"] != owner:
        raise TerminalError("Sitzung nicht gefunden.")
    if mode not in MODES:
        raise TerminalError("Unbekannte Freigabe.")
    if not re.match(r"^[\w.-]{1,64}$", user or "") or user == owner:
        raise TerminalError("Ungültiger Benutzer.")
    shared = _parse_share(_tmux(["show-options", "-v", "-t", sid, "@picore_share"]).stdout.strip())
    shared[user] = mode
    _tmux(["set-option", "-t", sid, "@picore_share", ",".join(f"{u}:{m}" for u, m in sorted(shared.items()))])
    # Changed from typing to watching: reconnect them as watchers
    _kick(sid, lambda c: c["user"] == user and c["mode"] != mode, "Die Freigabe wurde geändert.")


def unshare(sid: str, owner: str, user: str) -> None:
    s = session(sid)
    if not s or s["user"] != owner:
        raise TerminalError("Sitzung nicht gefunden.")
    shared = _parse_share(_tmux(["show-options", "-v", "-t", sid, "@picore_share"]).stdout.strip())
    shared.pop(user, None)
    if shared:
        _tmux(["set-option", "-t", sid, "@picore_share", ",".join(f"{u}:{m}" for u, m in sorted(shared.items()))])
    else:
        _tmux(["set-option", "-u", "-t", sid, "@picore_share"])
    _tmux(["set-option", "-u", "-t", sid, _resume_option(user)])
    _kick(sid, lambda c: c["user"] == user, "Die Freigabe wurde beendet.")


def _kick(sid: str, which, reason: str) -> None:
    for pid, c in list((_attached.get(sid) or {}).items()):
        if which(c):
            c["kicked"] = reason
            try:
                os.killpg(pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass


def _new_session(user: str, client: str, rows: int, cols: int, container: str) -> str:
    _ensure_server()
    sid = f"picore-{secrets.token_hex(6)}"
    cmd = ["docker", "exec", "-it", "-e", "TERM=xterm-256color", container, "sh", "-c",
           "command -v bash >/dev/null 2>&1 && exec bash || exec sh"] if container else []
    res = _tmux(["new-session", "-d", "-s", sid, "-x", str(max(10, min(cols, 1000))), "-y", str(max(2, min(rows, 500))),
                 "-c", pwd.getpwuid(os.getuid()).pw_dir, *cmd])
    if res.returncode != 0:
        raise TerminalError(f"Terminal konnte nicht gestartet werden: {res.stderr.strip()[:200]}")
    for key, value in (("@picore_user", user), ("@picore_container", container), ("@picore_client", client)):
        _tmux(["set-option", "-t", sid, key, value])
    try:
        import recordings
        if recordings.start(sid, user, container, client, cols, rows, _tmux):
            _tmux(["set-option", "-t", sid, "@picore_recorded", "1"])
    except Exception as e:
        logger.warning(f"Terminal recording for {sid} not started: {e}")
    return sid


def close_session(sid: str) -> None:
    if not session(sid):
        raise TerminalError("Sitzung nicht gefunden.")
    _tmux(["kill-session", "-t", sid])


def cleanup() -> None:
    """End sessions nobody has looked at for DETACHED_TTL."""
    now = time.time()
    for s in sessions():
        if not s["attached"] and now - s["activity"] > DETACHED_TTL:
            logger.info(f"Terminal session {s['id']} of '{s['user']}' ended after {DETACHED_TTL // 3600} h without use")
            _tmux(["kill-session", "-t", s["id"]])


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(2, min(int(rows), 500))
    cols = max(10, min(int(cols), 1000))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _shell_env() -> Dict[str, str]:
    user = pwd.getpwuid(os.getuid())
    return {
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "HOME": user.pw_dir,
        "USER": user.pw_name,
        "LOGNAME": user.pw_name,
        "SHELL": user.pw_shell or "/bin/bash",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }


def _spawn_shell(rows: int, cols: int, container: str = "", argv: Optional[List[str]] = None):
    """argv: run this instead of a login shell (attaching to a tmux session)."""
    user = pwd.getpwuid(os.getuid())
    master, slave = os.openpty()
    _set_winsize(master, rows, cols)
    env = _shell_env()
    shell = user.pw_shell or "/bin/bash"

    def make_controlling_tty():
        # New session + the pty as controlling terminal -> job control, Ctrl+C, sudo prompts work
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    # In a container: bash if the image has it, sh otherwise
    if argv is None:
        argv = ["docker", "exec", "-it", "-e", "TERM=xterm-256color", container, "sh", "-c",
                "command -v bash >/dev/null 2>&1 && exec bash || exec sh"] if container else [shell, "-l"]
    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=slave,
        cwd=user.pw_dir, env=env, start_new_session=True,
        preexec_fn=make_controlling_tty, close_fds=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    return proc, master


def _terminate(proc: subprocess.Popen) -> None:
    """Make sure the shell is gone (after the pty hangup), then reap it."""
    try:
        proc.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass  # group already gone (a zombie-only group reports EPERM on macOS)
        try:
            proc.wait(timeout=1.5)
            return
        except subprocess.TimeoutExpired:
            continue


async def run_session(websocket, username: str, client: str, rows: int, cols: int, container: str = "", sid: str = "",
                      read_only: bool = False) -> None:
    """Bridge one accepted WebSocket to a shell (on the Pi or in a container) until either side closes.
    With tmux: to a persistent session (sid = attach to that one, otherwise a new one;
    read_only = a session shared for watching)."""
    if not TMUX:
        await _bridge(websocket, username, client, rows, cols, container=container)
        return
    try:
        if not sid:
            sid = await asyncio.to_thread(_new_session, username, client, rows, cols, container)
            logger.info(f"Terminal session {sid} started by '{username}' from {client}")
        role = await asyncio.to_thread(access, sid, username)
        if not role:
            raise TerminalError("Sitzung nicht gefunden.")
        read_only = read_only or role == "view"
        token = await asyncio.to_thread(_set_resume, sid, username)
    except (TerminalError, OSError, subprocess.SubprocessError) as e:
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "exit", "reason": str(e)}))
            await websocket.close()
        return
    info = await asyncio.to_thread(session, sid) or {}
    await websocket.send_text(json.dumps({"type": "session", "id": sid, "resume": token, "persistent": True,
                                          "container": info.get("container", container), "created": info.get("created", 0),
                                          "role": role, "read_only": read_only, "owner": info.get("user", username),
                                          "recorded": info.get("recorded", False)}))
    argv = [TMUX, "-L", SOCKET, "-f", CONF, "attach-session", *(["-r"] if read_only else []), "-t", sid]
    ended = await _bridge(websocket, username, client, rows, cols, argv=argv, sid=sid, mode="view" if read_only else "write")
    logger.info(f"Terminal session {sid}: '{username}' from {client} {'left' if ended == 'browser' else ended}")


async def _bridge(websocket, username: str, client: str, rows: int, cols: int, container: str = "",
                  argv: Optional[List[str]] = None, sid: str = "", mode: str = "write") -> str:
    """Pump between WebSocket and a pty until one side ends. Returns "browser", "shell", "detached" or "idle"."""
    proc, master = await asyncio.to_thread(_spawn_shell, rows, cols, container, argv)
    conn = {"user": username, "mode": mode, "client": client}
    if sid:
        _attached.setdefault(sid, {})[proc.pid] = conn
    loop = asyncio.get_running_loop()
    output: asyncio.Queue = asyncio.Queue()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    last_input = time.time()
    if not sid:
        _sessions[proc.pid] = {"user": username, "client": client, "started": int(time.time())}
        logger.info(f"Terminal opened by '{username}' from {client} (pid {proc.pid})")

    def on_readable():
        try:
            data = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        output.put_nowait(data)  # b"" = shell (or the tmux client) exited

    loop.add_reader(master, on_readable)

    async def pump_output():
        while True:
            data = await output.get()
            if not data:
                return
            text = decoder.decode(data)
            if text:
                await websocket.send_text(json.dumps({"type": "output", "data": text}))

    async def pump_input():
        nonlocal last_input
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "input" and mode == "write":
                last_input = time.time()
                data = str(msg.get("data", "")).encode("utf-8")
                while data:
                    try:
                        written = os.write(master, data)
                        data = data[written:]
                    except BlockingIOError:
                        await asyncio.sleep(0.01)
            elif msg.get("type") == "resize":
                _set_winsize(master, msg.get("rows", 24), msg.get("cols", 80))
            # "ping" (keep-alive from the page) is read and ignored: it does not count as activity

    async def idle_watch():
        while time.time() - last_input < IDLE_TIMEOUT:
            await asyncio.sleep(30)

    tasks = [asyncio.create_task(t) for t in (pump_output(), pump_input(), idle_watch())]
    ended = "browser"
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if tasks[1] not in done:
            # Shell exited or idle timeout: tell the browser why. If the browser left
            # (input pump ended), there is nobody to tell - sending could block.
            ended = "shell" if tasks[0] in done else "idle"
            if ended == "shell" and sid and await asyncio.to_thread(session, sid):
                ended = "detached"   # the tmux client went away, the session did not (closed elsewhere?)
            reason = {"shell": "Shell beendet.", "detached": conn.get("kicked") or "Verbindung zur Sitzung getrennt.",
                      "idle": "Wegen Inaktivität getrennt (30 Min.)." + (" Die Sitzung läuft weiter." if sid else "")}[ended]
            if conn.get("kicked"):
                ended = "kicked"
            try:
                await asyncio.wait_for(websocket.send_text(json.dumps({"type": "exit", "reason": reason, "why": ended})), 2)
                await asyncio.wait_for(websocket.close(), 2)
            except Exception:
                pass
    finally:
        for t in tasks:
            t.cancel()
        loop.remove_reader(master)
        # Closing the pty master is a real hangup: the kernel sends SIGHUP to the shell,
        # which passes it on to its jobs (like an SSH disconnect). With tmux only the
        # attached client gets it - the session keeps running. Signals are the fallback.
        os.close(master)
        try:
            await asyncio.to_thread(_terminate, proc)
        finally:
            if sid:
                (_attached.get(sid) or {}).pop(proc.pid, None)
                if not _attached.get(sid):
                    _attached.pop(sid, None)
            else:
                _sessions.pop(proc.pid, None)
                logger.info(f"Terminal closed for '{username}' from {client} (pid {proc.pid})")
    return ended
