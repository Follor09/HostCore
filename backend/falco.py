"""
Container runtime protection (System -> Sicherheit -> Laufzeitschutz): Falco watches system calls and
reports suspicious behaviour in running containers (a shell, writes below system folders, ...).

- Setup installs Falco from its signed package source through pi-helper.sh falco-setup (modern
  eBPF driver, no kernel module) and turns on JSON output.
- Events are read from the journal once a minute into secwatch.db. The same rule in the same
  container is counted within ten minutes, so a noisy rule stays one line.
- Muting a rule (for one container or all) hides known-good behaviour; it stays listed and can be
  undone. Falco only observes - it blocks nothing.
"""

import json
import logging
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

import secdb
import settings_store
import tasks

logger = logging.getLogger("pi_dashboard.falco")

KEY = "falco"
HELPER = "/var/lib/pi-dashboard/src/pi-helper.sh"
UNITS = ("falco-modern-bpf.service", "falco-bpf.service", "falco-kmod.service", "falco.service")
LEVELS = {"emergency": "critical", "alert": "critical", "critical": "critical", "error": "critical", "warning": "warning", "notice": "info",
          "informational": "info", "debug": "info"}
GROUP_SECONDS = 600
KEEP_DAYS = 90


class FalcoError(Exception):
    pass


def config() -> Dict[str, Any]:
    return {"since": 0, "muted": [], "min_priority": "warning", **(settings_store.get(KEY, {}) or {})}


def _unit() -> str:
    for u in UNITS:
        try:
            res = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=5)
            if res.stdout.strip() == "active":
                return u
        except (OSError, subprocess.SubprocessError):
            return ""
    return ""


def status() -> Dict[str, Any]:
    installed = bool(shutil.which("falco"))
    unit = _unit() if installed else ""
    return {"supported": sys.platform.startswith("linux"), "installed": installed, "running": bool(unit), "unit": unit}


def setup(user: str = "") -> Dict[str, Any]:
    if not sys.platform.startswith("linux"):
        raise FalcoError("Nur unter Linux möglich.")
    try:
        return tasks.start("falco", "Falco einrichten", [{"shell": f"sudo -n {HELPER} falco-setup", "show": "Falco installieren und einrichten"}],
                           user=user, key="falco", timeout=1800)
    except tasks.TaskError as e:
        raise FalcoError(str(e))


def parse(line: str) -> Optional[Dict[str, Any]]:
    try:
        e = json.loads(line)
        f = e.get("output_fields") or {}
        return {"rule": str(e["rule"])[:200], "priority": str(e.get("priority") or "").lower(), "output": str(e.get("output") or "")[:1000],
                "container": str(f.get("container.name") or "host")[:120], "process": str(f.get("proc.cmdline") or f.get("proc.name") or "")[:300],
                "user": str(f.get("user.name") or "")[:60]}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def muted(event: Dict[str, Any], rules: List[Dict[str, str]]) -> bool:
    return any(m["rule"] == event["rule"] and m.get("container") in ("", None, event["container"]) for m in rules)


def store(events: List[Dict[str, Any]], now: int) -> int:
    added = 0
    with secdb.Db() as db:
        for e in events:
            row = db.execute("SELECT id FROM falco_events WHERE rule = ? AND container = ? AND last_ts >= ? AND state = 'open' ORDER BY id DESC LIMIT 1",
                             (e["rule"], e["container"], now - GROUP_SECONDS)).fetchone()
            if row:
                db.execute("UPDATE falco_events SET count = count + 1, last_ts = ? WHERE id = ?", (now, row["id"]))
            else:
                db.execute("INSERT INTO falco_events (ts, last_ts, rule, priority, container, process, user, output) VALUES (?,?,?,?,?,?,?,?)",
                           (now, now, e["rule"], e["priority"], e["container"], e["process"], e["user"], e["output"]))
                added += 1
        db.execute("DELETE FROM falco_events WHERE ts < ?", (now - KEEP_DAYS * 86400,))
    return added


def sync() -> int:
    if not shutil.which("falco") or not shutil.which("journalctl"):
        return 0
    unit = _unit()
    if not unit:
        return 0
    c = config()
    now = int(time.time())
    since = c["since"] or now - 3600
    try:
        res = subprocess.run(["journalctl", "--no-pager", "-o", "cat", "-u", unit, "--since", f"@{since}", "--until", f"@{now}"],
                             capture_output=True, text=True, timeout=30, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Falco: {e}")
        return 0
    events = [e for e in (parse(line) for line in res.stdout.splitlines() if line.startswith("{")) if e and not muted(e, c["muted"])]
    added = store(events, now) if events else 0
    settings_store.set(KEY, {**config(), "since": now})
    return added


def overview() -> Dict[str, Any]:
    with secdb.Db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM falco_events ORDER BY state = 'open' DESC, last_ts DESC LIMIT 300")]
    for r in rows:
        r["level"] = LEVELS.get(r["priority"], "info")
    return {"status": status(), "events": rows, "muted": config()["muted"], "open": sum(1 for r in rows if r["state"] == "open")}


def close(ids: Iterable[int], user: str = "") -> Dict[str, Any]:
    with secdb.Db() as db:
        db.executemany("UPDATE falco_events SET state = 'closed', closed_by = ? WHERE id = ?", [(user, int(i)) for i in list(ids)[:500]])
    return overview()


def mute(rule: str, container: str, undo: bool = False) -> Dict[str, Any]:
    rule, container = str(rule or "").strip()[:200], str(container or "").strip()[:120]
    if not rule:
        raise FalcoError("Regel fehlt.")
    c = config()
    items = [m for m in c["muted"] if not (m["rule"] == rule and m.get("container", "") == container)]
    if not undo:
        items.append({"rule": rule, "container": container})
    settings_store.set(KEY, {**c, "muted": items[-200:]})
    if not undo:
        with secdb.Db() as db:
            q = "UPDATE falco_events SET state = 'closed', closed_by = 'stummgeschaltet' WHERE rule = ? AND state = 'open'"
            db.execute(q + (" AND container = ?" if container else ""), (rule, container) if container else (rule,))
    return overview()


def alerts() -> List[Dict[str, Any]]:
    try:
        o = overview()
    except Exception:
        return []
    bad = [e for e in o["events"] if e["state"] == "open"]
    crit = sum(1 for e in bad if e["level"] == "critical")
    warn = sum(1 for e in bad if e["level"] == "warning")
    out = []
    if crit:
        out.append({"level": "critical", "text": f"Laufzeitschutz: {crit} kritische Ereignisse in Containern"})
    if warn:
        out.append({"level": "warning", "text": f"Laufzeitschutz: {warn} verdächtige Ereignisse in Containern"})
    if o["status"]["installed"] and not o["status"]["running"]:
        out.append({"level": "warning", "text": "Laufzeitschutz: Falco läuft nicht"})
    return out
