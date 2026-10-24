"""
Error digest: errors (priority err and worse) from the system journal of the last
days, grouped by source and a normalised message - so "this came 340 times since
Monday" stands out from the one-off noise.
"""

import json
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List

MAX_LINES = 30000
_cache: Dict[str, Any] = {}

_NORM = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", re.I), "<mac>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\b\d{4}-\d\d-\d\dT?[\d:.]*Z?\b"), "<time>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
]


def normalise(msg: str) -> str:
    msg = msg.strip()[:400]
    for rx, rep in _NORM:
        msg = rx.sub(rep, msg)
    return re.sub(r"\s+", " ", msg)


def digest(days: int = 7) -> Dict[str, Any]:
    days = 1 if days <= 1 else 30 if days >= 30 else 7
    hit = _cache.get(days)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    if not shutil.which("journalctl"):
        return {"supported": False}
    try:
        res = subprocess.run(["journalctl", "--no-pager", "-o", "json", "-p", "0..3", "--since", f"{days} days ago",
                              "-n", str(MAX_LINES), "--output-fields=MESSAGE,SYSLOG_IDENTIFIER,_SYSTEMD_UNIT,_COMM,PRIORITY,_TRANSPORT"],
                             capture_output=True, text=True, timeout=40, errors="replace")
    except subprocess.TimeoutExpired:
        return {"supported": True, "error": "Journal antwortet nicht.", "groups": []}
    now = time.time()
    day0 = now - days * 86400
    groups: Dict[tuple, Dict[str, Any]] = {}
    total = 0
    last_24h = 0
    lines = res.stdout.splitlines()
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE", "")
        if isinstance(msg, list):
            msg = bytes(msg).decode("utf-8", errors="replace")
        if not msg or not isinstance(msg, str):
            continue
        ts = int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6
        unit = e.get("_SYSTEMD_UNIT", "") or ""
        source = ("kernel" if e.get("_TRANSPORT") == "kernel" else
                  e.get("SYSLOG_IDENTIFIER") or unit.replace(".service", "") or e.get("_COMM") or "?")
        key = (source, normalise(msg))
        g = groups.setdefault(key, {"source": source, "unit": unit, "pattern": key[1], "sample": msg[:600], "count": 0,
                                    "first": ts, "last": ts, "priority": 7, "per_day": [0] * days})
        g["count"] += 1
        g["first"] = min(g["first"], ts)
        if ts >= g["last"]:
            g["last"], g["sample"] = ts, msg[:600]
        g["priority"] = min(g["priority"], int(e.get("PRIORITY", 3) or 3))
        idx = int((ts - day0) // 86400)
        if 0 <= idx < days:
            g["per_day"][idx] += 1
        total += 1
        if ts >= now - 86400:
            last_24h += 1
    items = sorted(groups.values(), key=lambda g: (-g["count"], -g["last"]))
    for g in items:
        g["new"] = g["first"] >= now - 86400 and g["count"] > 0
        g["ongoing"] = g["last"] >= now - 3600
        g["first"], g["last"] = int(g["first"]), int(g["last"])
    hint = ""
    if not lines and ("insufficient permissions" in res.stderr.lower() or "not seeing messages" in res.stderr.lower()):
        import os
        hint = f"Keine Berechtigung für System-Logs: sudo usermod -aG systemd-journal {os.environ.get('USER', '$USER')} && sudo systemctl restart raspberry-dashboard"
    data = {"supported": True, "days": days, "total": total, "groups": items[:200], "group_count": len(items),
            "last_24h": last_24h,
            "truncated": len(lines) >= MAX_LINES, "hint": hint}
    _cache[days] = (time.time(), data)
    return data
