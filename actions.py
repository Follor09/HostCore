"""
Custom quick actions: executable scripts in ~/pi-dashboard-actions become buttons.

Each script may describe itself in its first lines:
    # name: Backup starten
    # description: Sichert /home nach /mnt/backup
    # confirm: yes          (ask before running)
    # timeout: 900          (seconds, default 300, max 3600)

Scripts run as the dashboard user (no root - use your own sudo rule if a script needs
it), without arguments, in the actions folder. Output is kept in memory for the last
runs and streamed to the browser by polling.
"""

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

ACTIONS_DIR = os.environ.get("PI_DASHBOARD_ACTIONS", os.path.expanduser("~/pi-dashboard-actions"))
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
MAX_OUTPUT = 200_000           # characters kept per run (the tail)
KEEP_RUNS = 20
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r(?!\n)")

EXAMPLE = """#!/usr/bin/env bash
# name: System-Info
# description: Beispiel: zeigt Laufzeit, Speicher und Temperatur. Eigene Skripte in diesen Ordner legen und mit chmod +x ausführbar machen.
echo "== Laufzeit"
uptime
echo
echo "== Speicher"
df -h / | tail -1
echo
echo "== Temperatur"
vcgencmd measure_temp 2>/dev/null || echo "vcgencmd nicht verfügbar"
"""

_runs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _ensure_dir() -> None:
    if not os.path.isdir(ACTIONS_DIR):
        os.makedirs(ACTIONS_DIR, mode=0o755, exist_ok=True)
        path = os.path.join(ACTIONS_DIR, "system-info.sh")
        with open(path, "w") as f:
            f.write(EXAMPLE)
        os.chmod(path, 0o755)


def _meta(path: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    try:
        with open(path, errors="replace") as f:
            for _ in range(25):
                line = f.readline()
                if not line:
                    break
                m = re.match(r"^\s*(?:#|//|--)\s*(name|description|beschreibung|confirm|timeout)\s*:\s*(.+?)\s*$", line, re.I)
                if m:
                    key = m.group(1).lower().replace("beschreibung", "description")
                    meta.setdefault(key, m.group(2))
    except OSError:
        pass
    return meta


def _resolve(action_id: str) -> str:
    """Path of an action - only plain executable files directly inside the actions folder."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", action_id or ""):
        raise ValueError("Unbekannte Aktion.")
    base = os.path.realpath(ACTIONS_DIR)
    path = os.path.realpath(os.path.join(base, action_id))
    if os.path.dirname(path) != base or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise ValueError("Unbekannte Aktion.")
    return path


def list_actions() -> Dict[str, Any]:
    _ensure_dir()
    items: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for name in sorted(os.listdir(ACTIONS_DIR), key=str.lower):
        if name.startswith("."):
            continue
        try:
            path = _resolve(name)
        except ValueError:
            if os.path.isfile(os.path.join(ACTIONS_DIR, name)):
                skipped.append(name)  # e.g. not executable yet
            continue
        meta = _meta(path)
        try:
            timeout = max(5, min(int(meta.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT))
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        last = next((r for r in sorted(_runs.values(), key=lambda r: r["started"], reverse=True)
                     if r["action"] == name), None)
        items.append({"id": name, "name": meta.get("name") or re.sub(r"\.[a-z0-9]+$", "", name),
                      "description": meta.get("description", ""),
                      "confirm": meta.get("confirm", "").lower() in ("yes", "ja", "true", "1"),
                      "timeout": timeout, "last": _summary(last) if last else None})
    home = os.path.expanduser("~")
    shown = "~" + ACTIONS_DIR[len(home):] if ACTIONS_DIR.startswith(home + os.sep) else ACTIONS_DIR
    return {"dir": shown, "actions": items, "skipped": skipped}


def _summary(run: Dict[str, Any]) -> Dict[str, Any]:
    return {k: run[k] for k in ("id", "action", "name", "state", "exit_code", "started", "finished", "user")}


def _reader(run: Dict[str, Any], proc: subprocess.Popen) -> None:
    for chunk in iter(lambda: proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096), b""):
        text = ANSI.sub("", chunk.decode("utf-8", errors="replace"))
        with _lock:
            run["output"] += text
            if len(run["output"]) > MAX_OUTPUT:
                cut = len(run["output"]) - MAX_OUTPUT
                run["output"] = run["output"][cut:]
                run["dropped"] += cut


def _watch(run: Dict[str, Any], proc: subprocess.Popen, timeout: int) -> None:
    reader = threading.Thread(target=_reader, args=(run, proc), daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
        state = "stopped" if run.get("stop_requested") else ("done" if code == 0 else "failed")
    except subprocess.TimeoutExpired:
        _kill(proc)
        code = proc.wait()
        state = "timeout"
    reader.join(timeout=2)
    with _lock:
        run.update(state=state, exit_code=code, finished=time.time())


def _kill(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def run_action(action_id: str, user: str) -> Dict[str, Any]:
    path = _resolve(action_id)
    with _lock:
        if any(r["action"] == action_id and r["state"] == "running" for r in _runs.values()):
            raise RuntimeError("Diese Aktion läuft bereits.")
    meta = _meta(path)
    try:
        timeout = max(5, min(int(meta.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    env = {"HOME": os.path.expanduser("~"), "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "LANG": os.environ.get("LANG", "C.UTF-8"), "USER": os.environ.get("USER", ""),
           "PICORE_USER": user or "", "TERM": "dumb"}
    proc = subprocess.Popen([path], cwd=os.path.realpath(ACTIONS_DIR), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    run = {"id": uuid.uuid4().hex[:12], "action": action_id, "name": meta.get("name") or action_id,
           "state": "running", "exit_code": None, "started": time.time(), "finished": None,
           "output": "", "dropped": 0, "user": user, "pid": proc.pid, "proc": proc, "timeout": timeout}
    with _lock:
        _runs[run["id"]] = run
        # forget the oldest finished runs
        done = sorted((r for r in _runs.values() if r["state"] != "running"), key=lambda r: r["started"])
        for r in done[:max(0, len(_runs) - KEEP_RUNS)]:
            _runs.pop(r["id"], None)
    threading.Thread(target=_watch, args=(run, proc, timeout), daemon=True).start()
    return get_run(run["id"])


def get_run(run_id: str, offset: int = 0) -> Dict[str, Any]:
    with _lock:
        run = _runs.get(run_id)
        if not run:
            raise ValueError("Lauf nicht gefunden.")
        # offset counts characters since the start, including dropped ones
        start = max(0, offset - run["dropped"])
        return {**_summary(run), "output": run["output"][start:], "offset": run["dropped"] + len(run["output"]),
                "timeout": run["timeout"], "truncated": run["dropped"] > 0 and offset < run["dropped"]}


def stop_run(run_id: str) -> Dict[str, Any]:
    with _lock:
        run = _runs.get(run_id)
    if not run:
        raise ValueError("Lauf nicht gefunden.")
    if run["state"] == "running":
        run["stop_requested"] = True
        threading.Thread(target=_kill, args=(run["proc"],), daemon=True).start()
    return get_run(run_id)
