"""
sudo log (System -> Sicherheit -> sudo-Protokoll): who ran which command as root, per user and server.

- Read from the journal once a minute (SYSLOG_IDENTIFIER=sudo) into secwatch.db; kept 180 days,
  independent of how long the journal lives.
- The dashboard's own helper calls are marked and hidden by default, otherwise they'd drown
  everything else.
- Warning rules (built in, plus your own regular expressions) mark risky commands; denied attempts
  are always marked. Matches appear in the status overview until someone has reviewed them.
- With agents: the main dashboard merges the log of every server.
"""

import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import secdb
import settings_store

logger = logging.getLogger("pi_dashboard.sudolog")

KEY = "sudolog"
KEEP_DAYS = 180
OWN = ("/var/lib/pi-dashboard/src/", "/bin/systemctl --no-block", "/usr/bin/systemctl --no-block", "/usr/bin/smartctl -j")
LINE = re.compile(r"^\s*(?P<user>\S+) : (?P<rest>.*)$")
BUILTIN = [
    ("Benutzer oder Rechte geändert", r"\b(visudo|useradd|usermod|userdel|adduser|deluser|passwd|chpasswd|groupadd|gpasswd)\b", "warning"),
    ("Root-Shell gestartet", r"(^|/)(bash|sh|zsh|dash|su)( -[a-z]+)*$", "warning"),
    ("Sudo-Regeln geändert", r"/etc/sudoers", "critical"),
    ("Protokolle gelöscht", r"\b(rm|truncate|shred)\b.*(/var/log|\.bash_history)|journalctl .*--(vacuum|rotate)", "critical"),
    ("Firewall oder Dienst abgeschaltet", r"\b(ufw (disable|reset)|iptables -F|nft flush|systemctl (stop|disable|mask) (ssh|sshd|fail2ban|ufw|auditd|falco))", "warning"),
    ("Daten großflächig gelöscht", r"\brm -[a-z]*r[a-z]*f?[a-z]* +/(\s|$|etc|usr|var|home|boot)|\bdd .*of=/dev/|\bmkfs", "critical"),
    ("Weltweit schreibbar", r"\bchmod( -R)? +(0?777|a\+w)\b", "warning"),
]


class SudoLogError(Exception):
    pass


def config() -> Dict[str, Any]:
    return {"rules": [], "since": 0, "reviewed": 0, **(settings_store.get(KEY, {}) or {})}


