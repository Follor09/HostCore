"""
Patch deadlines (System-Werkzeuge -> Schwachstellen & Support -> Patch-Fristen): every known
vulnerability with an available fix gets a deadline by severity (default: critical 7, high 30,
medium 90, low 180 days), counted from the day the fix was first seen here.

- Sources: the vulnerability scan (vulns.py) - packages (debsecan) and container images (Trivy).
  debsecan has no "critical": high urgency + remotely exploitable counts as critical.
- Traffic light per finding: on time, due soon (last quarter of the period / 3 days), overdue.
  Overdue findings become an event once and show in the status overview.
- Exceptions per CVE with reason, until a date, by whom - expired exceptions count again.
  CVEs accepted in the vulnerability scan don't count either.
- Fixed findings are kept for a year: days to fix, within the deadline or not, mean time to fix
  per severity - the evidence for a patch process.
"""

import datetime
import re
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "patch_sla"
STATE_KEY = "patch_sla_state"
EVENTS_KEY = "patch_sla_events"
SEVERITIES = ["critical", "high", "medium", "low"]
LABELS = {"critical": "Kritisch", "high": "Hoch", "medium": "Mittel", "low": "Niedrig"}
DEFAULT_DAYS = {"critical": 7, "high": 30, "medium": 90, "low": 180}
KEEP_CLOSED = 365 * 86400
ID_RE = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA-[\w-]+|TEMP-[\w-]+|DSA-[\w-]+|[A-Z]+-\d{4}-[\w-]+)$")
_lock = threading.Lock()


class PatchSlaError(Exception):
    pass


def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    c = c if isinstance(c, dict) else {}
    days = {**DEFAULT_DAYS, **(c.get("days") or {})}
    return {"days": days, "unknown_as": c.get("unknown_as", "low"), "exceptions": dict(c.get("exceptions") or {})}


def save_settings(fields: Dict[str, Any]) -> Dict[str, Any]:
    c = config()
    days = {}
    for s in SEVERITIES:
        try:
            v = int((fields.get("days") or {}).get(s) or c["days"][s])
        except (TypeError, ValueError):
            raise PatchSlaError("Fristen: Anzahl Tage.")
        if not 1 <= v <= 730:
            raise PatchSlaError("Fristen: 1 bis 730 Tage.")
        days[s] = v
    unknown = str(fields.get("unknown_as") or c["unknown_as"])
    if unknown not in SEVERITIES:
        raise PatchSlaError("Unbekannte Einstufung.")
    c.update(days=days, unknown_as=unknown)
    settings_store.set(KEY, c)
    return overview()


def set_exception(vuln_id: str, reason: str, until: str, user: str, remove: bool = False) -> Dict[str, Any]:
    vuln_id = str(vuln_id or "").strip()
    if not ID_RE.match(vuln_id):
        raise PatchSlaError("Ungültige Kennung.")
    c = config()
    if remove:
        c["exceptions"].pop(vuln_id, None)
    else:
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise PatchSlaError("Bitte begründen, warum die Frist nicht eingehalten werden kann.")
        try:
            end = datetime.date.fromisoformat(str(until))
        except ValueError:
            raise PatchSlaError("Bitte ein Ablaufdatum angeben.")
        if end <= datetime.date.today() or end > datetime.date.today() + datetime.timedelta(days=365):
            raise PatchSlaError("Ausnahme: Ablaufdatum in der Zukunft, höchstens ein Jahr.")
        c["exceptions"][vuln_id] = {"reason": reason[:500], "until": end.isoformat(), "user": user, "ts": int(time.time())}
    settings_store.set(KEY, c)
    return overview()


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------
def _severity(sev: str, remote: bool, cfg: Dict[str, Any]) -> str:
    if sev == "high" and remote:
        return "critical"
    if sev in SEVERITIES:
        return sev
    return cfg["unknown_as"]


