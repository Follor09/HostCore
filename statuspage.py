"""
Public status page (/status): a plain page without login for family and friends -
"Jellyfin läuft, NAS kurz weg". Off by default; the admin picks which services, web
services and compose stacks appear, under which name.

Only names (as entered here), up/down and uptime leave the Pi - never URLs, IPs,
error messages or container details.
"""

import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "status_page"
KINDS = ("service", "health", "stack")
MAX_ITEMS = 30
HISTORY_DAYS = 30
CACHE_SECONDS = 30

_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


class StatusPageError(Exception):
    pass


def config() -> Dict[str, Any]:
    cfg = settings_store.get(KEY, {}) or {}
    return {"enabled": bool(cfg.get("enabled")), "title": cfg.get("title") or "Status",
            "description": cfg.get("description") or "", "show_history": cfg.get("show_history", True),
            "items": [i for i in cfg.get("items", []) if i.get("kind") in KINDS]}


def available() -> List[Dict[str, Any]]:
    """Everything that can be put on the page."""
    import containers
    import healthchecks
    import services_manager
    out = [{"kind": "service", "id": s["id"], "name": s["name"]} for s in services_manager.get_services_status()]
    out += [{"kind": "health", "id": c["id"], "name": c["name"]} for c in healthchecks.checks()]
    try:
        out += [{"kind": "stack", "id": s["name"], "name": s["name"]} for s in containers.stacks()]
    except Exception:
        pass
    return out


def save(enabled: bool, title: str, description: str, show_history: bool, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    title = (title or "").strip()[:80] or "Status"
    description = (description or "").strip()[:300]
    if len(items) > MAX_ITEMS:
        raise StatusPageError(f"Höchstens {MAX_ITEMS} Einträge.")
    known = {(a["kind"], a["id"]) for a in available()}
    clean = []
    for i in items:
        kind, iid = str(i.get("kind", "")), str(i.get("id", ""))
        if (kind, iid) not in known:
            raise StatusPageError(f"Unbekannter Eintrag: {iid}")
        label = str(i.get("label", "")).strip()[:60]
        if not label:
            raise StatusPageError("Jeder Eintrag braucht einen Namen.")
        clean.append({"kind": kind, "id": iid, "label": label})
    settings_store.set(KEY, {"enabled": bool(enabled), "title": title, "description": description,
                             "show_history": bool(show_history), "items": clean})
    _cache["data"] = None
    return config()


# ---------------------------------------------------------------------------
# Daily uptime bars
# ---------------------------------------------------------------------------
def _midnights(days: int) -> List[int]:
    """Start of each of the last `days` local days, oldest first (mktime normalizes day 0, -1 ...)."""
    y, m, d = time.localtime()[:3]
    return [int(time.mktime((y, m, d - k, 0, 0, 0, 0, 0, -1))) for k in range(days - 1, -1, -1)]


def _service_bars(periods: List[Dict[str, Any]], days: List[int], now: int) -> List[Optional[float]]:
    bars = []
    for i, start in enumerate(days):
        end = days[i + 1] if i + 1 < len(days) else now
        up = total = 0
        for p in periods:
            a, b = max(p["start"], start), min(p["end"], end)
            if b > a:
                total += b - a
                if p["status"] == "active":
                    up += b - a
        bars.append(round(up / total, 4) if total else None)
    return bars


def _health_bars(check_id: str, days: List[int]) -> List[Optional[float]]:
    import healthchecks
    with healthchecks._conn() as conn:
        rows = conn.execute("SELECT ts, ok FROM health_results WHERE check_id = ? AND ts >= ?", (check_id, days[0])).fetchall()
    sums: Dict[int, List[int]] = {}
    for ts, ok in rows:
        idx = max(i for i, d in enumerate(days) if d <= ts)
        sums.setdefault(idx, []).append(ok)
    return [round(sum(sums[i]) / len(sums[i]), 4) if i in sums else None for i in range(len(days))]


def _uptime(bars: List[Optional[float]]) -> Optional[float]:
    vals = [b for b in bars if b is not None]
    return round(sum(vals) / len(vals) * 100, 2) if vals else None


def _build() -> Dict[str, Any]:
    import containers
    import healthchecks
    import monitor
    import services_manager
    cfg = config()
    now = int(time.time())
    days = _midnights(HISTORY_DAYS)
    services = {s["id"]: s for s in services_manager.get_services_status()}
    checks = {c["id"]: c for c in healthchecks.status(1)["checks"]} if cfg["items"] else {}
    uptime = None
    stacks = None
    items = []
    for it in cfg["items"]:
        state, bars = "unknown", []
        if it["kind"] == "service":
            s = services.get(it["id"])
            if s:
                state = {"active": "up", "error": "down"}.get(s["status"], "down")
            if cfg["show_history"]:
                if uptime is None:
                    uptime = {u["id"]: u for u in monitor.service_uptime(HISTORY_DAYS)["services"]}
                u = uptime.get(it["id"])
                bars = _service_bars(u["periods"], days, now) if u else []
        elif it["kind"] == "health":
            c = checks.get(it["id"])
            if c and c.get("last"):
                state = "up" if c["last"]["ok"] else "down"
            if cfg["show_history"]:
                bars = _health_bars(it["id"], days)
        elif it["kind"] == "stack":
            if stacks is None:
                stacks = {s["name"]: s for s in containers.stacks()}
            s = stacks.get(it["id"])
            if s:
                state = {"active": "up", "warning": "degraded"}.get(s["level"], "down")
        items.append({"label": it["label"], "state": state, "bars": bars, "uptime": _uptime(bars)})

    states = [i["state"] for i in items if i["state"] != "unknown"]
    if not states:
        overall = "unknown"
    elif all(s == "up" for s in states):
        overall = "up"
    elif all(s == "down" for s in states):
        overall = "down"
    else:
        overall = "partial"
    return {"title": cfg["title"], "description": cfg["description"], "overall": overall, "items": items,
            "show_history": cfg["show_history"], "days": [time.strftime("%Y-%m-%d", time.localtime(d)) for d in days],
            "updated": now}


def public_status() -> Optional[Dict[str, Any]]:
    """None when the page is switched off."""
    if not config()["enabled"]:
        return None
    if not _cache["data"] or time.time() - _cache["ts"] > CACHE_SECONDS:
        _cache.update(ts=time.time(), data=_build())
    return _cache["data"]
