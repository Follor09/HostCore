"""
Homelab documentation (page /docs), stored in settings.json and shared by every device:

- inventory: hardware with purchase date, price, warranty and a link to a LAN device
- journal:   operations log ("what did I change and why"), also shown as events
- runbooks:  step-by-step procedures that are worked through as checklists; every run is kept
- service docs ("Steckbriefe"): purpose, address, ports, data location, docs link per service
"""

import calendar
import datetime
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

_lock = threading.Lock()
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DocsError(Exception):
    pass


def _text(value: Any, label: str, max_len: int, required: bool = False) -> str:
    v = str(value or "").strip()
    if required and not v:
        raise DocsError(f"{label} fehlt.")
    if len(v) > max_len:
        raise DocsError(f"{label}: höchstens {max_len} Zeichen.")
    return v


def _date(value: Any, label: str) -> str:
    v = str(value or "").strip()
    if v and not DATE_RE.match(v):
        raise DocsError(f"{label}: ungültiges Datum.")
    return v


def _update_list(key: str, fn):
    with _lock:
        items = list(settings_store.get(key, []) or [])
        result = fn(items)
        settings_store.set(key, items)
        return result


def _remove(key: str, item_id: str) -> None:
    with _lock:
        settings_store.set(key, [i for i in settings_store.get(key, []) or [] if i["id"] != item_id])


def _find(items: List[Dict[str, Any]], item_id: str, what: str) -> Dict[str, Any]:
    it = next((i for i in items if i["id"] == item_id), None)
    if not it:
        raise DocsError(f"{what} nicht gefunden.")
    return it


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
INVENTORY_KEY = "inventory"
INVENTORY_CATEGORIES = ["Raspberry Pi", "Server & PC", "Speicher", "Netzwerk", "Stromversorgung", "Zubehör", "Sonstiges"]
WARRANTY_SOON_DAYS = 60


def _clean_inventory(f: Dict[str, Any]) -> Dict[str, Any]:
    category = str(f.get("category") or "Sonstiges")
    if category not in INVENTORY_CATEGORIES:
        raise DocsError("Unbekannte Kategorie.")
    price = f.get("price")
    if price in (None, ""):
        price = None
    else:
        try:
            price = round(float(str(price).replace(",", ".")), 2)
        except ValueError:
            raise DocsError("Preis: bitte eine Zahl.")
        if not 0 <= price <= 1_000_000:
            raise DocsError("Preis: 0–1 000 000.")
    mac = str(f.get("mac") or "").strip().lower()
    if mac and not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", mac):
        raise DocsError("MAC-Adresse: Format aa:bb:cc:dd:ee:ff.")
    return {
        "name": _text(f.get("name"), "Name", 80, True), "category": category,
        "manufacturer": _text(f.get("manufacturer"), "Hersteller", 60), "model": _text(f.get("model"), "Modell", 80),
        "serial": _text(f.get("serial"), "Seriennummer", 80), "location": _text(f.get("location"), "Standort", 60),
        "purchased": _date(f.get("purchased"), "Kaufdatum"), "warranty_until": _date(f.get("warranty_until"), "Garantie bis"),
        "price": price, "mac": mac, "notes": _text(f.get("notes"), "Notizen", 1000),
    }


def inventory() -> Dict[str, Any]:
    today = datetime.date.today()
    devices = settings_store.get("lan_devices", {}) or {}
    items = []
    for it in settings_store.get(INVENTORY_KEY, []) or []:
        warranty = ""
        days_left = None
        if it.get("warranty_until"):
            days_left = (datetime.date.fromisoformat(it["warranty_until"]) - today).days
            warranty = "expired" if days_left < 0 else "soon" if days_left <= WARRANTY_SOON_DAYS else "ok"
        dev = devices.get(it.get("mac") or "") if it.get("mac") else None
        items.append({**it, "warranty": warranty, "warranty_days": days_left,
                      "device": {"ip": dev.get("ip"), "online": dev.get("online"), "last_seen": dev.get("last_seen"),
                                 "name": dev.get("alias") or dev.get("hostname")} if dev else None})
    items.sort(key=lambda i: (INVENTORY_CATEGORIES.index(i["category"]), i["name"].lower()))
    total = sum(i["price"] or 0 for i in items)
    return {"items": items, "categories": INVENTORY_CATEGORIES, "total_price": round(total, 2),
            "warranty_soon": sum(1 for i in items if i["warranty"] == "soon"),
            "lan_devices": sorted(({"mac": m, "name": d.get("alias") or d.get("hostname") or d.get("ip") or m, "ip": d.get("ip")}
                                   for m, d in devices.items()), key=lambda d: d["name"].lower())}


