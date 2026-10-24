"""
Custom quick actions: executable scripts in ~/pi-dashboard-actions become buttons.

Each script may describe itself in its first lines:
    # name: Backup starten
    # description: Sichert /home nach /mnt/backup
    # confirm: yes          (ask before running)
    # timeout: 900          (seconds, default 300, max 3600)

Scripts run as the dashboard user (no root - use your own sudo rule if a script needs
it), without arguments, in the actions folder. Each run is a task (tasks.py): it keeps
running without the page and across dashboard restarts, its output is kept in the task log.
"""

import os
import re
from typing import Any, Dict, List

ACTIONS_DIR = os.environ.get("PI_DASHBOARD_ACTIONS", os.path.expanduser("~/pi-dashboard-actions"))
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
MAX_OUTPUT = 200_000           # characters kept per run (the tail)
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
    import tasks
    _ensure_dir()
    recent = tasks.overview(limit=100, kind="action")["tasks"]
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
        last = next((t for t in recent if t["key"] == f"action:{name}"), None)
        items.append({"id": name, "name": meta.get("name") or re.sub(r"\.[a-z0-9]+$", "", name),
                      "description": meta.get("description", ""),
                      "confirm": meta.get("confirm", "").lower() in ("yes", "ja", "true", "1"),
                      "timeout": timeout, "last": _summary(last) if last else None})
    home = os.path.expanduser("~")
    shown = "~" + ACTIONS_DIR[len(home):] if ACTIONS_DIR.startswith(home + os.sep) else ACTIONS_DIR
    return {"dir": shown, "actions": items, "skipped": skipped}


STATE = {"running": "running", "ok": "done", "error": "failed", "lost": "failed", "cancelled": "stopped", "timeout": "timeout"}


def _summary(task: Dict[str, Any]) -> Dict[str, Any]:
    d = task.get("data") or {}
    return {"id": task["id"], "action": d.get("action", ""), "name": d.get("name", task["title"]), "state": STATE.get(task["state"], "failed"),
            "exit_code": task.get("exit"), "started": task["started"], "finished": task.get("ended"), "user": task.get("user", "")}


def run_action(action_id: str, user: str) -> Dict[str, Any]:
    import tasks
    path = _resolve(action_id)
    meta = _meta(path)
    try:
        timeout = max(5, min(int(meta.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    name = meta.get("name") or action_id
    env = {"PICORE_USER": user or "", "TERM": "dumb"}
    try:
        task = tasks.start("action", f"Aktion: {name}", [{"argv": [path], "show": name}], user=user, key=f"action:{action_id}",
                           cwd=os.path.realpath(ACTIONS_DIR), env=env, timeout=timeout,
                           data={"action": action_id, "name": name, "timeout": timeout})
    except tasks.TaskError:
        raise RuntimeError("Diese Aktion läuft bereits.")
    return get_run(task["id"])


def get_run(run_id: str, offset: int = 0) -> Dict[str, Any]:
    import tasks
    try:
        r = tasks.log(run_id, offset, limit=MAX_OUTPUT)
    except tasks.TaskError:
        raise ValueError("Lauf nicht gefunden.")
    task = r["task"]
    if task["kind"] != "action":
        raise ValueError("Lauf nicht gefunden.")
    return {**_summary(task), "output": ANSI.sub("", r["text"]), "offset": r["offset"],
            "timeout": (task.get("data") or {}).get("timeout", DEFAULT_TIMEOUT), "truncated": r["truncated"]}


def stop_run(run_id: str, user: str = "") -> Dict[str, Any]:
    import tasks
    try:
        tasks.cancel(run_id, user)
    except tasks.TaskError as e:
        if "nicht gefunden" in str(e):
            raise ValueError("Lauf nicht gefunden.")
    return get_run(run_id)
