"""
Data retention (Einstellungen -> Datenaufbewahrung): how long recorded data and how many versions
are kept, plus the disk space each store takes. The modules read their limit from here instead of
a constant; the hourly cleanup in monitor.Monitor (and each module's own trimming) applies it.
"""

import os
import sqlite3
from typing import Any, Dict, List

import settings_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEY = "retention"

# key: (label, description, unit, default, min, max)
ITEMS: Dict[str, tuple] = {
    "metrics": ("Messwerte", "CPU, Temperatur, RAM, Netzwerk, Sensoren, Programme und Container – eine Messung pro Minute", "days", 7, 3, 90),
    "uptime": ("Verfügbarkeit & Drosselung", "Zeiträume der Dienste und Drosselungs-Ereignisse", "days", 90, 30, 730),
    "health": ("Web-Dienst-Prüfungen", "Ergebnisse der Web-Dienst-Checks", "days", 30, 7, 365),
    "audit": ("Aktivitätsprotokoll", "Anmeldungen und Änderungen (auch über den MCP)", "days", 180, 30, 730),
    "traffic": ("Datenverbrauch", "Empfangene und gesendete Bytes pro Tag und Schnittstelle", "days", 730, 90, 1825),
    "disk": ("Speicherverlauf", "Belegung pro Tag für die Speicherprognose und große Ordner", "days", 400, 90, 1825),
    "trash": ("Papierkorb", "Gelöschte Dateien bleiben so lange wiederherstellbar", "days", 30, 1, 90),
    "wiki_versions": ("Wiki-Versionen", "Frühere Fassungen pro Wissensseite", "count", 20, 5, 100),
    "doc_versions": ("Doku-Versionen", "Frühere und gelöschte Fassungen von Inventar, Verträgen, Befehlen, Tagebuch, Runbooks, Steckbriefen und Wiki", "days", 365, 30, 1825),
    "board_versions": ("Board-Verlauf", "Gespeicherte Stände der Startseite", "count", 60, 10, 200),
    "webhook_events": ("Webhook-Meldungen", "Zuletzt empfangene Meldungen aller Webhooks", "count", 500, 100, 5000),
    "mcp_calls": ("MCP-Aufrufe", "Protokoll der Werkzeug-Aufrufe über den MCP-Server", "count", 1000, 100, 10000),
}


class RetentionError(Exception):
    pass


def _cfg() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return c if isinstance(c, dict) else {}


def get(key: str) -> int:
    _, _, _, default, lo, hi = ITEMS[key]
    try:
        return max(lo, min(hi, int(_cfg().get(key, default))))
    except (TypeError, ValueError):
        return default


def seconds(key: str) -> int:
    return get(key) * 86400


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(_cfg())
    for key, value in (fields or {}).items():
        if key not in ITEMS:
            continue
        label, _, _, _, lo, hi = ITEMS[key]
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise RetentionError(f"{label}: keine Zahl.")
        if not lo <= v <= hi:
            raise RetentionError(f"{label}: zwischen {lo} und {hi}.")
        c[key] = v
    settings_store.set(KEY, c)
    return overview()


# ---------------------------------------------------------------------------
# Disk space per store
# ---------------------------------------------------------------------------
def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def _db_tables(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out = []
    try:
        with sqlite3.connect(path, timeout=5) as conn:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
                out.append({"name": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]})
    except sqlite3.Error:
        pass
    return out


def stores() -> List[Dict[str, Any]]:
    import files
    out = []
    for label, rel, desc in (("Messwerte & Protokolle", "history.db", "Messwerte, Verfügbarkeit, Datenverbrauch, Strom, Container, Web-Dienst-Prüfungen, Aktivitätsprotokoll"),
                             ("Einstellungen & Doku", "settings.json", "Inventar, Tagebuch, Runbooks, Verträge, Notizen, Aufgaben …"),
                             ("Wissensdatenbank", "wiki.json", "Seiten mit Versionen"),
                             ("Board-Verlauf", "board_history.json", ""),
                             ("MCP-Aufrufe", "mcp_calls.json", ""),
                             ("Anhänge", "attachments", ""),
                             ("Icon-Cache", "icon_cache", "Wird bei Bedarf neu geladen"),
                             ("GeoIP-Datenbank", "geoip.db", "")):
        path = os.path.join(BASE_DIR, rel)
        if not os.path.exists(path):
            continue
        item = {"label": label, "file": rel, "description": desc, "bytes": _size(path)}
        if rel.endswith(".db") and rel != "geoip.db":
            item["tables"] = _db_tables(path)
        out.append(item)
    try:
        t = files.trash_list()
        out.append({"label": "Papierkorb", "file": files.TRASH_NAME, "description": f"{len(t.get('items', []))} Einträge",
                    "bytes": t.get("total", 0)})
    except Exception:
        pass
    return out


def overview() -> Dict[str, Any]:
    items = []
    for key, (label, desc, unit, default, lo, hi) in ITEMS.items():
        items.append({"key": key, "label": label, "description": desc, "unit": unit, "value": get(key),
                      "default": default, "min": lo, "max": hi})
    st = stores()
    return {"items": items, "stores": st, "total_bytes": sum(s["bytes"] for s in st)}


def cleanup_now() -> Dict[str, Any]:
    """Apply all limits right away and give the freed space of the databases back."""
    import audit
    import boards
    import files
    import healthchecks
    import monitor
    import wiki
    monitor.cleanup()
    healthchecks.cleanup()
    audit.cleanup()
    files.trash_purge()
    wiki.trim_versions()
    boards.trim_history()
    try:
        import versions
        versions.cleanup()
    except Exception:
        pass
    try:
        import webhooks
        webhooks.trim()
    except Exception:
        pass
    try:
        import mcp_server
        mcp_server.trim_calls()
    except Exception:
        pass
    try:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "history.db"), timeout=30)
        conn.execute("VACUUM")
        conn.close()
    except sqlite3.Error:
        pass
    return overview()
