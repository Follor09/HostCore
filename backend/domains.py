"""
Domain expiry (Dokumentation -> Domains): own domains with registrar, name servers, status and the
expiry date, read via RDAP (the successor of WHOIS) once a day. The RDAP server of each TLD comes
from the IANA bootstrap file. Some registries (e.g. DENIC for .de) don't publish an expiry date -
for those the renewal date can be entered by hand.

Warnings (n days before expiry) show up in the status summary and the daily e-mail.
"""

import datetime
import logging
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

import settings_store

logger = logging.getLogger("pi_dashboard.domains")

KEY = "domains"
STATUS_KEY = "domain_status"
BOOTSTRAP_KEY = "rdap_bootstrap"
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
# ccTLDs with an RDAP service that is missing from the IANA bootstrap file
EXTRA_SERVERS = {"de": "https://rdap.denic.de", "ch": "https://rdap.nic.ch", "li": "https://rdap.nic.ch",
                 "io": "https://rdap.identitydigital.services/rdap", "ac": "https://rdap.identitydigital.services/rdap",
                 "sh": "https://rdap.identitydigital.services/rdap"}
CHECK_INTERVAL = 24 * 3600
MAX_DOMAINS = 100
NAME_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_lock = threading.Lock()
_running = {"active": False}
_queue: set = set()


class DomainError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _status() -> Dict[str, Any]:
    s = settings_store.get(STATUS_KEY) or {}
    return s if isinstance(s, dict) else {}


def _ascii(name: str) -> str:
    name = (name or "").strip().lower().rstrip(".")
    name = re.sub(r"^https?://", "", name).split("/")[0]
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        raise DomainError("Ungültiger Domainname.")
    if not NAME_RE.match(name):
        raise DomainError("Ungültiger Domainname – z.B. example.de")
    return name


def _date(value: Any) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    try:
        return datetime.date.fromisoformat(v).isoformat()
    except ValueError:
        raise DomainError("Datum als JJJJ-MM-TT.")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _view(d: Dict[str, Any], st: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
    r = st.get(d["name"], {})
    expiry = r.get("expiry") or ""
    source = "rdap" if expiry else ""
    if d.get("manual_expiry") and not expiry:
        expiry, source = d["manual_expiry"], "manual"
    days = (datetime.date.fromisoformat(expiry[:10]) - today).days if expiry else None
    warn = d.get("warn_days") or 30
    state = ("unknown" if days is None else "expired" if days < 0 else "critical" if days <= min(7, warn)
             else "warning" if days <= warn else "ok")
    return {**d, "unicode": _unicode(d["name"]), "expiry": expiry[:10], "expiry_source": source, "days": days, "state": state,
            "registrar": r.get("registrar", ""), "nameservers": r.get("nameservers", []), "status": r.get("status", []),
            "registered": r.get("registered", ""), "changed": r.get("changed", ""), "dnssec": r.get("dnssec"),
            "checked": r.get("checked", 0), "error": r.get("error", ""), "rdap_server": r.get("server", ""),
            "no_expiry": bool(r.get("checked") and not r.get("error") and not r.get("expiry"))}


def _unicode(name: str) -> str:
    try:
        return name.encode("ascii").decode("idna")
    except UnicodeError:
        return name


def overview() -> Dict[str, Any]:
    today = datetime.date.today()
    st = _status()
    items = [_view(d, st, today) for d in _items()]
    order = {"expired": 0, "critical": 1, "warning": 2, "unknown": 3, "ok": 4}
    items.sort(key=lambda i: (order[i["state"]], i["days"] if i["days"] is not None else 99999, i["name"]))
    return {"domains": items, "running": _running["active"], "checked": max((i["checked"] for i in items), default=0),
            "warnings": sum(1 for i in items if i["state"] in ("expired", "critical", "warning"))}


def _de(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if len(iso) >= 10 else iso


def alerts() -> List[Dict[str, Any]]:
    out = []
    for d in overview()["domains"]:
        if d["state"] == "expired":
            out.append({"level": "critical", "text": f"Domain {d['unicode']} ist seit {-d['days']} Tagen abgelaufen"})
        elif d["state"] in ("critical", "warning"):
            out.append({"level": "critical" if d["state"] == "critical" else "warning",
                        "text": f"Domain {d['unicode']} läuft in {d['days']} Tagen ab ({_de(d['expiry'])})"})
    return out


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def save(domain_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    name = _ascii(fields.get("name", ""))
    try:
        warn = int(fields.get("warn_days") or 30)
    except (TypeError, ValueError):
        raise DomainError("Vorwarnzeit: Anzahl Tage.")
    if not 1 <= warn <= 365:
        raise DomainError("Vorwarnzeit: 1 bis 365 Tage.")
    clean = {"name": name, "warn_days": warn, "manual_expiry": _date(fields.get("manual_expiry")),
             "registrar_note": str(fields.get("registrar_note") or "").strip()[:80],
             "notes": str(fields.get("notes") or "").strip()[:500]}
    is_new = False
    with _lock:
        items = _items()
        if any(d["name"] == name and d["id"] != domain_id for d in items):
            raise DomainError("Diese Domain ist schon eingetragen.")
        if domain_id:
            d = next((x for x in items if x["id"] == domain_id), None)
            if not d:
                raise DomainError("Domain nicht gefunden.")
            is_new = d["name"] != name
            d.update(clean, updated=int(time.time()))
        else:
            if len(items) >= MAX_DOMAINS:
                raise DomainError(f"Höchstens {MAX_DOMAINS} Domains.")
            items.append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user})
            is_new = True
        settings_store.set(KEY, items)
    if is_new:
        threading.Thread(target=check_all, args=([name],), daemon=True).start()
    return overview()


def delete(domain_id: str) -> Dict[str, Any]:
    with _lock:
        items = _items()
        gone = next((d for d in items if d["id"] == domain_id), None)
        settings_store.set(KEY, [d for d in items if d["id"] != domain_id])
        if gone:
            st = _status()
            st.pop(gone["name"], None)
            settings_store.set(STATUS_KEY, st)
    return overview()


# ---------------------------------------------------------------------------
# RDAP
# ---------------------------------------------------------------------------
def _bootstrap(client: httpx.Client) -> Dict[str, str]:
    cached = settings_store.get(BOOTSTRAP_KEY) or {}
    if cached.get("ts", 0) > time.time() - 7 * 86400 and cached.get("map"):
        return cached["map"]
    try:
        data = client.get(BOOTSTRAP_URL).json()
        mapping = {}
        for tlds, urls in data.get("services", []):
            url = next((u for u in urls if u.startswith("https://")), urls[0] if urls else "")
            for tld in tlds:
                mapping[tld.lower()] = url.rstrip("/")
        settings_store.set(BOOTSTRAP_KEY, {"ts": int(time.time()), "map": mapping})
        return mapping
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"RDAP bootstrap failed: {e}")
        return cached.get("map", {})


