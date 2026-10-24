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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    elif kind == "text":
        text = str(raw.get("text") or "")[:4000]
        item.update(kind="text", text=re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text))
    elif kind == "links":
        links = []
        for link in (raw.get("links") if isinstance(raw.get("links"), list) else [])[:50]:
            if not isinstance(link, dict):
                continue
            url = _url(link.get("url"), "Linkliste", True)
            links.append({"name": _text(link.get("name"), 60) or re.sub(r"^https?://", "", url)[:60], "url": url})
        item.update(kind="links", title=_text(raw.get("title"), 40), links=links)
    elif kind == "rss":
        feeds = [_url(f, "RSS-Feed", True) for f in (raw.get("feeds") if isinstance(raw.get("feeds"), list) else [])[:5] if str(f or "").strip()]
        if not feeds:
            raise BoardError("RSS-Feed: Mindestens eine Feed-Adresse angeben.")
        item.update(kind="rss", title=_text(raw.get("title"), 40), feeds=feeds, limit=_int(raw.get("limit"), 3, 50, 15))
    elif kind == "calendar":
        item.update(kind="calendar", sources=[s for s in ("reminders", "tasks", "maintenance", "contracts")
                                              if s in (raw.get("sources") or ["reminders", "tasks", "maintenance", "contracts"])])
    elif kind == "remote":
        rid = _text(raw.get("remote"), 16)
        if not re.match(r"^[0-9a-f]{1,16}$", rid):
            raise BoardError("Remote-Pi: Bitte einen Pi auswählen.")
        item.update(kind="remote", remote=rid)
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


def save(data: Dict[str, Any], base_rev: int, user: str = "") -> Dict[str, Any]:
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
        _record(cfg, user)
    return cfg


def reset() -> Dict[str, Any]:
    with _lock:
        rev = int((settings_store.get("boards") or {}).get("rev", 0) or 0)
        cfg = default_config()
        cfg["rev"] = rev + 1
        settings_store.set("boards", cfg)
        _record(cfg, "", note="Auf Standard zurückgesetzt")
    return cfg


# ---------------------------------------------------------------------------
# History: every saved state, so an earlier layout can be restored
# ---------------------------------------------------------------------------
HISTORY_FILE = os.path.join(BASE_DIR, "board_history.json")
HISTORY_MERGE_SECONDS = 120   # dragging around saves often: one version per user and two minutes


def _history_max() -> int:
    import retention
    return retention.get("board_versions")


def trim_history() -> None:
    hist = _history()
    if len(hist) > _history_max():
        _write_history(hist[-_history_max():])


def _history() -> List[Dict[str, Any]]:
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _summary(cfg: Dict[str, Any]) -> Dict[str, int]:
    items = [it for b in cfg["boards"] for s in b["sections"] for it in s["items"]]
    return {"boards": len(cfg["boards"]), "sections": sum(len(b["sections"]) for b in cfg["boards"]),
            "apps": sum(1 for it in items if it["kind"] == "app"), "items": len(items)}


def _record(cfg: Dict[str, Any], user: str, note: str = "") -> None:
    hist = _history()
    now = int(time.time())
    entry = {"rev": cfg["rev"], "ts": now, "user": user or "", "note": note, "summary": _summary(cfg),
             "boards": cfg["boards"]}
    if hist and not note and not hist[-1].get("note") and hist[-1]["user"] == entry["user"] \
            and now - hist[-1]["ts"] < HISTORY_MERGE_SECONDS and len(hist) > 1:
        hist[-1] = {**entry, "since": hist[-1].get("since", hist[-1]["ts"])}
    else:
        hist.append(entry)
    _write_history(hist[-_history_max():])


def _write_history(hist: List[Dict[str, Any]]) -> None:
    try:
        with open(HISTORY_FILE + ".tmp", "w") as f:
            json.dump(hist, f, ensure_ascii=False)
        os.replace(HISTORY_FILE + ".tmp", HISTORY_FILE)
    except OSError:
        pass


def history() -> Dict[str, Any]:
    hist = _history()
    if not hist:  # first look at the history: the current state is version one
        cfg = get()
        _record(cfg, "", note="Stand beim ersten Öffnen")
        hist = _history()
    cur = get().get("rev")
    return {"current": cur, "versions": [{k: v for k, v in h.items() if k != "boards"} for h in reversed(hist)]}


