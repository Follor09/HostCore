"""
Dashboard settings in settings.json (untracked, so git updates never reset them).
Small key/value store shared by the weather widget, services list and settings page.

Writes are atomic (temporary file, fsync, rename) and serialised by a lock. A settings.json
that cannot be read is never overwritten: a write would otherwise replace every setting with
just the one key - it is kept as settings.json.broken-<time> and the last good copy
(settings.json.bak, refreshed on every write) is used instead.
"""

import json
import logging
import os
import shutil
import threading
import time
from typing import Any, Callable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
BACKUP_FILE = SETTINGS_FILE + ".bak"

_lock = threading.RLock()
logger = logging.getLogger("pi_dashboard")


def _load(path: str) -> dict:
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("settings.json is not an object")
    return data


def _read() -> dict:
    try:
        return _load(SETTINGS_FILE)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        return _recover(e)


def _recover(error: Exception) -> dict:
    """settings.json is unreadable (power loss, full disk, hand edit): keep it for inspection
    and continue with the last good copy."""
    with _lock:
        try:   # another thread may have recovered already
            return _load(SETTINGS_FILE)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            pass
        broken = f"{SETTINGS_FILE}.broken-{int(time.time())}"
        try:
            os.replace(SETTINGS_FILE, broken)
        except OSError:
            broken = ""
        try:
            data = _load(BACKUP_FILE)
            _write(data)
            logger.error(f"settings.json unreadable ({error}) - restored from settings.json.bak"
                         + (f", broken file kept as {os.path.basename(broken)}" if broken else ""))
            return data
        except (OSError, ValueError):
            logger.error(f"settings.json unreadable ({error}) and no usable backup - starting with empty settings"
                         + (f", broken file kept as {os.path.basename(broken)}" if broken else ""))
            return {}


def _write(data: dict) -> None:
    tmp = SETTINGS_FILE + ".tmp"
    # Owner only: holds mail, NetBox, MCP and backup credentials
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    if os.path.exists(SETTINGS_FILE):
        try:   # the previous, readable version becomes the backup
            _load(SETTINGS_FILE)
            shutil.copy2(SETTINGS_FILE, BACKUP_FILE)
            os.chmod(BACKUP_FILE, 0o600)
        except (OSError, ValueError):
            pass
    os.replace(tmp, SETTINGS_FILE)
    try:   # make the rename itself survive a power loss
        dfd = os.open(BASE_DIR, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def get(key: str, default: Any = None) -> Any:
    return _read().get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors get()
    with _lock:
        data = _read()
        data[key] = value
        _write(data)


def update(key: str, fn: Callable[[Any], Any], default: Any = None) -> Any:
    """Read, change and write one key under the lock - no lost update when two requests
    change the same list at once. fn gets the current value and returns the new one."""
    with _lock:
        data = _read()
        value = fn(data.get(key, default))
        data[key] = value
        _write(data)
        return value
