"""
File integrity monitoring (System -> Sicherheit -> Datei-Integrität): unexpected changes to system
files and configuration.

- A root scan (fimscan.py through pi-helper.sh, as a task) hashes the watched folders. The first
  scan is the baseline; every later one reports added, changed and removed files and becomes the
  new baseline, so a change is reported once and stays as a finding until someone closes it.
- Causes are looked up, so that only the unexplained remains: files of packages installed or
  updated since the last scan (dpkg.log) and changes during an approved change request are closed
  automatically and stay visible with their cause.
- Sensitive files (accounts, sudo, SSH, cron, boot) are critical; everything else is a warning.
- Daily by default; the result appears in the status overview and raises events.
"""

import glob
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import secdb
import settings_store
import tasks

logger = logging.getLogger("pi_dashboard.fim")

KEY = "fim"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(BASE_DIR, "tasks")
HELPER = "/var/lib/pi-dashboard/src/pi-helper.sh"
DEFAULTS = {"enabled": False, "hour": 3, "paths": ["/etc", "/usr/bin", "/usr/sbin", "/usr/local", "/boot", "/root/.ssh", "/var/spool/cron"],
            "exclude": [], "last_scan": 0, "last_files": 0, "last_day": "", "last_error": ""}
ALLOWED = ("/etc", "/usr/bin", "/usr/sbin", "/usr/lib/systemd", "/usr/local", "/bin", "/sbin", "/boot", "/opt", "/root/.ssh", "/var/spool/cron")
CRITICAL = re.compile(r"^(/etc/(passwd|shadow|group|gshadow|sudoers(\.d/.*)?|ssh/.*|pam\.d/.*|crontab|cron\.[^/]+/.*|ld\.so\.preload|systemd/system/.*)"
                      r"|/root/\.ssh/.*|/var/spool/cron/.*|/boot/.*|/usr/s?bin/(sudo|su|ssh|sshd|login|passwd))$")
KINDS = {"added": "Neu", "changed": "Geändert", "removed": "Entfernt", "meta": "Rechte geändert"}
CAUSES = {"": "Unerklärt", "package": "Paket-Update", "change": "Genehmigte Änderung"}
MAX_REPORT = 2000


class FimError(Exception):
    pass


def config() -> Dict[str, Any]:
    return {**DEFAULTS, **(settings_store.get(KEY, {}) or {})}


def _update(**fields: Any) -> Dict[str, Any]:
    c = {**config(), **fields}
    settings_store.set(KEY, c)
    return c


def save_config(fields: Dict[str, Any]) -> Dict[str, Any]:
    paths = [str(p).strip().rstrip("/") for p in fields.get("paths") or [] if str(p).strip()]
    for p in paths:
        if not any(p == a or p.startswith(a + "/") for a in ALLOWED) or ".." in p:
            raise FimError(f"{p}: erlaubt sind {', '.join(ALLOWED)}.")
    if not paths or len(paths) > 30:
        raise FimError("1 bis 30 Ordner angeben.")
    exclude = [str(p).strip() for p in fields.get("exclude") or [] if str(p).strip()][:100]
    for p in exclude:
        try:
            re.compile(p)
        except re.error:
            raise FimError(f"Kein gültiger Ausdruck: {p}")
    hour = int(fields.get("hour", 3))
    if not 0 <= hour <= 23:
        raise FimError("Stunde 0 bis 23.")
    _update(enabled=bool(fields.get("enabled")), hour=hour, paths=paths, exclude=exclude)
    return overview()


def start_scan(user: str = "") -> Dict[str, Any]:
    if not sys.platform.startswith("linux"):
        raise FimError("Nur unter Linux möglich.")
    c = config()
    os.makedirs(RESULT_DIR, mode=0o700, exist_ok=True)
    out = os.path.join(RESULT_DIR, f"fim-{int(time.time())}.scan")
    cmd = "sudo -n " + HELPER + " fim-scan " + " ".join(_quote(p) for p in c["paths"]) + " > " + _quote(out)
    try:
        return tasks.start("fim", "Datei-Integrität prüfen", [{"shell": cmd, "show": "Dateien prüfen (kann einige Minuten dauern)"}], user=user, key="fim",
                           timeout=2 * 3600, data={"result": out})
    except tasks.TaskError as e:
        raise FimError(str(e))


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _read_result(path: str) -> Dict[str, Tuple]:
    out = {}
    with open(path, errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
                out[r["p"]] = (r["s"], r["m"], r["u"], r["g"], r["z"], r["t"])
            except (ValueError, KeyError):
                continue
    return out


def _dpkg_since(since: int) -> Set[str]:
    """Files of packages installed, updated or removed since the last scan."""
    packages: Set[str] = set()
    for log in ("/var/log/dpkg.log", "/var/log/dpkg.log.1"):
        try:
            with open(log, errors="replace") as f:
                for line in f:
                    m = re.match(r"^(\S+ \S+) (install|upgrade|remove|purge|trigproc) (\S+)", line)
                    if m and int(time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))) >= since - 300:
                        packages.add(m.group(3).split(":")[0])
        except (OSError, ValueError):
            continue
    files: Set[str] = set()
    for pkg in packages:
        for name in glob.glob(f"/var/lib/dpkg/info/{glob.escape(pkg)}*.list") + glob.glob(f"/var/lib/dpkg/info/{glob.escape(pkg)}*.conffiles"):
            try:
                with open(name, errors="replace") as f:
                    files.update(x.strip() for x in f)
            except OSError:
                pass
    return files


