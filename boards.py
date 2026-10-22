"""
Boards for the start page (Homarr-style): a free grid of app tiles, widgets and the
dashboard's own cards, grouped into sections (categories), several boards per dashboard.

- config in settings.json under "boards" ({"rev", "boards": [...]}), shared by every device;
  every save is fully validated and carries the revision it was based on, so two browsers
  never silently overwrite each other
- app status: the Pi requests each app's address (or a separate ping address) and caches it
- icons: the dashboard-icons collection (homarr-labs, via jsDelivr) is searched and proxied
  through the Pi with a disk cache, so browsers never talk to the CDN and icons keep working offline
"""

import asyncio
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

import settings_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "icon_cache")
ICON_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
TREE_MAX_AGE = 7 * 86400
ICON_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}\.(svg|png|webp)$")
URL_RE = re.compile(r"^https?://[^\s<>\"'`]+$", re.I)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")

# Cards of the dashboard (data-widget in index.html) that can be placed on a board
CARDS = ["clock", "weather", "services", "cpu", "temp", "ram", "disk", "network", "fan", "power", "updates", "wol",
         "notes", "events", "actions", "health", "reminders", "kanban", "pi3d", "history", "workloads", "storage",
         "processes"]
ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q=%s",
    "google": "https://www.google.com/search?q=%s",
    "startpage": "https://www.startpage.com/do/search?q=%s",
    "bing": "https://www.bing.com/search?q=%s",
    "ecosia": "https://www.ecosia.org/search?q=%s",
}
MAX_BOARDS, MAX_SECTIONS, MAX_ITEMS = 20, 20, 300
MIN_COLUMNS, MAX_COLUMNS = 4, 24

_lock = threading.Lock()
_ping_cache: Dict[str, Dict[str, Any]] = {}
PING_MAX_AGE = 50
_tree: Dict[str, Any] = {"ts": 0, "names": None}


class BoardError(Exception):
    pass


class BoardConflict(BoardError):
    pass


# ---------------------------------------------------------------------------
# Default board (first start / reset): the familiar dashboard plus the own links as apps
# ---------------------------------------------------------------------------
DEFAULT_CARDS = [
    ("clock", 0, 0, 4, 6), ("weather", 4, 0, 4, 6), ("services", 8, 0, 4, 6),
    ("cpu", 0, 6, 3, 7), ("temp", 3, 6, 3, 7), ("ram", 6, 6, 3, 7), ("disk", 9, 6, 3, 7),
    ("network", 0, 13, 3, 6), ("fan", 3, 13, 3, 6), ("power", 6, 13, 3, 6), ("updates", 9, 13, 3, 6),
    ("wol", 0, 19, 3, 5), ("notes", 3, 19, 3, 5), ("events", 6, 19, 3, 5), ("actions", 9, 19, 3, 5),
    ("health", 0, 24, 3, 6), ("reminders", 3, 24, 3, 6), ("kanban", 6, 24, 3, 6), ("pi3d", 9, 24, 3, 6),
    ("history", 0, 30, 12, 8), ("workloads", 0, 38, 12, 5), ("storage", 0, 43, 6, 14), ("processes", 6, 43, 6, 14),
]


def _new_id() -> str:
    return secrets.token_hex(5)


