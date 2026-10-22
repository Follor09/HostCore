"""
Recurring reminders ("jeden 1. im Monat: Backup prüfen"). Due reminders show on their
card and in the status bar until they're ticked off; a repeating one then moves on to
its next date. Stored in settings.json, shared by every device.
"""

import calendar
import datetime
import secrets
import threading
import time
from typing import Any, Dict, List

import settings_store

KEY = "reminders"
REPEATS = {"none": "Einmalig", "daily": "Täglich", "weekly": "Wöchentlich", "monthly": "Monatlich", "yearly": "Jährlich"}
MAX_ITEMS = 100

_lock = threading.Lock()


class ReminderError(Exception):
    pass


def _today() -> datetime.date:
    return datetime.date.today()


def _load() -> Dict[str, Any]:
    data = settings_store.get(KEY, {}) or {}
    return {"items": list(data.get("items", [])), "done_total": int(data.get("done_total", 0)),
            "on_time": int(data.get("on_time", 0))}


def _save(data: Dict[str, Any]) -> Dict[str, Any]:
    settings_store.set(KEY, data)
    return overview(data)


def _add_months(d: datetime.date, months: int, day: int) -> datetime.date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return datetime.date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _next(item: Dict[str, Any], due: datetime.date) -> datetime.date:
    every = max(1, int(item.get("every", 1)))
    rep = item["repeat"]
    anchor = int(item.get("day") or due.day)      # "every 31st" stays the last day in short months
    if rep == "daily":
        return due + datetime.timedelta(days=every)
    if rep == "weekly":
        return due + datetime.timedelta(weeks=every)
    if rep == "monthly":
        return _add_months(due, every, anchor)
    return _add_months(due, 12 * every, anchor)    # yearly


def overview(data: Dict[str, Any] = None) -> Dict[str, Any]:
    data = data or _load()
    today = _today()
    items = []
    for it in data["items"]:
        due = datetime.date.fromisoformat(it["due"])
        days = (due - today).days
        state = "overdue" if days < 0 else "today" if days == 0 else "soon" if days <= 7 else "later"
        items.append({**it, "days": days, "state": state, "repeat_label": REPEATS.get(it["repeat"], "")})
    items.sort(key=lambda i: (i["due"], i["title"].lower()))
    return {"items": items, "due": sum(1 for i in items if i["days"] <= 0),
            "done_total": data["done_total"], "on_time": data["on_time"], "repeats": REPEATS}


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    title = str(fields.get("title") or "").strip()
    if not title or len(title) > 100:
        raise ReminderError("Titel: 1–100 Zeichen.")
    try:
        due = datetime.date.fromisoformat(str(fields.get("due") or ""))
    except ValueError:
        raise ReminderError("Ungültiges Datum.")
    repeat = str(fields.get("repeat") or "none")
    if repeat not in REPEATS:
        raise ReminderError("Unbekannte Wiederholung.")
    try:
        every = int(fields.get("every") or 1)
    except (TypeError, ValueError):
        every = 0
    if not 1 <= every <= 365:
        raise ReminderError("Intervall: 1–365.")
    return {"title": title, "due": due.isoformat(), "repeat": repeat, "every": every, "day": due.day}


def create(fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    with _lock:
        data = _load()
        if len(data["items"]) >= MAX_ITEMS:
            raise ReminderError(f"Höchstens {MAX_ITEMS} Erinnerungen.")
        data["items"].append({"id": secrets.token_hex(5), **_clean(fields), "created": int(time.time()), "user": user})
        return _save(data)


def _find(data: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    it = next((i for i in data["items"] if i["id"] == item_id), None)
    if not it:
        raise ReminderError("Erinnerung nicht gefunden.")
    return it


def update(item_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        data = _load()
        _find(data, item_id).update(_clean(fields))
        return _save(data)


def done(item_id: str, user: str = "") -> Dict[str, Any]:
    """Tick off: one-off reminders go away, repeating ones move to their next date after today."""
    with _lock:
        data = _load()
        it = _find(data, item_id)
        today = _today()
        due = datetime.date.fromisoformat(it["due"])
        data["done_total"] += 1
        if due >= today:
            data["on_time"] += 1
        if it["repeat"] == "none":
            data["items"].remove(it)
        else:
            nxt = _next(it, due)
            while nxt <= today:
                nxt = _next(it, nxt)
            it.update(due=nxt.isoformat(), last_done=int(time.time()), last_done_by=user)
        return _save(data)


def snooze(item_id: str, days: int = 1) -> Dict[str, Any]:
    with _lock:
        data = _load()
        it = _find(data, item_id)
        it["due"] = (_today() + datetime.timedelta(days=max(1, min(int(days), 30)))).isoformat()
        return _save(data)


def delete(item_id: str) -> Dict[str, Any]:
    with _lock:
        data = _load()
        data["items"] = [i for i in data["items"] if i["id"] != item_id]
        return _save(data)


def due_items() -> List[Dict[str, Any]]:
    return [i for i in overview()["items"] if i["days"] <= 0]
