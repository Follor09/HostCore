"""
File explorer + storage analysis for mounted drives: browse, preview, download,
upload, edit text files, delete into a recycle bin (where the dashboard user may write)
and temporary share links.

Runs with the dashboard user's permissions (no sudo): whatever that user can't read
stays hidden. On top of that a denylist keeps secrets out of reach even when they are
readable (dashboard login, GitHub token, SSH / kube / docker credentials) and the
virtual filesystems (/proc, /sys, /dev, /run) are off limits.
Every path is resolved with realpath first, so symlinks can't sneak around the checks.
"""

import heapq
import json
import os
import shutil
import re
import secrets
import stat
import subprocess
import time
from typing import Any, Dict, List, Optional

import services_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

MAX_ENTRIES = 2000
LARGEST_FILES = 25
SCAN_TIME_LIMIT = 60  # seconds for "largest files"

DENIED = [os.path.realpath(p) for p in (
    os.path.join(BASE_DIR, "auth.json"),
    os.path.join(BASE_DIR, "settings.json"),
    os.path.join(BASE_DIR, "history.db"),          # activity + login log
    os.path.join(BASE_DIR, "history.db-journal"),
    os.path.join(BASE_DIR, ".git"),
    os.path.join(HOME, ".ssh"),
    os.path.join(HOME, ".kube"),
    os.path.join(HOME, ".gnupg"),
    os.path.join(HOME, ".docker"),
    os.path.join(HOME, ".git-credentials"),
    os.path.join(HOME, ".netrc"),
    os.path.join(HOME, ".config", "gh"),
    os.path.join(HOME, ".config", "rclone"),
    "/etc/rancher",
    "/proc", "/sys", "/dev", "/run",
)]

_size_cache: Dict[str, tuple] = {}


class FileAccessError(Exception):
    pass


def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _roots() -> List[str]:
    return sorted({os.path.realpath(d["mount"]) for d in services_manager.get_mounted_drives()})


TRASH_NAME = ".picore-trash"


def is_denied(real: str) -> bool:
    # The recycle bin is only reachable through the trash_* functions
    return any(_inside(real, d) for d in DENIED) or TRASH_NAME in real.split("/")


def resolve(path: str) -> str:
    """Validate a path from the browser and return its real path."""
    if not path or "\x00" in path:
        raise FileAccessError("Ungültiger Pfad.")
    real = os.path.realpath(path)
    if not any(_inside(real, root) for root in _roots()):
        raise FileAccessError("Pfad liegt auf keinem eingehängten Laufwerk.")
    if is_denied(real):
        raise FileAccessError("Dieser Pfad ist aus Sicherheitsgründen gesperrt.")
    return real


def _root_of(real: str) -> str:
    return max((r for r in _roots() if _inside(real, r)), key=len)


def list_dir(path: str) -> Dict[str, Any]:
    real = resolve(path)
    if not os.path.isdir(real):
        raise FileAccessError("Kein Ordner.")
    entries = []
    truncated = False
    try:
        with os.scandir(real) as it:
            for i, entry in enumerate(it):
                if entry.name == TRASH_NAME:
                    continue
                if i >= MAX_ENTRIES:
                    truncated = True
                    break
                full = os.path.join(real, entry.name)
                item = {"name": entry.name, "path": full, "type": "file", "size": None, "mtime": None,
                        "symlink": entry.is_symlink(), "locked": is_denied(os.path.realpath(full))}
                try:
                    st = entry.stat(follow_symlinks=True)
                    if stat.S_ISDIR(st.st_mode):
                        item["type"] = "dir"
                    elif stat.S_ISREG(st.st_mode):
                        item["size"] = st.st_size
                    else:
                        item["type"] = "other"
                    item["mtime"] = int(st.st_mtime)
                except OSError:
                    item["type"] = "broken"
                entries.append(item)
    except PermissionError:
        raise FileAccessError("Keine Leserechte für diesen Ordner.")

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    root = _root_of(real)
    return {"path": real, "root": root, "parent": None if real == root else os.path.dirname(real),
            "entries": entries, "truncated": truncated}