def default_config() -> Dict[str, Any]:
    """Search and the own links as apps on top, the dashboard's cards in the category "System"."""
    top: List[Dict[str, Any]] = [{"id": _new_id(), "kind": "search", "engine": "duckduckgo", "apps": True,
                                  "custom_url": "", "x": 0, "y": 0, "w": 12, "h": 1}]
    for i, link in enumerate((settings_store.get("custom_links", []) or [])[:24]):
        top.append({"id": _new_id(), "kind": "app", "name": link.get("name", "")[:40], "url": link.get("url", ""),
                    "icon": "", "description": "", "ping": True, "ping_url": "", "new_tab": True,
                    "x": i % 12, "y": 1 + (i // 12) * 2, "w": 1, "h": 2})
    cards = [{"id": _new_id(), "kind": "card", "card": card, "x": x, "y": y, "w": w, "h": h}
             for card, x, y, w, h in DEFAULT_CARDS]
    board = {"id": "start", "name": "", "columns": 12, "background": {"url": "", "dim": 40},
             "sections": [{"id": _new_id(), "name": "", "collapsed": False, "items": top},
                          {"id": _new_id(), "name": "System", "collapsed": False, "items": cards}]}
    return {"rev": 1, "boards": [board]}


def get() -> Dict[str, Any]:
    cfg = settings_store.get("boards")
    if not isinstance(cfg, dict) or not cfg.get("boards"):
        with _lock:
            cfg = settings_store.get("boards")
            if not isinstance(cfg, dict) or not cfg.get("boards"):
                cfg = default_config()
                cfg["icons_pending"] = True
                settings_store.set("boards", cfg)
    return cfg


_icon_fill = {"last": 0.0, "task": None}


async def ensure() -> Dict[str, Any]:
    """get(), and once after creating the default board: icons for the apps taken over from the own links
    (in the background, so a Pi without internet never delays the page; retried every 10 minutes)."""
    cfg = get()
    task = _icon_fill["task"]
    if cfg.get("icons_pending") and (task is None or task.done()) and time.time() - _icon_fill["last"] > 600:
        _icon_fill["last"] = time.time()
        _icon_fill["task"] = asyncio.create_task(_fill_icons())
    return cfg


async def _fill_icons() -> None:
    try:
        names = await _names()
    except BoardError:
        return
    with _lock:
        cfg = settings_store.get("boards") or {}
        for b in cfg.get("boards", []):
            for s in b.get("sections", []):
                for it in s.get("items", []):
                    if it.get("kind") == "app" and not it.get("icon"):
                        slug = re.sub(r"[^a-z0-9]+", "-", it.get("name", "").lower()).strip("-")
                        it["icon"] = names.get(slug) or names.get(slug.replace("-", "")) or ""
        cfg.pop("icons_pending", None)
        cfg["rev"] = int(cfg.get("rev", 0)) + 1  # open browsers reload instead of overwriting the icons
        settings_store.set("boards", cfg)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _text(v: Any, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(v or "")).strip()[:n]


def _url(v: Any, what: str, required: bool = False) -> str:
    u = _text(v, 500)
    if not u:
        if required:
            raise BoardError(f"{what}: Adresse fehlt.")
        return ""
    if not URL_RE.match(u):
        if re.match(r"^[\w.-]+(:\d+)?(/[^\s<>\"'`]*)?$", u):
            u = "http://" + u
        else:
            raise BoardError(f"{what}: Adresse muss mit http:// oder https:// beginnen.")
    return u


def _int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _item(raw: Dict[str, Any], columns: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise BoardError("Ungültiges Element.")
    item_id = str(raw.get("id") or "")
    if not ITEM_ID_RE.match(item_id):
        item_id = _new_id()
    w = _int(raw.get("w"), 1, columns, 2)
    item = {"id": item_id, "x": _int(raw.get("x"), 0, columns - w, 0), "y": _int(raw.get("y"), 0, 2000, 0),
            "w": w, "h": _int(raw.get("h"), 1, 40, 2)}
    kind = raw.get("kind")
    if kind == "card":
        if raw.get("card") not in CARDS:
            raise BoardError("Unbekannte Karte.")
        item.update(kind="card", card=raw["card"])
    elif kind == "app":
        name = _text(raw.get("name"), 40)
        if not name:
            raise BoardError("App: Name fehlt.")
        icon = _text(raw.get("icon"), 500)
        if icon and not (ICON_FILE_RE.match(icon) or URL_RE.match(icon)):
            raise BoardError(f"{name}: Ungültiges Icon.")
        item.update(kind="app", name=name, url=_url(raw.get("url"), name, True), icon=icon,
                    description=_text(raw.get("description"), 120), ping=bool(raw.get("ping", True)),
                    ping_url=_url(raw.get("ping_url"), f"{name} (Status-Adresse)"), new_tab=bool(raw.get("new_tab", True)))
    elif kind == "search":
        engine = raw.get("engine") or "duckduckgo"
        custom = ""
        if engine == "custom":
            custom = _url(raw.get("custom_url"), "Suche", True)
            if "%s" not in custom:
                raise BoardError("Suche: Die eigene Adresse braucht %s als Platzhalter für den Suchbegriff.")
        elif engine not in ENGINES:
            raise BoardError("Suche: Unbekannte Suchmaschine.")
        item.update(kind="search", engine=engine, custom_url=custom, apps=bool(raw.get("apps", True)))
    elif kind == "iframe":
        item.update(kind="iframe", title=_text(raw.get("title"), 40), url=_url(raw.get("url"), "Webseite", True))
    else:
        raise BoardError("Unbekannte Art von Element.")
    return item


def _board(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise BoardError("Ungültiges Board.")
    board_id = str(raw.get("id") or "")
    if not ID_RE.match(board_id):
        raise BoardError("Ungültige Board-Kennung.")
    columns = _int(raw.get("columns"), MIN_COLUMNS, MAX_COLUMNS, 12)
    bg = raw.get("background") if isinstance(raw.get("background"), dict) else {}
    sections_raw = raw.get("sections") if isinstance(raw.get("sections"), list) else []
    if not sections_raw:
        sections_raw = [{"name": "", "items": []}]
    if len(sections_raw) > MAX_SECTIONS:
        raise BoardError(f"Höchstens {MAX_SECTIONS} Kategorien pro Board.")
    sections, count, seen = [], 0, set()
    for i, s in enumerate(sections_raw):
        if not isinstance(s, dict):
            raise BoardError("Ungültige Kategorie.")
        items = []
        for it in (s.get("items") if isinstance(s.get("items"), list) else []):
            item = _item(it, columns)
            if item["id"] in seen:
                item["id"] = _new_id()
            seen.add(item["id"])
            items.append(item)
        count += len(items)
        sid = str(s.get("id") or "")
        sections.append({"id": sid if ITEM_ID_RE.match(sid) else _new_id(),
                         "name": "" if i == 0 else (_text(s.get("name"), 40) or "Kategorie"),
                         "collapsed": bool(s.get("collapsed")) and i > 0, "items": items})
    if count > MAX_ITEMS:
        raise BoardError(f"Höchstens {MAX_ITEMS} Elemente pro Board.")
    return {"id": board_id, "name": _text(raw.get("name"), 40), "columns": columns,
            "background": {"url": _url(bg.get("url"), "Hintergrundbild"), "dim": _int(bg.get("dim"), 0, 90, 40)},
            "sections": sections}


def save(data: Dict[str, Any], base_rev: int) -> Dict[str, Any]:
    boards_raw = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(boards_raw, list) or not boards_raw:
        raise BoardError("Mindestens ein Board wird gebraucht.")
    if len(boards_raw) > MAX_BOARDS:
        raise BoardError(f"Höchstens {MAX_BOARDS} Boards.")
    boards = [_board(b) for b in boards_raw]
    if len({b["id"] for b in boards}) != len(boards):
        raise BoardError("Zwei Boards haben dieselbe Kennung.")
    with _lock:
        cur = get()
        if int(base_rev) != int(cur.get("rev", 0)):
            raise BoardConflict("Das Board wurde inzwischen auf einem anderen Gerät geändert.")
        cfg = {"rev": int(cur.get("rev", 0)) + 1, "boards": boards}
        if cur.get("icons_pending"):
            cfg["icons_pending"] = True
        settings_store.set("boards", cfg)
    return cfg


def reset() -> Dict[str, Any]:
    with _lock:
        rev = int((settings_store.get("boards") or {}).get("rev", 0) or 0)
        cfg = default_config()
        cfg["rev"] = rev + 1
        settings_store.set("boards", cfg)
    return cfg


# ---------------------------------------------------------------------------
# App status
# ---------------------------------------------------------------------------
async def _ping_one(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    hit = _ping_cache.get(url)
    if hit and time.time() - hit["ts"] < PING_MAX_AGE:
        return hit
    t0 = time.time()
    try:
        res = await client.get(url)
        result = {"ok": res.status_code < 500, "code": res.status_code, "ms": round((time.time() - t0) * 1000)}
    except httpx.HTTPError as e:
        result = {"ok": False, "code": None, "ms": None, "error": type(e).__name__}
    result["ts"] = time.time()
    _ping_cache[url] = result
    return result


async def status(board_id: str) -> Dict[str, Any]:
    board = next((b for b in get()["boards"] if b["id"] == board_id), None)
    if not board:
        raise BoardError("Board nicht gefunden.")
    apps = [it for s in board["sections"] for it in s["items"] if it.get("kind") == "app" and it.get("ping")]
    if not apps:
        return {"apps": {}}
    # Home services often use their own certificates: reachability counts, not the certificate
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0), verify=False, follow_redirects=True,
                                 headers={"User-Agent": "PiCore-Dashboard"}) as client:
        urls = {it["id"]: it.get("ping_url") or it["url"] for it in apps}
        unique = list(dict.fromkeys(urls.values()))
        results = await asyncio.gather(*(_ping_one(client, u) for u in unique))
    by_url = dict(zip(unique, results))
    return {"apps": {i: {k: v for k, v in by_url[u].items() if k != "ts"} for i, u in urls.items()}}


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------
def _tree_path() -> str:
    return os.path.join(ICON_DIR, "tree.json")


async def _names() -> Dict[str, str]:
    """icon name -> best file (svg before webp before png)."""
    if _tree["names"] is not None and time.time() - _tree["ts"] < TREE_MAX_AGE:
        return _tree["names"]
    path = _tree_path()
    data = None
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < TREE_MAX_AGE:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    if data is None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                res = await client.get(f"{ICON_CDN}/tree.json")
                res.raise_for_status()
                data = res.json()
            os.makedirs(ICON_DIR, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(path + ".tmp", path)
        except (httpx.HTTPError, ValueError, OSError):
            if os.path.exists(path):  # offline: an old list is better than none
                with open(path) as f:
                    data = json.load(f)
            else:
                raise BoardError("Icon-Liste nicht erreichbar – der Pi braucht dafür einmal Internet.")
    names: Dict[str, str] = {}
    for ext in ("png", "webp", "svg"):  # later wins: svg is preferred
        for file in data.get(ext, []) if isinstance(data, dict) else []:
            if isinstance(file, str) and ICON_FILE_RE.match(file):
                names[file.rsplit(".", 1)[0]] = file
    _tree.update(ts=time.time(), names=names)
    return names


async def search_icons(q: str, limit: int = 48) -> List[Dict[str, str]]:
    names = await _names()
    q = re.sub(r"[^a-z0-9]+", "-", (q or "").lower()).strip("-")
    if not q:
        return []
    compact = q.replace("-", "")
    ranked = []
    for name, file in names.items():
        flat = name.replace("-", "")
        if name == q or flat == compact:
            score = 0
        elif name.startswith(q) or flat.startswith(compact):
            score = 1
        elif compact in flat:
            score = 2
        else:
            continue
        # Variants (-dark, -light) after the plain icon
        ranked.append((score, name.endswith(("-dark", "-light")), len(name), name, file))
    ranked.sort()
    return [{"name": n, "file": f} for _, _, _, n, f in ranked[:limit]]


async def icon(file: str) -> Optional[str]:
    """Path of the cached icon file (downloaded on first use)."""
    if not ICON_FILE_RE.match(file):
        return None
    path = os.path.join(ICON_DIR, file)
    if os.path.exists(path):
        return path
    ext = file.rsplit(".", 1)[1]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            res = await client.get(f"{ICON_CDN}/{ext}/{file}")
    except httpx.HTTPError:
        return None
    if res.status_code != 200 or len(res.content) > 2_000_000:
        return None
    os.makedirs(ICON_DIR, exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(res.content)
    os.replace(path + ".tmp", path)
    return path