def history_version(rev: int) -> Dict[str, Any]:
    h = next((h for h in _history() if h["rev"] == rev), None)
    if not h:
        raise BoardError("Version nicht gefunden.")
    return h


def restore(rev: int, base_rev: int, user: str = "") -> Dict[str, Any]:
    h = history_version(rev)
    with _lock:
        cur = get()
        if int(base_rev) != int(cur.get("rev", 0)):
            raise BoardConflict("Das Board wurde inzwischen auf einem anderen Gerät geändert.")
        cfg = {"rev": int(cur.get("rev", 0)) + 1, "boards": [_board(b) for b in h["boards"]]}
        settings_store.set("boards", cfg)
        when = time.strftime("%d.%m. %H:%M", time.localtime(h["ts"]))
        _record(cfg, user, note=f"Wiederhergestellt: Stand vom {when}")
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


# ---------------------------------------------------------------------------
# RSS / Atom feeds
# ---------------------------------------------------------------------------
_feed_cache: Dict[str, Dict[str, Any]] = {}
FEED_MAX_AGE = 900
FEED_MAX_BYTES = 2_000_000


def _parse_date(text: str) -> int:
    import email.utils
    import datetime as dt
    text = (text or "").strip()
    if not text:
        return 0
    try:
        return int(email.utils.parsedate_to_datetime(text).timestamp())
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _parse_feed(data: bytes, source: str) -> List[Dict[str, Any]]:
    import xml.etree.ElementTree as ET
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data[:65536].lower():
        raise BoardError("Feed mit DOCTYPE/ENTITY wird aus Sicherheitsgründen nicht gelesen.")
    root = ET.fromstring(data)
    strip = lambda tag: tag.split("}", 1)[-1]  # noqa: E731 - namespaces don't matter here
    title = ""
    entries = []
    for el in root.iter():
        tag = strip(el.tag)
        if tag == "title" and not title and el.text:
            title = el.text.strip()
        if tag not in ("item", "entry"):
            continue
        fields: Dict[str, str] = {}
        for child in el:
            ctag = strip(child.tag)
            if ctag == "link":
                href = child.get("href") or (child.text or "").strip()
                if href and (child.get("rel") in (None, "alternate")) and "link" not in fields:
                    fields["link"] = href
            elif ctag in ("title", "pubDate", "published", "updated", "date") and child.text:
                fields.setdefault(ctag, child.text.strip())
        link = fields.get("link", "")
        entries.append({"title": re.sub(r"\s+", " ", fields.get("title", ""))[:200] or "(ohne Titel)",
                        "link": link if URL_RE.match(link) else "",
                        "ts": _parse_date(fields.get("pubDate") or fields.get("published") or fields.get("updated") or fields.get("date", "")),
                        "source": title[:60] or source})
    return entries


async def _feed(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    hit = _feed_cache.get(url)
    if hit and time.time() - hit["ts"] < FEED_MAX_AGE:
        return hit
    try:
        res = await client.get(url)
        res.raise_for_status()
        if len(res.content) > FEED_MAX_BYTES:
            raise BoardError("Feed ist zu groß.")
        result = {"entries": _parse_feed(res.content, re.sub(r"^https?://", "", url).split("/")[0]), "error": ""}
    except (httpx.HTTPError, BoardError, Exception) as e:  # broken XML raises ParseError
        msg = str(e) if isinstance(e, BoardError) else f"{type(e).__name__}"
        result = {"entries": (hit or {}).get("entries", []), "error": f"{url}: {msg}"}
    result["ts"] = time.time()
    _feed_cache[url] = result
    return result


def _item_of(board_id: str, item_id: str, kind: str) -> Dict[str, Any]:
    board = next((b for b in get()["boards"] if b["id"] == board_id), None)
    item = next((it for s in (board or {}).get("sections", []) for it in s["items"] if it["id"] == item_id), None)
    if not item or item.get("kind") != kind:
        raise BoardError("Element nicht gefunden.")
    return item


async def rss(board_id: str, item_id: str) -> Dict[str, Any]:
    item = _item_of(board_id, item_id, "rss")
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True,
                                 headers={"User-Agent": "PiCore-Dashboard (RSS)"}) as client:
        results = await asyncio.gather(*(_feed(client, u) for u in item["feeds"]))
    entries = sorted((e for r in results for e in r["entries"]), key=lambda e: e["ts"], reverse=True)
    return {"entries": entries[:item["limit"]], "errors": [r["error"] for r in results if r["error"]]}