def _current(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Fixable findings now: key -> details."""
    import vulns
    d = vulns._load()
    out: Dict[str, Dict[str, Any]] = {}
    for v in (d.get("packages") or {}).get("items", []):
        if v.get("fixed") and not v.get("obsolete"):
            out[f"pkg|{v['id']}|{v['package']}"] = {"id": v["id"], "package": v["package"], "target": "Pakete des Servers", "kind": "package",
                                                    "severity": _severity(v["severity"], v.get("remote", False), cfg), "remote": v.get("remote", False)}
    for i in d.get("images", []):
        for v in i.get("vulns", []):
            if v.get("fixed"):
                out[f"img|{i['ref']}|{v['id']}|{v['package']}"] = {"id": v["id"], "package": v["package"], "target": i.get("image", i["ref"]),
                                                                   "kind": "image", "severity": _severity(v["severity"], False, cfg),
                                                                   "fixed_version": v.get("fixed_version", "")}
    return out


def sync() -> Dict[str, Any]:
    """Opens new findings, closes the ones that are gone (fixed or no longer installed)."""
    import vulns
    cfg = config()
    checked = vulns._load().get("checked", 0)
    with _lock:
        st = settings_store.get(STATE_KEY) or {}
        if st.get("synced") == checked and st.get("open") is not None:
            return st
        now = int(time.time())
        cur = _current(cfg) if checked else {}
        # Trivy results are cut to the most serious findings per image: what fell off the list is not fixed
        truncated = {i["ref"] for i in vulns._load().get("images", []) if i.get("truncated")}
        open_: Dict[str, Any] = dict(st.get("open") or {})
        closed: List[Dict[str, Any]] = [c for c in (st.get("closed") or []) if c["closed"] >= now - KEEP_CLOSED]
        for key, v in cur.items():
            if key not in open_:
                open_[key] = {**v, "first_seen": checked or now}
            else:
                open_[key].update(severity=v["severity"], target=v["target"])
        for key in [k for k in open_ if k not in cur and not (k.startswith("img|") and k.split("|")[1] in truncated)]:
            o = open_.pop(key)
            days = (now - o["first_seen"]) / 86400
            closed.append({**o, "key": key, "closed": checked or now, "days": round(days, 1),
                           "in_time": days <= cfg["days"][o["severity"]]})
        st = {"synced": checked, "open": open_, "closed": closed[-3000:], "notified": [k for k in st.get("notified", []) if k in open_]}
        settings_store.set(STATE_KEY, st)
    return st


def _view(key: str, o: Dict[str, Any], cfg: Dict[str, Any], ignored: Dict[str, Any], now: float) -> Dict[str, Any]:
    days = cfg["days"][o["severity"]]
    due = o["first_seen"] + days * 86400
    left = (due - now) / 86400
    exc = cfg["exceptions"].get(o["id"])
    exc_active = bool(exc and exc["until"] >= datetime.date.today().isoformat())
    state = ("accepted" if o["id"] in ignored else "exception" if exc_active else "overdue" if left < 0
             else "soon" if left <= max(3, days / 4) else "ok")
    return {**o, "key": key, "due": int(due), "days_left": round(left, 1), "deadline_days": days, "state": state,
            "exception": exc, "exception_active": exc_active, "label": LABELS[o["severity"]]}


def overview() -> Dict[str, Any]:
    import vulns
    cfg = config()
    st = sync()
    ignored = vulns._ignored()
    now = time.time()
    items = [_view(k, o, cfg, ignored, now) for k, o in (st.get("open") or {}).items()]
    order = {"overdue": 0, "soon": 1, "ok": 2, "exception": 3, "accepted": 4}
    sev = {s: n for n, s in enumerate(SEVERITIES)}
    items.sort(key=lambda i: (order[i["state"]], sev[i["severity"]], i["due"]))
    closed = st.get("closed") or []
    stats = {}
    for s in SEVERITIES:
        cl = [c for c in closed if c["severity"] == s]
        op = [i for i in items if i["severity"] == s and i["state"] not in ("accepted",)]
        stats[s] = {"open": len(op), "overdue": sum(1 for i in op if i["state"] == "overdue"),
                    "closed": len(cl), "in_time": sum(1 for c in cl if c["in_time"]),
                    "mttr": round(sum(c["days"] for c in cl) / len(cl), 1) if cl else None, "deadline": cfg["days"][s], "label": LABELS[s]}
    total_closed = len(closed)
    return {"items": items[:600], "truncated": len(items) > 600, "stats": stats, "settings": {"days": cfg["days"], "unknown_as": cfg["unknown_as"]},
            "exceptions": [{"id": k, **v, "active": v["until"] >= datetime.date.today().isoformat()} for k, v in sorted(cfg["exceptions"].items())],
            "counts": {"open": sum(1 for i in items if i["state"] in ("ok", "soon", "overdue")), "overdue": sum(1 for i in items if i["state"] == "overdue"),
                       "soon": sum(1 for i in items if i["state"] == "soon")},
            "compliance": round(100 * sum(1 for c in closed if c["in_time"]) / total_closed) if total_closed else None,
            "scanned": vulns._load().get("checked", 0), "labels": LABELS}


def check_overdue() -> None:
    """Called by the monitor: an event for each finding that just became overdue."""
    try:
        o = overview()
    except Exception:
        return
    over = [i for i in o["items"] if i["state"] == "overdue"]
    with _lock:
        st = settings_store.get(STATE_KEY) or {}
        notified = set(st.get("notified") or [])
        fresh = [i for i in over if i["key"] not in notified]
        if not fresh:
            return
        st["notified"] = sorted(notified | {i["key"] for i in fresh})
        settings_store.set(STATE_KEY, st)
    events = (settings_store.get(EVENTS_KEY, []) or [])[-199:]
    worst = "critical" if any(i["severity"] == "critical" for i in fresh) else "warning"
    events.append({"ts": int(time.time()), "level": worst,
                   "title": f"{len(fresh)} {'Sicherheitslücke' if len(fresh) == 1 else 'Sicherheitslücken'} über der Patch-Frist",
                   "detail": ", ".join(sorted({f"{i['id']} ({i['package']})" for i in fresh}))[:240]})
    settings_store.set(EVENTS_KEY, events)


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def alerts() -> List[Dict[str, Any]]:
    try:
        o = overview()
    except Exception:
        return []
    crit = sum(1 for i in o["items"] if i["state"] == "overdue" and i["severity"] == "critical")
    n = o["counts"]["overdue"]
    if not n:
        return []
    return [{"level": "critical" if crit else "warning", "text": f"{n} {'Sicherheitslücke' if n == 1 else 'Sicherheitslücken'} über der Patch-Frist"}]


def markdown() -> str:
    o = overview()
    fmt = lambda ts: time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"
    out = ["# Patch-Fristen", "", f"Stand: {time.strftime('%d.%m.%Y %H:%M')} · letzter Scan {fmt(o['scanned'])}", "",
           "| Schwere | Frist (Tage) | Offen | Überfällig | Behoben (12 Mon.) | In Frist | Ø Tage bis Behebung |", "|---|---|---|---|---|---|---|"]
    for s in SEVERITIES:
        x = o["stats"][s]
        out.append(f"| {x['label']} | {x['deadline']} | {x['open']} | {x['overdue']} | {x['closed']} | {x['in_time']} | {x['mttr'] if x['mttr'] is not None else '–'} |")
    out += ["", f"Einhaltung gesamt: {o['compliance'] if o['compliance'] is not None else '–'} %", "", "## Offene Lücken", "",
            "| CVE | Paket | Ziel | Schwere | Seit | Fällig | Status |", "|---|---|---|---|---|---|---|"]
    names = {"overdue": "überfällig", "soon": "bald fällig", "ok": "in Frist", "exception": "Ausnahme", "accepted": "akzeptiert"}
    for i in o["items"]:
        out.append(f"| {i['id']} | {i['package']} | {i['target']} | {i['label']} | {fmt(i['first_seen'])} | {fmt(i['due'])} | {names[i['state']]} |")
    if o["exceptions"]:
        out += ["", "## Ausnahmen", "", "| CVE | Begründung | Bis | Von |", "|---|---|---|---|"]
        out += [f"| {e['id']} | {e['reason']} | {e['until']} | {e['user']} |" for e in o["exceptions"]]
    return "\n".join(out) + "\n"