def _change_for(mtime: int) -> str:
    """Title of an approved change that was being carried out when the file changed."""
    try:
        import changes
        for c in changes.overview()["changes"]:
            if c["status"] in ("umsetzung", "abgeschlossen", "genehmigt"):
                start = c.get("start") or c.get("created") or 0
                end = c.get("end") or start
                if start - 900 <= mtime <= end + 900:
                    return c["title"]
    except Exception as e:
        logger.warning(f"FIM change lookup: {e}")
    return ""


def compare(old: Dict[str, Tuple], new: Dict[str, Tuple], excluded: List[str]) -> List[Tuple[str, str, Optional[Tuple]]]:
    rx = [re.compile(p) for p in excluded]
    out = []
    for path in sorted(set(old) | set(new)):
        if any(r.search(path) for r in rx):
            continue
        a, b = old.get(path), new.get(path)
        if a is None:
            out.append((path, "added", b))
        elif b is None:
            out.append((path, "removed", None))
        elif a[0] != b[0]:
            out.append((path, "changed", b))
        elif a[1:4] != b[1:4]:
            out.append((path, "meta", b))
    return out


def ingest(path: str) -> Dict[str, Any]:
    """Compare a finished scan with the baseline, record findings, make it the new baseline."""
    c = config()
    new = _read_result(path)
    if not new:
        raise FimError("Der Scan hat keine Dateien geliefert.")
    now = int(time.time())
    with secdb.Db() as db:
        old = {r["path"]: (r["sha"], r["mode"], r["uid"], r["gid"], r["size"], r["mtime"]) for r in db.execute("SELECT * FROM fim_files")}
        first = not old
        diff = [] if first else compare(old, new, c["exclude"])
        packaged = _dpkg_since(c["last_scan"]) if diff else set()
        for p, kind, row in diff[:MAX_REPORT]:
            cause, detail = "", ""
            mtime = row[5] if row else now
            if p in packaged:
                cause = "package"
            else:
                title = _change_for(mtime)
                if title:
                    cause, detail = "change", title
            level = "critical" if CRITICAL.match(p) else "warning"
            db.execute("INSERT INTO fim_findings (path, kind, level, cause, detail, ts, state) VALUES (?,?,?,?,?,?,?)",
                       (p, kind, level, cause, detail, now, "closed" if cause else "open"))
        db.execute("DELETE FROM fim_files")
        db.executemany("INSERT INTO fim_files VALUES (?,?,?,?,?,?,?)", [(p, *v) for p, v in new.items()])
        db.execute("DELETE FROM fim_findings WHERE state = 'closed' AND ts < ?", (now - 365 * 86400,))
    _update(last_scan=now, last_files=len(new), last_error="", baseline=c.get("baseline") or now)
    return {"files": len(new), "changes": len(diff), "first": first}


def _task_done(task: Dict[str, Any]) -> None:
    path = (task.get("data") or {}).get("result")
    try:
        if task.get("state") == "ok" and path:
            ingest(path)
        else:
            _update(last_error=f"Scan: {tasks.STATES.get(task.get('state'), task.get('state'))}")
    except FimError as e:
        _update(last_error=str(e))
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


tasks.on_done("fim", _task_done)


def _rows(state: str = "", limit: int = 300) -> List[Dict[str, Any]]:
    q = "SELECT * FROM fim_findings" + (" WHERE state = ?" if state else "") + " ORDER BY state = 'open' DESC, ts DESC, level LIMIT ?"
    with secdb.Db() as db:
        rows = db.execute(q, (state, limit) if state else (limit,)).fetchall()
    return [{**dict(r), "kind_label": KINDS.get(r["kind"], r["kind"]), "cause_label": CAUSES.get(r["cause"], r["cause"])} for r in rows]


def overview() -> Dict[str, Any]:
    with secdb.Db() as db:
        counts = {r["level"]: r["n"] for r in db.execute("SELECT level, COUNT(*) n FROM fim_findings WHERE state = 'open' GROUP BY level")}
        watched = db.execute("SELECT COUNT(*) n FROM fim_files").fetchone()["n"]
    return {"config": config(), "supported": sys.platform.startswith("linux"), "watched": watched, "findings": _rows(),
            "open": sum(counts.values()), "critical": counts.get("critical", 0), "warning": counts.get("warning", 0), "allowed": ALLOWED}


def close(ids: Iterable[int], note: str, user: str = "") -> Dict[str, Any]:
    ids = [int(i) for i in ids][:MAX_REPORT]
    if not ids:
        raise FimError("Keine Befunde ausgewählt.")
    note = str(note or "").strip()[:500]
    if not note:
        raise FimError("Bitte kurz begründen, warum die Änderung in Ordnung ist.")
    with secdb.Db() as db:
        db.executemany("UPDATE fim_findings SET state = 'closed', note = ?, closed_by = ?, closed_ts = ? WHERE id = ?",
                       [(note, user, int(time.time()), i) for i in ids])
    return overview()


def tick(now: Optional[float] = None) -> None:
    c = config()
    if not c["enabled"]:
        return
    lt = time.localtime(now or time.time())
    today = time.strftime("%Y-%m-%d", lt)
    if lt.tm_hour == c["hour"] and c.get("last_day") != today:
        _update(last_day=today)
        try:
            start_scan("Zeitplan")
        except FimError as e:
            _update(last_error=str(e))


def alerts() -> List[Dict[str, Any]]:
    try:
        o = overview()
    except Exception:
        return []
    out = []
    if o["critical"]:
        out.append({"level": "critical", "text": f"Datei-Integrität: {o['critical']} unerklärte Änderungen an sensiblen Dateien"})
    if o["warning"]:
        out.append({"level": "warning", "text": f"Datei-Integrität: {o['warning']} unerklärte Änderungen"})
    if o["config"]["last_error"]:
        out.append({"level": "warning", "text": f"Datei-Integrität: {o['config']['last_error']}"})
    return out