# ---------------------------------------------------------------------------
# Calendar: reminders (with their repeats), task due dates and maintenance windows
# ---------------------------------------------------------------------------
def calendar(start: str, days: int, sources: List[str]) -> Dict[str, Any]:
    import datetime as dt
    import kanban
    import maintenance
    import reminders
    try:
        first = dt.date.fromisoformat(start)
    except ValueError:
        raise BoardError("Ungültiges Datum.")
    days = max(1, min(int(days), 62))
    last = first + dt.timedelta(days=days - 1)
    events: List[Dict[str, Any]] = []
    if "reminders" in sources:
        for it in reminders.overview()["items"]:
            due = dt.date.fromisoformat(it["due"])
            if due < first and it["state"] == "overdue":
                events.append({"date": due.isoformat(), "title": it["title"], "type": "reminder", "overdue": True})
            n = 0
            while due <= last and n < 400:
                if due >= first:
                    events.append({"date": due.isoformat(), "title": it["title"], "type": "reminder",
                                   "overdue": due < dt.date.today(), "repeat": it.get("repeat_label", "")})
                if it["repeat"] == "none":
                    break
                due = reminders._next(it, due)
                n += 1
    if "tasks" in sources:
        for c in kanban.board()["cards"]:
            if c.get("due") and c["column"] != "done" and first.isoformat() <= c["due"] <= last.isoformat():
                events.append({"date": c["due"], "title": c["title"], "type": "task", "overdue": c["due_state"] == "overdue"})
    if "maintenance" in sources:
        for w in maintenance.overview()["windows"]:
            a = dt.datetime.fromtimestamp(w["start"]).date()
            b = dt.datetime.fromtimestamp(max(w["start"], w["end"] - 1)).date()  # ending at midnight: not the next day
            d = max(a, first)
            while d <= min(b, last):
                events.append({"date": d.isoformat(), "title": w["title"], "type": "maintenance", "state": w["state"],
                               "start": w["start"], "end": w["end"]})
                d += dt.timedelta(days=1)
    if "contracts" in sources:
        import docs
        for c in docs.contracts()["items"]:
            if c["state"] == "ended":
                continue
            if c["cancel_by"] and c["notice_days"] and first.isoformat() <= c["cancel_by"] <= last.isoformat():
                events.append({"date": c["cancel_by"], "title": f"Kündigen bis: {c['name']}", "type": "contract",
                               "overdue": c["state"] == "missed"})
            if c["next_end"] and first.isoformat() <= c["next_end"] <= last.isoformat():
                events.append({"date": c["next_end"], "title": f"{'Verlängerung' if c['auto_renew'] else 'Ende'}: {c['name']}",
                               "type": "contract", "overdue": False})
    events.sort(key=lambda e: (e["date"], e["type"], e["title"].lower()))
    return {"start": first.isoformat(), "days": days, "today": dt.date.today().isoformat(), "events": events}


# ---------------------------------------------------------------------------
# Remote Pi widget: a compact snapshot of an agent
# ---------------------------------------------------------------------------
_remote_cache: Dict[str, Dict[str, Any]] = {}


