"""
File explorer + storage analysis for mounted drives: browse, preview, download,
upload (where the dashboard user may write) and temporary share links.

Runs with the dashboard user's permissions (no sudo): whatever that user can't read
stays hidden. On top of that a denylist keeps secrets out of reach even when they are
readable (dashboard login, GitHub token, SSH / kube / docker credentials) and the
virtual filesystems (/proc, /sys, /dev, /run) are off limits.
Every path is resolved with realpath first, so symlinks can't sneak around the checks.
"""

import heapq
import os
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


def is_denied(real: str) -> bool:
    return any(_inside(real, d) for d in DENIED)


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
