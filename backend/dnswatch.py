"""
DNS monitoring (System -> Netzwerk -> DNS-Überwachung): own DNS records are queried every
15 minutes. A changed answer, a failed lookup or an answer that doesn't match the expected
value becomes an event (and so reaches the e-mail notifications); failures and mismatches show
up in the status summary.
"""

import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import nettools
import settings_store

logger = logging.getLogger("pi_dashboard.dnswatch")

KEY = "dns_watch"
STATE_KEY = "dns_watch_state"
EVENTS_KEY = "dns_watch_events"
CHECK_INTERVAL = 900
MAX_ITEMS = 50
_lock = threading.Lock()
_running = {"active": False}


class DnsWatchError(Exception):
    pass


def _items() -> List[Dict[str, Any]]:
    return list(settings_store.get(KEY, []) or [])


def _state() -> Dict[str, Any]:
    s = settings_store.get(STATE_KEY) or {}
    return s if isinstance(s, dict) else {}


def _values(answers: List[Dict[str, Any]], rtype: str) -> List[str]:
    """The answer values of the asked type, normalised for comparing (CNAME chains are kept)."""
    vals = [str(a["value"]).rstrip(".").lower() for a in answers if a["type"] in (rtype, "CNAME")]
    return sorted(set(vals))


def _expected(text: str) -> List[str]:
    return sorted({v.strip().rstrip(".").lower() for v in str(text or "").replace(";", ",").split(",") if v.strip()})


def _status(item: Dict[str, Any], st: Dict[str, Any]) -> str:
    if not st.get("checked"):
        return "unknown"
    if st.get("error"):
        return "error"
    exp = _expected(item.get("expected", ""))
    if exp and not set(exp) <= set(st.get("values", [])):
        return "mismatch"
    return "ok"


def overview() -> Dict[str, Any]:
    st = _state()
    items = []
    for it in _items():
        s = st.get(it["id"], {})
        items.append({**it, "values": s.get("values", []), "checked": s.get("checked", 0), "ms": s.get("ms"),
                      "error": s.get("error", ""), "changed": s.get("changed", 0), "previous": s.get("previous", []),
                      "status": _status(it, s)})
    order = {"error": 0, "mismatch": 1, "unknown": 2, "ok": 3}
    items.sort(key=lambda i: (order[i["status"]], i["name"]))
    events = list(reversed(settings_store.get(EVENTS_KEY, []) or []))[:50]
    return {"items": items, "events": events, "types": list(nettools.TYPES), "servers": list(nettools.SERVERS),
            "running": _running["active"], "problems": sum(1 for i in items if i["status"] in ("error", "mismatch"))}


def alerts() -> List[Dict[str, Any]]:
    out = []
    for i in overview()["items"]:
        if i["status"] == "error":
            out.append({"level": "warning", "text": f"DNS {i['name']} ({i['type']}): {i['error']}"})
        elif i["status"] == "mismatch":
            out.append({"level": "warning", "text": f"DNS {i['name']} ({i['type']}) zeigt nicht auf {i['expected']}"})
    return out