async def remote(remote_id: str) -> Dict[str, Any]:
    import remotes
    r = remotes.get(remote_id)
    if not r:
        raise BoardError("Remote-Pi nicht gefunden – unter Einstellungen → Remote-Pis hinzufügen.")
    hit = _remote_cache.get(remote_id)
    if hit and time.time() - hit["ts"] < 8:
        return hit["data"]
    base = {"id": r["id"], "name": r["name"]}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            res = await client.get(f"{r['url']}/api/system", headers={"Authorization": f"Bearer {r['token']}"})
        res.raise_for_status()
        s = res.json()
        temp = (s.get("temperature") or {}).get("celsius")
        data = {**base, "online": True, "hostname": s.get("host_name", ""), "model": s.get("model", ""),
                "cpu": (s.get("cpu") or {}).get("percent"), "temp": temp,
                "ram": (s.get("memory") or {}).get("percent"), "disk": (s.get("disk") or {}).get("percent"),
                "uptime": (s.get("uptime") or {}).get("formatted", "") if isinstance(s.get("uptime"), dict) else "",
                "throttled": bool((s.get("throttled") or {}).get("any_now")) if isinstance(s.get("throttled"), dict) else False}
    except (httpx.HTTPError, ValueError) as e:
        remotes.mark_offline(r["id"], f"Nicht erreichbar ({type(e).__name__})")
        data = {**base, "online": False, "error": f"Nicht erreichbar ({type(e).__name__})"}
    _remote_cache[remote_id] = {"ts": time.time(), "data": data}
    return data


# ---------------------------------------------------------------------------
# Discovery: apps from Docker / Kubernetes, devices from the LAN scan and NetBox, web ports of a device
# ---------------------------------------------------------------------------
# Order = preference when a container publishes several ports (web UIs first, 8000 is often an API/agent port)
WEB_PORTS = [80, 443, 8080, 8443, 9443, 9000, 8006, 8096, 8123, 3000, 3001, 5000, 5001, 8081, 8088, 8888, 9090,
             8000, 81, 2283, 32400, 7878, 8989, 9696, 8384, 19999, 5055, 8920]
HTTPS_PORTS = {443, 8443, 9443, 8006, 5001, 8920}


def _icon_guess(names: Dict[str, str], *candidates: str) -> str:
    for c in candidates:
        slug = re.sub(r"[^a-z0-9]+", "-", (c or "").lower()).strip("-")
        for s in (slug, slug.replace("-", ""), slug.split("-")[0]):
            if s and s in names:
                return names[s]
    return ""


def _docker_apps(names: Dict[str, str]) -> List[Dict[str, Any]]:
    import containers
    out = []
    for c in containers.docker_list().get("containers", []):
        if c.get("state") != "running":
            continue
        ports = []
        for m in re.finditer(r"(?:0\.0\.0\.0|\[::\]|::|[\d.]+):(\d+)->(\d+)/tcp", c.get("ports", "")):
            host, inner = int(m.group(1)), int(m.group(2))
            if host not in [p[0] for p in ports]:
                ports.append((host, inner))
        if not ports:
            continue
        rank = lambda p: (WEB_PORTS.index(p[1]) if p[1] in WEB_PORTS else 99, p[0])  # noqa: E731
        host, inner = sorted(ports, key=rank)[0]
        image = c.get("image", "").split("@")[0].rsplit(":", 1)[0].split("/")[-1]
        name = c.get("service") or c.get("name", "")
        out.append({"source": "docker", "name": name.replace("-", " ").replace("_", " ").title()[:40],
                    "port": host, "scheme": "https" if inner in HTTPS_PORTS or host in HTTPS_PORTS else "http",
                    "detail": f"{c.get('image', '')}" + (f" · Stack {c['stack']}" if c.get("stack") else ""),
                    "icon": _icon_guess(names, image, c.get("service", ""), c.get("name", ""))})
    return out


def _kube_apps(names: Dict[str, str]) -> List[Dict[str, Any]]:
    import containers
    import homelab
    out: List[Dict[str, Any]] = []
    if not homelab._kube_cmd() or homelab._kube_env() is None:
        return out
    try:
        res = containers._kubectl(["get", "ingress", "-A", "-o", "json"], timeout=15)
        items = json.loads(res.stdout or "{}").get("items", []) if res.returncode == 0 else []
    except Exception:
        items = []
    for ing in items:
        meta, spec = ing.get("metadata", {}), ing.get("spec", {})
        tls_hosts = {h for t in spec.get("tls", []) or [] for h in t.get("hosts", []) or []}
        for rule in spec.get("rules", []) or []:
            host = rule.get("host")
            if not host or "*" in host:
                continue
            path = ((rule.get("http") or {}).get("paths") or [{}])[0].get("path") or "/"
            out.append({"source": "kubernetes", "name": meta.get("name", host).replace("-", " ").title()[:40],
                        "url": f"{'https' if host in tls_hosts else 'http'}://{host}{path if path != '/' else ''}",
                        "detail": f"Ingress {meta.get('namespace', '')}/{meta.get('name', '')}",
                        "icon": _icon_guess(names, meta.get("name", ""), host.split(".")[0])})
    try:
        overview = containers.kube_overview()
    except Exception:
        overview = {}
    for svc in overview.get("services", []) or []:
        for p in svc.get("ports", []):
            m = re.search(r"\(Node (\d+)\)", p)
            if m and p.endswith("/tcp"):
                out.append({"source": "kubernetes", "name": svc["name"].replace("-", " ").title()[:40],
                            "port": int(m.group(1)), "scheme": "http",
                            "detail": f"NodePort {svc['namespace']}/{svc['name']}",
                            "icon": _icon_guess(names, svc["name"])})
                break
    return out


