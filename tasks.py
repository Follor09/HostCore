"""
Tasks (like Proxmox's task log): long-running work - compose actions, app installs, automatic
updates, quick actions, database dumps, system updates - runs as a task with its own log.

Each task is started as a transient systemd user unit (picore-task-<id>, needs lingering for
the dashboard user, set by install.sh) running taskrun.py. So it does not depend on the
browser at all and keeps running when the dashboard restarts; without a user systemd it
falls back to a detached process. Everything lives in tasks/: <id>.json (what to run),
<id>.log (output), <id>.status (written by the runner). The last MAX_KEEP tasks are kept.

When a task ends, the dashboard runs the "on_done" hook its module registered (refresh a
cache, prune old dumps ...) - also for tasks that finished while the dashboard was down.
apt runs (root, own unit, see apt-run.sh) appear here as well, with their own log.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("pi_dashboard.tasks")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_DIR = os.path.join(BASE_DIR, "tasks")
RUNNER = os.path.join(BASE_DIR, "taskrun.py")
MAX_KEEP = 300
KEEP_DAYS = 60
ID_RE = re.compile(r"^[0-9a-f]{16}$")
STATES = {"running": "Läuft", "ok": "OK", "error": "Fehler", "cancelled": "Abgebrochen", "timeout": "Zeitüberschreitung",
          "lost": "Unterbrochen"}
_hooks: Dict[str, Callable[[Dict[str, Any]], None]] = {}
_lock = threading.Lock()
_user_systemd: Optional[bool] = None


class TaskError(Exception):
    pass


def on_done(kind: str, fn: Callable[[Dict[str, Any]], None]) -> None:
    """fn(task) runs once in the dashboard after a task of this kind has ended."""
    _hooks[kind] = fn


def _path(task_id: str, ext: str) -> str:
    if not ID_RE.match(task_id or ""):
        raise TaskError("Aufgabe nicht gefunden.")
    return os.path.join(TASK_DIR, f"{task_id}.{ext}")


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _systemd_env() -> Dict[str, str]:
    uid = os.getuid()
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env


def user_systemd() -> bool:
    """Whether transient user units work here (user manager running, i.e. lingering on)."""
    global _user_systemd
    if _user_systemd is None:
        ok = False
        if shutil.which("systemd-run") and os.path.exists(f"/run/user/{os.getuid()}/bus"):
            try:
                res = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True,
                                     timeout=5, env=_systemd_env())
                ok = res.stdout.strip() in ("running", "degraded")
            except (OSError, subprocess.SubprocessError):
                ok = False
        _user_systemd = ok
    return _user_systemd


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------
def start(kind: str, title: str, steps: List[Dict[str, Any]], user: str = "", key: str = "", cwd: str = "",
          env: Optional[Dict[str, str]] = None, timeout: int = 3600, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """steps: [{"argv": [...]} | {"shell": "..."}, optional "show": text for the log].
    key: one running task per key (e.g. "stack:nextcloud"); data: free details for the hook."""
    os.makedirs(TASK_DIR, mode=0o700, exist_ok=True)
    with _lock:
        if key and any(t["key"] == key and t["state"] == "running" for t in _all(limit=50)):
            raise TaskError("Dafür läuft schon eine Aufgabe.")
        task_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(2)}"
        spec = {"id": task_id, "kind": kind, "title": title[:200], "user": user, "key": key, "cwd": cwd, "env": env or {},
                "timeout": int(timeout), "steps": steps, "created": int(time.time()), "data": data or {}}
        _write_json(_path(task_id, "json"), spec)
        open(_path(task_id, "log"), "wb").close()
        cmd = [sys.executable, RUNNER, TASK_DIR, task_id]
        launched = False
        if user_systemd():
            unit = f"picore-task-{task_id}"
            sd = ["systemd-run", "--user", "--unit", unit, "--collect", "--quiet", f"--working-directory={cwd or BASE_DIR}",
                  "--property=KillMode=control-group", "--property=TimeoutStopSec=30"]
            for k in ("PATH", "HOME", "LANG", "USER", "LOGNAME"):
                if os.environ.get(k):
                    sd.append(f"--setenv={k}={os.environ[k]}")
            try:
                res = subprocess.run(sd + cmd, capture_output=True, text=True, timeout=15, env=_systemd_env())
                launched = res.returncode == 0
                if launched:
                    spec["unit"] = unit
                else:
                    logger.warning(f"systemd-run failed ({res.stderr.strip()[:200]}), starting task directly")
            except (OSError, subprocess.SubprocessError) as e:
                logger.warning(f"systemd-run failed ({e}), starting task directly")
        if not launched:
            subprocess.Popen(cmd, cwd=cwd or BASE_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        _write_json(_path(task_id, "json"), spec)
    return get(task_id)


def register_external(kind: str, title: str, user: str, log_path: str, status_path: str, key: str = "") -> Dict[str, Any]:
    """A run started elsewhere (apt via its root unit) that has its own log and status file."""
    os.makedirs(TASK_DIR, mode=0o700, exist_ok=True)
    task_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(2)}"
    spec = {"id": task_id, "kind": kind, "title": title, "user": user, "key": key, "steps": [], "created": int(time.time()),
            "external": {"log": log_path, "status": status_path}, "data": {}}
    _write_json(_path(task_id, "json"), spec)
    return get(task_id)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _state(spec: Dict[str, Any]) -> Dict[str, Any]:
    ext = spec.get("external")
    if ext:
        st = _read_json(ext["status"]) or {}
        if st.get("started", 0) < spec["created"] - 5:
            st = {}   # still the status of an earlier run
        state = st.get("state") or "running"
        state = {"done": "ok", "failed": "error"}.get(state, state)
        if state == "running" and time.time() - spec["created"] > 6 * 3600:
            state = "lost"
        return {"state": state, "started": st.get("started") or spec["created"], "ended": st.get("finished"), "exit": st.get("exit_code")}
    st = _read_json(_path(spec["id"], "status"))
    if not st:
        # Not started yet - or the runner never came up
        return {"state": "running" if time.time() - spec["created"] < 30 else "lost", "started": spec["created"], "ended": None, "exit": None}
    if st["state"] == "running" and not _alive(st.get("pid")):
        st = {**st, "state": "lost"}
    return st


def _public(spec: Dict[str, Any]) -> Dict[str, Any]:
    st = _state(spec)
    out = {k: spec.get(k) for k in ("id", "kind", "title", "user", "key", "created", "data")}
    out.update(state=st["state"], state_label=STATES.get(st["state"], st["state"]), started=st.get("started") or spec["created"],
               ended=st.get("ended"), exit=st.get("exit"), steps=len(spec.get("steps") or []), step=st.get("step", 0))
    out["duration"] = int((out["ended"] or time.time()) - out["started"])
    return out


def _ids() -> List[str]:
    try:
        return sorted((f[:-5] for f in os.listdir(TASK_DIR) if f.endswith(".json") and ID_RE.match(f[:-5])), reverse=True)
    except FileNotFoundError:
        return []


def _all(limit: int = MAX_KEEP) -> List[Dict[str, Any]]:
    out = []
    for task_id in _ids()[:limit]:
        spec = _read_json(_path(task_id, "json"))
        if spec:
            out.append(_public(spec))
    return out


def get(task_id: str) -> Dict[str, Any]:
    spec = _read_json(_path(task_id, "json"))
    if not spec:
        raise TaskError("Aufgabe nicht gefunden.")
    return _public(spec)


def overview(limit: int = 100, kind: str = "", key: str = "") -> Dict[str, Any]:
    items = [t for t in _all() if (not kind or t["kind"] == kind) and (not key or t["key"] == key)]
    return {"tasks": items[:limit], "running": sum(1 for t in items if t["state"] == "running"),
            "failed_24h": sum(1 for t in items if t["state"] in ("error", "timeout", "lost") and t["started"] > time.time() - 86400),
            "durable": user_systemd()}


def log(task_id: str, offset: int = 0, limit: int = 512 * 1024) -> Dict[str, Any]:
    """Output from byte offset on (the page polls with the returned offset)."""
    spec = _read_json(_path(task_id, "json"))
    if not spec:
        raise TaskError("Aufgabe nicht gefunden.")
    path = spec["external"]["log"] if spec.get("external") else _path(task_id, "log")
    try:
        size = os.path.getsize(path)
        start = max(0, min(int(offset), size))
        truncated = False
        if size - start > limit:
            start, truncated = size - limit, True
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(limit)
    except OSError:
        size, data, truncated = 0, b"", False
    return {"task": _public(spec), "text": data.decode("utf-8", errors="replace"), "offset": start + len(data),
            "size": size, "truncated": truncated}


def tail(task_id: str, chars: int = 200_000) -> str:
    r = log(task_id, 0, limit=chars)
    return r["text"]


# ---------------------------------------------------------------------------
# Changing
# ---------------------------------------------------------------------------
def cancel(task_id: str, by: str = "") -> Dict[str, Any]:
    spec = _read_json(_path(task_id, "json"))
    if not spec:
        raise TaskError("Aufgabe nicht gefunden.")
    if spec.get("external"):
        raise TaskError("Systemupdates lassen sich nicht abbrechen.")
    st = _state(spec)
    if st["state"] != "running":
        raise TaskError("Die Aufgabe läuft nicht mehr.")
    with open(_path(task_id, "log"), "ab") as f:
        f.write(f"\n[Abbruch angefordert von {by or 'unbekannt'}]\n".encode())
    if st.get("pid"):
        try:
            os.kill(st["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif spec.get("unit"):
        subprocess.run(["systemctl", "--user", "stop", spec["unit"]], capture_output=True, timeout=20, env=_systemd_env())
    return get(task_id)


def _cleanup() -> None:
    ids = _ids()
    cutoff = time.time() - KEEP_DAYS * 86400
    for n, task_id in enumerate(ids):
        if n < 20:
            continue
        spec = _read_json(_path(task_id, "json")) or {}
        if n >= MAX_KEEP or spec.get("created", 0) < cutoff:
            if spec and _state(spec)["state"] == "running":
                continue
            for ext in ("json", "log", "status"):
                try:
                    os.remove(_path(task_id, ext))
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Hooks, events
# ---------------------------------------------------------------------------
def poll() -> None:
    """Run the on_done hooks of tasks that ended (once; remembered in the task's own file)."""
    for task_id in _ids()[:50]:
        spec = _read_json(_path(task_id, "json"))
        if not spec or spec.get("hooked"):
            continue
        t = _public(spec)
        if t["state"] == "running":
            continue
        spec["hooked"] = True
        _write_json(_path(task_id, "json"), spec)
        fn = _hooks.get(spec["kind"])
        if fn:
            try:
                fn(t)
            except Exception as e:
                logger.warning(f"Task hook {spec['kind']} {task_id}: {e}")


def events_since(since: float) -> List[Dict[str, Any]]:
    out = []
    failed_before: Dict[str, bool] = {}
    for t in reversed(_all(limit=150)):          # oldest first
        bad = t["state"] in ("error", "timeout", "lost")
        if bad and (t["ended"] or t["started"]) >= since:
            out.append({"ts": int(t["ended"] or t["started"]), "level": "critical" if (t.get("data") or {}).get("critical") else "warning",
                        "title": f"Aufgabe fehlgeschlagen: {t['title']}",
                        "detail": t["state_label"] + (f" (Exit {t['exit']})" if t.get("exit") not in (None, 0) else ""),
                        "key": f"task:{t['key'] or t['id']}", "link": f"#aufgabe={t['id']}"})
        elif t["state"] == "ok" and failed_before.get(t["key"]) and t["ended"] and t["ended"] >= since:
            # The same thing worked again: closes the alarm of the failed one
            out.append({"ts": int(t["ended"]), "level": "good", "title": f"Aufgabe wieder erfolgreich: {t['title']}", "detail": "",
                        "key": f"task:{t['key']}"})
        if t["key"] and t["state"] != "running":
            failed_before[t["key"]] = bad
    return out


async def background_runner() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(poll)
        except Exception as e:
            logger.warning(f"Tasks: {e}")
        if int(time.time()) % 3600 < 5:
            await asyncio.to_thread(_cleanup)