def _vcard_name(entity: Dict[str, Any]) -> str:
    for item in (entity.get("vcardArray") or [None, []])[1]:
        if isinstance(item, list) and len(item) >= 4 and item[0] in ("fn", "org") and item[3]:
            return str(item[3])
    return entity.get("handle", "")


def lookup(name: str, client: httpx.Client, servers: Dict[str, str]) -> Dict[str, Any]:
    tld = name.rsplit(".", 1)[-1]
    base = servers.get(tld) or EXTRA_SERVERS.get(tld) or "https://rdap.org"
    res = client.get(f"{base}/domain/{name}", headers={"Accept": "application/rdap+json, application/json"})
    if res.status_code == 404:
        raise DomainError("Bei der Registry nicht gefunden (nicht registriert?)")
    if res.status_code == 429:
        raise DomainError("Registry-Limit erreicht, morgen erneut")
    if res.status_code >= 400:
        raise DomainError(f"RDAP-Fehler {res.status_code}")
    data = res.json()
    events = {e.get("eventAction"): e.get("eventDate", "") for e in data.get("events", []) if isinstance(e, dict)}
    registrar = ""
    for ent in data.get("entities", []) or []:
        if "registrar" in (ent.get("roles") or []):
            registrar = _vcard_name(ent)
            break
    ns = sorted({(n.get("ldhName") or "").lower().rstrip(".") for n in data.get("nameservers", []) or [] if n.get("ldhName")})
    sec = data.get("secureDNS") or {}
    return {"expiry": (events.get("expiration") or "")[:10], "registered": (events.get("registration") or "")[:10],
            "changed": (events.get("last changed") or events.get("last update of RDAP database") or "")[:10],
            "registrar": registrar, "nameservers": ns, "status": [str(s) for s in data.get("status", [])][:10],
            "dnssec": sec.get("delegationSigned") if isinstance(sec, dict) and "delegationSigned" in sec else None,
            "server": base}


def check_all(names: Optional[List[str]] = None) -> Dict[str, Any]:
    with _lock:
        if _running["active"]:
            _queue.update(names or [d["name"] for d in _items()])  # picked up when the running check ends
            return overview()
        _running["active"] = True
    return _check(names)


def _check(names: Optional[List[str]]) -> Dict[str, Any]:
    try:
        targets = list(names or [d["name"] for d in _items()])
        _lookup(targets, names is None)
        while True:
            with _lock:
                more = sorted(_queue - set(targets))
                _queue.clear()
            if not more:
                break
            targets += more
            _lookup(more, False)
    finally:
        _running["active"] = False
    return overview()


def _lookup(targets: List[str], full: bool) -> None:
    results = {}
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "PiCore-Dashboard"}) as client:
        servers = _bootstrap(client)
        for name in targets:
            try:
                results[name] = {**lookup(name, client, servers), "checked": int(time.time()), "error": ""}
            except (DomainError, httpx.HTTPError, ValueError) as e:
                old = _status().get(name, {})
                msg = str(e) if isinstance(e, DomainError) else f"Nicht erreichbar ({type(e).__name__})"
                results[name] = {**old, "checked": int(time.time()), "error": msg}
            time.sleep(0.5)  # be gentle with the registries
    with _lock:
        st = _status()
        st.update(results)
        known = {d["name"] for d in _items()}
        settings_store.set(STATUS_KEY, {k: v for k, v in st.items() if k in known})
        if full:
            settings_store.set("domain_checked", int(time.time()))


def start_check() -> Dict[str, Any]:
    with _lock:
        if not _running["active"]:
            _running["active"] = True
            threading.Thread(target=_check, args=(None,), daemon=True).start()
    return overview()


def check_if_due() -> None:
    if _items() and time.time() - (settings_store.get("domain_checked") or 0) > CHECK_INTERVAL:
        check_all()
