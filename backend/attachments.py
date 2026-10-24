"""
Attachments for documentation entries (inventory, contracts, service docs, wiki pages):
invoices, manuals, photos ... stored under attachments/<kind>/<id>/ next to the dashboard.
Only document and image types, at most 25 MB per file and 100 files per entry; images and PDFs
are shown in the browser in a sandbox, everything else is downloaded.
"""

import os
import re
import shutil
import time
import unicodedata
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(BASE_DIR, "attachments")
KINDS = ("inventory", "contracts", "servicedocs", "wiki")
TYPES = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".txt": "text/plain", ".md": "text/plain", ".log": "text/plain", ".csv": "text/csv",
    ".json": "application/json", ".yaml": "text/plain", ".yml": "text/plain", ".conf": "text/plain", ".zip": "application/zip",
}
INLINE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".md", ".log", ".csv"}
MAX_FILE = 25 * 1024 * 1024
MAX_FILES = 100
ID_RE = re.compile(r"^[\w:.@-]{1,90}$")


class AttachmentError(Exception):
    pass


def _dir(kind: str, item_id: str) -> str:
    if kind not in KINDS:
        raise AttachmentError("Unbekannte Art.")
    if not ID_RE.match(item_id or ""):
        raise AttachmentError("Ungültiger Eintrag.")
    return os.path.join(ROOT, kind, item_id.replace(":", "_"))


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename(str(name or "").replace("\\", "/")))
    name = re.sub(r"[\x00-\x1f\x7f/]", "", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:120]
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in TYPES:
        raise AttachmentError(f"Erlaubt sind: {', '.join(sorted(e.lstrip('.') for e in TYPES))}.")
    return name


def list_files(kind: str, item_id: str) -> List[Dict[str, Any]]:
    d = _dir(kind, item_id)
    if not os.path.isdir(d):
        return []
    out = []
    for e in os.scandir(d):
        if e.is_file(follow_symlinks=False) and not e.name.startswith("."):
            st = e.stat()
            out.append({"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime),
                        "inline": os.path.splitext(e.name)[1].lower() in INLINE})
    return sorted(out, key=lambda f: -f["mtime"])


def counts(kind: str) -> Dict[str, int]:
    base = os.path.join(ROOT, kind)
    if kind not in KINDS or not os.path.isdir(base):
        return {}
    out = {}
    for e in os.scandir(base):
        if e.is_dir():
            n = sum(1 for f in os.scandir(e.path) if f.is_file() and not f.name.startswith("."))
            if n:
                out[e.name] = n
    return out


def target(kind: str, item_id: str, name: str, size: Optional[int]) -> str:
    d = _dir(kind, item_id)
    name = clean_name(name)
    if size is not None and size > MAX_FILE:
        raise AttachmentError("Datei zu groß (höchstens 25 MB).")
    os.makedirs(d, exist_ok=True)
    if len(list_files(kind, item_id)) >= MAX_FILES:
        raise AttachmentError(f"Höchstens {MAX_FILES} Anhänge pro Eintrag.")
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(d, name), 2
    while os.path.exists(path):
        path = os.path.join(d, f"{stem} ({n}){ext}")
        n += 1
    return path


def file_path(kind: str, item_id: str, name: str) -> Dict[str, Any]:
    d = _dir(kind, item_id)
    if name not in {f["name"] for f in list_files(kind, item_id)}:
        raise AttachmentError("Anhang nicht gefunden.")
    ext = os.path.splitext(name)[1].lower()
    return {"path": os.path.join(d, name), "media": TYPES.get(ext, "application/octet-stream"), "inline": ext in INLINE}


def delete(kind: str, item_id: str, name: str) -> List[Dict[str, Any]]:
    os.remove(file_path(kind, item_id, name)["path"])
    return list_files(kind, item_id)


def delete_all(kind: str, item_id: str) -> None:
    """When the entry itself is deleted."""
    try:
        shutil.rmtree(_dir(kind, item_id))
    except (OSError, AttachmentError):
        pass
