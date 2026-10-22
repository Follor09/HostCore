"""
Knowledge base (page /wissen): free Markdown pages in folders for everything that doesn't fit a
runbook or service doc. Stored in wiki.json next to the dashboard (not settings.json - pages can be
long); every save keeps the previous version (last 20 per page).
"""

import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIKI_FILE = os.path.join(BASE_DIR, "wiki.json")
MAX_PAGES = 1000
MAX_CONTENT = 100_000
MAX_VERSIONS = 20
_lock = threading.Lock()


class WikiError(Exception):
    pass


def _load() -> Dict[str, Any]:
    try:
        with open(WIKI_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("pages"), list) else {"pages": []}
    except (OSError, ValueError):
        return {"pages": []}


def _save(data: Dict[str, Any]) -> None:
    with open(WIKI_FILE + ".tmp", "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(WIKI_FILE + ".tmp", WIKI_FILE)


def _summary(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: p.get(k) for k in ("id", "title", "folder", "created", "updated", "user")} | {"size": len(p.get("content", ""))}


def pages() -> Dict[str, Any]:
    items = sorted(_load()["pages"], key=lambda p: ((p.get("folder") or "").lower(), p["title"].lower()))
    return {"pages": [_summary(p) for p in items], "folders": sorted({p.get("folder") or "" for p in items} - {""}, key=str.lower)}


def page(page_id: str) -> Dict[str, Any]:
    p = next((x for x in _load()["pages"] if x["id"] == page_id), None)
    if not p:
        raise WikiError("Seite nicht gefunden.")
    return {**{k: v for k, v in p.items() if k != "versions"},
            "versions": [{"ts": v["ts"], "user": v.get("user", ""), "title": v.get("title", ""), "size": len(v.get("content", ""))}
                         for v in reversed(p.get("versions", []))]}


def version(page_id: str, ts: int) -> Dict[str, Any]:
    p = next((x for x in _load()["pages"] if x["id"] == page_id), None)
    v = next((v for v in (p or {}).get("versions", []) if v["ts"] == ts), None)
    if not v:
        raise WikiError("Version nicht gefunden.")
    return v


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    title = re.sub(r"[\x00-\x1f]", "", str(fields.get("title") or "")).strip()[:120]
    if not title:
        raise WikiError("Titel fehlt.")
    folder = "/".join(s.strip() for s in re.sub(r"[\x00-\x1f\\\\]", "", str(fields.get("folder") or "")).split("/") if s.strip())[:120]
    content = str(fields.get("content") or "").replace("\r\n", "\n")
    if len(content) > MAX_CONTENT:
        raise WikiError(f"Höchstens {MAX_CONTENT:,} Zeichen pro Seite.".replace(",", "."))
    return {"title": title, "folder": folder, "content": content}


def save(page_id: Optional[str], fields: Dict[str, Any], user: str = "", base: Optional[int] = None) -> Dict[str, Any]:
    clean = _clean(fields)
    now = int(time.time())
    with _lock:
        data = _load()
        if page_id:
            p = next((x for x in data["pages"] if x["id"] == page_id), None)
            if not p:
                raise WikiError("Seite nicht gefunden.")
            if base is not None and int(base) != int(p.get("updated", 0)):
                raise WikiError("Die Seite wurde inzwischen von jemand anderem geändert – bitte neu laden.")
            if any(p.get(k) != clean[k] for k in ("title", "folder", "content")):
                p.setdefault("versions", []).append({"ts": p.get("updated", now), "user": p.get("user", ""),
                                                      "title": p["title"], "content": p.get("content", "")})
                p["versions"] = p["versions"][-MAX_VERSIONS:]
                p.update(clean, updated=now, user=user or "")
        else:
            if len(data["pages"]) >= MAX_PAGES:
                raise WikiError(f"Höchstens {MAX_PAGES} Seiten.")
            p = {"id": secrets.token_hex(5), **clean, "created": now, "updated": now, "user": user or "", "versions": []}
            data["pages"].append(p)
        _save(data)
    return page(p["id"])


def delete(page_id: str) -> Dict[str, Any]:
    with _lock:
        data = _load()
        data["pages"] = [p for p in data["pages"] if p["id"] != page_id]
        _save(data)
    return pages()


def all_pages() -> List[Dict[str, Any]]:
    """For the full-text search."""
    return _load()["pages"]
