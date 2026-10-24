"""
Change management (Dokumentation -> Betrieb -> Änderungsanträge): planned changes as a request
with risk, affected services, plan, rollback plan and check - approved by a second person
before they are carried out.

Workflow: Entwurf -> Beantragt -> Genehmigt / Abgelehnt -> In Umsetzung -> Abgeschlossen
(erfolgreich, mit Problemen, zurückgerollt) or Abgebrochen. Every step lands in the change's
timeline; the result goes to the Betriebstagebuch.

- Four eyes: the requester cannot approve their own change - unless nobody else may approve
  (then it is recorded as "Selbstfreigabe"). Low-risk standard changes can be pre-approved.
- Approving can create the maintenance window for the planned time and services at once.
- Overlapping approved changes on the same services are pointed out.
- Waiting approvals and changes past their planned end appear in the status overview.
"""

import datetime
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "changes"
RISKS = {"gering": "Gering", "mittel": "Mittel", "hoch": "Hoch"}
STATUSES = {"entwurf": "Entwurf", "beantragt": "Beantragt", "genehmigt": "Genehmigt", "abgelehnt": "Abgelehnt",
            "umsetzung": "In Umsetzung", "abgeschlossen": "Abgeschlossen", "abgebrochen": "Abgebrochen"}
RESULTS = {"erfolgreich": "Erfolgreich", "probleme": "Mit Problemen", "zurueckgerollt": "Zurückgerollt"}
OPEN = ("entwurf", "beantragt", "genehmigt", "umsetzung")
MAX_CHANGES = 1000
APPROVE_PERM = "changes.approve"
_lock = threading.Lock()


class ChangeError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _save(items: List[Dict[str, Any]]) -> None:
    settings_store.set(KEY, items[-MAX_CHANGES:])


def _get(items: List[Dict[str, Any]], change_id: str) -> Dict[str, Any]:
    c = next((x for x in items if x["id"] == change_id), None)
    if not c:
        raise ChangeError("Änderung nicht gefunden.")
    return c


