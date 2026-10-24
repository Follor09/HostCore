"""
Version history for documentation and knowledge base (Dokumentation, Wissen): every change of an
inventory item, contract, command, journal entry, runbook, service doc or wiki page is kept as a
version - who changed it when, a field-by-field comparison, and "restore" (also for deleted items).

app.py takes a snapshot of the affected collection before and after each changing request
(track_before / track_after), so the modules themselves don't need to know about versions.
Stored in history.db (table doc_versions); kept per retention "doc_versions" (days) and at most
MAX_PER_ITEM versions per item.
"""

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import history
import settings_store

MAX_PER_ITEM = 100
IGNORE_FIELDS = {"runs", "versions", "updated", "updated_by", "edited", "edited_by", "last_done", "running", "present"}
# kind -> (label, settings key or "", field with the item's name)
KINDS: Dict[str, Tuple[str, str, str]] = {
    "inventory": ("Inventar", "inventory", "name"),
    "contracts": ("Verträge & Abos", "contracts", "name"),
    "snippets": ("Befehle", "snippets", "title"),
    "journal": ("Betriebstagebuch", "journal", "title"),
    "runbooks": ("Runbooks", "runbooks", "title"),
    "services": ("Steckbriefe", "service_docs", "name"),
    "wiki": ("Wissen", "", "title"),
}
# Request paths whose changes are tracked: path regex -> kind
PATHS = [(re.compile(r"^/api/docs/(inventory|contracts|snippets|journal|runbooks|services)(/[^/]+)?(/delete)?$"), None),
         (re.compile(r"^/api/wiki(/[^/]+)?(/delete)?$"), "wiki")]
_lock = threading.Lock()


class VersionError(Exception):
    pass


def _conn():
    conn = history._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS doc_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, item_id TEXT, title TEXT, ts INTEGER,
        user TEXT, action TEXT, data TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS doc_versions_item ON doc_versions (kind, item_id, id)")
    return conn


def kind_for(path: str) -> Optional[str]:
    for rx, kind in PATHS:
        m = rx.match(path)
        if m:
            return kind or m.group(1)
    return None


# ---------------------------------------------------------------------------
# Current state of a collection
# ---------------------------------------------------------------------------
def _clean(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if k not in IGNORE_FIELDS}


def collection(kind: str) -> Dict[str, Dict[str, Any]]:
    """{item id: data without volatile fields}"""
    if kind == "wiki":
        import wiki
        return {p["id"]: _clean(p) for p in wiki._load()["pages"]}
    key = KINDS[kind][1]
    if kind == "services":
        return {t: _clean({"target": t, **d}) for t, d in (settings_store.get(key, {}) or {}).items()}
    return {i["id"]: _clean(i) for i in settings_store.get(key, []) or [] if isinstance(i, dict) and i.get("id")}


def _title(kind: str, data: Dict[str, Any]) -> str:
    return str(data.get(KINDS[kind][2]) or data.get("title") or data.get("name") or "")[:120]


def track_before(path: str) -> Optional[Tuple[str, Dict[str, Dict[str, Any]]]]:
    kind = kind_for(path)
    return (kind, collection(kind)) if kind else None


def track_after(before: Tuple[str, Dict[str, Dict[str, Any]]], user: str, action: str = "") -> None:
    """action: label for created/changed items instead of create/update (e.g. "restore")."""
    kind, old = before
    new = collection(kind)
    now = int(time.time())
    rows = []
    for item_id, data in new.items():
        if item_id not in old:
            rows.append((kind, item_id, _title(kind, data), now, user, action or "create", json.dumps(data, ensure_ascii=False)))
        elif old[item_id] != data:
            rows.append((kind, item_id, _title(kind, data), now, user, action or "update", json.dumps(data, ensure_ascii=False)))
    for item_id, data in old.items():
        if item_id not in new:
            rows.append((kind, item_id, _title(kind, data), now, user, "delete", json.dumps(data, ensure_ascii=False)))
    if not rows:
        return
    with _lock, _conn() as conn:
        for row in rows:
            # The first version of an item that existed before the history: keep its old state too
            if row[1] in old and row[5] in ("update", "delete", "restore") and not conn.execute(
                    "SELECT 1 FROM doc_versions WHERE kind = ? AND item_id = ? LIMIT 1", (kind, row[1])).fetchone():
                base = old[row[1]]
                conn.execute("INSERT INTO doc_versions (kind, item_id, title, ts, user, action, data) VALUES (?,?,?,?,?,?,?)",
                             (kind, row[1], _title(kind, base), int(base.get("created") or base.get("ts") or now - 1),
                              base.get("user", ""), "original", json.dumps(base, ensure_ascii=False)))
            conn.execute("INSERT INTO doc_versions (kind, item_id, title, ts, user, action, data) VALUES (?,?,?,?,?,?,?)", row)
            conn.execute("""DELETE FROM doc_versions WHERE kind = ? AND item_id = ? AND id NOT IN (
                SELECT id FROM doc_versions WHERE kind = ? AND item_id = ? ORDER BY id DESC LIMIT ?)""",
                         (kind, row[1], kind, row[1], MAX_PER_ITEM))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def item_versions(kind: str, item_id: str) -> Dict[str, Any]:
    if kind not in KINDS:
        raise VersionError("Unbekannte Art.")
    with _conn() as conn:
        rows = conn.execute("SELECT id, ts, user, action, title, data FROM doc_versions WHERE kind = ? AND item_id = ? ORDER BY id DESC",
                            (kind, item_id)).fetchall()
    current = collection(kind).get(item_id)
    return {"kind": kind, "label": KINDS[kind][0], "item_id": item_id, "current": current,
            "versions": [{"id": r[0], "ts": r[1], "user": r[2], "action": r[3], "title": r[4], "data": json.loads(r[5])} for r in rows]}