def inventory_save(item_id: Optional[str], fields: Dict[str, Any]) -> Dict[str, Any]:
    clean = _clean_inventory(fields)

    def apply(items):
        if item_id:
            _find(items, item_id, "Eintrag").update(clean, updated=int(time.time()))
        else:
            if len(items) >= 500:
                raise DocsError("Höchstens 500 Einträge.")
            items.append({"id": secrets.token_hex(5), **clean, "created": int(time.time())})
    _update_list(INVENTORY_KEY, apply)
    return inventory()


def inventory_delete(item_id: str) -> Dict[str, Any]:
    _remove(INVENTORY_KEY, item_id)
    return inventory()


def warranty_alerts(days: int = 30) -> List[Dict[str, Any]]:
    return [i for i in inventory()["items"] if i["warranty_days"] is not None and 0 <= i["warranty_days"] <= days]


# ---------------------------------------------------------------------------
# Operations journal
# ---------------------------------------------------------------------------
JOURNAL_KEY = "journal"
JOURNAL_CATEGORIES = ["Änderung", "Wartung", "Störung", "Installation", "Notiz"]
JOURNAL_MAX = 2000


def journal(query: str = "", category: str = "", limit: int = 200) -> Dict[str, Any]:
    items = sorted(settings_store.get(JOURNAL_KEY, []) or [], key=lambda e: e["ts"], reverse=True)
    q = (query or "").strip().lower()
    if q:
        items = [e for e in items if q in e["title"].lower() or q in e.get("text", "").lower() or q in e.get("ref", "").lower()]
    if category:
        items = [e for e in items if e["category"] == category]
    return {"entries": items[:max(1, min(limit, 1000))], "total": len(items), "categories": JOURNAL_CATEGORIES}


