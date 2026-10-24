"""
Backup check (System -> Backups): watches backups that run elsewhere and warns when one is
overdue. Three kinds:

- restic repository: newest snapshot, number of snapshots, size (raw data, once a day)
- Borg repository: newest archive, number of archives, size on disk
- Folder: newest file below a folder (e.g. rsync targets, database dumps, Proxmox vzdump)

Checked hourly as the dashboard user (the repository must be readable by it). Passwords stay
in settings.json. A change of state (overdue, check failed, current again) becomes an event, so
it reaches the event feed and the e-mail notifications.
"""

import fnmatch
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

logger = logging.getLogger("pi_dashboard.backups")

KEY = "backups"
STATUS_KEY = "backup_status"
EVENTS_KEY = "backup_events"
KINDS = {"restic": "restic", "borg": "Borg", "folder": "Ordner"}
MAX_JOBS = 30
CHECK_INTERVAL = 3600
SIZE_INTERVAL = 24 * 3600
FOLDER_MAX_FILES = 50_000
ENV_ALLOWED = re.compile(r"^(RESTIC_|AWS_|B2_|AZURE_|GOOGLE_|OS_|ST_|RCLONE_|BORG_RSH$|BORG_REMOTE_PATH$)[A-Z0-9_]*$")
_lock = threading.Lock()
_running: Dict[str, bool] = {}


class BackupError(Exception):
    pass


def _jobs() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _status() -> Dict[str, Any]:
    s = settings_store.get(STATUS_KEY) or {}
    return s if isinstance(s, dict) else {}


def tools() -> Dict[str, bool]:
    return {"restic": bool(shutil.which("restic")), "borg": bool(shutil.which("borg"))}


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _state(job: Dict[str, Any], st: Dict[str, Any], now: float) -> str:
    if not st.get("checked"):
        return "unknown"
    if st.get("error"):
        return "error"
    last = st.get("last_backup")
    if not last:
        return "overdue"
    return "overdue" if now - last > job.get("max_age_hours", 26) * 3600 else "ok"


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {**{k: v for k, v in job.items() if k not in ("password", "env")},
            "has_password": bool(job.get("password")), "env_keys": sorted((job.get("env") or {}).keys())}


def overview() -> Dict[str, Any]:
    now = time.time()
    st = _status()
    items = []
    for job in _jobs():
        s = st.get(job["id"], {})
        state = _state(job, s, now)
        items.append({**_public(job), **{k: v for k, v in s.items() if k != "state"}, "state": state,
                      "age_hours": round((now - s["last_backup"]) / 3600, 1) if s.get("last_backup") else None,
                      "running": _running.get(job["id"], False)})
    order = {"error": 0, "overdue": 1, "unknown": 2, "ok": 3}
    items.sort(key=lambda i: (order[i["state"]], i["name"].lower()))
    return {"jobs": items, "kinds": KINDS, "tools": tools(),
            "problems": sum(1 for i in items if i["state"] in ("error", "overdue"))}


def alerts() -> List[Dict[str, Any]]:
    out = []
    for j in overview()["jobs"]:
        if j["state"] == "error":
            out.append({"level": "warning", "text": f"Backup {j['name']}: Prüfung fehlgeschlagen – {j.get('error', '')}"})
        elif j["state"] == "overdue":
            age = j["age_hours"]
            late = age is None or age > 3 * j.get("max_age_hours", 26)
            out.append({"level": "critical" if late else "warning",
                        "text": f"Backup {j['name']} überfällig" + (f" – letztes vor {_fmt_age(age)}" if age is not None else " – noch keins gefunden")})
    return out


