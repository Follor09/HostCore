"""
Problem management (Dokumentation -> Betrieb -> Probleme): the cause behind recurring incidents.

- A problem bundles incidents, gets a root cause analysis and - once the cause is known but not
  yet removed - becomes a "known error" with a workaround. It is solved through a change request
  (changes.py), which can be created from the problem.
- Status: Neu -> In Analyse -> Bekannter Fehler -> Gelöst -> Geschlossen. "Gelöst" needs a
  solution; with a linked change it should be completed successfully (pointed out otherwise).
- Suggestions: services with two or more incidents within 90 days that no problem covers yet.
- Open incidents on a service with a known error are pointed out together with the workaround.
"""

import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "problems"
STATUSES = {"neu": "Neu", "analyse": "In Analyse", "bekannt": "Bekannter Fehler", "geloest": "Gelöst", "geschlossen": "Geschlossen"}
PRIORITIES = {"niedrig": "Niedrig", "mittel": "Mittel", "hoch": "Hoch"}
OPEN = ("neu", "analyse", "bekannt")
SUGGEST_DAYS = 90
MAX_PROBLEMS = 500
_lock = threading.Lock()


class ProblemError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _get(items: List[Dict[str, Any]], pid: str) -> Dict[str, Any]:
    p = next((x for x in items if x["id"] == pid), None)
    if not p:
        raise ProblemError("Problem nicht gefunden.")
    return p


