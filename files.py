"""
Read-only file explorer + storage analysis for mounted drives.

Runs with the dashboard user's permissions (no sudo): whatever that user can't read
stays hidden. On top of that a denylist keeps secrets out of reach even when they are
readable (dashboard login, GitHub token, SSH / kube / docker credentials) and the
virtual filesystems (/proc, /sys, /dev, /run) are off limits.
Every path is resolved with realpath first, so symlinks can't sneak around the checks.
"""

import heapq
import os
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