def journal_save(entry_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    category = str(fields.get("category") or "Notiz")
    if category not in JOURNAL_CATEGORIES:
        raise DocsError("Unbekannte Kategorie.")
    try:
        ts = int(fields.get("ts") or time.time())
    except (TypeError, ValueError):
        raise DocsError("Ungültiger Zeitpunkt.")
    if ts > time.time() + 86400:
        raise DocsError("Der Zeitpunkt liegt in der Zukunft.")
    clean = {"title": _text(fields.get("title"), "Titel", 120, True), "text": _text(fields.get("text"), "Text", 4000),
             "category": category, "ref": _text(fields.get("ref"), "Bezug", 80), "ts": ts}

    def apply(items):
        if entry_id:
            _find(items, entry_id, "Eintrag").update(clean, edited=int(time.time()), edited_by=user)
        else:
            items.append({"id": secrets.token_hex(5), **clean, "user": user, "created": int(time.time())})
            del items[:-JOURNAL_MAX]
    _update_list(JOURNAL_KEY, apply)
    return journal()


def journal_delete(entry_id: str) -> Dict[str, Any]:
    _remove(JOURNAL_KEY, entry_id)
    return journal()


def journal_between(since: float, until: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(JOURNAL_KEY, []) or [] if since <= e["ts"] <= until]


# ---------------------------------------------------------------------------
# Runbooks
# ---------------------------------------------------------------------------
RUNBOOKS_KEY = "runbooks"
RUNS_KEPT = 30


def _clean_runbook(f: Dict[str, Any], old_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw = f.get("steps") or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    steps = []
    by_text = {s["text"]: s["id"] for s in old_steps}
    for s in raw:
        text = (s.get("text") if isinstance(s, dict) else str(s)).strip()
        if not text:
            continue
        if len(text) > 300:
            raise DocsError("Ein Schritt hat höchstens 300 Zeichen.")
        sid = (s.get("id") if isinstance(s, dict) else None) or by_text.get(text) or secrets.token_hex(4)
        steps.append({"id": sid, "text": text})
    if not steps:
        raise DocsError("Mindestens ein Schritt.")
    if len(steps) > 100:
        raise DocsError("Höchstens 100 Schritte.")
    return {"title": _text(f.get("title"), "Titel", 100, True), "description": _text(f.get("description"), "Beschreibung", 1000),
            "steps": steps}


def runbooks() -> Dict[str, Any]:
    items = sorted(settings_store.get(RUNBOOKS_KEY, []) or [], key=lambda r: r["title"].lower())
    for r in items:
        done = [x for x in r.get("runs", []) if x["status"] == "done"]
        r["last_done"] = max((x["finished"] for x in done), default=None)
        r["running"] = next((x for x in r.get("runs", []) if x["status"] == "running"), None)
    return {"runbooks": items}


def runbook_save(runbook_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    def apply(items):
        if runbook_id:
            rb = _find(items, runbook_id, "Runbook")
            rb.update(_clean_runbook(fields, rb["steps"]), updated=int(time.time()), updated_by=user)
        else:
            if len(items) >= 100:
                raise DocsError("Höchstens 100 Runbooks.")
            items.append({"id": secrets.token_hex(5), **_clean_runbook(fields, []), "runs": [], "created": int(time.time()), "user": user})
    _update_list(RUNBOOKS_KEY, apply)
    return runbooks()


def runbook_delete(runbook_id: str) -> Dict[str, Any]:
    _remove(RUNBOOKS_KEY, runbook_id)
    return runbooks()


def run_action(runbook_id: str, action: str, user: str = "", step: str = "", checked: bool = True, note: str = "") -> Dict[str, Any]:
    """start | check (step, checked) | finish (note) | abort"""
    log_entry = None

    def apply(items):
        nonlocal log_entry
        rb = _find(items, runbook_id, "Runbook")
        runs = rb.setdefault("runs", [])
        cur = next((x for x in runs if x["status"] == "running"), None)
        now = int(time.time())
        if action == "start":
            if cur:
                raise DocsError("Für dieses Runbook läuft schon ein Durchlauf.")
            runs.append({"id": secrets.token_hex(4), "started": now, "user": user, "status": "running", "checked": {}, "finished": None})
        elif not cur:
            raise DocsError("Kein laufender Durchlauf.")
        elif action == "check":
            if step not in {s["id"] for s in rb["steps"]}:
                raise DocsError("Unbekannter Schritt.")
            if checked:
                cur["checked"][step] = {"ts": now, "user": user}
            else:
                cur["checked"].pop(step, None)
        elif action in ("finish", "abort"):
            cur.update(status="done" if action == "finish" else "aborted", finished=now, finished_by=user,
                       note=_text(note, "Notiz", 1000), total=len(rb["steps"]))
            if action == "finish":
                missing = len(rb["steps"]) - len([s for s in rb["steps"] if s["id"] in cur["checked"]])
                log_entry = {"title": f"Runbook ausgeführt: {rb['title']}", "category": "Wartung", "ref": rb["title"],
                             "text": (f"{missing} Schritt(e) übersprungen. " if missing else "") + (note or "").strip()}
        else:
            raise DocsError("Unbekannte Aktion.")
        del runs[:-RUNS_KEPT]
    _update_list(RUNBOOKS_KEY, apply)
    if log_entry:
        journal_save(None, log_entry, user)
    return runbooks()


# ---------------------------------------------------------------------------
# Service docs ("Steckbriefe")
# ---------------------------------------------------------------------------
SERVICEDOCS_KEY = "service_docs"
SERVICEDOC_FIELDS = [("purpose", "Zweck", 500), ("url", "Adresse", 300), ("ports", "Ports", 120), ("data", "Daten liegen in", 300),
                     ("backup", "Sicherung", 300), ("docs_url", "Dokumentation", 300), ("owner", "Zuständig", 80),
                     ("notes", "Notizen", 2000)]


def servicedocs() -> Dict[str, Any]:
    import statuspage
    docs = settings_store.get(SERVICEDOCS_KEY, {}) or {}
    try:
        available = [{"target": f"{a['kind']}:{a['id']}", "name": a["name"], "kind": a["kind"]} for a in statuspage.available()]
    except Exception:
        available = []
    known = {a["target"] for a in available}
    items = [{"target": t, **d, "present": t in known or t.startswith("custom:")} for t, d in docs.items()]
    items.sort(key=lambda d: d.get("name", "").lower())
    return {"docs": items, "available": available, "fields": [{"key": k, "label": l} for k, l, _ in SERVICEDOC_FIELDS]}


def servicedoc_save(target: str, fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    target = str(target or "").strip()
    if not target:
        target = f"custom:{secrets.token_hex(4)}"
    if not re.match(r"^(service|health|stack|custom):[\w.@-]{1,80}$", target):
        raise DocsError("Ungültiges Ziel.")
    clean = {"name": _text(fields.get("name"), "Name", 80, True)}
    for key, label, max_len in SERVICEDOC_FIELDS:
        clean[key] = _text(fields.get(key), label, max_len)
    for key in ("url", "docs_url"):
        if clean[key] and not re.match(r"^https?://", clean[key]):
            raise DocsError(f"{dict((k, l) for k, l, _ in SERVICEDOC_FIELDS)[key]}: muss mit http:// oder https:// beginnen.")
    with _lock:
        docs = dict(settings_store.get(SERVICEDOCS_KEY, {}) or {})
        if target not in docs and len(docs) >= 300:
            raise DocsError("Höchstens 300 Steckbriefe.")
        docs[target] = {**clean, "updated": int(time.time()), "updated_by": user}
        settings_store.set(SERVICEDOCS_KEY, docs)
    return servicedocs()


def servicedoc_delete(target: str) -> Dict[str, Any]:
    with _lock:
        docs = dict(settings_store.get(SERVICEDOCS_KEY, {}) or {})
        docs.pop(target, None)
        settings_store.set(SERVICEDOCS_KEY, docs)
    return servicedocs()


# ---------------------------------------------------------------------------
# Contracts & subscriptions: domains, servers, licenses ... with costs and notice periods
# ---------------------------------------------------------------------------
CONTRACTS_KEY = "contracts"
CONTRACT_CATEGORIES = ["Domain", "Server & Hosting", "Lizenz", "Abo", "Internet & Telefon", "Versicherung", "Sonstiges"]
CONTRACT_INTERVALS = {"monthly": "Monatlich", "quarterly": "Vierteljährlich", "yearly": "Jährlich", "once": "Einmalig"}
_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _money(value: Any, label: str) -> Optional[float]:
    v = str(value if value is not None else "").strip().replace(",", ".")
    if not v:
        return None
    try:
        n = round(float(v), 2)
    except ValueError:
        raise DocsError(f"{label}: ungültiger Betrag.")
    if not 0 <= n <= 1_000_000:
        raise DocsError(f"{label}: ungültiger Betrag.")
    return n


def _clean_contract(f: Dict[str, Any]) -> Dict[str, Any]:
    interval = str(f.get("interval") or "yearly")
    if interval not in CONTRACT_INTERVALS:
        raise DocsError("Unbekannter Zahlungsrhythmus.")
    category = str(f.get("category") or "Sonstiges")
    if category not in CONTRACT_CATEGORIES:
        category = "Sonstiges"
    try:
        notice = int(f.get("notice_days") or 0)
    except (TypeError, ValueError):
        raise DocsError("Kündigungsfrist: Anzahl Tage.")
    if not 0 <= notice <= 730:
        raise DocsError("Kündigungsfrist: 0 bis 730 Tage.")
    link = _text(f.get("link"), "Link", 300)
    if link and not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    return {"name": _text(f.get("name"), "Name", 80, True), "provider": _text(f.get("provider"), "Anbieter", 80),
            "category": category, "cost": _money(f.get("cost"), "Kosten"), "interval": interval,
            "start": _date(f.get("start"), "Beginn"), "end": _date(f.get("end"), "Laufzeitende / Verlängerung"),
            "notice_days": notice, "auto_renew": bool(f.get("auto_renew", True)),
            "link": link, "notes": _text(f.get("notes"), "Notizen", 1000)}


def _contract_view(c: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
    end = datetime.date.fromisoformat(c["end"]) if c.get("end") else None
    # Recurring contracts that renew themselves: the next renewal after today
    if end and c.get("auto_renew") and c["interval"] in _MONTHS:
        n = 0
        while end < today and n < 600:
            m = end.month - 1 + _MONTHS[c["interval"]]
            y, m = end.year + m // 12, m % 12 + 1
            end = datetime.date(y, m, min(end.day, calendar.monthrange(y, m)[1]))
            n += 1
    cancel_by = end - datetime.timedelta(days=c.get("notice_days") or 0) if end else None
    days_cancel = (cancel_by - today).days if cancel_by else None
    notice = c.get("notice_days") or 0
    state = ("ended" if end and end < today else "notice" if notice and days_cancel is not None and 0 <= days_cancel <= 30
             else "missed" if notice and days_cancel is not None and days_cancel < 0 and end and end >= today else "ok")
    cost = c.get("cost")
    monthly = None if cost is None or c["interval"] == "once" else cost / _MONTHS[c["interval"]]
    return {**c, "next_end": end.isoformat() if end else "", "cancel_by": cancel_by.isoformat() if cancel_by else "",
            "days_to_cancel": days_cancel, "state": state, "monthly": round(monthly, 2) if monthly is not None else None,
            "interval_label": CONTRACT_INTERVALS[c["interval"]]}


def contracts() -> Dict[str, Any]:
    today = datetime.date.today()
    items = [_contract_view(c, today) for c in settings_store.get(CONTRACTS_KEY, []) or []]
    items.sort(key=lambda c: (c["state"] == "ended", c["cancel_by"] or c["next_end"] or "9999", c["name"].lower()))
    active = [c for c in items if c["state"] != "ended"]
    monthly = round(sum(c["monthly"] or 0 for c in active), 2)
    return {"items": items, "categories": CONTRACT_CATEGORIES, "intervals": CONTRACT_INTERVALS,
            "monthly_total": monthly, "yearly_total": round(monthly * 12, 2),
            "notice_soon": sum(1 for c in items if c["state"] == "notice")}


def contract_save(contract_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean_contract(fields)

    def apply(items):
        if contract_id:
            it = _find(items, contract_id, "Vertrag")
            it.update(clean, updated=int(time.time()))
        else:
            if len(items) >= 300:
                raise DocsError("Höchstens 300 Verträge.")
            items.append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user})
    _update_list(CONTRACTS_KEY, apply)
    return contracts()


def contract_delete(contract_id: str) -> Dict[str, Any]:
    _remove(CONTRACTS_KEY, contract_id)
    return contracts()


# ---------------------------------------------------------------------------
# Command snippets: often used commands with a description and tags
# ---------------------------------------------------------------------------
SNIPPETS_KEY = "snippets"


def _clean_snippet(f: Dict[str, Any]) -> Dict[str, Any]:
    command = str(f.get("command") or "").replace("\r\n", "\n").strip("\n")
    if not command.strip():
        raise DocsError("Befehl fehlt.")
    if len(command) > 4000:
        raise DocsError("Befehl: höchstens 4000 Zeichen.")
    tags = f.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    tags = list(dict.fromkeys(str(x).strip().lower()[:20] for x in tags if str(x).strip()))[:8]
    return {"title": _text(f.get("title"), "Titel", 100, True), "command": command,
            "description": _text(f.get("description"), "Beschreibung", 500), "tags": tags}


def snippets() -> Dict[str, Any]:
    items = sorted(settings_store.get(SNIPPETS_KEY, []) or [], key=lambda s: s["title"].lower())
    return {"items": items, "tags": sorted({t for s in items for t in s.get("tags", [])})}


def snippet_save(snippet_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean_snippet(fields)

    def apply(items):
        if snippet_id:
            _find(items, snippet_id, "Befehl").update(clean, updated=int(time.time()))
        else:
            if len(items) >= 500:
                raise DocsError("Höchstens 500 Befehle.")
            items.append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user})
    _update_list(SNIPPETS_KEY, apply)
    return snippets()


def snippet_delete(snippet_id: str) -> Dict[str, Any]:
    _remove(SNIPPETS_KEY, snippet_id)
    return snippets()
