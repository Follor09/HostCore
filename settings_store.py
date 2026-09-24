"""
Dashboard settings in settings.json (untracked, so git updates never reset them).
Small key/value store shared by the weather widget, services list and settings page.
"""

import json
import os
import threading
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get(key: str, default: Any = None) -> Any:
    return _read().get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors get()
    with _lock:
        data = _read()
        data[key] = value
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)