def _fmt_age(hours: float) -> str:
    return f"{hours:.0f} Std." if hours < 48 else f"{hours / 24:.0f} Tagen"


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def save(job_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    kind = str(fields.get("kind") or "")
    if kind not in KINDS:
        raise BackupError("Unbekannte Art.")
    name = str(fields.get("name") or "").strip()[:60]
    if not name:
        raise BackupError("Name fehlt.")
    target = str(fields.get("target") or "").strip()
    if not target or target.startswith("-") or len(target) > 500 or "\n" in target:
        raise BackupError("Repository bzw. Ordner fehlt oder ist ungültig.")
    if kind == "folder":
        target = os.path.realpath(os.path.expanduser(target))
        if not os.path.isdir(target):
            raise BackupError("Ordner nicht gefunden.")
    try:
        max_age = int(fields.get("max_age_hours") or 26)
    except (TypeError, ValueError):
        raise BackupError("Höchstalter: Anzahl Stunden.")
    if not 1 <= max_age <= 24 * 90:
        raise BackupError("Höchstalter: 1 Stunde bis 90 Tage.")
    pattern = str(fields.get("pattern") or "").strip()[:100]
    env_raw = fields.get("env")
    with _lock:
        jobs = _jobs()
        job = next((j for j in jobs if j["id"] == job_id), None) if job_id else None
        if job_id and not job:
            raise BackupError("Backup nicht gefunden.")
        env = {} if fields.get("clear_env") else dict((job or {}).get("env") or {})
        if env_raw:  # empty: keep the stored variables (they hold secrets and are never sent back)
            env = {}
            lines = env_raw.splitlines() if isinstance(env_raw, str) else [f"{k}={v}" for k, v in dict(env_raw).items()]
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                k, sep, v = line.partition("=")
                k = k.strip()
                if not sep or not ENV_ALLOWED.match(k):
                    raise BackupError(f"Umgebungsvariable „{k}“ nicht erlaubt (nur RESTIC_*, AWS_*, B2_*, AZURE_*, GOOGLE_*, RCLONE_*, BORG_RSH …).")
                env[k] = v.strip()
        clean = {"name": name, "kind": kind, "target": target, "max_age_hours": max_age, "pattern": pattern,
                 "notes": str(fields.get("notes") or "").strip()[:300], "env": env}
        password = fields.get("password")
        if job:
            job.update(clean, updated=int(time.time()))
            if password:
                job["password"] = str(password)
            if fields.get("clear_password"):
                job.pop("password", None)
        else:
            if len(jobs) >= MAX_JOBS:
                raise BackupError(f"Höchstens {MAX_JOBS} Backups.")
            job = {"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user}
            if password:
                job["password"] = str(password)
            jobs.append(job)
        settings_store.set(KEY, jobs)
    start_check(job["id"])
    return overview()


def delete(job_id: str) -> Dict[str, Any]:
    with _lock:
        settings_store.set(KEY, [j for j in _jobs() if j["id"] != job_id])
        st = _status()
        st.pop(job_id, None)
        settings_store.set(STATUS_KEY, st)
    return overview()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _run(cmd: List[str], env: Dict[str, str], timeout: int) -> str:
    full_env = {**os.environ, **env}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=full_env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise BackupError(f"Zeitüberschreitung nach {timeout} s")
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip().splitlines()
        msg = next((l for l in reversed(err) if l.strip()), f"Exit-Code {res.returncode}")
        if msg.lstrip().startswith("{"):  # restic --json reports errors as JSON too
            try:
                msg = json.loads(msg).get("message") or msg
            except ValueError:
                pass
        msg = (msg.replace("Fatal: ", "").strip().splitlines() or [""])[0]
        if re.search(r"wrong password|passphrase supplied.*incorrect|password is incorrect", " ".join(err), re.I):
            msg = "Falsches Passwort"
        raise BackupError(msg[:200])
    return res.stdout


def _ts(value: str) -> Optional[float]:
    """RFC 3339 from restic / ISO from borg -> unix time."""
    import datetime
    if not value:
        return None
    v = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        dt = datetime.datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _check_restic(job: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    if not shutil.which("restic"):
        raise BackupError("restic ist auf diesem Pi nicht installiert (sudo apt install restic)")
    env = {**(job.get("env") or {}), "RESTIC_REPOSITORY": job["target"]}
    if job.get("password"):
        env["RESTIC_PASSWORD"] = job["password"]
    snaps = json.loads(_run(["restic", "snapshots", "--json", "--no-lock"], env, 180) or "[]") or []
    snaps.sort(key=lambda s: _ts(s.get("time", "")) or 0)
    out = {"snapshots": len(snaps), "size": old.get("size"), "size_checked": old.get("size_checked", 0)}
    if snaps:
        s = snaps[-1]
        out["last_backup"] = _ts(s.get("time", ""))
        out["latest"] = {"id": s.get("short_id") or s.get("id", "")[:8], "host": s.get("hostname", ""),
                         "paths": s.get("paths", [])[:5], "tags": s.get("tags", []) or []}
    if time.time() - out["size_checked"] > SIZE_INTERVAL:
        try:
            stats = json.loads(_run(["restic", "stats", "--json", "--mode", "raw-data", "--no-lock"], env, 600))
            out.update(size=stats.get("total_size"), size_checked=int(time.time()))
        except (BackupError, ValueError) as e:
            logger.info(f"restic stats for {job['name']}: {e}")
    return out


def _check_borg(job: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    if not shutil.which("borg"):
        raise BackupError("Borg ist auf diesem Pi nicht installiert (sudo apt install borgbackup)")
    env = {**(job.get("env") or {}), "BORG_REPO": job["target"], "BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK": "yes",
           "BORG_RELOCATED_REPO_ACCESS_IS_OK": "yes", "BORG_PASSPHRASE": job.get("password", "")}
    listing = json.loads(_run(["borg", "list", "--json", "--lock-wait", "30"], env, 180))
    archives = listing.get("archives", [])
    archives.sort(key=lambda a: _ts(a.get("start") or a.get("time", "")) or 0)
    out = {"snapshots": len(archives)}
    if archives:
        a = archives[-1]
        out["last_backup"] = _ts(a.get("start") or a.get("time", ""))
        out["latest"] = {"id": a.get("name", ""), "host": "", "paths": [], "tags": []}
    try:
        info = json.loads(_run(["borg", "info", "--json", "--lock-wait", "30"], env, 180))
        out["size"] = ((info.get("cache") or {}).get("stats") or {}).get("unique_csize")
    except (BackupError, ValueError) as e:
        logger.info(f"borg info for {job['name']}: {e}")
    return out


def _check_folder(job: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    root = job["target"]
    if not os.path.isdir(root):
        raise BackupError("Ordner nicht (mehr) vorhanden oder nicht lesbar")
    pattern = job.get("pattern") or ""
    newest, newest_path, count, size = 0.0, "", 0, 0
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".picore-trash")]
        for n in names:
            if pattern and not fnmatch.fnmatch(n, pattern):
                continue
            try:
                st = os.stat(os.path.join(dirpath, n), follow_symlinks=False)
            except OSError:
                continue
            count += 1
            size += st.st_size
            if st.st_mtime > newest:
                newest, newest_path = st.st_mtime, os.path.join(dirpath, n)
            if count >= FOLDER_MAX_FILES:
                break
        if count >= FOLDER_MAX_FILES:
            break
    out = {"snapshots": count, "size": size, "truncated": count >= FOLDER_MAX_FILES}
    if newest:
        out["last_backup"] = newest
        out["latest"] = {"id": os.path.relpath(newest_path, root), "host": "", "paths": [], "tags": []}
    return out


CHECKERS = {"restic": _check_restic, "borg": _check_borg, "folder": _check_folder}

TITLES = {"overdue": ("warning", "Backup {name} überfällig"), "error": ("warning", "Backup-Prüfung {name} fehlgeschlagen"),
          "ok": ("good", "Backup {name} wieder aktuell")}


def check(job_id: str) -> None:
    job = next((j for j in _jobs() if j["id"] == job_id), None)
    if not job:
        return
    old = _status().get(job_id, {})
    t0 = time.time()
    try:
        res = CHECKERS[job["kind"]](job, old)
        res.update(error="")
    except (BackupError, ValueError, OSError) as e:
        res = {k: old.get(k) for k in ("last_backup", "latest", "snapshots", "size", "size_checked") if k in old}
        res["error"] = str(e) if isinstance(e, BackupError) else f"{type(e).__name__}: {e}"[:200]
    res.update(checked=int(time.time()), duration=round(time.time() - t0, 1))
    new_state = _state(job, res, time.time())
    res["state"] = new_state
    prev_state = old.get("state")
    with _lock:
        st = _status()
        st[job_id] = res
        settings_store.set(STATUS_KEY, st)
        if prev_state and prev_state != new_state and new_state in TITLES and not (prev_state == "unknown" and new_state == "ok"):
            level, title = TITLES[new_state]
            detail = res.get("error", "") if new_state == "error" else (
                f"letztes Backup {time.strftime('%d.%m. %H:%M', time.localtime(res['last_backup']))}" if res.get("last_backup") else "")
            events = (settings_store.get(EVENTS_KEY, []) or [])[-199:]
            events.append({"ts": int(time.time()), "level": level, "title": title.format(name=job["name"]), "detail": detail, "job": job_id})
            settings_store.set(EVENTS_KEY, events)


def _check_thread(job_id: str) -> None:
    try:
        check(job_id)
    except Exception as e:
        logger.warning(f"Backup check {job_id}: {e}")
    finally:
        _running.pop(job_id, None)


def start_check(job_id: str) -> Dict[str, Any]:
    with _lock:
        if not _running.get(job_id):
            _running[job_id] = True
            threading.Thread(target=_check_thread, args=(job_id,), daemon=True).start()
    return overview()


def check_if_due() -> None:
    """Hourly from the monitor; also catches the state 'overdue' between two checks."""
    st = _status()
    now = time.time()
    for job in _jobs():
        s = st.get(job["id"], {})
        if now - s.get("checked", 0) >= CHECK_INTERVAL - 60:
            start_check(job["id"])