async def discover() -> Dict[str, Any]:
    try:
        names = await _names()
    except BoardError:
        names = {}
    docker_apps, kube_apps = await asyncio.gather(asyncio.to_thread(_docker_apps, names), asyncio.to_thread(_kube_apps, names))
    import netscan
    devices = []
    lan = await asyncio.to_thread(netscan.devices)
    seen_hosts = set()
    for d in lan.get("devices", []):  # online first: a remembered old device never doubles the current one
        if d.get("ip") and not d.get("self") and d["ip"] not in seen_hosts:
            seen_hosts.add(d["ip"])
            devices.append({"source": "lan", "name": d.get("alias") or (d.get("hostname") or "").split(".")[0] or d.get("vendor") or d["ip"],
                            "host": d["ip"], "online": bool(d.get("online")), "detail": d.get("vendor", "")})
    try:
        import netbox
        if netbox.public().get("configured"):
            nb = await netbox.data()
            known = {d["host"] for d in devices}
            for o in nb.get("objects", []):
                if o.get("primary_ip") and o["primary_ip"] not in known:
                    devices.append({"source": "netbox", "name": o["name"], "host": o["primary_ip"],
                                    "online": o.get("lan") == "online", "detail": " · ".join(filter(None, [o.get("role"), o.get("model")]))})
    except Exception:
        pass
    devices.sort(key=lambda d: (not d["online"], d["name"].lower()))
    return {"apps": docker_apps + kube_apps, "devices": devices[:200],
            "docker": bool(docker_apps), "kubernetes": bool(kube_apps)}


def _private_host(host: str) -> bool:
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        a = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (a.is_private or a.is_loopback or a.is_link_local) or a.is_unspecified:
            return False
    return bool(infos)


async def probe(host: str) -> Dict[str, Any]:
    """Which common web ports answer on a device in the home network, with the page title."""
    host = (host or "").strip()
    if not re.match(r"^[A-Za-z0-9.:-]{1,253}$", host):
        raise BoardError("Ungültige Adresse.")
    if not await asyncio.to_thread(_private_host, host):
        raise BoardError("Nur Geräte im Heimnetz oder VPN lassen sich prüfen.")

    async def open_port(port: int) -> bool:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.5)
            w.close()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    flags = await asyncio.gather(*(open_port(p) for p in WEB_PORTS))
    ports = [p for p, ok in zip(WEB_PORTS, flags) if ok]
    shown = f"[{host}]" if ":" in host else host
    found = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0), verify=False, follow_redirects=True) as client:
        async def page(port: int):
            order = ["https", "http"] if port in HTTPS_PORTS else ["http", "https"]
            for scheme in order:
                url = f"{scheme}://{shown}" + ("" if (scheme, port) in (("http", 80), ("https", 443)) else f":{port}")
                try:
                    res = await client.get(url + "/")
                except httpx.HTTPError:
                    continue
                m = re.search(rb"<title[^>]*>([^<]{1,120})</title>", res.content[:65536], re.I)
                title = m.group(1).decode("utf-8", "replace").strip() if m else ""
                return {"url": url, "port": port, "status": res.status_code, "title": re.sub(r"\s+", " ", title)}
            return None
        results = await asyncio.gather(*(page(p) for p in ports))
    found = [r for r in results if r]
    return {"host": host, "found": found}