def _ts(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(datetime.datetime.fromisoformat(str(value)).timestamp())
        except ValueError:
            raise ChangeError("Ungültiger Zeitpunkt.")


def _text(value: Any, label: str, max_len: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ChangeError(f"{label} fehlt.")
    if len(text) > max_len:
        raise ChangeError(f"{label}: höchstens {max_len} Zeichen.")
    return text


def _approvers(exclude: str = "") -> List[str]:
    import auth
    return [u["username"] for u in auth.list_users()
            if not u["disabled"] and not u["expired"] and u["username"] != exclude and auth.can(u["username"], APPROVE_PERM)]


def _names() -> Dict[str, str]:
    try:
        import statuspage
        return {f"{a['kind']}:{a['id']}": a["name"] for a in statuspage.available()}
    except Exception:
        return {}


def _conflicts(c: Dict[str, Any], items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Other approved / running changes on a shared service whose planned time overlaps."""
    if not c.get("start") or not c.get("end"):
        return []
    out = []
    for o in items:
        if o["id"] == c["id"] or o["status"] not in ("genehmigt", "umsetzung") or not o.get("start") or not o.get("end"):
            continue
        if o["start"] < c["end"] and c["start"] < o["end"] and set(o.get("services") or []) & set(c.get("services") or []):
            out.append({"id": o["id"], "title": o["title"], "start": o["start"], "end": o["end"]})
    return out


def _view(c: Dict[str, Any], items: List[Dict[str, Any]], names: Dict[str, str]) -> Dict[str, Any]:
    now = time.time()
    return {**c, "status_label": STATUSES[c["status"]], "risk_label": RISKS[c["risk"]],
            "result_label": RESULTS.get(c.get("result") or "", ""), "open": c["status"] in OPEN,
            "service_names": [names.get(s, s.split(":", 1)[-1]) for s in c.get("services") or []],
            "overdue": bool(c["status"] in ("genehmigt", "umsetzung") and c.get("end") and c["end"] < now),
            "conflicts": _conflicts(c, items)}


def overview(user: str = "") -> Dict[str, Any]:
    import auth
    items = _items()
    names = _names()
    views = [_view(c, items, names) for c in items]
    views.sort(key=lambda c: (not c["open"], -(c.get("start") or c["created"])))
    return {"changes": views[:300], "risks": RISKS, "statuses": STATUSES, "results": RESULTS,
            "targets": [{"target": k, "name": v} for k, v in names.items()],
            "can_approve": bool(user and auth.can(user, APPROVE_PERM)), "approvers": _approvers(),
            "waiting": sum(1 for c in views if c["status"] == "beantragt"),
            "open": sum(1 for c in views if c["open"])}


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    risk = str(fields.get("risk") or "mittel")
    if risk not in RISKS:
        raise ChangeError("Unbekanntes Risiko.")
    start, end = _ts(fields.get("start")), _ts(fields.get("end"))
    if start and end and end <= start:
        raise ChangeError("Das geplante Ende muss nach dem Beginn liegen.")
    names = _names()
    services = [s for s in (str(x) for x in (fields.get("services") or [])) if s in names][:30]
    return {"title": _text(fields.get("title"), "Titel", 120, True), "reason": _text(fields.get("reason"), "Begründung", 2000),
            "description": _text(fields.get("description"), "Beschreibung", 4000), "risk": risk, "services": services,
            "plan": _text(fields.get("plan"), "Ablauf", 4000), "rollback": _text(fields.get("rollback"), "Rückfallplan", 3000),
            "verify": _text(fields.get("verify"), "Prüfung", 2000), "start": start, "end": end,
            "standard": bool(fields.get("standard")) and risk == "gering",
            "maintenance": bool(fields.get("maintenance"))}


def _log(c: Dict[str, Any], status: str, text: str, user: str) -> None:
    c.setdefault("timeline", []).append({"ts": int(time.time()), "status": status, "text": text[:2000], "user": user})
    c["status"] = status


def save(change_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean(fields)
    with _lock:
        items = _items()
        if change_id:
            c = _get(items, change_id)
            if c["status"] not in ("entwurf", "beantragt", "abgelehnt"):
                raise ChangeError("Genehmigte Änderungen lassen sich nicht mehr bearbeiten – abbrechen und neu beantragen.")
            if c["status"] == "beantragt" or c["status"] == "abgelehnt":
                _log(c, "entwurf", "Bearbeitet – Freigabe muss neu beantragt werden.", user)
            c.update(clean, edited=int(time.time()), edited_by=user)
        else:
            c = {"id": secrets.token_hex(5), **clean, "status": "entwurf", "user": user, "created": int(time.time()), "timeline": []}
            _log(c, "entwurf", "Angelegt.", user)
            items.append(c)
        _save(items)
    return {**overview(user), "saved": c["id"]}


def _check_ready(c: Dict[str, Any]) -> None:
    missing = [label for key, label in (("plan", "Ablauf"), ("rollback", "Rückfallplan"), ("start", "geplanter Beginn"), ("end", "geplantes Ende"))
               if not c.get(key)]
    if c["risk"] != "gering" and not c.get("verify"):
        missing.append("Prüfung")
    if missing:
        raise ChangeError(f"Zum Beantragen fehlt: {', '.join(missing)}.")


def _maintenance(c: Dict[str, Any], user: str) -> str:
    import maintenance
    if not c.get("maintenance") or not c.get("services") or not c.get("start") or not c.get("end") or c["end"] <= time.time():
        return ""
    before = {w["id"] for w in maintenance.overview()["windows"]}
    try:
        maintenance.create(f"Änderung: {c['title']}", c["start"], c["end"], c["services"], f"Änderungsantrag {c['id']}", user)
    except maintenance.MaintenanceError as e:
        raise ChangeError(f"Wartungsfenster: {e}")
    new = [w["id"] for w in maintenance.overview()["windows"] if w["id"] not in before]
    return new[0] if new else ""


def _journal(c: Dict[str, Any], user: str) -> None:
    import docs
    try:
        text = f"Ergebnis: {RESULTS.get(c.get('result') or '', STATUSES[c['status']])}."
        if c.get("result_note"):
            text += f" {c['result_note']}"
        docs.journal_save(None, {"title": f"Änderung: {c['title']}", "category": "Änderung", "ref": f"Antrag {c['id']}", "text": text[:4000]}, user)
    except Exception:
        pass


def action(change_id: str, act: str, user: str = "", note: str = "", result: str = "") -> Dict[str, Any]:
    """submit | approve | reject | start | complete (result) | cancel | comment"""
    import auth
    note = _text(note, "Notiz", 2000)
    with _lock:
        items = _items()
        c = _get(items, change_id)
        st = c["status"]
        if act == "submit":
            if st not in ("entwurf", "abgelehnt"):
                raise ChangeError("Nur Entwürfe lassen sich beantragen.")
            _check_ready(c)
            _log(c, "beantragt", note or "Freigabe beantragt.", user)
            if c.get("standard"):
                c["approved_by"], c["approved_at"] = user, int(time.time())
                _log(c, "genehmigt", "Standardänderung mit geringem Risiko – vorab genehmigt.", user)
                c["maintenance_id"] = _maintenance(c, user)
        elif act in ("approve", "reject"):
            if st != "beantragt":
                raise ChangeError("Diese Änderung wartet nicht auf eine Freigabe.")
            if not auth.can(user, APPROVE_PERM):
                raise ChangeError("Keine Berechtigung zum Freigeben von Änderungen.")
            self_approval = user == c["user"]
            if self_approval and _approvers(exclude=user):
                raise ChangeError(f"Vier-Augen-Prinzip: eine andere Person muss freigeben ({', '.join(_approvers(exclude=user)[:3])}).")
            if act == "reject":
                if not note:
                    raise ChangeError("Bitte begründen, warum die Änderung abgelehnt wird.")
                _log(c, "abgelehnt", note, user)
            else:
                c["approved_by"], c["approved_at"] = user, int(time.time())
                _log(c, "genehmigt", (note or "Genehmigt.") + (" (Selbstfreigabe – kein zweiter Freigeber vorhanden)" if self_approval else ""), user)
                c["maintenance_id"] = _maintenance(c, user)
        elif act == "start":
            if st != "genehmigt":
                raise ChangeError("Erst nach der Freigabe umsetzen.")
            c["started_at"] = int(time.time())
            _log(c, "umsetzung", note or "Umsetzung begonnen.", user)
        elif act == "complete":
            if st != "umsetzung":
                raise ChangeError("Die Umsetzung läuft nicht.")
            if result not in RESULTS:
                raise ChangeError("Bitte das Ergebnis wählen.")
            if result != "erfolgreich" and not note:
                raise ChangeError("Bitte kurz beschreiben, was nicht geklappt hat.")
            c.update(result=result, result_note=note, finished_at=int(time.time()))
            _log(c, "abgeschlossen", f"{RESULTS[result]}." + (f" {note}" if note else ""), user)
            _journal(c, user)
        elif act == "cancel":
            if st not in OPEN:
                raise ChangeError("Die Änderung ist schon beendet.")
            c["finished_at"] = int(time.time())
            _log(c, "abgebrochen", note or "Abgebrochen.", user)
            if st == "umsetzung":
                _journal(c, user)
        elif act == "comment":
            if not note:
                raise ChangeError("Kommentar fehlt.")
            c.setdefault("timeline", []).append({"ts": int(time.time()), "status": st, "text": note, "user": user, "comment": True})
        else:
            raise ChangeError("Unbekannte Aktion.")
        _save(items)
    return overview(user)


def delete(change_id: str, user: str = "") -> Dict[str, Any]:
    with _lock:
        items = _items()
        c = _get(items, change_id)
        if c["status"] not in ("entwurf", "abgelehnt"):
            raise ChangeError("Nur Entwürfe und abgelehnte Anträge lassen sich löschen – sonst abbrechen.")
        _save([x for x in items if x["id"] != change_id])
    return overview(user)


def markdown(change_id: str) -> str:
    c = _get(_items(), change_id)
    names = _names()
    fmt = lambda ts: time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "–"
    out = [f"# Änderung: {c['title']}", "", f"- Status: {STATUSES[c['status']]}" + (f" ({RESULTS[c['result']]})" if c.get("result") else ""),
           f"- Risiko: {RISKS[c['risk']]}" + (" · Standardänderung" if c.get("standard") else ""),
           f"- Geplant: {fmt(c.get('start'))} – {fmt(c.get('end'))}",
           f"- Betroffen: {', '.join(names.get(s, s) for s in c.get('services') or []) or '–'}",
           f"- Beantragt von: {c['user']}" + (f" · genehmigt von {c['approved_by']} ({fmt(c.get('approved_at'))})" if c.get("approved_by") else ""), ""]
    for key, label in (("reason", "Begründung"), ("description", "Beschreibung"), ("plan", "Ablauf"), ("rollback", "Rückfallplan"), ("verify", "Prüfung")):
        if c.get(key):
            out += [f"## {label}", "", c[key], ""]
    out += ["## Verlauf", ""] + [f"- {fmt(t['ts'])} · {STATUSES.get(t['status'], t['status'])} · {t['user']}: {t['text']}" for t in c.get("timeline", [])]
    return "\n".join(out) + "\n"


def events_since(since: float) -> List[Dict[str, Any]]:
    out = []
    for c in _items():
        for t in c.get("timeline", []):
            if t["ts"] < since or t.get("comment") or t["status"] == "entwurf":
                continue
            level = {"beantragt": "info", "genehmigt": "info", "abgelehnt": "warning", "umsetzung": "info", "abgebrochen": "warning"}.get(t["status"], "info")
            if t["status"] == "abgeschlossen":
                level = "good" if c.get("result") == "erfolgreich" else "warning"
            verb = {"umsetzung": "wird umgesetzt"}.get(t["status"], STATUSES[t["status"]].lower())
            out.append({"ts": t["ts"], "level": level, "title": f"Änderung {verb}: {c['title']}",
                        "detail": f"{t['user']} · {t['text'][:160]}", "link": "/doku#aenderungsantraege"})
    return out


def alerts() -> List[Dict[str, Any]]:
    out = []
    now = time.time()
    for c in _items():
        if c["status"] == "beantragt":
            out.append({"level": "info", "text": f"Änderung wartet auf Freigabe: {c['title']}"})
        elif c["status"] in ("genehmigt", "umsetzung") and c.get("end") and c["end"] < now:
            out.append({"level": "warning", "text": f"Änderung über das geplante Ende hinaus offen: {c['title']}"})
    return out
