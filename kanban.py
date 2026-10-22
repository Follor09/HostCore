"""
Task board (kanban): cards in three columns (open, in progress, done) with labels and
a due date. Stored in settings.json, shared by every device, like the notes.
"""

import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "kanban"
COLUMNS = [("idea", "Offen"), ("doing", "In Arbeit"), ("done", "Erledigt")]
COLUMN_IDS = [c[0] for c in COLUMNS]
MAX_CARDS = 300
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_lock = threading.Lock()


class KanbanError(Exception):
    pass


def _load() -> Dict[str, Any]:
    data = settings_store.get(KEY, {}) or {}
    return {"cards": [c for c in data.get("cards", []) if c.get("column") in COLUMN_IDS],
            "done_total": int(data.get("done_total", 0)), "version": int(data.get("version", 0))}


def _save(data: Dict[str, Any]) -> Dict[str, Any]:
    data["version"] = int(time.time() * 1000)
    settings_store.set(KEY, data)
    return board(data)


def _ordered(cards: List[Dict[str, Any]], column: str) -> List[Dict[str, Any]]:
    return sorted((c for c in cards if c["column"] == column), key=lambda c: c.get("pos", 0))


def board(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data or _load()
    today = time.strftime("%Y-%m-%d")
    cards = []
    for col in COLUMN_IDS:
        for c in _ordered(data["cards"], col):
            due = c.get("due") or ""
            state = "" if not due or col == "done" else "overdue" if due < today else "today" if due == today else ""
            cards.append({**c, "due_state": state})
    return {"columns": [{"id": cid, "name": name, "count": sum(1 for c in cards if c["column"] == cid)} for cid, name in COLUMNS],
            "cards": cards, "done_total": data["done_total"], "version": data["version"],
            "due": sum(1 for c in cards if c["due_state"] in ("overdue", "today"))}


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "title" in fields:
        title = str(fields["title"] or "").strip()
        if not title or len(title) > 120:
            raise KanbanError("Titel: 1–120 Zeichen.")
        out["title"] = title
    if "desc" in fields:
        desc = str(fields["desc"] or "").strip()
        if len(desc) > 2000:
            raise KanbanError("Beschreibung: höchstens 2000 Zeichen.")
        out["desc"] = desc
    if "labels" in fields:
        labels = fields["labels"] or []
        if isinstance(labels, str):
            labels = labels.split(",")
        labels = list(dict.fromkeys(str(l).strip()[:20] for l in labels if str(l).strip()))
        if len(labels) > 5:
            raise KanbanError("Höchstens 5 Labels.")
        out["labels"] = labels
    if "due" in fields:
        due = str(fields["due"] or "").strip()
        if due and not DATE_RE.match(due):
            raise KanbanError("Ungültiges Datum.")
        out["due"] = due
    return out


def _place(data: Dict[str, Any], card: Dict[str, Any], column: str, index: Optional[int]) -> None:
    """Put the card into column at index (end if None) and renumber that column."""
    if column not in COLUMN_IDS:
        raise KanbanError("Unbekannte Spalte.")
    if column == "done" and card.get("column") != "done":
        card["done_at"] = int(time.time())
        if not card.get("counted"):
            card["counted"] = True
            data["done_total"] += 1
    elif column != "done":
        card.pop("done_at", None)
    card["column"] = column
    others = [c for c in _ordered(data["cards"], column) if c["id"] != card["id"]]
    index = len(others) if index is None else max(0, min(int(index), len(others)))
    others.insert(index, card)
    for i, c in enumerate(others):
        c["pos"] = i


def create(fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    with _lock:
        data = _load()
        if len(data["cards"]) >= MAX_CARDS:
            raise KanbanError(f"Höchstens {MAX_CARDS} Karten – erledigte bitte aufräumen.")
        clean = _clean({"title": fields.get("title"), "desc": fields.get("desc", ""),
                        "labels": fields.get("labels", []), "due": fields.get("due", "")})
        card = {"id": secrets.token_hex(5), **clean, "column": "idea", "created": int(time.time()), "user": user}
        data["cards"].append(card)
        _place(data, card, fields.get("column") or "idea", 0 if fields.get("top") else None)
        return _save(data)


def update(card_id: str, fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    with _lock:
        data = _load()
        card = next((c for c in data["cards"] if c["id"] == card_id), None)
        if not card:
            raise KanbanError("Karte nicht gefunden – vielleicht schon gelöscht.")
        card.update(_clean(fields))
        card["updated"] = int(time.time())
        card["updated_by"] = user
        if "column" in fields or "index" in fields:
            _place(data, card, fields.get("column") or card["column"], fields.get("index"))
        return _save(data)


def delete(card_id: str) -> Dict[str, Any]:
    with _lock:
        data = _load()
        data["cards"] = [c for c in data["cards"] if c["id"] != card_id]
        return _save(data)


def clear_done() -> Dict[str, Any]:
    with _lock:
        data = _load()
        data["cards"] = [c for c in data["cards"] if c["column"] != "done"]
        return _save(data)