def _text(value: Any, label: str, max_len: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ProblemError(f"{label} fehlt.")
    if len(text) > max_len:
        raise ProblemError(f"{label}: höchstens {max_len} Zeichen.")
    return text


def _incidents() -> Dict[str, Dict[str, Any]]:
    import incidents
    return {i["id"]: i for i in incidents.overview()["incidents"]}


def _changes() -> Dict[str, Dict[str, Any]]:
    import changes
    return {c["id"]: c for c in changes.overview()["changes"]}


def _suggestions(items: List[Dict[str, Any]], incs: Dict[str, Dict[str, Any]], names: Dict[str, str]) -> List[Dict[str, Any]]:
    since = time.time() - SUGGEST_DAYS * 86400
    covered = {iid for p in items for iid in p.get("incidents", [])}
    covered_svc = {s for p in items if p["status"] in OPEN for s in p.get("services", [])}
    by_svc: Dict[str, List[Dict[str, Any]]] = {}
    for i in incs.values():
        if i["started"] < since or i["id"] in covered:
            continue
        for s in i.get("services") or []:
            by_svc.setdefault(s, []).append(i)
    out = []
    for svc, lst in by_svc.items():
        if len(lst) >= 2 and svc not in covered_svc:
            out.append({"service": svc, "name": names.get(svc, svc.split(":", 1)[-1]), "count": len(lst),
                        "incidents": [{"id": i["id"], "title": i["title"], "started": i["started"]} for i in sorted(lst, key=lambda x: -x["started"])][:10]})
    return sorted(out, key=lambda x: -x["count"])


def _view(p: Dict[str, Any], incs: Dict[str, Dict[str, Any]], chgs: Dict[str, Dict[str, Any]], names: Dict[str, str]) -> Dict[str, Any]:
    linked = [{"id": i, "title": incs[i]["title"], "started": incs[i]["started"], "open": incs[i]["open"], "severity": incs[i]["severity"]}
              for i in p.get("incidents", []) if i in incs]
    change = chgs.get(p.get("change_id") or "")
    warn = ""
    if p["status"] in ("geloest", "geschlossen") and change and not (change["status"] == "abgeschlossen" and change.get("result") == "erfolgreich"):
        warn = f"Der verknüpfte Änderungsantrag ist {change['status_label'].lower()}" + (f" ({change['result_label']})" if change.get("result_label") else "") + "."
    return {**p, "status_label": STATUSES[p["status"]], "priority_label": PRIORITIES[p["priority"]], "open": p["status"] in OPEN,
            "incident_list": linked, "service_names": [names.get(s, s.split(":", 1)[-1]) for s in p.get("services", [])],
            "change": change and {"id": change["id"], "title": change["title"], "status_label": change["status_label"],
                                  "result_label": change.get("result_label", "")}, "warning": warn,
            "recurrences": len(linked)}


def overview() -> Dict[str, Any]:
    import changes
    items = _items()
    incs = _incidents()
    chgs = _changes()
    names = changes._names()
    views = [_view(p, incs, chgs, names) for p in items]
    views.sort(key=lambda p: (not p["open"], {"hoch": 0, "mittel": 1, "niedrig": 2}[p["priority"]], -p["created"]))
    # Open incidents on a service with a known error: show the workaround
    known = [p for p in views if p["status"] == "bekannt" and p.get("workaround")]
    hints = []
    for i in incs.values():
        if not i["open"]:
            continue
        for p in known:
            if set(i.get("services") or []) & set(p.get("services") or []) or i["id"] in p.get("incidents", []):
                hints.append({"incident": i["id"], "incident_title": i["title"], "problem": p["id"], "problem_title": p["title"],
                              "workaround": p["workaround"]})
    return {"problems": views, "statuses": STATUSES, "priorities": PRIORITIES, "suggestions": _suggestions(items, incs, names),
            "known_error_hints": hints, "targets": [{"target": k, "name": v} for k, v in names.items()],
            "incidents": [{"id": i["id"], "title": i["title"], "started": i["started"]} for i in sorted(incs.values(), key=lambda x: -x["started"])][:200],
            "changes": [{"id": c["id"], "title": c["title"], "status_label": c["status_label"]} for c in chgs.values()][:200],
            "open": sum(1 for p in views if p["open"]), "known_errors": sum(1 for p in views if p["status"] == "bekannt")}


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    import changes
    priority = str(fields.get("priority") or "mittel")
    if priority not in PRIORITIES:
        raise ProblemError("Unbekannte Priorität.")
    names = changes._names()
    incs = _incidents()
    chg = str(fields.get("change_id") or "")
    if chg and chg not in _changes():
        raise ProblemError("Änderungsantrag nicht gefunden.")
    return {"title": _text(fields.get("title"), "Titel", 120, True), "description": _text(fields.get("description"), "Beschreibung", 3000),
            "priority": priority, "services": [s for s in (str(x) for x in fields.get("services") or []) if s in names][:20],
            "incidents": [i for i in (str(x) for x in fields.get("incidents") or []) if i in incs][:50],
            "cause": _text(fields.get("cause"), "Ursache", 4000), "workaround": _text(fields.get("workaround"), "Workaround", 3000),
            "solution": _text(fields.get("solution"), "Lösung", 3000), "change_id": chg}


def _log(p: Dict[str, Any], text: str, user: str) -> None:
    p.setdefault("timeline", []).append({"ts": int(time.time()), "status": p["status"], "text": text[:1000], "user": user})


def save(pid: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean(fields)
    with _lock:
        items = _items()
        if pid:
            p = _get(items, pid)
            before = set(p.get("incidents", []))
            p.update(clean, edited=int(time.time()), edited_by=user)
            added = set(clean["incidents"]) - before
            if added:
                _log(p, f"{len(added)} {'Störung' if len(added) == 1 else 'Störungen'} zugeordnet.", user)
        else:
            if len(items) >= MAX_PROBLEMS:
                raise ProblemError(f"Höchstens {MAX_PROBLEMS} Probleme.")
            p = {"id": secrets.token_hex(5), **clean, "status": "neu", "user": user, "created": int(time.time()), "timeline": []}
            _log(p, "Erfasst.", user)
            items.append(p)
        settings_store.set(KEY, items)
    return {**overview(), "saved": p["id"]}


def set_status(pid: str, status: str, note: str, user: str = "") -> Dict[str, Any]:
    if status not in STATUSES:
        raise ProblemError("Unbekannter Status.")
    with _lock:
        items = _items()
        p = _get(items, pid)
        if status == "bekannt" and not p.get("workaround"):
            raise ProblemError("Für einen bekannten Fehler bitte zuerst den Workaround eintragen.")
        if status in ("geloest", "geschlossen") and not p.get("solution"):
            raise ProblemError("Bitte zuerst die Lösung eintragen.")
        if status in ("bekannt", "geloest") and not p.get("cause"):
            raise ProblemError("Bitte zuerst die Ursache eintragen.")
        p["status"] = status
        if status in ("geloest", "geschlossen"):
            p.setdefault("solved", int(time.time()))
        else:
            p.pop("solved", None)
        _log(p, _text(note, "Notiz", 1000) or f"Status: {STATUSES[status]}.", user)
        settings_store.set(KEY, items)
    return overview()


def create_change(pid: str, user: str = "") -> Dict[str, Any]:
    """A change request for the solution, linked to the problem."""
    import changes
    with _lock:
        items = _items()
        p = _get(items, pid)
        if p.get("change_id"):
            raise ProblemError("Es ist schon ein Änderungsantrag verknüpft.")
    res = changes.save(None, {"title": f"Lösung: {p['title']}"[:120], "risk": "mittel", "services": p.get("services", []),
                              "reason": f"Problem {p['id']}: {p['title']}\n\nUrsache: {p.get('cause') or '–'}"[:2000],
                              "description": p.get("solution") or "", "maintenance": True}, user)
    with _lock:
        items = _items()
        p = _get(items, pid)
        p["change_id"] = res["saved"]
        _log(p, "Änderungsantrag für die Lösung angelegt.", user)
        settings_store.set(KEY, items)
    return {**overview(), "change": res["saved"]}


def delete(pid: str) -> Dict[str, Any]:
    with _lock:
        items = _items()
        _get(items, pid)
        settings_store.set(KEY, [x for x in items if x["id"] != pid])
    return overview()


def markdown() -> str:
    o = overview()
    fmt = lambda ts: time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"
    out = ["# Probleme und bekannte Fehler", ""]
    for p in o["problems"]:
        out += [f"## {p['title']}", "", f"- Status: {p['status_label']} · Priorität: {p['priority_label']} · erfasst {fmt(p['created'])} von {p['user']}",
                f"- Dienste: {', '.join(p['service_names']) or '–'}", f"- Störungen: {len(p['incident_list'])}"]
        if p["change"]:
            out.append(f"- Änderungsantrag: {p['change']['title']} ({p['change']['status_label']})")
        for key, label in (("description", "Beschreibung"), ("cause", "Ursache"), ("workaround", "Workaround"), ("solution", "Lösung")):
            if p.get(key):
                out += ["", f"**{label}:** {p[key]}"]
        out.append("")
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    try:
        o = overview()
    except Exception:
        return []
    out = []
    if o["suggestions"]:
        s = o["suggestions"][0]
        out.append({"level": "info", "text": f"{s['name']}: {s['count']} Störungen in {SUGGEST_DAYS} Tagen – Problem erfassen?"})
    return out