def save_rules(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    clean = []
    for r in rules[:50]:
        name, rx = str(r.get("name") or "").strip()[:80], str(r.get("pattern") or "").strip()[:300]
        if not name or not rx:
            raise SudoLogError("Regel: Name und Ausdruck angeben.")
        try:
            re.compile(rx)
        except re.error as e:
            raise SudoLogError(f"{name}: ungültiger Ausdruck ({e}).")
        clean.append({"name": name, "pattern": rx, "level": "critical" if r.get("level") == "critical" else "warning"})
    settings_store.set(KEY, {**config(), "rules": clean})
    with secdb.Db() as db:
        rows = db.execute("SELECT id, command, ok FROM sudo_log WHERE ts >= ?", (int(time.time()) - 30 * 86400,)).fetchall()
        for row in rows:
            rule, level = classify(row["command"], bool(row["ok"]), clean)
            db.execute("UPDATE sudo_log SET rule = ?, level = ? WHERE id = ?", (rule, level, row["id"]))
    return overview()


def classify(command: str, ok: bool, custom: Optional[List[Dict[str, Any]]] = None) -> tuple:
    if not ok:
        return "Abgelehnt", "warning"
    best = ("", "")
    for name, rx, level in [*BUILTIN, *[(r["name"], r["pattern"], r["level"]) for r in (custom if custom is not None else config()["rules"])]]:
        try:
            if re.search(rx, command[:2000]):
                if level == "critical":
                    return name, level
                best = best if best[0] else (name, level)
        except re.error:
            continue
    return best


def parse(message: str) -> Optional[Dict[str, Any]]:
    """  niko : TTY=pts/0 ; PWD=/home/niko ; USER=root ; COMMAND=/usr/bin/apt update
         niko : 3 incorrect password attempts ; TTY=pts/0 ; PWD=/ ; USER=root ; COMMAND=/bin/ls"""
    m = LINE.match(message)
    if not m or "COMMAND=" not in m.group("rest"):
        return None
    head, _, command = m.group("rest").partition("COMMAND=")
    parts = [p.strip() for p in head.split(" ; ") if p.strip(" ;")]
    fields, reason = {}, ""
    for p in parts:
        k, eq, v = p.partition("=")
        if eq and k in ("TTY", "PWD", "USER"):
            fields[k] = v.strip(" ;")
        else:
            reason = p.strip(" ;")
    return {"user": m.group("user"), "tty": fields.get("TTY", ""), "cwd": fields.get("PWD", ""), "runas": fields.get("USER", "root"),
            "command": command.strip(), "ok": not reason, "reason": reason}


def sync() -> int:
    """New sudo lines from the journal; the UNIQUE index keeps a repeated read harmless."""
    if not shutil.which("journalctl"):
        return 0
    c = config()
    since = c["since"] or int(time.time()) - 7 * 86400
    try:
        res = subprocess.run(["journalctl", "--no-pager", "-o", "json", "--output-fields=MESSAGE", "SYSLOG_IDENTIFIER=sudo", "--since", f"@{since - 120}"],
                             capture_output=True, text=True, timeout=30, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"sudo log: {e}")
        return 0
    added, last = 0, since
    custom = c["rules"]
    with secdb.Db() as db:
        for line in res.stdout.splitlines():
            try:
                row = json.loads(line)
                ts = int(row["__REALTIME_TIMESTAMP"]) // 1000000
                msg = row["MESSAGE"]
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(msg, str):
                continue
            p = parse(msg)
            if not p:
                continue
            last = max(last, ts)
            own = int(any(x in p["command"] for x in OWN))
            rule, level = ("", "") if own else classify(p["command"], p["ok"], custom)
            cur = db.execute("INSERT OR IGNORE INTO sudo_log (ts, user, runas, tty, cwd, command, ok, reason, rule, level, own) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (ts, p["user"], p["runas"], p["tty"], p["cwd"], p["command"][:2000], int(p["ok"]), p["reason"][:200], rule, level, own))
            added += cur.rowcount
        db.execute("DELETE FROM sudo_log WHERE ts < ?", (int(time.time()) - KEEP_DAYS * 86400,))
    settings_store.set(KEY, {**config(), "since": last})
    return added


def entries(days: int = 7, user: str = "", q: str = "", flagged: bool = False, own: bool = False, limit: int = 500) -> Dict[str, Any]:
    days, limit = max(1, min(int(days), KEEP_DAYS)), max(1, min(int(limit), 2000))
    where, args = ["ts >= ?"], [int(time.time()) - days * 86400]
    if user:
        where.append("user = ?")
        args.append(user)
    if q:
        where.append("command LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if flagged:
        where.append("level != ''")
    if not own:
        where.append("own = 0")
    sql = " AND ".join(where)
    with secdb.Db() as db:
        rows = [dict(r) for r in db.execute(f"SELECT * FROM sudo_log WHERE {sql} ORDER BY ts DESC, id DESC LIMIT ?", (*args, limit))]
        users = [dict(r) for r in db.execute(
            "SELECT user, COUNT(*) n, SUM(ok = 0) denied, SUM(level != '') flagged, MAX(ts) last FROM sudo_log WHERE ts >= ? AND own = 0 GROUP BY user ORDER BY n DESC", (args[0],))]
    return {"entries": rows, "users": users, "days": days, "supported": bool(shutil.which("journalctl"))}


def overview() -> Dict[str, Any]:
    return {**entries(), "rules": config()["rules"], "builtin": [{"name": n, "level": lv} for n, _, lv in BUILTIN], "open": unreviewed(), "keep_days": KEEP_DAYS}


def unreviewed() -> Dict[str, int]:
    with secdb.Db() as db:
        r = db.execute("SELECT SUM(level = 'critical') c, SUM(level = 'warning') w FROM sudo_log WHERE id > ? AND own = 0", (config()["reviewed"],)).fetchone()
    return {"critical": r["c"] or 0, "warning": r["w"] or 0}


def mark_reviewed(user: str = "") -> Dict[str, Any]:
    with secdb.Db() as db:
        top = db.execute("SELECT MAX(id) m FROM sudo_log").fetchone()["m"] or 0
    settings_store.set(KEY, {**config(), "reviewed": top, "reviewed_by": user, "reviewed_ts": int(time.time())})
    return overview()


async def fleet_entries(days: int, user: str, q: str, flagged: bool) -> Dict[str, Any]:
    """The log of every server in one list (each row says where it comes from)."""
    import asyncio
    import platform
    import httpx
    import remotes
    params = {"days": days, "user": user, "q": q, "flagged": int(flagged)}

    async def one(client: httpx.AsyncClient, r: Dict[str, Any]) -> Dict[str, Any]:
        try:
            res = await client.get(f"{r['url']}/api/sudo-log/entries", params=params, headers={"Authorization": f"Bearer {r['token']}"})
            if res.status_code == 404:
                return {"name": r["name"], "entries": [], "error": "Agent zu alt"}
            res.raise_for_status()
            return {"name": r["name"], "entries": res.json().get("entries", []), "error": ""}
        except (httpx.HTTPError, ValueError) as e:
            return {"name": r["name"], "entries": [], "error": f"Nicht erreichbar ({type(e).__name__})"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        local, *others = await asyncio.gather(asyncio.to_thread(entries, days, user, q, flagged), *(one(client, r) for r in remotes._all()))
    rows = [{**e, "server": platform.node() or "Dieser Server"} for e in local["entries"]]
    for o in others:
        rows += [{**e, "server": o["name"]} for e in o["entries"]]
    rows.sort(key=lambda e: -e["ts"])
    return {"entries": rows[:1000], "errors": [{"name": o["name"], "error": o["error"]} for o in others if o["error"]], "servers": 1 + len(others)}


def alerts() -> List[Dict[str, Any]]:
    try:
        o = unreviewed()
    except Exception:
        return []
    out = []
    if o["critical"]:
        out.append({"level": "critical", "text": f"sudo-Protokoll: {o['critical']} kritische Befehle nicht geprüft"})
    if o["warning"]:
        out.append({"level": "warning", "text": f"sudo-Protokoll: {o['warning']} auffällige Befehle nicht geprüft"})
    return out
