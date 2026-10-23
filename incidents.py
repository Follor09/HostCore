"""
Incident management (Dokumentation -> Betrieb -> Störungen): an incident has a title, severity,
the affected services, a status and a timeline of updates ("Ursache gefunden", "behoben" …).
Opening and resolving become events; incidents marked public appear on the public status page
while open and for a week after they were resolved.
"""

import datetime
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "incidents"
SEVERITIES = {"gering": "Gering", "mittel": "Mittel", "schwer": "Schwer"}
STATUSES = {"untersuchung": "Wird untersucht", "identifiziert": "Ursache gefunden", "beobachtung": "Wird beobachtet",
            "behoben": "Behoben"}
PUBLIC_DAYS = 7
MAX_INCIDENTS = 1000
_lock = threading.Lock()


class IncidentError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _ts(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(datetime.datetime.fromisoformat(str(value)).timestamp())
        except ValueError:
            raise IncidentError("Ungültiger Zeitpunkt.")


def _view(i: Dict[str, Any]) -> Dict[str, Any]:
    end = i.get("resolved") or int(time.time())
    return {**i, "open": i["status"] != "behoben", "duration": end - i["started"],
            "status_label": STATUSES[i["status"]], "severity_label": SEVERITIES[i["severity"]]}


def overview(state: str = "") -> Dict[str, Any]:
    items = [_view(i) for i in _items()]
    items.sort(key=lambda i: (not i["open"], -i["started"]))
    if state == "open":
        items = [i for i in items if i["open"]]
    try:
        import statuspage
        targets = [{"target": f"{a['kind']}:{a['id']}", "name": a["name"]} for a in statuspage.available()]
    except Exception:
        targets = []
    return {"incidents": items[:300], "open": sum(1 for i in items if i["open"]), "severities": SEVERITIES,
            "statuses": STATUSES, "targets": targets}


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    title = str(fields.get("title") or "").strip()[:120]
    if not title:
        raise IncidentError("Titel fehlt.")
    severity = str(fields.get("severity") or "mittel")
    if severity not in SEVERITIES:
        raise IncidentError("Unbekannte Schwere.")
    services = [str(s)[:120] for s in (fields.get("services") or [])][:30]
    return {"title": title, "severity": severity, "services": services, "public": bool(fields.get("public")),
            "summary": str(fields.get("summary") or "").strip()[:2000]}


def save(incident_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean(fields)
    now = int(time.time())
    with _lock:
        items = _items()
        if incident_id:
            i = next((x for x in items if x["id"] == incident_id), None)
            if not i:
                raise IncidentError("Störung nicht gefunden.")
            i.update(clean, started=_ts(fields.get("started"), i["started"]))
        else:
            if len(items) >= MAX_INCIDENTS:
                items = sorted(items, key=lambda x: x["started"])[1:]
            status = str(fields.get("status") or "untersuchung")
            if status not in STATUSES:
                raise IncidentError("Unbekannter Status.")
            started = _ts(fields.get("started"), now)
            text = str(fields.get("text") or "").strip()[:2000] or "Störung erfasst."
            i = {"id": secrets.token_hex(5), **clean, "status": status, "started": started,
                 "resolved": now if status == "behoben" else None, "user": user,
                 "updates": [{"ts": now, "status": status, "text": text, "user": user}]}
            items.append(i)
        settings_store.set(KEY, items)
    return _view(i)


def add_update(incident_id: str, status: str, text: str, user: str = "") -> Dict[str, Any]:
    if status not in STATUSES:
        raise IncidentError("Unbekannter Status.")
    text = str(text or "").strip()[:2000]
    if not text:
        raise IncidentError("Text fehlt.")
    now = int(time.time())
    with _lock:
        items = _items()
        i = next((x for x in items if x["id"] == incident_id), None)
        if not i:
            raise IncidentError("Störung nicht gefunden.")
        i.setdefault("updates", []).append({"ts": now, "status": status, "text": text, "user": user})
        i["updates"] = i["updates"][-200:]
        i["status"] = status
        i["resolved"] = now if status == "behoben" else None
        settings_store.set(KEY, items)
    return _view(i)


def delete(incident_id: str) -> Dict[str, Any]:
    with _lock:
        settings_store.set(KEY, [i for i in _items() if i["id"] != incident_id])
    return overview()


def events_since(since: float) -> List[Dict[str, Any]]:
    out = []
    for i in _items():
        level = {"gering": "info", "mittel": "warning", "schwer": "critical"}[i["severity"]]
        if i["started"] >= since:
            out.append({"ts": i["started"], "level": level, "title": f"Störung: {i['title']}", "detail": SEVERITIES[i["severity"]]})
        if i.get("resolved") and i["resolved"] >= since:
            out.append({"ts": i["resolved"], "level": "good", "title": f"Störung behoben: {i['title']}",
                        "detail": _dur(i["resolved"] - i["started"])})
    return out


def _dur(sec: int) -> str:
    if sec < 3600:
        return f"nach {max(1, sec // 60)} Min."
    if sec < 86400:
        return f"nach {sec // 3600} Std. {sec % 3600 // 60} Min."
    return f"nach {sec // 86400} Tagen"


def open_alerts() -> List[Dict[str, Any]]:
    return [{"level": {"gering": "info", "mittel": "warning", "schwer": "critical"}[i["severity"]], "text": f"Störung: {i['title']} ({STATUSES[i['status']]})"}
            for i in _items() if i["status"] != "behoben"]


def public() -> List[Dict[str, Any]]:
    """For the public status page: no names of users, no internal summary."""
    since = time.time() - PUBLIC_DAYS * 86400
    out = []
    for i in sorted(_items(), key=lambda x: -x["started"]):
        if not i.get("public") or (i["status"] == "behoben" and (i.get("resolved") or 0) < since):
            continue
        out.append({"title": i["title"], "severity": i["severity"], "status": i["status"], "status_label": STATUSES[i["status"]],
                    "started": i["started"], "resolved": i.get("resolved"),
                    "updates": [{"ts": u["ts"], "status_label": STATUSES.get(u["status"], ""), "text": u["text"]}
                                for u in reversed(i.get("updates", []))][:10]})
    return out[:10]
