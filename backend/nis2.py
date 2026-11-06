"""
NIS-2 reporting deadlines (Compliance -> NIS-2-Meldefristen): the stages of § 32 BSIG / Art. 23
NIS-2 for one security incident.

- A case starts from the moment of awareness (not from the end of the analysis): early warning
  within 24 hours, incident notification within 72 hours, final report one month after the
  notification was submitted; while an incident is still ongoing a progress report takes the
  final report's place and the final report follows within a month of closing it.
- Personal data involved: the separate 72-hour notification to the data protection authority
  (Art. 33 GDPR) is tracked alongside.
- For each stage: a prepared text from the case, then who submitted it, when and the reference.
  HostCore doesn't submit anything itself; reports go through the BSI portal.
- A structure to work with, no legal advice: whether an incident is significant is your decision.
"""

import calendar
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "nis2"
STATUSES = {"pruefung": "In Prüfung", "meldepflichtig": "Meldepflichtig", "nicht_meldepflichtig": "Nicht meldepflichtig", "abgeschlossen": "Abgeschlossen"}
ENTITIES = {"": "Noch nicht festgelegt", "besonders_wichtig": "Besonders wichtige Einrichtung", "wichtig": "Wichtige Einrichtung", "nicht_betroffen": "Nicht betroffen"}
CRITERIA = {"betrieb": "Schwerwiegende Betriebsstörung der Dienste", "finanziell": "Erheblicher finanzieller Verlust",
            "dritte": "Erheblicher Schaden für andere Personen oder Unternehmen"}
STAGES = {"early": "Frühwarnung", "notification": "Meldung", "progress": "Fortschrittsmeldung", "final": "Abschlussmeldung", "gdpr": "Meldung an die Datenschutzbehörde"}
WARN_HOURS = {"early": 6, "notification": 24, "final": 24 * 7, "gdpr": 24}
MAX_CASES = 500
_lock = threading.Lock()


class Nis2Error(Exception):
    pass


def _data() -> Dict[str, Any]:
    return {"entity": "", "contact": "", "phone": "", "sector": "", "cases": [], **(settings_store.get(KEY, {}) or {})}


def _ts(value: Any, label: str) -> int:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        raise Nis2Error(f"{label}: ungültiger Zeitpunkt.")
    if ts <= 0 or ts > time.time() + 300:
        raise Nis2Error(f"{label}: der Zeitpunkt darf nicht in der Zukunft liegen.")
    return ts


def add_month(ts: int) -> int:
    t = time.localtime(ts)
    year, month = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    day = min(t.tm_mday, calendar.monthrange(year, month)[1])
    return int(time.mktime((year, month, day, t.tm_hour, t.tm_min, t.tm_sec, 0, 0, -1)))


