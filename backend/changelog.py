"""
"What's new": the dashboard's own Git history, grouped by the auto-updates that
brought it (dashboard_starts in settings.json records which commit ran when).
"""

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import settings_store

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache: Dict[str, Any] = {"ts": 0, "head": "", "data": None}


def _git(*args: str) -> Optional[str]:
    try:
        res = subprocess.run(["git", "-C", BASE_DIR, *args], capture_output=True, text=True, timeout=10, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def _commit(line: str) -> Optional[Dict[str, Any]]:
    parts = line.split("\x1f")
    if len(parts) < 4:
        return None
    full, short, ts, subject = parts[:4]
    body = parts[4] if len(parts) > 4 else ""
    body = "\n".join(l for l in body.strip().splitlines() if not re.match(r"^(Co-Authored-By|Signed-off-by):", l, re.I)).strip()
    # "Area: change one; change two" -> title + points
    title, points = subject, []
    m = re.match(r"^([^:]{2,60}):\s+(.+)$", subject)
    if m:
        title, rest = m.group(1), m.group(2)
        points = [p.strip() for p in rest.split("; ") if p.strip()]
    elif "; " in subject:
        points = [p.strip() for p in subject.split("; ")]
        title = ""
    return {"hash": full, "short": short, "ts": int(ts), "subject": subject, "title": title,
            "points": points, "body": body[:1500]}


def _log(rev: str = "HEAD", limit: int = 80) -> List[Dict[str, Any]]:
    out = _git("log", f"-n{limit}", "--format=%H%x1f%h%x1f%ct%x1f%s%x1f%b%x1e", rev)
    if not out:
        return []
    return [c for c in (_commit(chunk.strip("\n")) for chunk in out.split("\x1e")) if c]


def changelog() -> Dict[str, Any]:
    head = (_git("rev-parse", "HEAD") or "").strip()
    if not head:
        return {"available": False}
    if _cache["data"] and _cache["head"] == head and time.time() - _cache["ts"] < 300:
        return _cache["data"]
    commits = _log()
    known = {c["hash"] for c in commits}
    # Updates: consecutive dashboard starts with a different commit
    starts = settings_store.get("dashboard_starts", []) or []
    updates = []
    prev = None
    for s in starts:
        c = s.get("commit") or ""
        if prev and c and c != prev:
            rng = _log(f"{prev}..{c}", 60) if _git("cat-file", "-e", f"{prev}^{{commit}}") is not None else []
            updates.append({"ts": s["ts"], "from": prev, "to": c, "commits": [x["hash"] for x in rng if x["hash"] in known]})
        prev = c or prev
    updates.reverse()
    data = {"available": True, "head": head, "short": head[:7], "commits": commits, "updates": updates[:30],
            "last_update": updates[0] if updates else None}
    _cache.update(ts=time.time(), head=head, data=data)
    return data