def dir_sizes(path: str) -> Dict[str, Any]:
    """Size of every direct subfolder via du (stays on this filesystem, cached 5 min)."""
    real = resolve(path)
    cached = _size_cache.get(real)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        res = subprocess.run(["du", "-x", "-k", "-d", "1", "--", real],
                             capture_output=True, text=True, timeout=120, errors="replace")
    except subprocess.TimeoutExpired:
        raise FileAccessError("Berechnung dauerte zu lange (> 2 Min.).")
    sizes, total = {}, None
    for line in res.stdout.splitlines():
        kb, _, p = line.partition("\t")
        if not kb.isdigit():
            continue
        if p == real:
            total = int(kb) * 1024
        elif os.path.dirname(p) == real and not is_denied(os.path.realpath(p)):
            sizes[os.path.basename(p)] = int(kb) * 1024
    unreadable = sum(1 for line in res.stderr.splitlines() if "Permission denied" in line)
    result = {"path": real, "sizes": sizes, "total": total, "unreadable": unreadable}
    _size_cache[real] = (time.time(), result)
    return result


def largest_files(path: str) -> Dict[str, Any]:
    """Top files below path on the same filesystem (time-limited walk)."""
    real = resolve(path)
    dev = os.stat(real).st_dev
    heap: List[tuple] = []
    start = time.time()
    scanned, timed_out = 0, False
    for dirpath, dirnames, filenames in os.walk(real, onerror=lambda e: None):
        # Prune other filesystems and denied paths before descending
        dirnames[:] = [d for d in dirnames
                       if not is_denied(os.path.realpath(os.path.join(dirpath, d)))
                       and _same_dev(os.path.join(dirpath, d), dev)]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            scanned += 1
            item = (st.st_size, full, int(st.st_mtime))
            if len(heap) < LARGEST_FILES:
                heapq.heappush(heap, item)
            elif st.st_size > heap[0][0]:
                heapq.heapreplace(heap, item)
        if time.time() - start > SCAN_TIME_LIMIT:
            timed_out = True
            break
    files = [{"path": p, "name": os.path.basename(p), "dir": os.path.dirname(p), "size": s, "mtime": m}
             for s, p, m in sorted(heap, reverse=True) if not is_denied(os.path.realpath(p))]
    return {"path": real, "files": files, "scanned": scanned, "timed_out": timed_out}


def _same_dev(path: str, dev: int) -> bool:
    try:
        return os.lstat(path).st_dev == dev
    except OSError:
        return False


def download_path(path: str) -> str:
    real = resolve(path)
    if not os.path.isfile(real):
        raise FileAccessError("Keine Datei.")
    if not os.access(real, os.R_OK):
        raise FileAccessError("Keine Leserechte für diese Datei.")
    return real



# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
PREVIEW_BYTES = 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon", ".avif": "image/avif"}
# SVG is deliberately not an image here: it can carry scripts, so it is shown as text


def image_type(real: str) -> Optional[str]:
    return IMAGE_TYPES.get(os.path.splitext(real)[1].lower())


def preview(path: str) -> Dict[str, Any]:
    real = download_path(path)
    size = os.path.getsize(real)
    info = {"path": real, "name": os.path.basename(real), "size": size, "mtime": int(os.path.getmtime(real))}
    if image_type(real):
        return {**info, "kind": "image"}
    with open(real, "rb") as f:
        head = f.read(PREVIEW_BYTES)
    if b"\x00" in head[:8192]:
        return {**info, "kind": "binary"}
    text = head.decode("utf-8", errors="replace")
    if size > PREVIEW_BYTES:
        text = text[:text.rfind("\n") + 1] or text  # don't end mid-line
    return {**info, "kind": "text", "content": text, "truncated": size > PREVIEW_BYTES}


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
MAX_UPLOAD = 4 * 1024 ** 3        # 4 GB per file
FREE_RESERVE = 200 * 1024 ** 2    # never fill the disk completely