def deadlines(c: Dict[str, Any], now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = now or time.time()
    aware, rep = c["aware"], c.get("reports", {})
    out = []
    if c["status"] == "meldepflichtig" or (c["status"] == "abgeschlossen" and c.get("reportable")):
        notified = rep.get("notification", {}).get("submitted")
        base = notified or aware + 72 * 3600
        ongoing = not c.get("handled")
        out += [{"stage": "early", "due": aware + 24 * 3600}, {"stage": "notification", "due": aware + 72 * 3600}]
        if ongoing:
            out.append({"stage": "progress", "due": add_month(base)})
        else:
            out.append({"stage": "final", "due": max(add_month(base), add_month(c["handled"]))})
    if c.get("personal_data") and c["status"] != "nicht_meldepflichtig":
        out.append({"stage": "gdpr", "due": aware + 72 * 3600})
    for d in out:
        r = rep.get(d["stage"]) or {}
        d.update(label=STAGES[d["stage"]], submitted=r.get("submitted", 0), reference=r.get("reference", ""), by=r.get("by", ""),
                 late=bool(r.get("submitted") and r["submitted"] > d["due"]), left=int(d["due"] - now))
        d["state"] = "done" if d["submitted"] else "overdue" if d["left"] < 0 else "soon" if d["left"] < WARN_HOURS.get(d["stage"], 24) * 3600 else "open"
    return out


def _view(c: Dict[str, Any]) -> Dict[str, Any]:
    return {**c, "status_label": STATUSES[c["status"]], "deadlines": deadlines(c), "open": c["status"] in ("pruefung", "meldepflichtig")}


def overview() -> Dict[str, Any]:
    import incidents
    d = _data()
    views = sorted((_view(c) for c in d["cases"]), key=lambda c: (not c["open"], -c["aware"]))
    try:
        incs = [{"id": i["id"], "title": i["title"], "started": i["started"]} for i in sorted(incidents.overview()["incidents"], key=lambda x: -x["started"])][:100]
    except Exception:
        incs = []
    return {"entity": d["entity"], "contact": d["contact"], "phone": d["phone"], "sector": d["sector"], "entities": ENTITIES, "cases": views, "statuses": STATUSES,
            "criteria": CRITERIA, "stages": STAGES, "incidents": incs, "open": sum(1 for c in views if c["open"])}


def save_profile(f: Dict[str, Any]) -> Dict[str, Any]:
    entity = str(f.get("entity") or "")
    if entity not in ENTITIES:
        raise Nis2Error("Unbekannte Einstufung.")
    with _lock:
        d = _data()
        d.update(entity=entity, contact=str(f.get("contact") or "").strip()[:200], phone=str(f.get("phone") or "").strip()[:60], sector=str(f.get("sector") or "").strip()[:120])
        settings_store.set(KEY, d)
    return overview()


def _clean(f: Dict[str, Any]) -> Dict[str, Any]:
    title = str(f.get("title") or "").strip()
    if not 1 <= len(title) <= 160:
        raise Nis2Error("Titel fehlt (höchstens 160 Zeichen).")
    status = str(f.get("status") or "pruefung")
    if status not in STATUSES or status == "abgeschlossen":
        raise Nis2Error("Unbekannter Status.")
    handled = _ts(f["handled"], "Abschluss der Bearbeitung") if f.get("handled") else 0
    aware = _ts(f.get("aware"), "Kenntnisnahme")
    if handled and handled < aware:
        raise Nis2Error("Der Abschluss der Bearbeitung liegt vor der Kenntnisnahme.")
    criteria = [x for x in f.get("criteria") or [] if x in CRITERIA]
    reason = str(f.get("reason") or "").strip()[:2000]
    if status == "nicht_meldepflichtig" and not reason:
        raise Nis2Error("Bitte begründen, warum keine Meldepflicht besteht – das ist der Nachweis.")
    return {"title": title, "aware": aware, "status": status, "incident_id": str(f.get("incident_id") or "")[:20], "criteria": criteria, "reason": reason,
            "description": str(f.get("description") or "").strip()[:4000], "impact": str(f.get("impact") or "").strip()[:3000],
            "indicators": str(f.get("indicators") or "").strip()[:3000], "measures": str(f.get("measures") or "").strip()[:3000],
            "cause": str(f.get("cause") or "").strip()[:3000], "malicious": bool(f.get("malicious")), "cross_border": bool(f.get("cross_border")),
            "personal_data": bool(f.get("personal_data")), "handled": handled}


def save(cid: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean(fields)
    with _lock:
        d = _data()
        if cid:
            c = _case(d, cid)
            if c["status"] == "abgeschlossen":
                raise Nis2Error("Abgeschlossene Fälle bleiben unverändert.")
            if c["aware"] != clean["aware"]:
                _log(c, "Zeitpunkt der Kenntnisnahme geändert – die Fristen wurden neu berechnet.", user)
            c.update(clean, edited=int(time.time()), edited_by=user)
            _log(c, f"Bearbeitet (Status: {STATUSES[c['status']]}).", user)
        else:
            if len(d["cases"]) >= MAX_CASES:
                raise Nis2Error(f"Höchstens {MAX_CASES} Fälle.")
            c = {"id": secrets.token_hex(5), **clean, "user": user, "created": int(time.time()), "reports": {}, "timeline": []}
            _log(c, "Fall angelegt.", user)
            d["cases"].append(c)
        settings_store.set(KEY, d)
    return {**overview(), "saved": c["id"]}


def _case(d: Dict[str, Any], cid: str) -> Dict[str, Any]:
    c = next((x for x in d["cases"] if x["id"] == cid), None)
    if not c:
        raise Nis2Error("Fall nicht gefunden.")
    return c


def _log(c: Dict[str, Any], text: str, user: str) -> None:
    c.setdefault("timeline", []).append({"ts": int(time.time()), "text": text[:500], "user": user})


def report(cid: str, stage: str, submitted: Any, reference: str, note: str, user: str = "") -> Dict[str, Any]:
    """Record that a stage was submitted (outside HostCore)."""
    if stage not in STAGES:
        raise Nis2Error("Unbekannte Meldestufe.")
    ts = _ts(submitted, "Zeitpunkt der Meldung")
    with _lock:
        d = _data()
        c = _case(d, cid)
        c.setdefault("reports", {})[stage] = {"submitted": ts, "reference": str(reference or "").strip()[:120], "note": str(note or "").strip()[:1000], "by": user,
                                              "recorded": int(time.time())}
        _log(c, f"{STAGES[stage]} eingereicht.", user)
        settings_store.set(KEY, d)
    return overview()


def close(cid: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        d = _data()
        c = _case(d, cid)
        if c["status"] == "pruefung":
            raise Nis2Error("Erst entscheiden, ob eine Meldepflicht besteht.")
        missing = [x["label"] for x in deadlines(c) if not x["submitted"] and x["stage"] != "progress"]
        if missing:
            raise Nis2Error(f"Noch nicht eingereicht: {', '.join(missing)}.")
        if c["status"] == "meldepflichtig" and not c.get("handled"):
            raise Nis2Error("Bitte zuerst den Abschluss der Bearbeitung eintragen.")
        c["reportable"] = c["status"] == "meldepflichtig"
        c["status"] = "abgeschlossen"
        _log(c, "Fall abgeschlossen.", user)
        settings_store.set(KEY, d)
    return overview()


def delete(cid: str) -> Dict[str, Any]:
    with _lock:
        d = _data()
        c = _case(d, cid)
        if c.get("reports"):
            raise Nis2Error("Fälle mit eingereichten Meldungen bleiben als Nachweis erhalten.")
        d["cases"] = [x for x in d["cases"] if x["id"] != cid]
        settings_store.set(KEY, d)
    return overview()


def _fmt(ts: Any) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "–"


def draft(cid: str, stage: str) -> str:
    """Prepared text for a stage, to copy into the reporting portal."""
    if stage not in STAGES:
        raise Nis2Error("Unbekannte Meldestufe.")
    d = _data()
    c = _case(d, cid)
    yn = lambda v: "ja" if v else "nein"
    out = [f"# {STAGES[stage]}: {c['title']}", "", f"- Einrichtung: {ENTITIES[d['entity']]}" + (f", Sektor {d['sector']}" if d["sector"] else ""),
           f"- Ansprechstelle: {d['contact'] or '–'}" + (f", {d['phone']}" if d["phone"] else ""), f"- Kenntnis seit: {_fmt(c['aware'])}", ""]
    if stage == "early":
        out += [f"- Verdacht auf rechtswidrige oder böswillige Handlung: {yn(c['malicious'])}", f"- Grenzüberschreitende Auswirkungen möglich: {yn(c['cross_border'])}", ""]
        sections = [("Beschreibung", "description")]
    elif stage == "gdpr":
        sections = [("Art der Verletzung", "description"), ("Wahrscheinliche Folgen", "impact"), ("Ergriffene Maßnahmen", "measures")]
    else:
        out += [f"- Verdacht auf rechtswidrige oder böswillige Handlung: {yn(c['malicious'])}", f"- Grenzüberschreitende Auswirkungen: {yn(c['cross_border'])}", ""]
        sections = [("Beschreibung", "description"), ("Schweregrad und Auswirkungen", "impact"), ("Kompromittierungsindikatoren", "indicators")]
        if stage in ("progress", "final"):
            sections += [("Ursache", "cause"), ("Abhilfemaßnahmen", "measures")]
    if c["criteria"] and stage != "gdpr":
        out += ["## Einstufung als erheblich", ""] + [f"- {CRITERIA[x]}" for x in c["criteria"]] + [""]
    for label, key in sections:
        out += [f"## {label}", "", c.get(key) or "(noch offen)", ""]
    return "\n".join(out)


def markdown() -> str:
    o = overview()
    out = ["# NIS-2-Meldefristen", "", f"Einstufung: {ENTITIES[o['entity']]}", ""]
    for c in o["cases"]:
        out += [f"## {c['title']}", "", f"- Status: {c['status_label']} · Kenntnis: {_fmt(c['aware'])}"]
        if c.get("reason"):
            out.append(f"- Begründung: {c['reason']}")
        out += ["", "| Stufe | Frist | Eingereicht | Referenz | Von |", "|---|---|---|---|---|"]
        out += [f"| {x['label']} | {_fmt(x['due'])} | {_fmt(x['submitted'])}{' (verspätet)' if x['late'] else ''} | {x['reference'] or '–'} | {x['by'] or '–'} |" for x in c["deadlines"]]
        out.append("")
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    out = []
    for c in _data()["cases"]:
        if c["status"] == "pruefung" and time.time() - c["aware"] > 12 * 3600:
            out.append({"level": "critical", "text": f"NIS-2: „{c['title']}“ ist noch nicht eingestuft – die 24-Stunden-Frist läuft seit {_fmt(c['aware'])}"})
        for d in deadlines(c):
            if d["state"] == "overdue":
                out.append({"level": "critical", "text": f"NIS-2: {d['label']} für „{c['title']}“ ist überfällig (Frist {_fmt(d['due'])})"})
            elif d["state"] == "soon":
                out.append({"level": "warning", "text": f"NIS-2: {d['label']} für „{c['title']}“ fällig bis {_fmt(d['due'])}"})
    return out