def save(item_id: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    name = str(fields.get("name") or "").strip().rstrip(".").lower()
    if not name or not nettools.NAME_RE.match(name):
        raise DnsWatchError("Ungültiger Name – z.B. nas.example.de")
    rtype = str(fields.get("type") or "A").upper()
    if rtype not in nettools.TYPES or rtype == "PTR":
        raise DnsWatchError("Unbekannter Eintragstyp.")
    server = str(fields.get("server") or "system").strip()
    if server not in nettools.SERVERS:
        try:
            import ipaddress
            server = str(ipaddress.ip_address(server))
        except ValueError:
            raise DnsWatchError("DNS-Server: system, cloudflare, google, quad9 oder eine IP-Adresse.")
    clean = {"name": name, "type": rtype, "server": server, "expected": ", ".join(_expected(fields.get("expected", ""))),
             "note": str(fields.get("note") or "").strip()[:200]}
    with _lock:
        items = _items()
        if item_id:
            it = next((x for x in items if x["id"] == item_id), None)
            if not it:
                raise DnsWatchError("Eintrag nicht gefunden.")
            it.update(clean)
        else:
            if len(items) >= MAX_ITEMS:
                raise DnsWatchError(f"Höchstens {MAX_ITEMS} Einträge.")
            it = {"id": secrets.token_hex(5), **clean, "created": int(time.time()), "user": user}
            items.append(it)
        settings_store.set(KEY, items)
        st = _state()
        st.pop(it["id"], None)    # changed query: start fresh, no "changed" event for the edit itself
        settings_store.set(STATE_KEY, st)
    threading.Thread(target=check_all, args=([it["id"]],), daemon=True).start()
    return overview()


def delete(item_id: str) -> Dict[str, Any]:
    with _lock:
        settings_store.set(KEY, [i for i in _items() if i["id"] != item_id])
        st = _state()
        st.pop(item_id, None)
        settings_store.set(STATE_KEY, st)
    return overview()


def _event(level: str, title: str, detail: str = "") -> Dict[str, Any]:
    return {"ts": int(time.time()), "level": level, "title": title, "detail": detail}


def check_all(ids: Optional[List[str]] = None) -> Dict[str, Any]:
    full = ids is None
    with _lock:
        if full and _running["active"]:
            return overview()
        if full:
            _running["active"] = True
    try:
        items = [i for i in _items() if ids is None or i["id"] in ids]
        results, new_events = {}, []
        old_state = _state()
        for it in items:
            prev = old_state.get(it["id"], {})
            res: Dict[str, Any] = {"checked": int(time.time()), "values": prev.get("values", []), "error": "",
                                   "changed": prev.get("changed", 0), "previous": prev.get("previous", [])}
            try:
                r = nettools.dns_lookup(it["name"], it["type"], it["server"])
                res["ms"] = r["ms"]
                if not r["ok"]:
                    res["error"] = r["rcode"]
                else:
                    vals = _values(r["answers"], it["type"])
                    if not vals:
                        res["error"] = "Keine Antwort für diesen Typ"
                    else:
                        if prev.get("checked") and not prev.get("error") and prev.get("values") and vals != prev["values"]:
                            res.update(changed=res["checked"], previous=prev["values"])
                            new_events.append(_event("warning", f"DNS {it['name']} ({it['type']}) geändert",
                                                     f"{', '.join(prev['values'])} → {', '.join(vals)}"))
                        res["values"] = vals
            except ValueError as e:
                res["error"] = str(e)
            was, now_status = _status(it, prev), _status(it, res)
            if prev.get("checked") and was != now_status:
                if now_status == "error":
                    new_events.append(_event("warning", f"DNS {it['name']} ({it['type']}) nicht auflösbar", res["error"]))
                elif now_status == "mismatch":
                    new_events.append(_event("warning", f"DNS {it['name']} ({it['type']}) zeigt nicht mehr auf {it['expected']}",
                                             ", ".join(res["values"])))
                elif now_status == "ok" and was in ("error", "mismatch"):
                    new_events.append(_event("good", f"DNS {it['name']} ({it['type']}) wieder in Ordnung", ", ".join(res["values"])))
            results[it["id"]] = res
        with _lock:
            st = _state()
            st.update(results)
            known = {i["id"] for i in _items()}
            settings_store.set(STATE_KEY, {k: v for k, v in st.items() if k in known})
            if new_events:
                settings_store.set(EVENTS_KEY, ((settings_store.get(EVENTS_KEY, []) or []) + new_events)[-300:])
            if ids is None:
                settings_store.set("dns_watch_checked", int(time.time()))
    finally:
        if full:
            _running["active"] = False
    return overview()


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def check_if_due() -> None:
    if _items() and time.time() - (settings_store.get("dns_watch_checked") or 0) >= CHECK_INTERVAL - 30:
        check_all()
