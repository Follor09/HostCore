"""
Maintenance windows: planned work on all services or a chosen few. During a window,
outages of the covered services don't count against their availability, the status
bar stays quiet (one info line instead) and the public status page says "Wartung".

Targets use the status page ids: "service:<id>", "health:<id>", "stack:<name>".
"""

import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import settings_store

KEY = "maintenance"
KEEP_DAYS = 120
MAX_WINDOWS = 200
MAX_HOURS = 14 * 24

_lock = threading.Lock()


class MaintenanceError(Exception):
    pass


def _all() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _save(items: List[Dict[str, Any]]) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    settings_store.set(KEY, [w for w in items if w["end"] >= cutoff][-MAX_WINDOWS:])


def _state(w: Dict[str, Any], now: float) -> str:
    return "active" if w["start"] <= now < w["end"] else "planned" if now < w["start"] else "past"


def overview() -> Dict[str, Any]:
    import statuspage
    now = time.time()
    items = sorted(_all(), key=lambda w: w["start"], reverse=True)
    names = {f"{a['kind']}:{a['id']}": a["name"] for a in statuspage.available()}
    return {"windows": [{**w, "state": _state(w, now),
                         "scope_names": [names.get(t, t.split(":", 1)[-1]) for t in w["scope"]] if w["scope"] != "all" else []}
                        for w in items],
            "available": [{"id": k, "name": v, "kind": k.split(":")[0]} for k, v in names.items()],
            "active": sum(1 for w in items if _state(w, now) == "active")}


def create(title: str, start: int, end: int, scope: Any, note: str = "", user: str = "") -> Dict[str, Any]:
    title = (title or "").strip()[:100]
    if not title:
        raise MaintenanceError("Titel fehlt.")
    start, end = int(start), int(end)
    if end <= start:
        raise MaintenanceError("Das Ende muss nach dem Beginn liegen.")
    if end <= time.time():
        raise MaintenanceError("Das Wartungsfenster liegt komplett in der Vergangenheit.")
    if end - start > MAX_HOURS * 3600:
        raise MaintenanceError(f"Ein Wartungsfenster dauert höchstens {MAX_HOURS // 24} Tage.")
    if scope != "all":
        scope = [str(t) for t in (scope or []) if isinstance(t, str) and ":" in t][:50]
        if not scope:
            raise MaintenanceError("Mindestens einen Dienst auswählen – oder „Alle Dienste“.")
    # A window can't be put into the past afterwards: that would hide outages that already happened
    start = max(start, int(time.time()) - 60)
    with _lock:
        items = _all()
        items.append({"id": secrets.token_hex(5), "title": title, "start": start,
                      "end": end, "scope": scope, "note": (note or "").strip()[:500], "user": user, "created": int(time.time())})
        _save(items)
    return overview()


def end_now(window_id: str) -> Dict[str, Any]:
    with _lock:
        items = _all()
        w = next((x for x in items if x["id"] == window_id), None)
        if not w:
            raise MaintenanceError("Wartungsfenster nicht gefunden.")
        now = int(time.time())
        if w["start"] > now:
            items.remove(w)           # not started yet: cancel
        else:
            w["end"] = min(w["end"], now)
        _save(items)
    return overview()


def delete(window_id: str) -> Dict[str, Any]:
    with _lock:
        _save([w for w in _all() if w["id"] != window_id])
    return overview()


# ---------------------------------------------------------------------------
# Queries for uptime, events, status bar and status page
# ---------------------------------------------------------------------------
def intervals(target: str, since: float = 0, until: Optional[float] = None) -> List[Tuple[int, int]]:
    """Maintenance time spans that cover target, clipped to [since, until]."""
    until = time.time() if until is None else until
    out = []
    for w in _all():
        if w["scope"] != "all" and target not in w["scope"]:
            continue
        a, b = max(w["start"], since), min(w["end"], until)
        if b > a:
            out.append((int(a), int(b)))
    return out


def overlap(spans: List[Tuple[int, int]], a: float, b: float) -> float:
    return sum(max(0, min(b, y) - max(a, x)) for x, y in spans)


def active(target: Optional[str] = None) -> List[Dict[str, Any]]:
    now = time.time()
    return [w for w in _all() if w["start"] <= now < w["end"] and (target is None or w["scope"] == "all" or target in w["scope"])]


def upcoming(days: int = 7) -> List[Dict[str, Any]]:
    now = time.time()
    return sorted((w for w in _all() if now < w["start"] <= now + days * 86400), key=lambda w: w["start"])


def covers(target: str, ts: float) -> bool:
    return any(w["start"] <= ts < w["end"] and (w["scope"] == "all" or target in w["scope"]) for w in _all())