def upload_target(directory: str, name: str, overwrite: bool, size: Optional[int]) -> str:
    real_dir = resolve(directory)
    if not os.path.isdir(real_dir):
        raise FileAccessError("Kein Ordner.")
    if not os.access(real_dir, os.W_OK | os.X_OK):
        raise FileAccessError("Keine Schreibrechte in diesem Ordner.")
    name = (name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name or len(name.encode()) > 255:
        raise FileAccessError("Ungültiger Dateiname.")
    target = os.path.join(real_dir, name)
    if is_denied(os.path.realpath(target)):
        raise FileAccessError("Dieser Pfad ist aus Sicherheitsgründen gesperrt.")
    if os.path.lexists(target):
        if os.path.isdir(target) or os.path.islink(target):
            raise FileAccessError("Dort gibt es schon einen Ordner oder Link mit diesem Namen.")
        if not overwrite:
            raise FileExistsError(name)
    if size is not None:
        if size > MAX_UPLOAD:
            raise FileAccessError("Datei zu groß (max. 4 GB).")
        if size > shutil_free(real_dir) - FREE_RESERVE:
            raise FileAccessError("Nicht genug freier Speicher.")
    return target


def shutil_free(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


# ---------------------------------------------------------------------------
# Share links
# ---------------------------------------------------------------------------
SHARE_HOURS = (1, 24, 168)


def _shares() -> Dict[str, Dict[str, Any]]:
    import settings_store
    items = settings_store.get("shares", {}) or {}
    now = time.time()
    live = {t: s for t, s in items.items() if s.get("expires", 0) > now}
    if len(live) != len(items):
        settings_store.set("shares", live)
    return live


def create_share(path: str, hours: int, user: str) -> Dict[str, Any]:
    import settings_store
    real = download_path(path)
    if hours not in SHARE_HOURS:
        raise FileAccessError("Gültigkeit muss 1 Std., 24 Std. oder 7 Tage sein.")
    shares = _shares()
    if len(shares) >= 50:
        raise FileAccessError("Zu viele aktive Freigaben (max. 50) – alte zuerst beenden.")
    token = secrets.token_urlsafe(24)
    shares[token] = {"path": real, "name": os.path.basename(real), "size": os.path.getsize(real),
                     "created": int(time.time()), "expires": int(time.time() + hours * 3600),
                     "user": user or "", "downloads": 0}
    settings_store.set("shares", shares)
    return {"token": token, **shares[token]}


def list_shares() -> List[Dict[str, Any]]:
    return sorted(({"token": t, **s} for t, s in _shares().items()), key=lambda s: -s["created"])


def delete_share(token: str) -> None:
    import settings_store
    shares = _shares()
    shares.pop(token, None)
    settings_store.set("shares", shares)


def shared_file(token: str) -> Dict[str, Any]:
    """The file behind a share link - checked again on every download."""
    import settings_store
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        raise FileAccessError("Link ungültig.")
    shares = _shares()
    share = shares.get(token)
    if not share:
        raise FileAccessError("Link abgelaufen oder ungültig.")
    real = download_path(share["path"])   # still on a drive, not blocked, readable
    share["downloads"] = share.get("downloads", 0) + 1
    settings_store.set("shares", shares)
    return {**share, "path": real}


# ---------------------------------------------------------------------------
# Text editor
# ---------------------------------------------------------------------------
EDIT_BYTES = 2 * 1024 * 1024
COMPOSE_NAMES = re.compile(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$")


def _writable_file(real: str) -> bool:
    return os.access(real, os.W_OK) and os.access(os.path.dirname(real), os.W_OK | os.X_OK)


def read_text(path: str) -> Dict[str, Any]:
    real = download_path(path)
    size = os.path.getsize(real)
    if size > EDIT_BYTES:
        raise FileAccessError("Zu groß zum Bearbeiten (max. 2 MB).")
    with open(real, "rb") as f:
        data = f.read()
    if b"\x00" in data[:8192]:
        raise FileAccessError("Binärdatei – kann nicht bearbeitet werden.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileAccessError("Keine UTF-8-Textdatei – kann nicht sicher bearbeitet werden.")
    st = os.stat(real)
    return {"path": real, "name": os.path.basename(real), "content": text, "size": size,
            "mtime": str(st.st_mtime_ns), "writable": _writable_file(real),
            "compose": bool(COMPOSE_NAMES.match(os.path.basename(real)))}


def _validate(real: str, text: str) -> None:
    """Syntax check for formats where a typo breaks a service."""
    ext = os.path.splitext(real)[1].lower()
    if ext == ".json":
        try:
            json.loads(text)
        except ValueError as e:
            raise FileAccessError(f"Ungültiges JSON: {e}")
    if COMPOSE_NAMES.match(os.path.basename(real)) and shutil.which("docker"):
        # Check a copy next to the original: relative paths and .env stay valid
        tmp = os.path.join(os.path.dirname(real), f".{os.path.basename(real)}.check-{secrets.token_hex(3)}.yml")
        try:
            with open(tmp, "x", encoding="utf-8") as f:
                f.write(text)
            res = subprocess.run(["docker", "compose", "-f", tmp, "config", "-q"], cwd=os.path.dirname(real),
                                 capture_output=True, text=True, timeout=30, errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return  # can't check: don't block saving
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if res.returncode != 0:
            err = res.stderr.strip().replace(tmp, os.path.basename(real)).replace(os.path.basename(tmp), os.path.basename(real))
            raise FileAccessError(f"Compose-Datei ungültig: {err.splitlines()[-1][:300] if err else 'Fehler'}")


def write_text(path: str, content: str, mtime: Optional[str]) -> Dict[str, Any]:
    real = resolve(path)
    if not os.path.isfile(real):
        raise FileAccessError("Keine Datei.")
    if not _writable_file(real):
        raise FileAccessError("Keine Schreibrechte für diese Datei.")
    data = content.encode("utf-8")
    if len(data) > EDIT_BYTES:
        raise FileAccessError("Zu groß (max. 2 MB).")
    st = os.stat(real)
    # nanoseconds as a string: too big for a JavaScript number
    if mtime is not None and str(st.st_mtime_ns) != str(mtime):
        raise FileExistsError("Die Datei wurde inzwischen geändert.")
    _validate(real, content)
    # Write a sibling and swap it in: never a half-written config; keep mode (and owner, where allowed)
    tmp = os.path.join(os.path.dirname(real), f".{os.path.basename(real)}.edit-{secrets.token_hex(4)}")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass
        os.replace(tmp, real)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"path": real, "size": len(data), "mtime": str(os.stat(real).st_mtime_ns)}


def create_file(directory: str, name: str) -> Dict[str, Any]:
    target = upload_target(directory, name, overwrite=False, size=0)
    with open(target, "x"):
        pass
    return {"path": target}


def create_folder(directory: str, name: str) -> Dict[str, Any]:
    target = upload_target(directory, name, overwrite=False, size=0)
    os.mkdir(target)
    return {"path": target}


# ---------------------------------------------------------------------------
# Recycle bin: deleting moves into <drive>/.picore-trash/<id>/ (same filesystem, so it's
# a rename, not a copy). Items older than TRASH_DAYS are removed for good.
# ---------------------------------------------------------------------------
TRASH_DAYS = 30


def _protected(real: str) -> bool:
    """Things that must never be deleted from the browser."""
    keep = {os.path.realpath(p) for p in (HOME, BASE_DIR, "/", "/home", "/etc", "/usr", "/var", "/boot", "/opt")}
    if real in keep or real in _roots():
        return True
    # the dashboard itself, anything inside it and anything containing it
    return _inside(BASE_DIR, real) or _inside(real, os.path.realpath(BASE_DIR))


def _trash_dir_for(real: str) -> str:
    """A writable recycle bin on the same filesystem as `real`."""
    dev = os.lstat(real).st_dev
    candidates = [os.path.join(HOME, ".local", "share", TRASH_NAME)]
    root = _root_of(real)
    candidates.append(os.path.join(root, TRASH_NAME))
    # then the highest writable folder above the item on the same filesystem
    parts = os.path.dirname(real).split("/")
    for i in range(2, len(parts) + 1):
        candidates.append(os.path.join("/".join(parts[:i]) or "/", TRASH_NAME))
    home_trash = candidates[0]
    try:
        if os.lstat(HOME).st_dev == dev and not os.path.exists(home_trash):
            os.makedirs(home_trash, mode=0o700)
            _remember_trash(home_trash)
    except OSError:
        pass
    for c in candidates:
        parent = os.path.dirname(c)
        try:
            if os.lstat(parent).st_dev != dev:
                continue
        except OSError:
            continue
        if os.path.isdir(c) and not os.path.islink(c) and os.access(c, os.W_OK | os.X_OK):
            return c
        if not os.path.exists(c) and os.access(parent, os.W_OK | os.X_OK):
            os.makedirs(c, mode=0o700, exist_ok=True)
            _remember_trash(c)
            return c
    raise FileAccessError("Kein Papierkorb auf diesem Laufwerk möglich (keine Schreibrechte).")


def _remember_trash(path: str) -> None:
    import settings_store
    dirs = settings_store.get("trash_dirs", []) or []
    if path not in dirs:
        settings_store.set("trash_dirs", dirs + [path])


def _trash_dirs() -> List[str]:
    import settings_store
    dirs = set(settings_store.get("trash_dirs", []) or [])
    dirs.add(os.path.join(HOME, ".local", "share", TRASH_NAME))
    return sorted(d for d in dirs if os.path.isdir(d) and not os.path.islink(d))


def _tree_size(path: str) -> int:
    if not os.path.isdir(path) or os.path.islink(path):
        return os.lstat(path).st_size
    total = 0
    for dirpath, _, filenames in os.walk(path, onerror=lambda e: None):
        for n in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                pass
    return total


def trash_move(path: str, user: str = "") -> Dict[str, Any]:
    if not path or "\x00" in path:
        raise FileAccessError("Ungültiger Pfad.")
    # The item itself (a symlink is deleted as a link, not its target)
    parent = resolve(os.path.dirname(os.path.abspath(path)))
    real = os.path.join(parent, os.path.basename(os.path.abspath(path)))
    if not os.path.lexists(real):
        raise FileAccessError("Nicht gefunden.")
    if is_denied(os.path.realpath(real)) or _protected(os.path.realpath(real)) or _protected(real):
        raise FileAccessError("Das darf hier nicht gelöscht werden.")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise FileAccessError("Keine Schreibrechte im Ordner.")
    is_dir = os.path.isdir(real) and not os.path.islink(real)
    trash = _trash_dir_for(real)
    item_id = f"{int(time.time())}-{secrets.token_hex(4)}"
    box = os.path.join(trash, item_id)
    os.mkdir(box, 0o700)
    size = _tree_size(real)
    info = {"id": item_id, "path": real, "name": os.path.basename(real), "type": "dir" if is_dir else "file",
            "size": size, "deleted": int(time.time()), "user": user}
    with open(os.path.join(box, "info.json"), "w") as f:
        json.dump(info, f)
    try:
        os.rename(real, os.path.join(box, "data"))
    except OSError as e:
        shutil.rmtree(box, ignore_errors=True)
        raise FileAccessError(f"Löschen fehlgeschlagen: {e.strerror or e}")
    return info


def _trash_items() -> List[Dict[str, Any]]:
    items = []
    for trash in _trash_dirs():
        try:
            names = os.listdir(trash)
        except OSError:
            continue
        for name in names:
            if not re.fullmatch(r"\d+-[0-9a-f]{8}", name):
                continue
            box = os.path.join(trash, name)
            try:
                with open(os.path.join(box, "info.json")) as f:
                    info = json.load(f)
            except (OSError, ValueError):
                continue
            if not os.path.lexists(os.path.join(box, "data")):
                continue
            info.update(id=name, box=box, expires=info.get("deleted", 0) + TRASH_DAYS * 86400)
            items.append(info)
    items.sort(key=lambda i: -i.get("deleted", 0))
    return items


def trash_list() -> Dict[str, Any]:
    items = _trash_items()
    return {"items": [{k: v for k, v in i.items() if k != "box"} for i in items],
            "total": sum(i.get("size", 0) for i in items), "days": TRASH_DAYS}


def _trash_item(item_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"\d+-[0-9a-f]{8}", item_id or ""):
        raise FileAccessError("Ungültiger Eintrag.")
    item = next((i for i in _trash_items() if i["id"] == item_id), None)
    if not item:
        raise FileAccessError("Nicht mehr im Papierkorb.")
    return item


def trash_restore(item_id: str) -> Dict[str, Any]:
    item = _trash_item(item_id)
    target = item["path"]
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise FileAccessError(f"Der ursprüngliche Ordner {parent} existiert nicht mehr.")
    resolve(parent)
    if os.path.lexists(target):
        stem, ext = os.path.splitext(os.path.basename(target))
        if item["type"] == "dir":
            stem, ext = os.path.basename(target), ""
        for n in range(1, 100):
            candidate = os.path.join(parent, f"{stem} (wiederhergestellt{'' if n == 1 else ' ' + str(n)}){ext}")
            if not os.path.lexists(candidate):
                target = candidate
                break
        else:
            raise FileAccessError("Name bereits vergeben.")
    try:
        os.rename(os.path.join(item["box"], "data"), target)
    except OSError as e:
        raise FileAccessError(f"Wiederherstellen fehlgeschlagen: {e.strerror or e}")
    shutil.rmtree(item["box"], ignore_errors=True)
    return {"path": target}


def _remove_box(box: str) -> None:
    data = os.path.join(box, "data")
    if os.path.isdir(data) and not os.path.islink(data):
        # rmtree needs write access to every folder: make our own folders writable first
        for dirpath, dirnames, _ in os.walk(data):
            for d in [dirpath] + [os.path.join(dirpath, x) for x in dirnames]:
                try:
                    if not os.path.islink(d):
                        os.chmod(d, stat.S_IMODE(os.lstat(d).st_mode) | 0o700)
                except OSError:
                    pass
    shutil.rmtree(box, ignore_errors=True)


def trash_delete(item_id: str) -> None:
    _remove_box(_trash_item(item_id)["box"])


def trash_empty() -> int:
    items = _trash_items()
    for i in items:
        _remove_box(i["box"])
    return len(items)


def trash_purge() -> int:
    now = time.time()
    old = [i for i in _trash_items() if i["expires"] < now]
    for i in old:
        _remove_box(i["box"])
    return len(old)
