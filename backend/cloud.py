"""
Files page ("Dateien", /dateien): a small own cloud on the main dashboard, like Nextcloud.

Spaces (each a folder under the cloud root, default <dashboard>/cloud, can be moved to a big
drive in the settings):
- "me":       users/<user>/ - the user's own files; only they and admins see them
- "shared":   shared/       - one folder for everybody with the cloud permission
- "user:<n>": users/<n>/    - another user's files (admins only)

Paths from the browser are relative to a space; every one is resolved with realpath and must
stay inside its space (no "..", no symlinks leading out). Deleting moves into a recycle bin
per space (.trash/, emptied after trash_days). Share links (/s/c/<token>) hand out one file,
or a folder as ZIP, without login until they expire. Favorites per user. An optional quota
limits each user's own space. The private folders are hidden from the server file explorer
(files.is_denied), so files.read does not reach them.

Settings in settings.json "cloud" (root, quota_mb, trash_days), links in "cloud_shares",
favorites in "cloud_favorites".
"""

import json
import os
import re
import secrets
import shutil
import stat
import time
import unicodedata
from typing import Any, Dict, Iterator, List, Optional, Tuple

import settings_store

KEY = "cloud"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(BASE_DIR, "cloud")
USER_RE = re.compile(r"^[\w.@-]{1,64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
TRASH_ID_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_UPLOAD = 4 * 1024 ** 3
FREE_RESERVE = 200 * 1024 ** 2
MAX_LIST = 5000
WALK_LIMIT = 20000
SHARE_HOURS = (1, 24, 168, 720)
MAX_SHARES = 100
PREVIEW_BYTES = 512 * 1024
KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".ico"},
    "video": {".mp4", ".webm", ".mov", ".m4v", ".ogv"},
    "audio": {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac", ".opus"},
    "pdf": {".pdf"},
    "text": {".txt", ".md", ".log", ".csv", ".json", ".yaml", ".yml", ".conf", ".ini", ".toml", ".xml", ".sh", ".py",
             ".js", ".ts", ".css", ".html", ".sql", ".env.example", ".cfg", ".service", ".svg"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst"},
    "office": {".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".rtf"},
}
# Served inline (sandboxed) for the preview; everything else only as a download. SVG/HTML never inline.
INLINE_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".bmp": "image/bmp", ".avif": "image/avif", ".ico": "image/x-icon", ".pdf": "application/pdf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".m4v": "video/mp4", ".ogv": "video/ogg",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".aac": "audio/aac", ".opus": "audio/ogg",
}
HIDDEN = (".trash", ".cloud-upload-")


class CloudError(Exception):
    pass


class Conflict(CloudError):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return {"root": c.get("root") or "", "quota_mb": int(c.get("quota_mb") or 0), "trash_days": int(c.get("trash_days") or 30)}


def root() -> str:
    r = os.path.realpath(config()["root"] or DEFAULT_ROOT)
    if not os.path.isdir(r):
        os.makedirs(r, mode=0o700, exist_ok=True)
    return r


def private_root() -> str:
    """users/ below the cloud root - for the file explorer's denylist (no makedirs here)."""
    return os.path.join(os.path.realpath(config()["root"] or DEFAULT_ROOT), "users")


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    c = config()
    if "root" in fields:
        new = str(fields.get("root") or "").strip()
        if new:
            if not os.path.isabs(new) or "\x00" in new:
                raise CloudError("Speicherort: bitte einen absoluten Pfad angeben.")
            real = os.path.realpath(new)
            if real in ("/", "/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/bin", "/boot"):
                raise CloudError("Dieser Ordner kann nicht der Speicherort sein.")
            try:
                os.makedirs(real, mode=0o700, exist_ok=True)
            except OSError as e:
                raise CloudError(f"Ordner kann nicht angelegt werden: {e.strerror or e}")
            if not os.access(real, os.W_OK | os.X_OK):
                raise CloudError("Keine Schreibrechte im neuen Speicherort.")
            new = real
        c["root"] = new
    if "quota_mb" in fields:
        try:
            q = int(fields.get("quota_mb") or 0)
        except (TypeError, ValueError):
            raise CloudError("Kontingent: keine Zahl.")
        if not 0 <= q <= 10 * 1024 * 1024:
            raise CloudError("Kontingent: 0 (unbegrenzt) bis 10 TB.")
        c["quota_mb"] = q
    if "trash_days" in fields:
        try:
            d = int(fields.get("trash_days") or 30)
        except (TypeError, ValueError):
            raise CloudError("Papierkorb: keine Zahl.")
        if not 1 <= d <= 365:
            raise CloudError("Papierkorb: 1 bis 365 Tage.")
        c["trash_days"] = d
    settings_store.set(KEY, c)
    return config()


# ---------------------------------------------------------------------------
# Spaces and paths
# ---------------------------------------------------------------------------
def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def space_key(space: str, user: str, admin: bool) -> str:
    """UI space -> internal key: "u/<name>" or "s"."""
    if space == "me":
        if not USER_RE.match(user or ""):
            raise CloudError("Unbekannter Benutzer.")
        return f"u/{user}"
    if space == "shared":
        return "s"
    if space.startswith("user:"):
        name = space[5:]
        if not admin:
            raise CloudError("Nur Administratoren sehen die Dateien anderer Benutzer.")
        if not USER_RE.match(name):
            raise CloudError("Unbekannter Benutzer.")
        return f"u/{name}" if name != user else f"u/{user}"
    raise CloudError("Unbekannter Bereich.")


def key_space(key: str, user: str) -> str:
    """Internal key -> UI space as the given user sees it."""
    if key == "s":
        return "shared"
    name = key[2:]
    return "me" if name == user else f"user:{name}"


def key_dir(key: str, create: bool = True) -> str:
    base = os.path.join(root(), "shared") if key == "s" else os.path.join(root(), "users", key[2:])
    if create and not os.path.isdir(base):
        os.makedirs(base, mode=0o700, exist_ok=True)
    return base


def _clean_rel(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip("/")
    if "\x00" in rel:
        raise CloudError("Ungültiger Pfad.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(HIDDEN) for p in parts):
        raise CloudError("Ungültiger Pfad.")
    return "/".join(parts)


def resolve(key: str, rel: str) -> Tuple[str, str]:
    """(real path, clean relative path) - always inside the space."""
    base = os.path.realpath(key_dir(key))
    rel = _clean_rel(rel)
    real = os.path.realpath(os.path.join(base, rel)) if rel else base
    if not _inside(real, base):
        raise CloudError("Pfad liegt außerhalb des Bereichs.")
    return real, rel


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name or len(name.encode()) > 255:
        raise CloudError("Ungültiger Name.")
    if name.startswith(HIDDEN) or re.search(r"[\x00-\x1f\x7f]", name):
        raise CloudError("Ungültiger Name.")
    return name


def kind_of(name: str) -> str:
    low = name.lower()
    ext = os.path.splitext(low)[1]
    for kind, exts in KINDS.items():
        if ext in exts or (kind == "text" and low.endswith(".env.example")):
            return kind
    return "other"


def _visible(name: str) -> bool:
    return not name.startswith(HIDDEN)


def _entry(real: str, rel: str, favs: set, key: str) -> Optional[Dict[str, Any]]:
    try:
        st = os.stat(real)
    except OSError:
        return None
    is_dir = stat.S_ISDIR(st.st_mode)
    if not is_dir and not stat.S_ISREG(st.st_mode):
        return None
    name = os.path.basename(real) if rel else ""
    return {"name": name, "path": rel, "type": "dir" if is_dir else "file", "size": None if is_dir else st.st_size,
            "mtime": int(st.st_mtime), "kind": "folder" if is_dir else kind_of(name),
            "fav": f"{key}:{rel}" in favs}


def list_dir(key: str, rel: str, user: str) -> Dict[str, Any]:
    real, rel = resolve(key, rel)
    if not os.path.isdir(real):
        raise CloudError("Ordner nicht gefunden.")
    favs = set(favorites_raw(user))
    entries, truncated = [], False
    with os.scandir(real) as it:
        for e in it:
            if not _visible(e.name):
                continue
            if len(entries) >= MAX_LIST:
                truncated = True
                break
            item = _entry(os.path.join(real, e.name), f"{rel}/{e.name}".lstrip("/"), favs, key)
            if item:
                entries.append(item)
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].casefold()))
    return {"path": rel, "entries": entries, "truncated": truncated}


