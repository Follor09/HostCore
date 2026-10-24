"""
Knowledge base (page /wissen): free Markdown pages in folders for everything that doesn't fit a
runbook or service doc. Stored in wiki.json next to the dashboard (not settings.json - pages can be
long); every save keeps the previous version (last 20 per page by default, see retention.py).
"""

import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI_FILE = os.path.join(BASE_DIR, "wiki.json")
MAX_PAGES = 1000
MAX_CONTENT = 100_000
_lock = threading.Lock()


class WikiError(Exception):
    pass


def _load() -> Dict[str, Any]:
    try:
        with open(WIKI_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("pages"), list) else {"pages": []}
    except (OSError, ValueError):
        return {"pages": []}


def _save(data: Dict[str, Any]) -> None:
    with open(WIKI_FILE + ".tmp", "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(WIKI_FILE + ".tmp", WIKI_FILE)


def _summary(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: p.get(k) for k in ("id", "title", "folder", "created", "updated", "user")} | {"size": len(p.get("content", ""))}


def pages() -> Dict[str, Any]:
    items = sorted(_load()["pages"], key=lambda p: ((p.get("folder") or "").lower(), p["title"].lower()))
    return {"pages": [_summary(p) for p in items], "folders": sorted({p.get("folder") or "" for p in items} - {""}, key=str.lower)}


def page(page_id: str) -> Dict[str, Any]:
    p = next((x for x in _load()["pages"] if x["id"] == page_id), None)
    if not p:
        raise WikiError("Seite nicht gefunden.")
    return {**{k: v for k, v in p.items() if k != "versions"},
            "versions": [{"ts": v["ts"], "user": v.get("user", ""), "title": v.get("title", ""), "size": len(v.get("content", ""))}
                         for v in reversed(p.get("versions", []))]}


def version(page_id: str, ts: int) -> Dict[str, Any]:
    p = next((x for x in _load()["pages"] if x["id"] == page_id), None)
    v = next((v for v in (p or {}).get("versions", []) if v["ts"] == ts), None)
    if not v:
        raise WikiError("Version nicht gefunden.")
    return v


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    title = re.sub(r"[\x00-\x1f]", "", str(fields.get("title") or "")).strip()[:120]
    if not title:
        raise WikiError("Titel fehlt.")
    folder = "/".join(s.strip() for s in re.sub(r"[\x00-\x1f\\\\]", "", str(fields.get("folder") or "")).split("/") if s.strip())[:120]
    content = str(fields.get("content") or "").replace("\r\n", "\n")
    if len(content) > MAX_CONTENT:
        raise WikiError(f"Höchstens {MAX_CONTENT:,} Zeichen pro Seite.".replace(",", "."))
    return {"title": title, "folder": folder, "content": content}


def save(page_id: Optional[str], fields: Dict[str, Any], user: str = "", base: Optional[int] = None) -> Dict[str, Any]:
    clean = _clean(fields)
    now = int(time.time())
    with _lock:
        data = _load()
        if page_id:
            p = next((x for x in data["pages"] if x["id"] == page_id), None)
            if not p:
                raise WikiError("Seite nicht gefunden.")
            if base is not None and int(base) != int(p.get("updated", 0)):
                raise WikiError("Die Seite wurde inzwischen von jemand anderem geändert – bitte neu laden.")
            if any(p.get(k) != clean[k] for k in ("title", "folder", "content")):
                p.setdefault("versions", []).append({"ts": p.get("updated", now), "user": p.get("user", ""),
                                                      "title": p["title"], "content": p.get("content", "")})
                p["versions"] = p["versions"][-_max_versions():]
                p.update(clean, updated=now, user=user or "")
        else:
            if len(data["pages"]) >= MAX_PAGES:
                raise WikiError(f"Höchstens {MAX_PAGES} Seiten.")
            p = {"id": secrets.token_hex(5), **clean, "created": now, "updated": now, "user": user or "", "versions": []}
            data["pages"].append(p)
        _save(data)
    return page(p["id"])


def _max_versions() -> int:
    import retention
    return retention.get("wiki_versions")


def trim_versions() -> None:
    keep = _max_versions()
    with _lock:
        data = _load()
        if any(len(p.get("versions", [])) > keep for p in data["pages"]):
            for p in data["pages"]:
                p["versions"] = p.get("versions", [])[-keep:]
            _save(data)


def delete(page_id: str) -> Dict[str, Any]:
    with _lock:
        data = _load()
        data["pages"] = [p for p in data["pages"] if p["id"] != page_id]
        _save(data)
    return pages()


def all_pages() -> List[Dict[str, Any]]:
    """For the full-text search."""
    return _load()["pages"]


# ---------------------------------------------------------------------------
# Templates: built-in outlines plus every page in the folder "Vorlagen"
# Placeholders {{titel}}, {{datum}} and {{benutzer}} are filled in when a page is created.
# ---------------------------------------------------------------------------
TEMPLATE_FOLDER = "Vorlagen"
BUILTIN_TEMPLATES = [
    {"id": "builtin:geraet", "title": "Gerät", "folder": "Geräte", "description": "Hardware mit Daten, Zugang, Konfiguration und Wartung",
     "content": "Stand: {{datum}} · {{benutzer}}\n\n## Überblick\n\n| | |\n|---|---|\n| Hersteller / Modell | |\n| Seriennummer | |\n"
                "| Standort | |\n| IP-Adresse | |\n| MAC-Adresse | |\n| Betriebssystem | |\n\n## Zugang\n\n- Verwaltung: \n- Anmeldung: (Zugangsdaten nicht hier, sondern im Passwort-Manager)\n\n"
                "## Konfiguration\n\n## Wartung\n\n- [ ] Firmware prüfen\n- [ ] Backup der Konfiguration\n\n## Verlauf\n\n- {{datum}}: Seite angelegt\n"},
    {"id": "builtin:stoerung", "title": "Störungsbericht", "folder": "Störungen", "description": "Ablauf, Ursache und Maßnahmen einer Störung",
     "content": "Datum: {{datum}} · Bericht: {{benutzer}}\n\n## Zusammenfassung\n\nWas ist passiert, in einem Satz.\n\n## Auswirkung\n\n- Betroffene Dienste: \n- Dauer: \n\n"
                "## Zeitlinie\n\n| Zeit | Ereignis |\n|---|---|\n| | Störung bemerkt |\n| | Ursache gefunden |\n| | Behoben |\n\n"
                "## Ursache\n\n## Lösung\n\n## Maßnahmen\n\n- [ ] \n"},
    {"id": "builtin:anleitung", "title": "Anleitung", "folder": "Anleitungen", "description": "Ziel, Voraussetzungen, Schritte, Prüfung",
     "content": "## Ziel\n\n## Voraussetzungen\n\n- \n\n## Schritte\n\n1. \n2. \n3. \n\n## Prüfen\n\n```\n\n```\n\n## Rückgängig machen\n\n"},
    {"id": "builtin:dienst", "title": "Dienst", "folder": "Dienste", "description": "Anwendung mit Installation, Backup und Updates",
     "content": "## Zweck\n\n## Adresse & Ports\n\n- Web: \n- Ports: \n\n## Installation\n\n## Konfiguration\n\n- Daten liegen in: \n\n"
                "## Backup & Wiederherstellung\n\n## Updates\n\n## Bekannte Probleme\n"},
    {"id": "builtin:entscheidung", "title": "Entscheidung", "folder": "Entscheidungen", "description": "Warum etwas so gelöst wurde (Kontext, Alternativen, Folgen)",
     "content": "Datum: {{datum}} · {{benutzer}} · Status: entschieden\n\n## Kontext\n\n## Entscheidung\n\n## Alternativen\n\n| Möglichkeit | Vorteile | Nachteile |\n|---|---|---|\n| | | |\n\n## Folgen\n"},
    {"id": "builtin:netzwerk", "title": "Netzwerk", "folder": "Netzwerk", "description": "Subnetze, VLANs, wichtige Adressen",
     "content": "## Subnetze\n\n| Netz | VLAN | Zweck | Gateway | DHCP |\n|---|---|---|---|---|\n| | | | | |\n\n## Wichtige Adressen\n\n| Adresse | Gerät |\n|---|---|\n| | |\n\n"
                "## DNS\n\n## Firewall-Regeln\n"},
]


def templates() -> Dict[str, Any]:
    own = [{"id": p["id"], "title": p["title"], "folder": "", "description": "Eigene Vorlage", "content": p.get("content", ""), "own": True}
           for p in _load()["pages"] if (p.get("folder") or "").split("/")[0] == TEMPLATE_FOLDER]
    own.sort(key=lambda t: t["title"].lower())
    return {"templates": [{**t, "own": False} for t in BUILTIN_TEMPLATES] + own, "folder": TEMPLATE_FOLDER}
