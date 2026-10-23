"""
Incident management (Dokumentation -> Betrieb -> Störungen): an incident has a title, severity,
the affected services, a status and a timeline of updates ("Ursache gefunden", "behoben" …).
Opening and resolving become events; incidents marked public appear on the public status page
while open and for a week after they were resolved.

A resolved incident can get a post-mortem: summary, impact, root cause, what went well / badly
and actions with owner and due date. Actions can become cards on the task board; their state
follows the card (done when the card is in "Erledigt"). Overdue open actions reach the daily mail.
"""

import datetime
import re
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
MAX_ACTIONS = 20
PM_FIELDS = {"summary": 3000, "impact": 3000, "cause": 3000, "went_well": 2000, "went_badly": 2000, "lessons": 2000}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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


def _cards() -> Dict[str, Dict[str, Any]]:
    try:
        import kanban
        return {c["id"]: c for c in kanban.board()["cards"]}
    except Exception:
        return {}


def _pm_view(pm: Optional[Dict[str, Any]], cards: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pm:
        return None
    today = time.strftime("%Y-%m-%d")
    actions = []
    for a in pm.get("actions", []):
        card = cards.get(a.get("card", ""))
        done = bool(a.get("done")) or bool(card and card["column"] == "done")
        actions.append({**a, "done": done, "card_exists": bool(card), "card_column": card["column"] if card else "",
                        "overdue": bool(not done and a.get("due") and a["due"] < today)})
    return {**pm, "actions": actions, "open_actions": sum(1 for a in actions if not a["done"])}


def _view(i: Dict[str, Any], cards: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    end = i.get("resolved") or int(time.time())
    return {**i, "open": i["status"] != "behoben", "duration": end - i["started"],
            "status_label": STATUSES[i["status"]], "severity_label": SEVERITIES[i["severity"]],
            "postmortem": _pm_view(i.get("postmortem"), cards if cards is not None else _cards())}


def overview(state: str = "") -> Dict[str, Any]:
    cards = _cards()
    items = [_view(i, cards) for i in _items()]
    items.sort(key=lambda i: (not i["open"], -i["started"]))
    if state == "open":
        items = [i for i in items if i["open"]]
    try:
        import statuspage
        targets = [{"target": f"{a['kind']}:{a['id']}", "name": a["name"]} for a in statuspage.available()]
    except Exception:
        targets = []
    return {"incidents": items[:300], "open": sum(1 for i in items if i["open"]), "severities": SEVERITIES,
            "statuses": STATUSES, "targets": targets,
            "open_actions": sum((i["postmortem"] or {}).get("open_actions", 0) for i in items)}


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


def save_postmortem(incident_id: str, fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    pm: Dict[str, Any] = {k: str(fields.get(k) or "").strip()[:n] for k, n in PM_FIELDS.items()}
    actions = []
    for a in (fields.get("actions") or [])[:MAX_ACTIONS]:
        text = str(a.get("text") or "").strip()[:200]
        if not text:
            continue
        due = str(a.get("due") or "").strip()
        if due and not DATE_RE.match(due):
            raise IncidentError("Termin als JJJJ-MM-TT.")
        actions.append({"id": str(a.get("id") or "")[:12] or secrets.token_hex(4), "text": text,
                        "owner": str(a.get("owner") or "").strip()[:60], "due": due, "done": bool(a.get("done")),
                        "card": str(a.get("card") or "")[:12]})
    pm["actions"] = actions
    with _lock:
        items = _items()
        i = next((x for x in items if x["id"] == incident_id), None)
        if not i:
            raise IncidentError("Störung nicht gefunden.")
        old = i.get("postmortem") or {}
        pm.update(created=old.get("created") or int(time.time()), user=old.get("user") or user,
                  updated=int(time.time()), updated_by=user)
        i["postmortem"] = pm
        settings_store.set(KEY, items)
    if fields.get("create_tasks"):
        return create_tasks(incident_id, user)
    return _view(i)


def create_tasks(incident_id: str, user: str = "") -> Dict[str, Any]:
    """A task board card for every action that has none (yet), labelled "Post-Mortem"."""
    import kanban
    with _lock:
        items = _items()
        i = next((x for x in items if x["id"] == incident_id), None)
        if not i or not i.get("postmortem"):
            raise IncidentError("Keine Nachbetrachtung vorhanden.")
        cards = _cards()
        for a in i["postmortem"].get("actions", []):
            if a.get("done") or (a.get("card") and a["card"] in cards):
                continue
            desc = f"Maßnahme aus der Nachbetrachtung der Störung „{i['title']}“."
            if a.get("owner"):
                desc += f"\nVerantwortlich: {a['owner']}"
            try:
                board = kanban.create({"title": a["text"][:120], "desc": desc, "labels": ["Post-Mortem"], "due": a.get("due", "")}, user)
            except kanban.KanbanError as e:
                raise IncidentError(str(e))
            new = [c for c in board["cards"] if c["id"] not in cards]
            if new:
                a["card"] = new[0]["id"]
                cards[new[0]["id"]] = new[0]
        settings_store.set(KEY, items)
    return _view(i)


def overdue_actions() -> List[Dict[str, Any]]:
    out = []
    cards = _cards()
    for i in _items():
        pm = _pm_view(i.get("postmortem"), cards)
        for a in (pm or {}).get("actions", []):
            if a["overdue"]:
                out.append({"level": "warning", "text": f"Maßnahme überfällig: {a['text']} ({i['title']})"})
    return out


def markdown(incident_id: str) -> str:
    i = next((x for x in _items() if x["id"] == incident_id), None)
    if not i:
        raise IncidentError("Störung nicht gefunden.")
    v = _view(i)
    pm = v["postmortem"] or {}
    fmt = lambda ts: time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))  # noqa: E731
    lines = [f"# Nachbetrachtung: {i['title']}", "",
             f"- Schwere: {v['severity_label']}", f"- Beginn: {fmt(i['started'])}"]
    if i.get("resolved"):
        lines.append(f"- Behoben: {fmt(i['resolved'])} ({_dur(i['resolved'] - i['started'])})")
    if i.get("services"):
        lines.append(f"- Betroffen: {', '.join(i['services'])}")
    for key, title in (("summary", "Zusammenfassung"), ("impact", "Auswirkung"), ("cause", "Ursache"),
                       ("went_well", "Was gut lief"), ("went_badly", "Was schlecht lief"), ("lessons", "Erkenntnisse")):
        if pm.get(key):
            lines += ["", f"## {title}", "", pm[key]]
    lines += ["", "## Ablauf", ""] + [f"- {fmt(u['ts'])} – {STATUSES.get(u['status'], u['status'])}: {u['text']}" for u in i.get("updates", [])]
    if pm.get("actions"):
        lines += ["", "## Maßnahmen", ""]
        for a in pm["actions"]:
            extra = ", ".join(filter(None, [a.get("owner"), f"bis {a['due']}" if a.get("due") else ""]))
            lines.append(f"- [{'x' if a['done'] else ' '}] {a['text']}{f' ({extra})' if extra else ''}")
    return "\n".join(lines) + "\n"


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
