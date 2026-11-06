"""
Supplier register (Compliance -> Dienstleister): external providers with their criticality, data
processing agreement, certificates, review dates and the services that depend on them.

- Critical suppliers need a review at least yearly; missing processing agreements for suppliers
  that handle personal data, expired certificates and due reviews show in the status overview.
- A review is recorded with result and note and stays in the supplier's history.
"""

import datetime
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "suppliers"
CRITICALITY = {"niedrig": "Niedrig", "mittel": "Mittel", "hoch": "Hoch", "kritisch": "Kritisch"}
INTERVAL = {"niedrig": 36, "mittel": 24, "hoch": 12, "kritisch": 12}     # review interval in months
RESULTS = {"ok": "Ohne Befund", "auflagen": "Mit Auflagen", "kritisch": "Kritischer Befund"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOON_DAYS = 30
MAX_ITEMS = 500
_lock = threading.Lock()


class SupplierError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _get(items: List[Dict[str, Any]], sid: str) -> Dict[str, Any]:
    s = next((x for x in items if x["id"] == sid), None)
    if not s:
        raise SupplierError("Dienstleister nicht gefunden.")
    return s


def _date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if not DATE_RE.match(text):
            raise ValueError
        datetime.date.fromisoformat(text)
    except ValueError:
        raise SupplierError(f"{label}: ungültiges Datum.")
    return text


def _days(date: str) -> Optional[int]:
    return (datetime.date.fromisoformat(date) - datetime.date.today()).days if date else None


def _next_review(s: Dict[str, Any]) -> str:
    if s.get("next_review"):
        return s["next_review"]
    last = s.get("last_review") or time.strftime("%Y-%m-%d", time.localtime(s["created"]))
    d = datetime.date.fromisoformat(last) + datetime.timedelta(days=int(INTERVAL[s["criticality"]] * 30.44))
    return d.isoformat()


def _issues(s: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    if s["personal_data"] and not s["dpa_signed"]:
        out.append({"level": "critical", "text": "Auftragsverarbeitung ohne AV-Vertrag"})
    days = _days(s["dpa_until"])
    if s["personal_data"] and days is not None and days < 0:
        out.append({"level": "critical", "text": "AV-Vertrag abgelaufen"})
    days = _days(s["cert_until"])
    if days is not None and days < 0:
        out.append({"level": "warning", "text": f"Zertifikat {s['certificates'] or ''} abgelaufen".replace("  ", " ")})
    elif days is not None and days <= SOON_DAYS:
        out.append({"level": "info", "text": f"Zertifikat läuft in {days} Tagen ab"})
    days = _days(_next_review(s))
    if days is not None and days < 0:
        out.append({"level": "warning", "text": "Prüfung überfällig"})
    elif days is not None and days <= SOON_DAYS:
        out.append({"level": "info", "text": f"Prüfung in {days} Tagen fällig"})
    if s["criticality"] == "kritisch" and not s["exit_plan"]:
        out.append({"level": "warning", "text": "Kritischer Dienstleister ohne Ausstiegsplan"})
    return out


def _view(s: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    return {**s, "criticality_label": CRITICALITY[s["criticality"]], "next_review_date": _next_review(s), "issues": _issues(s),
            "service_names": [names.get(x, x.split(":", 1)[-1]) for x in s.get("services", [])]}


def overview() -> Dict[str, Any]:
    import changes
    names = changes._names()
    rank = {"kritisch": 0, "hoch": 1, "mittel": 2, "niedrig": 3}
    views = sorted((_view(s, names) for s in _items()), key=lambda s: (rank[s["criticality"]], s["name"].lower()))
    return {"suppliers": views, "criticality": CRITICALITY, "results": RESULTS, "targets": [{"target": k, "name": v} for k, v in names.items()],
            "issues": sum(1 for s in views if any(i["level"] != "info" for i in s["issues"]))}


def _clean(f: Dict[str, Any]) -> Dict[str, Any]:
    name = str(f.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise SupplierError("Name fehlt (höchstens 120 Zeichen).")
    crit = str(f.get("criticality") or "mittel")
    if crit not in CRITICALITY:
        raise SupplierError("Unbekannte Kritikalität.")
    return {"name": name, "service": str(f.get("service") or "").strip()[:200], "contact": str(f.get("contact") or "").strip()[:200],
            "criticality": crit, "personal_data": bool(f.get("personal_data")), "dpa_signed": _date(f.get("dpa_signed"), "AV-Vertrag vom"),
            "dpa_until": _date(f.get("dpa_until"), "AV-Vertrag bis"), "certificates": str(f.get("certificates") or "").strip()[:200],
            "cert_until": _date(f.get("cert_until"), "Zertifikat gültig bis"), "location": str(f.get("location") or "").strip()[:80],
            "third_country": bool(f.get("third_country")), "sub_processors": str(f.get("sub_processors") or "").strip()[:500],
            "exit_plan": str(f.get("exit_plan") or "").strip()[:2000], "next_review": _date(f.get("next_review"), "Nächste Prüfung"),
            "notes": str(f.get("notes") or "").strip()[:2000], "services": [str(x)[:120] for x in f.get("services") or []][:30]}


def save(sid: Optional[str], f: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean(f)
    with _lock:
        items = _items()
        if sid:
            _get(items, sid).update(clean, edited=int(time.time()), edited_by=user)
        else:
            if len(items) >= MAX_ITEMS:
                raise SupplierError(f"Höchstens {MAX_ITEMS} Dienstleister.")
            sid = secrets.token_hex(5)
            items.append({"id": sid, **clean, "created": int(time.time()), "user": user, "reviews": [], "last_review": ""})
        settings_store.set(KEY, items)
    return {**overview(), "saved": sid}


def review(sid: str, date: str, result: str, note: str, user: str = "") -> Dict[str, Any]:
    date = _date(date, "Datum")
    if not date:
        raise SupplierError("Datum der Prüfung fehlt.")
    if result not in RESULTS:
        raise SupplierError("Unbekanntes Ergebnis.")
    with _lock:
        items = _items()
        s = _get(items, sid)
        s.setdefault("reviews", []).append({"date": date, "result": result, "result_label": RESULTS[result], "note": str(note or "").strip()[:1000], "user": user})
        s["last_review"] = max(s.get("last_review") or "", date)
        s["next_review"] = ""     # back to the interval of the criticality
        settings_store.set(KEY, items)
    return overview()


def delete(sid: str) -> Dict[str, Any]:
    with _lock:
        items = _items()
        _get(items, sid)
        settings_store.set(KEY, [x for x in items if x["id"] != sid])
    return overview()


def markdown() -> str:
    out = ["# Dienstleister-Register", "", "| Dienstleister | Leistung | Kritikalität | Personenbezug | AV-Vertrag | Zertifikate | Letzte Prüfung | Nächste Prüfung |", "|---|---|---|---|---|---|---|---|"]
    for s in overview()["suppliers"]:
        out.append(f"| {s['name']} | {s['service'] or '–'} | {s['criticality_label']} | {'ja' if s['personal_data'] else 'nein'} | {s['dpa_signed'] or '–'} | {s['certificates'] or '–'} | {s['last_review'] or '–'} | {s['next_review_date']} |")
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    try:
        rows = overview()["suppliers"]
    except Exception:
        return []
    return [{"level": i["level"], "text": f"{s['name']}: {i['text']}"} for s in rows for i in s["issues"]]