def deleted(kind: str = "") -> List[Dict[str, Any]]:
    """Items whose newest version is a deletion and that don't exist any more."""
    kinds = [kind] if kind else list(KINDS)
    out = []
    with _conn() as conn:
        for k in kinds:
            if k not in KINDS:
                continue
            existing = collection(k)
            rows = conn.execute("""SELECT v.id, v.item_id, v.title, v.ts, v.user FROM doc_versions v
                JOIN (SELECT item_id, MAX(id) AS mid FROM doc_versions WHERE kind = ? GROUP BY item_id) last ON v.id = last.mid
                WHERE v.kind = ? AND v.action = 'delete' ORDER BY v.ts DESC LIMIT 100""", (k, k)).fetchall()
            out += [{"kind": k, "label": KINDS[k][0], "version": r[0], "item_id": r[1], "title": r[2], "ts": r[3], "user": r[4]}
                    for r in rows if r[1] not in existing]
    return sorted(out, key=lambda x: -x["ts"])


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT id, kind, item_id, title, ts, user, action FROM doc_versions WHERE action != 'original' ORDER BY id DESC LIMIT ?",
                            (max(1, min(limit, 200)),)).fetchall()
    return [{"id": r[0], "kind": r[1], "label": KINDS.get(r[1], (r[1],))[0], "item_id": r[2], "title": r[3], "ts": r[4],
             "user": r[5], "action": r[6]} for r in rows]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _write(kind: str, item_id: str, data: Dict[str, Any]) -> None:
    now = int(time.time())
    if kind == "wiki":
        import wiki
        with wiki._lock:
            store = wiki._load()
            page = next((p for p in store["pages"] if p["id"] == item_id), None)
            fields = {k: data.get(k, "") for k in ("title", "folder", "content")}
            if page:
                page.setdefault("versions", []).append({"ts": page.get("updated", now), "user": page.get("user", ""),
                                                        "title": page["title"], "content": page.get("content", "")})
                page.update(fields, updated=now)
            else:
                store["pages"].append({**data, "id": item_id, "updated": now, "versions": []})
            wiki._save(store)
        return
    key = KINDS[kind][1]
    if kind == "services":
        docs = dict(settings_store.get(key, {}) or {})
        docs[item_id] = {**{k: v for k, v in data.items() if k != "target"}, "updated": now}
        settings_store.set(key, docs)
        return
    import docs as docs_mod
    with docs_mod._lock:
        items = list(settings_store.get(key, []) or [])
        old = next((i for i in items if i.get("id") == item_id), None)
        if old:
            # keep what is not part of the version (runbook runs ...)
            keep = {k: v for k, v in old.items() if k in IGNORE_FIELDS}
            old.clear()
            old.update({**data, **keep, "id": item_id})
        else:
            items.append({**data, "id": item_id, **({"runs": []} if kind == "runbooks" else {})})
        settings_store.set(key, items)


def restore(version_id: int, user: str) -> Dict[str, Any]:
    with _conn() as conn:
        r = conn.execute("SELECT kind, item_id, data FROM doc_versions WHERE id = ?", (int(version_id),)).fetchone()
    if not r:
        raise VersionError("Version nicht gefunden.")
    kind, item_id, data = r[0], r[1], json.loads(r[2])
    before = (kind, collection(kind))
    _write(kind, item_id, data)
    track_after(before, user, "restore")
    return item_versions(kind, item_id)


def cleanup() -> None:
    import retention
    with _lock, _conn() as conn:
        cutoff = int(time.time()) - retention.seconds("doc_versions")
        # never the newest version of an item (that is what a deleted item is restored from)
        conn.execute("""DELETE FROM doc_versions WHERE ts < ? AND id NOT IN (
            SELECT MAX(id) FROM doc_versions GROUP BY kind, item_id)""", (cutoff,))