def _walk(base: str, limit: int = WALK_LIMIT) -> Iterator[Tuple[str, str, os.stat_result]]:
    """(real path, relative path, stat) of every visible file below base, without following links."""
    stack, n = [("", base)], 0
    while stack:
        rel_dir, d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not _visible(e.name) or e.is_symlink():
                        continue
                    rel = f"{rel_dir}/{e.name}".lstrip("/")
                    if e.is_dir(follow_symlinks=False):
                        stack.append((rel, e.path))
                    elif e.is_file(follow_symlinks=False):
                        n += 1
                        if n > limit:
                            return
                        yield e.path, rel, e.stat(follow_symlinks=False)
        except OSError:
            continue


def tree_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(st.st_size for _, _, st in _walk(path, 10 ** 9))


def usage(key: str) -> int:
    return tree_size(key_dir(key))


def disk_free() -> int:
    st = os.statvfs(root())
    return st.f_bavail * st.f_frsize


def _check_quota(key: str, extra: int) -> None:
    quota = config()["quota_mb"] * 1024 * 1024
    if key.startswith("u/") and quota and usage(key) + extra > quota:
        raise CloudError(f"Dein Speicher ist voll (Kontingent {config()['quota_mb']} MB) – erst Platz schaffen.")


def keys_for(user: str, admin: bool) -> List[str]:
    """The spaces a user reaches: their own, the shared one, for admins every user's."""
    keys = [f"u/{user}", "s"]
    if admin:
        users_dir = os.path.join(root(), "users")
        if os.path.isdir(users_dir):
            keys += [f"u/{n}" for n in sorted(os.listdir(users_dir)) if USER_RE.match(n) and n != user]
    return keys


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------
def free_name(directory: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(directory, name), 2
    while os.path.lexists(path):
        path = os.path.join(directory, f"{stem} ({n}){ext}")
        n += 1
    return path


def upload_target(key: str, rel_dir: str, name: str, overwrite: bool, size: Optional[int], subdir: str = "") -> str:
    """Where an upload goes. subdir: folder path inside the upload (a dropped folder), created as needed."""
    real_dir, _ = resolve(key, rel_dir)
    if subdir:
        real_dir, _ = resolve(key, f"{rel_dir}/{subdir}")
        os.makedirs(real_dir, mode=0o700, exist_ok=True)
        real_dir, _ = resolve(key, f"{rel_dir}/{subdir}")
    if not os.path.isdir(real_dir):
        raise CloudError("Ordner nicht gefunden.")
    name = clean_name(name)
    target = os.path.join(real_dir, name)
    if os.path.lexists(target):
        if os.path.isdir(target) or os.path.islink(target):
            raise CloudError("Dort gibt es schon einen Ordner mit diesem Namen.")
        if not overwrite:
            raise Conflict(name)
    if size is not None:
        if size > MAX_UPLOAD:
            raise CloudError("Datei zu groß (max. 4 GB).")
        if size > disk_free() - FREE_RESERVE:
            raise CloudError("Nicht genug freier Speicher.")
        _check_quota(key, size)
    return target


def quota_left(key: str) -> Optional[int]:
    quota = config()["quota_mb"] * 1024 * 1024
    if not key.startswith("u/") or not quota:
        return None
    return max(0, quota - usage(key))


def mkdir(key: str, rel_dir: str, name: str) -> Dict[str, Any]:
    real_dir, rel_dir = resolve(key, rel_dir)
    name = clean_name(name)
    target = os.path.join(real_dir, name)
    if os.path.lexists(target):
        raise Conflict(name)
    os.mkdir(target, 0o700)
    return {"path": f"{rel_dir}/{name}".lstrip("/")}


def rename(key: str, rel: str, new_name: str) -> Dict[str, Any]:
    real, rel = resolve(key, rel)
    if not rel:
        raise CloudError("Der Bereich selbst kann nicht umbenannt werden.")
    if not os.path.lexists(real):
        raise CloudError("Nicht gefunden.")
    new_name = clean_name(new_name)
    target = os.path.join(os.path.dirname(real), new_name)
    if os.path.lexists(target) and target != real:
        raise Conflict(new_name)
    os.rename(real, target)
    new_rel = "/".join([*rel.split("/")[:-1], new_name])
    _move_favorites(f"{key}:{rel}", f"{key}:{new_rel}")
    return {"path": new_rel}


def transfer(src_key: str, rels: List[str], dst_key: str, dst_rel: str, mode: str) -> Dict[str, Any]:
    """Move or copy entries into a folder (also between spaces). Names that exist get " (2)"."""
    if mode not in ("move", "copy"):
        raise CloudError("Unbekannter Vorgang.")
    dst_real, dst_rel = resolve(dst_key, dst_rel)
    if not os.path.isdir(dst_real):
        raise CloudError("Zielordner nicht gefunden.")
    sources = []
    for rel in rels[:500]:
        real, rel = resolve(src_key, rel)
        if not rel or not os.path.lexists(real):
            raise CloudError("Nicht gefunden.")
        if os.path.isdir(real) and _inside(dst_real, real):
            raise CloudError("Ein Ordner kann nicht in sich selbst verschoben werden.")
        sources.append((real, rel))
    extra = sum(tree_size(r) for r, _ in sources) if (mode == "copy" or src_key != dst_key) else 0
    if extra:
        _check_quota(dst_key, extra)
    done = []
    for real, rel in sources:
        if mode == "move" and os.path.dirname(real) == dst_real:
            continue
        target = free_name(dst_real, os.path.basename(real))
        if mode == "move":
            shutil.move(real, target)
            _move_favorites(f"{src_key}:{rel}", f"{dst_key}:{dst_rel}/{os.path.basename(target)}".replace(":/", ":"))
        elif os.path.isdir(real):
            shutil.copytree(real, target, symlinks=True)
        else:
            shutil.copy2(real, target)
        done.append(os.path.basename(target))
    return {"count": len(done)}


# ---------------------------------------------------------------------------
# Recycle bin (per space: <root>/.trash/<key with _>/<id>/{item, meta.json})
# ---------------------------------------------------------------------------
def _trash_dir(key: str) -> str:
    return os.path.join(root(), ".trash", key.replace("/", "_"))


def trash_move(key: str, rel: str, by: str) -> Dict[str, Any]:
    real, rel = resolve(key, rel)
    if not rel:
        raise CloudError("Der Bereich selbst kann nicht gelöscht werden.")
    if not os.path.lexists(real):
        raise CloudError("Nicht gefunden.")
    item_id = secrets.token_hex(8)
    box = os.path.join(_trash_dir(key), item_id)
    os.makedirs(box, mode=0o700)
    size = tree_size(real)
    is_dir = os.path.isdir(real)
    shutil.move(real, os.path.join(box, "item"))
    with open(os.path.join(box, "meta.json"), "w") as f:
        json.dump({"name": os.path.basename(real), "path": rel, "deleted": int(time.time()), "by": by,
                   "size": size, "type": "dir" if is_dir else "file"}, f)
    _drop_favorites(f"{key}:{rel}")
    return {"id": item_id}


def _trash_items(key: str) -> List[Dict[str, Any]]:
    d = _trash_dir(key)
    out = []
    if not os.path.isdir(d):
        return out
    for item_id in os.listdir(d):
        if not TRASH_ID_RE.match(item_id):
            continue
        try:
            with open(os.path.join(d, item_id, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"id": item_id, "key": key, **meta})
    return out


def trash_list(keys: List[str], user: str) -> Dict[str, Any]:
    trash_purge()
    days = config()["trash_days"]
    items = []
    for key in keys:
        for it in _trash_items(key):
            items.append({**{k: v for k, v in it.items() if k != "key"}, "space": key_space(key, user),
                          "expires": it["deleted"] + days * 86400})
    items.sort(key=lambda x: -x["deleted"])
    return {"items": items, "days": days, "size": sum(i["size"] for i in items)}


def _trash_box(key: str, item_id: str) -> str:
    if not TRASH_ID_RE.match(item_id or ""):
        raise CloudError("Nicht im Papierkorb.")
    box = os.path.join(_trash_dir(key), item_id)
    if not os.path.isfile(os.path.join(box, "meta.json")):
        raise CloudError("Nicht im Papierkorb.")
    return box


def trash_restore(key: str, item_id: str) -> Dict[str, Any]:
    box = _trash_box(key, item_id)
    with open(os.path.join(box, "meta.json")) as f:
        meta = json.load(f)
    parent_rel = "/".join(meta["path"].split("/")[:-1])
    try:
        parent, parent_rel = resolve(key, parent_rel)
    except CloudError:
        parent, parent_rel = resolve(key, "")
    os.makedirs(parent, mode=0o700, exist_ok=True)
    _check_quota(key, int(meta.get("size") or 0))
    target = free_name(parent, meta["name"])
    shutil.move(os.path.join(box, "item"), target)
    shutil.rmtree(box, ignore_errors=True)
    return {"path": f"{parent_rel}/{os.path.basename(target)}".lstrip("/")}


def trash_delete(key: str, item_id: str) -> None:
    shutil.rmtree(_trash_box(key, item_id), ignore_errors=True)


def trash_empty(keys: List[str]) -> int:
    n = 0
    for key in keys:
        for it in _trash_items(key):
            shutil.rmtree(os.path.join(_trash_dir(key), it["id"]), ignore_errors=True)
            n += 1
    return n


def trash_purge() -> int:
    limit = time.time() - config()["trash_days"] * 86400
    base = os.path.join(root(), ".trash")
    n = 0
    if not os.path.isdir(base):
        return 0
    for d in os.listdir(base):
        key = "s" if d == "s" else d.replace("_", "/", 1)
        for it in _trash_items(key):
            if it["deleted"] < limit:
                shutil.rmtree(os.path.join(_trash_dir(key), it["id"]), ignore_errors=True)
                n += 1
    return n


# ---------------------------------------------------------------------------
# Favorites ("<key>:<rel>" per user)
# ---------------------------------------------------------------------------
def favorites_raw(user: str) -> List[str]:
    return list((settings_store.get("cloud_favorites") or {}).get(user, []))


def set_favorite(user: str, key: str, rel: str, on: bool) -> None:
    real, rel = resolve(key, rel)
    if not rel or not os.path.lexists(real):
        raise CloudError("Nicht gefunden.")
    ref = f"{key}:{rel}"

    def change(all_favs):
        all_favs = dict(all_favs or {})
        mine = [f for f in all_favs.get(user, []) if f != ref]
        if on:
            mine.insert(0, ref)
        all_favs[user] = mine[:500]
        return all_favs
    settings_store.update("cloud_favorites", change, {})


def _rewrite_favorites(fn) -> None:
    def change(all_favs):
        out = {}
        for u, refs in (all_favs or {}).items():
            out[u] = [r2 for r2 in (fn(r) for r in refs) if r2]
        return out
    if settings_store.get("cloud_favorites"):
        settings_store.update("cloud_favorites", change, {})


def _move_favorites(old: str, new: str) -> None:
    _rewrite_favorites(lambda r: new + r[len(old):] if r == old or r.startswith(old + "/") else r)


def _drop_favorites(ref: str) -> None:
    _rewrite_favorites(lambda r: None if r == ref or r.startswith(ref + "/") else r)


def favorites(user: str, admin: bool) -> List[Dict[str, Any]]:
    allowed = set(keys_for(user, admin))
    favs = set(favorites_raw(user))
    out = []
    for ref in favorites_raw(user):
        key, _, rel = ref.partition(":")
        if key not in allowed:
            continue
        try:
            real, rel = resolve(key, rel)
        except CloudError:
            continue
        item = _entry(real, rel, favs, key)
        if item:
            out.append({**item, "space": key_space(key, user)})
    return out


# ---------------------------------------------------------------------------
# Recent files and search across the spaces a user reaches
# ---------------------------------------------------------------------------
def recent(user: str, admin: bool, limit: int = 60) -> List[Dict[str, Any]]:
    import heapq
    favs = set(favorites_raw(user))
    rows = []
    for key in keys_for(user, admin):
        for real, rel, st in _walk(key_dir(key)):
            rows.append((st.st_mtime, key, rel, st.st_size))
    out = []
    for mtime, key, rel, size in heapq.nlargest(limit, rows):
        name = rel.rsplit("/", 1)[-1]
        out.append({"name": name, "path": rel, "type": "file", "size": size, "mtime": int(mtime), "kind": kind_of(name),
                    "fav": f"{key}:{rel}" in favs, "space": key_space(key, user)})
    return out


def search(user: str, admin: bool, q: str, limit: int = 300) -> Dict[str, Any]:
    q = (q or "").strip().casefold()
    if len(q) < 2 or len(q) > 200:
        raise CloudError("Suchbegriff: 2 bis 200 Zeichen.")
    favs = set(favorites_raw(user))
    out = []
    for key in keys_for(user, admin):
        base = key_dir(key)
        stack = [("", base)]
        while stack and len(out) < limit:
            rel_dir, d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if not _visible(e.name) or e.is_symlink():
                            continue
                        rel = f"{rel_dir}/{e.name}".lstrip("/")
                        is_dir = e.is_dir(follow_symlinks=False)
                        if is_dir:
                            stack.append((rel, e.path))
                        if q in e.name.casefold():
                            item = _entry(e.path, rel, favs, key)
                            if item:
                                out.append({**item, "space": key_space(key, user)})
                                if len(out) >= limit:
                                    break
            except OSError:
                continue
    out.sort(key=lambda x: (x["type"] != "dir", -x["mtime"]))
    return {"q": q, "results": out, "truncated": len(out) >= limit}


# ---------------------------------------------------------------------------
# Download / preview
# ---------------------------------------------------------------------------
def file_path(key: str, rel: str) -> str:
    real, rel = resolve(key, rel)
    if not rel or not os.path.isfile(real):
        raise CloudError("Datei nicht gefunden.")
    return real


def inline_type(real: str) -> Optional[str]:
    return INLINE_MEDIA.get(os.path.splitext(real)[1].lower())


def text_preview(key: str, rel: str) -> Dict[str, Any]:
    real = file_path(key, rel)
    size = os.path.getsize(real)
    with open(real, "rb") as f:
        head = f.read(PREVIEW_BYTES)
    if b"\x00" in head[:8192]:
        raise CloudError("Keine Textdatei.")
    text = head.decode("utf-8", errors="replace")
    return {"content": text, "truncated": size > PREVIEW_BYTES, "size": size}


def zip_entries(key: str, rels: List[str]) -> Dict[str, Any]:
    """Files for a ZIP of these entries (folders with everything inside)."""
    entries = []
    names = []
    for rel in rels[:200]:
        real, rel = resolve(key, rel)
        if not os.path.lexists(real):
            raise CloudError("Nicht gefunden.")
        top = os.path.basename(real) or "Dateien"
        names.append(top)
        if os.path.isdir(real):
            entries += [(p, f"{top}/{r}") for p, r, _ in _walk(real)]
        elif os.path.isfile(real):
            entries.append((real, top))
    if not entries:
        raise CloudError("Nichts zum Herunterladen.")
    name = f"{names[0]}.zip" if len(names) == 1 else "Dateien.zip"
    return {"name": name, "files": entries}


# ---------------------------------------------------------------------------
# Share links (/s/c/<token>)
# ---------------------------------------------------------------------------
def _shares() -> Dict[str, Dict[str, Any]]:
    items = settings_store.get("cloud_shares") or {}
    now = time.time()
    live = {t: s for t, s in items.items() if s.get("expires", 0) > now}
    if len(live) != len(items):
        settings_store.set("cloud_shares", live)
    return live


def create_share(key: str, rel: str, hours: int, user: str) -> Dict[str, Any]:
    real, rel = resolve(key, rel)
    if not rel or not os.path.lexists(real):
        raise CloudError("Nicht gefunden.")
    if hours not in SHARE_HOURS:
        raise CloudError("Gültigkeit: 1 Std., 24 Std., 7 oder 30 Tage.")
    token = secrets.token_urlsafe(24)
    entry = {"key": key, "path": rel, "name": os.path.basename(real), "type": "dir" if os.path.isdir(real) else "file",
             "created": int(time.time()), "expires": int(time.time() + hours * 3600), "user": user, "downloads": 0}

    def change(items):
        items = {t: s for t, s in (items or {}).items() if s.get("expires", 0) > time.time()}
        if len(items) >= MAX_SHARES:
            raise CloudError(f"Zu viele aktive Freigaben (max. {MAX_SHARES}) – alte zuerst beenden.")
        items[token] = entry
        return items
    settings_store.update("cloud_shares", change, {})
    return {"token": token, **entry}


def list_shares(user: str, admin: bool) -> List[Dict[str, Any]]:
    out = []
    for t, s in _shares().items():
        if s["user"] == user or admin:
            out.append({"token": t, **{k: v for k, v in s.items() if k != "key"}, "space": key_space(s["key"], user)})
    return sorted(out, key=lambda s: -s["created"])


def delete_share(token: str, user: str, admin: bool) -> None:
    def change(items):
        items = dict(items or {})
        s = items.get(token)
        if not s or (s["user"] != user and not admin):
            raise CloudError("Freigabe nicht gefunden.")
        items.pop(token)
        return items
    settings_store.update("cloud_shares", change, {})


def shared(token: str) -> Dict[str, Any]:
    """What a public link hands out - checked again on every download."""
    if not TOKEN_RE.match(token or ""):
        raise CloudError("Link ungültig.")
    s = _shares().get(token)
    if not s:
        raise CloudError("Link abgelaufen oder ungültig.")
    real, rel = resolve(s["key"], s["path"])
    if not os.path.lexists(real):
        raise CloudError("Die Datei gibt es nicht mehr.")

    def count(items):
        items = dict(items or {})
        if token in items:
            items[token] = {**items[token], "downloads": items[token].get("downloads", 0) + 1}
        return items
    settings_store.update("cloud_shares", count, {})
    if os.path.isdir(real):
        return {"type": "dir", **zip_entries(s["key"], [rel]), "path": real}
    return {"type": "file", "path": real, "name": os.path.basename(real)}


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview(user: str, admin: bool) -> Dict[str, Any]:
    own = usage(f"u/{user}")
    out = {"user": user, "admin": admin, "own": own, "shared": usage("s"), "quota": config()["quota_mb"] * 1024 * 1024,
           "free": disk_free(), "config": config() if admin else None, "default_root": DEFAULT_ROOT if admin else None,
           "share_hours": list(SHARE_HOURS)}
    if admin:
        users_dir = os.path.join(root(), "users")
        out["users"] = sorted(({"name": n, "size": usage(f"u/{n}")} for n in (os.listdir(users_dir) if os.path.isdir(users_dir) else [])
                               if USER_RE.match(n) and n != user), key=lambda x: x["name"].casefold())
    return out


def uploads_elsewhere(can_exchange: bool, can_attachments: bool) -> Dict[str, Any]:
    """Files that came in outside the cloud: uploads through exchange links and doc attachments."""
    out: Dict[str, Any] = {"exchange": None, "attachments": None}
    if can_exchange:
        import exchange
        links = []
        for x in exchange.list_links():
            ups = []
            for u in reversed(x.get("uploads") or []):
                path = os.path.join(x["folder"], u["name"])
                ups.append({**u, "path": path, "exists": os.path.isfile(path)})
            links.append({"token": x["token"], "name": x.get("name"), "folder": x["folder"], "mode": x.get("mode"),
                          "note": x.get("note", ""), "expires": x.get("expires"), "user": x.get("user", ""), "uploads": ups})
        out["exchange"] = links
    if can_attachments:
        import attachments
        items = []
        for kind in attachments.KINDS:
            base = os.path.join(attachments.ROOT, kind)
            if not os.path.isdir(base):
                continue
            for item_dir in os.listdir(base):
                d = os.path.join(base, item_dir)
                if not os.path.isdir(d):
                    continue
                for e in os.scandir(d):
                    if e.is_file(follow_symlinks=False) and not e.name.startswith("."):
                        st = e.stat()
                        items.append({"kind": kind, "item": item_dir, "name": e.name, "size": st.st_size, "mtime": int(st.st_mtime),
                                      "inline": os.path.splitext(e.name)[1].lower() in attachments.INLINE})
        items.sort(key=lambda x: -x["mtime"])
        out["attachments"] = items[:1000]
    return out
