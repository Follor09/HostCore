"""
Web terminal: a real login shell in a pseudo-terminal, streamed over a WebSocket.

Safety:
- Opening a terminal needs the password again (not just the session cookie). That
  yields a one-time ticket valid for 60 s, which the WebSocket must present.
- The WebSocket also checks the session cookie and that the Origin is the dashboard
  itself (blocks cross-site WebSocket hijacking).
- The shell runs as the dashboard user; sudo prompts for a password as usual.
- At most MAX_SESSIONS at once; idle sessions close after IDLE_TIMEOUT.
"""

import asyncio
import codecs
import fcntl
import json
import logging
import os
import pwd
import secrets
import signal
import struct
import subprocess
import termios
import time
from typing import Dict, Optional

logger = logging.getLogger("pi_dashboard.terminal")

TICKET_TTL = 60
MAX_SESSIONS = 3
IDLE_TIMEOUT = 30 * 60

_tickets: Dict[str, tuple] = {}   # ticket -> (username, expires)
_sessions: Dict[int, dict] = {}   # pid -> info


def issue_ticket(username: str) -> str:
    now = time.time()
    for t, (_, exp) in list(_tickets.items()):
        if exp < now:
            _tickets.pop(t, None)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (username, now + TICKET_TTL)
    return ticket


def redeem_ticket(ticket: Optional[str], username: str) -> bool:
    """One-time use: a ticket is consumed even if it turns out to be invalid."""
    entry = _tickets.pop(ticket or "", None)
    return bool(entry) and entry[0] == username and entry[1] >= time.time()


def session_count() -> int:
    return len(_sessions)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(2, min(int(rows), 500))
    cols = max(10, min(int(cols), 1000))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _spawn_shell(rows: int, cols: int):
    user = pwd.getpwuid(os.getuid())
    master, slave = os.openpty()
    _set_winsize(master, rows, cols)
    env = {
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "HOME": user.pw_dir,
        "USER": user.pw_name,
        "LOGNAME": user.pw_name,
        "SHELL": user.pw_shell or "/bin/bash",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    shell = user.pw_shell or "/bin/bash"

    def make_controlling_tty():
        # New session + the pty as controlling terminal -> job control, Ctrl+C, sudo prompts work
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [shell, "-l"], stdin=slave, stdout=slave, stderr=slave,
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


async def run_session(websocket, username: str, client: str, rows: int, cols: int) -> None:
    """Bridge one accepted WebSocket to a fresh shell until either side closes."""
    proc, master = await asyncio.to_thread(_spawn_shell, rows, cols)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue = asyncio.Queue()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    last_input = time.time()
    _sessions[proc.pid] = {"user": username, "client": client, "started": int(time.time())}
    logger.info(f"Terminal opened by '{username}' from {client} (pid {proc.pid})")

    def on_readable():
        try:
            data = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        output.put_nowait(data)  # b"" = shell exited

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
            if msg.get("type") == "input":
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

    async def idle_watch():
        while time.time() - last_input < IDLE_TIMEOUT:
            await asyncio.sleep(30)

    tasks = [asyncio.create_task(t) for t in (pump_output(), pump_input(), idle_watch())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if tasks[1] not in done:
            # Shell exited or idle timeout: tell the browser why. If the browser left
            # (input pump ended), there is nobody to tell - sending could block.
            reason = "Shell beendet." if tasks[0] in done else "Wegen Inaktivität geschlossen (30 Min.)."
            try:
                await asyncio.wait_for(websocket.send_text(json.dumps({"type": "exit", "reason": reason})), 2)
                await asyncio.wait_for(websocket.close(), 2)
            except Exception:
                pass
    finally:
        for t in tasks:
            t.cancel()
        loop.remove_reader(master)
        # Closing the pty master is a real hangup: the kernel sends SIGHUP to the shell,
        # which passes it on to its jobs (like an SSH disconnect). Signals are the fallback.
        os.close(master)
        try:
            await asyncio.to_thread(_terminate, proc)
        finally:
            _sessions.pop(proc.pid, None)
            logger.info(f"Terminal closed for '{username}' from {client} (pid {proc.pid})")
