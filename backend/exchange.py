"""
Exchange links ("Briefkasten"): a public link for one folder on the server, without login.

- mode "upload":   others can upload files into the folder (nothing is listed)
- mode "download": others can see and download the files directly in the folder
- mode "both":     both of the above
Every link expires (1 hour to 30 days), uploads have a total size limit per link, names are
cleaned and never overwrite an existing file, sub folders and hidden files are never shown.
Stored in settings.json under "exchange"; every public access goes into the activity log.
"""

import os
import re
import secrets
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional

import files
import settings_store

KEY = "exchange"
MODES = {"upload": "Nur hochladen", "download": "Nur herunterladen", "both": "Hoch- und herunterladen"}
HOURS = (1, 24, 168, 720)
MAX_LINKS = 30
MAX_LIST = 500
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_lock = threading.Lock()


class ExchangeError(Exception):
    pass


def _all() -> Dict[str, Dict[str, Any]]:
    items = settings_store.get(KEY, {}) or {}
    now = time.time()
    live = {t: x for t, x in items.items() if x.get("expires", 0) > now}
    if len(live) != len(items):
        settings_store.set(KEY, live)
    return live


def _public(token: str, x: Dict[str, Any]) -> Dict[str, Any]:
    return {"token": token, **{k: v for k, v in x.items() if k != "uploads"}, "uploads": x.get("uploads", [])[-20:],
            "mode_label": MODES[x["mode"]]}


def create(folder: str, mode: str, hours: int, max_mb: int, note: str, user: str, can_write: bool) -> Dict[str, Any]:
    if mode not in MODES:
        raise ExchangeError("Unbekannte Art.")
    if hours not in HOURS:
        raise ExchangeError("Gültigkeit: 1 Std., 24 Std., 7 Tage oder 30 Tage.")
    if mode != "download" and not can_write:
        raise ExchangeError("Zum Hochladen-Lassen braucht es die Berechtigung „Dateien hochladen“.")
    try:
        real = files.resolve(folder)
    except files.FileAccessError as e:
        raise ExchangeError(str(e))
    if not os.path.isdir(real):
        raise ExchangeError("Bitte einen Ordner angeben.")
    if mode != "download" and not os.access(real, os.W_OK | os.X_OK):
        raise ExchangeError("In diesen Ordner darf das Dashboard nicht schreiben.")
    if not os.access(real, os.R_OK | os.X_OK):
        raise ExchangeError("Dieser Ordner ist nicht lesbar.")
    try:
        max_mb = int(max_mb)
    except (TypeError, ValueError):
        max_mb = 500
    if not 1 <= max_mb <= 20480:
        raise ExchangeError("Limit: 1 MB bis 20 GB.")
    with _lock:
        items = _all()
        if len(items) >= MAX_LINKS:
            raise ExchangeError(f"Höchstens {MAX_LINKS} aktive Austausch-Links – alte zuerst beenden.")
        token = secrets.token_urlsafe(24)
        items[token] = {"folder": real, "name": os.path.basename(real.rstrip("/")) or real, "mode": mode,
                        "created": int(time.time()), "expires": int(time.time() + hours * 3600), "user": user or "",
                        "max_bytes": max_mb * 1024 * 1024, "used_bytes": 0, "uploads": [], "downloads": 0,
                        "note": str(note or "").strip()[:200]}
        settings_store.set(KEY, items)
        return _public(token, items[token])


def list_links() -> List[Dict[str, Any]]:
    return sorted((_public(t, x) for t, x in _all().items()), key=lambda x: -x["created"])


def delete(token: str) -> None:
    with _lock:
        items = _all()
        items.pop(token, None)
        settings_store.set(KEY, items)


def _get(token: str) -> Dict[str, Any]:
    if not TOKEN_RE.match(token or ""):
        raise ExchangeError("Link ungültig.")
    x = _all().get(token)
    if not x:
        raise ExchangeError("Link abgelaufen oder ungültig.")
    if not os.path.isdir(x["folder"]) or files.is_denied(x["folder"]):
        raise ExchangeError("Der Ordner ist nicht mehr verfügbar.")
    return x


def _listing(folder: str) -> List[Dict[str, Any]]:
    out = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for e in entries:
        if e.name.startswith(".") or not e.is_file(follow_symlinks=False):
            continue
        real = os.path.realpath(e.path)
        if files.is_denied(real) or os.path.dirname(real) != os.path.realpath(folder):
            continue
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        out.append({"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda f: -f["mtime"])
    return out[:MAX_LIST]


def info(token: str) -> Dict[str, Any]:
    x = _get(token)
    left = max(0, x["max_bytes"] - x.get("used_bytes", 0))
    return {"name": x["name"], "mode": x["mode"], "expires": x["expires"], "note": x.get("note", ""),
            "upload": x["mode"] in ("upload", "both"), "download": x["mode"] in ("download", "both"),
            "max_bytes": x["max_bytes"], "left_bytes": left,
            "files": _listing(x["folder"]) if x["mode"] in ("download", "both") else []}


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", os.path.basename(str(name or "").replace("\\", "/")))
    name = re.sub(r"[\x00-\x1f\x7f/]", "", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:120]
    if not name:
        name = "upload"
    return name


def upload_target(token: str, name: str, size: Optional[int]) -> Dict[str, Any]:
    x = _get(token)
    if x["mode"] not in ("upload", "both"):
        raise ExchangeError("Über diesen Link kann nichts hochgeladen werden.")
    left = x["max_bytes"] - x.get("used_bytes", 0)
    if size is not None and size > left:
        raise ExchangeError("Das Limit dieses Links ist erreicht.")
    base = clean_name(name)
    stem, ext = os.path.splitext(base)
    target = os.path.join(x["folder"], base)
    n = 2
    while os.path.lexists(target):  # never overwrite: "foto (2).jpg"
        target = os.path.join(x["folder"], f"{stem} ({n}){ext}")
        n += 1
        if n > 999:
            raise ExchangeError("Zu viele Dateien mit diesem Namen.")
    if files.is_denied(os.path.realpath(target)):
        raise ExchangeError("Dieser Pfad ist gesperrt.")
    if size is not None and size > files.shutil_free(x["folder"]) - files.FREE_RESERVE:
        raise ExchangeError("Auf dem Server ist nicht genug Platz.")
    return {"target": target, "left": left}


def record_upload(token: str, name: str, size: int, ip: str) -> None:
    with _lock:
        items = _all()
        x = items.get(token)
        if not x:
            return
        x["used_bytes"] = x.get("used_bytes", 0) + size
        x.setdefault("uploads", []).append({"name": name, "size": size, "ts": int(time.time()), "ip": ip})
        x["uploads"] = x["uploads"][-200:]
        settings_store.set(KEY, items)


def download_path(token: str, name: str) -> str:
    x = _get(token)
    if x["mode"] not in ("download", "both"):
        raise ExchangeError("Über diesen Link kann nichts heruntergeladen werden.")
    if name not in {f["name"] for f in _listing(x["folder"])}:
        raise ExchangeError("Datei nicht gefunden.")
    with _lock:
        items = _all()
        if token in items:
            items[token]["downloads"] = items[token].get("downloads", 0) + 1
            settings_store.set(KEY, items)
    return os.path.join(x["folder"], name)
