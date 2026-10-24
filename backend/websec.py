"""
Web security for running the dashboard on the internet (Einstellungen -> Sicherheit).

- Real client IP behind Cloudflare (Tunnel / proxy) or an own reverse proxy: CF-Connecting-IP
  or X-Forwarded-For are only believed from trusted proxy addresses. Login lockouts, the
  activity log and the network restriction below all use this address.
- Security headers on every response (CSP, frame protection, no sniffing, referrer policy,
  HSTS behind HTTPS) and no caching of API answers.
- Rate limit for requests from the internet without login (login page, share and exchange links,
  status page); the home network and VPN are not limited.
- Areas only from the home network / VPN: chosen permissions (terminal, users, firewall …)
  are refused for requests from the internet, whatever the account.
- Confirm again: chosen permissions need the password (and 2FA code) again before a
  changing request, valid for some minutes per session.
- Two-factor policy: required for administrators or everybody.
- New-login notice: a login from an address the account has not used yet becomes an event
  (and an e-mail, depending on the e-mail settings).
- Checklist "Öffentlich erreichbar": what is still missing for running on the internet.
"""

import hashlib
import ipaddress
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "websec"
EVENTS_KEY = "security_events"
PROXY_MODES = {"none": "Direkt (kein Proxy)", "cloudflare": "Cloudflare (Tunnel oder Proxy)", "proxy": "Eigener Reverse-Proxy"}
TWOFA_POLICIES = {"none": "Freiwillig", "admins": "Pflicht für Administratoren", "all": "Pflicht für alle"}
DEFAULT_TRUSTED = ["127.0.0.0/8", "::1/128", "172.16.0.0/12", "10.42.0.0/16"]   # local, Docker, k3s pods
DEFAULTS: Dict[str, Any] = {
    "proxy": "none", "trusted": DEFAULT_TRUSTED, "twofa": "none",
    "reauth": True, "reauth_minutes": 15,
    "reauth_perms": ["users.manage", "firewall.manage", "system.power", "updates.install", "apps.install"],
    "restrict": False, "restrict_perms": ["terminal", "users.manage", "firewall.manage", "system.power"],
    "internal_networks": [], "notify_new_login": True, "hsts": True, "anon_rate": 120, "password_reset": True,
}
# Home network / VPN: private ranges, CGNAT (NetBird, Tailscale), link-local, IPv6 ULA
INTERNAL = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                                                "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10")]
_elevated: Dict[str, float] = {}
_hits: Dict[str, List[float]] = {}
_lock = threading.Lock()
_cfg_cache: Dict[str, Any] = {"ts": 0.0, "cfg": None}


class WebsecError(Exception):
    pass


def config() -> Dict[str, Any]:
    """Settings, cached for a few seconds (asked on every request)."""
    now = time.time()
    if _cfg_cache["cfg"] is None or now - _cfg_cache["ts"] > 5:
        c = settings_store.get(KEY) or {}
        _cfg_cache.update(ts=now, cfg={**DEFAULTS, **(c if isinstance(c, dict) else {})})
    return _cfg_cache["cfg"]


def _networks(items: List[str]) -> List[Any]:
    out = []
    for n in items or []:
        try:
            out.append(ipaddress.ip_network(str(n).strip(), strict=False))
        except ValueError:
            continue
    return out


def _in(ip: str, nets: List[Any]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in nets)


# ---------------------------------------------------------------------------
# Client address and scheme
# ---------------------------------------------------------------------------
def _peer(conn: Any) -> str:
    return conn.client.host if getattr(conn, "client", None) else ""


def _trusted_peer(conn: Any) -> bool:
    c = config()
    return c["proxy"] != "none" and _in(_peer(conn), _networks(c["trusted"]))


def client_ip(conn: Any) -> str:
    """Request or WebSocket -> the visitor's address (proxy headers only from trusted proxies).
    uvicorn already applies X-Forwarded-For for proxies on the same host (127.0.0.1)."""
    peer = _peer(conn)
    if not _trusted_peer(conn):
        return peer or "unknown"
    h = conn.headers
    if config()["proxy"] == "cloudflare" and h.get("cf-connecting-ip"):
        cand = h["cf-connecting-ip"].strip()
    else:
        # The rightmost address that is not one of our proxies
        hops = [x.strip() for x in (h.get("x-forwarded-for") or "").split(",") if x.strip()]
        trusted = _networks(config()["trusted"])
        cand = next((x for x in reversed(hops) if not _in(x, trusted)), hops[0] if hops else peer)
    try:
        return str(ipaddress.ip_address(cand))
    except ValueError:
        return peer or "unknown"


def scheme(conn: Any) -> str:
    if _trusted_peer(conn):
        proto = (conn.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if proto in ("http", "https"):
            return proto
        if config()["proxy"] == "cloudflare" and conn.headers.get("cf-visitor", "").find('"https"') >= 0:
            return "https"
    return conn.url.scheme if hasattr(conn, "url") else "http"


def is_internal(ip: str) -> bool:
    return _in(ip, INTERNAL + _networks(config()["internal_networks"]))


# ---------------------------------------------------------------------------
# Restrictions
# ---------------------------------------------------------------------------
def blocked_from_outside(perm: Optional[str], ip: str) -> bool:
    c = config()
    return bool(c["restrict"] and perm and perm in c["restrict_perms"] and not is_internal(ip))


def needs_reauth(perm: Optional[str], method: str) -> bool:
    c = config()
    return bool(c["reauth"] and perm and perm in c["reauth_perms"] and method not in ("GET", "HEAD", "OPTIONS"))


def session_key(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:32]


def elevate(key: str) -> int:
    until = time.time() + max(1, int(config()["reauth_minutes"])) * 60
    with _lock:
        _elevated[key] = until
        for k in [k for k, v in _elevated.items() if v < time.time()]:
            _elevated.pop(k, None)
    return int(until)


def elevated_until(key: str) -> int:
    return int(_elevated.get(key, 0)) if _elevated.get(key, 0) > time.time() else 0


def twofa_required(group: str) -> bool:
    p = config()["twofa"]
    return p == "all" or (p == "admins" and group == "admin")


def rate_ok(ip: str, bucket: str = "anon") -> bool:
    """Sliding window per minute for requests without login."""
    limit = int(config()["anon_rate"] or 0)
    if limit <= 0:
        return True
    now = time.time()
    key = f"{bucket}|{ip}"
    with _lock:
        hits = [t for t in _hits.get(key, []) if now - t < 60]
        hits.append(now)
        _hits[key] = hits
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
                _hits.pop(k, None)
    return len(hits) <= limit


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
def csp(host: str) -> str:
    ws = f" ws://{host} wss://{host}" if host else ""
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https: http:; "
            f"connect-src 'self'{ws}; frame-src https: http:; frame-ancestors 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'")


def apply_headers(request: Any, response: Any) -> None:
    h = response.headers
    path = request.url.path
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    ctype = h.get("content-type", "")
    if ctype.startswith("text/html") and "content-security-policy" not in h:
        h["Content-Security-Policy"] = csp(request.headers.get("host", ""))
    if path.startswith("/api/") and "cache-control" not in h:
        h["Cache-Control"] = "no-store"
    if config()["hsts"] and scheme(request) == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def record_event(level: str, title: str, detail: str = "") -> None:
    events = (settings_store.get(EVENTS_KEY, []) or [])[-299:]
    events.append({"ts": int(time.time()), "level": level, "title": title, "detail": detail})
    settings_store.set(EVENTS_KEY, events)


def events_since(since: float) -> List[Dict[str, Any]]:
    return [e for e in settings_store.get(EVENTS_KEY, []) or [] if e["ts"] >= since]


def _net_of(ip: str) -> str:
    """Compare logins by network, not exact address (mobile networks change the last part)."""
    try:
        a = ipaddress.ip_address(ip)
        return str(ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False))
    except ValueError:
        return ip


def check_new_login(username: str, ip: str, known: List[str], agent: str) -> None:
    if not config()["notify_new_login"] or not known or is_internal(ip):
        return
    if _net_of(ip) in {_net_of(k) for k in known if k}:
        return
    country = ""
    try:
        import geoip
        country = geoip.lookup(ip)
    except Exception:
        pass
    browser = agent.split(")")[0].split("(")[-1][:80] if "(" in agent else agent[:80]
    record_event("warning", f"Anmeldung von neuer Adresse: {username}",
                 f"{ip}{f' ({country})' if country else ''}{f' · {browser}' if browser else ''}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def public_config() -> Dict[str, Any]:
    import auth
    perms = [{"id": p[0], "label": p[1]} for p in auth.PERMISSIONS]
    return {**config(), "proxy_modes": PROXY_MODES, "twofa_policies": TWOFA_POLICIES, "permissions": perms,
            "default_trusted": DEFAULT_TRUSTED}


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    import auth
    c = dict(config())
    if "proxy" in fields:
        if fields["proxy"] not in PROXY_MODES:
            raise WebsecError("Unbekannte Proxy-Art.")
        c["proxy"] = fields["proxy"]
    for key in ("trusted", "internal_networks"):
        if key in fields:
            items = fields[key] if isinstance(fields[key], list) else str(fields[key]).replace(",", "\n").splitlines()
            nets = []
            for n in items:
                n = str(n).strip()
                if not n:
                    continue
                try:
                    nets.append(str(ipaddress.ip_network(n, strict=False)))
                except ValueError:
                    raise WebsecError(f"Ungültiges Netz: {n} – z.B. 172.17.0.0/16 oder 192.168.1.10")
            c[key] = nets[:30]
    if "twofa" in fields:
        if fields["twofa"] not in TWOFA_POLICIES:
            raise WebsecError("Unbekannte Regel.")
        c["twofa"] = fields["twofa"]
    for key in ("reauth", "restrict", "notify_new_login", "hsts", "password_reset"):
        if key in fields:
            c[key] = bool(fields[key])
    if "reauth_minutes" in fields:
        c["reauth_minutes"] = max(1, min(120, int(fields["reauth_minutes"] or 15)))
    if "anon_rate" in fields:
        c["anon_rate"] = max(0, min(10000, int(fields["anon_rate"] or 0)))
    valid = set(auth.ALL_PERMISSIONS)
    for key in ("reauth_perms", "restrict_perms"):
        if key in fields:
            c[key] = [p for p in fields[key] or [] if p in valid]
    settings_store.set(KEY, {k: c[k] for k in DEFAULTS})
    _cfg_cache["cfg"] = None
    return public_config()


def checklist(request: Any) -> List[Dict[str, Any]]:
    """What is still missing for running on the internet (state: ok / warning / info)."""
    import auth
    c = config()
    out: List[Dict[str, Any]] = []

    def add(cid: str, state: str, title: str, detail: str) -> None:
        out.append({"id": cid, "state": state, "title": title, "detail": detail})

    https = scheme(request) == "https"
    add("https", "ok" if https else "warning", "Verschlüsselte Verbindung (HTTPS)",
        "Diese Seite kommt über HTTPS." if https else
        "Diese Seite kommt über HTTP. Öffentlich nur über HTTPS (Cloudflare oder Reverse-Proxy) – sonst gehen Passwörter im Klartext durchs Netz.")
    peer, ip = _peer(request), client_ip(request)
    if c["proxy"] == "none":
        add("proxy", "info" if is_internal(peer) else "ok", "Echte Besucher-Adresse",
            "Kein Proxy eingestellt. Läuft das Dashboard hinter Cloudflare oder einem Reverse-Proxy, hier die Proxy-Art wählen – sonst sehen Sperren und Protokoll nur die Adresse des Proxys.")
    else:
        seen = ip != peer
        add("proxy", "ok" if seen or not _trusted_peer(request) else "warning", "Echte Besucher-Adresse",
            f"Deine Adresse: {ip}" + ("" if seen else f" (der Proxy {peer} schickt keine Besucher-Adresse – Proxy-Einstellung prüfen)"))
    add("public-view", "warning" if auth.public_view() else "ok", "Ansehen ohne Login",
        "Eingeschaltet – im Internet sieht so jeder Systemdaten des Pi." if auth.public_view() else "aus")
    users = auth.list_users()
    admins = [u for u in users if u["group"] == "admin" and not u["disabled"]]
    no2fa = [u["username"] for u in admins if not u["totp"] and not u["passkeys"]]
    if c["twofa"] != "none":
        add("twofa", "ok", "Zwei-Faktor-Anmeldung", TWOFA_POLICIES[c["twofa"]])
    else:
        add("twofa", "warning" if no2fa else "ok", "Zwei-Faktor-Anmeldung",
            f"Freiwillig – ohne zweiten Faktor: {', '.join(no2fa)}. Für den öffentlichen Betrieb „Pflicht für Administratoren“ wählen."
            if no2fa else "Alle Administratoren haben sie eingerichtet.")
    others = [u["username"] for u in users if u["group"] != "admin" and not u["disabled"] and not u["totp"] and not u["passkeys"]]
    if others and c["twofa"] != "all":
        add("twofa-users", "info", "Weitere Konten ohne zweiten Faktor", ", ".join(others[:10]) + (" …" if len(others) > 10 else ""))
    add("reauth", "ok" if c["reauth"] else "warning", "Erneute Bestätigung",
        f"Kritische Aktionen verlangen das Passwort erneut (gilt {c['reauth_minutes']} Min.)." if c["reauth"]
        else "Aus – wer eine Sitzung übernimmt, kann sofort Benutzer, Firewall und Updates ändern.")
    add("restrict", "ok" if c["restrict"] else "info", "Kritische Bereiche nur aus Heimnetz/VPN",
        f"An – aus dem Internet gesperrt: {len(c['restrict_perms'])} Berechtigungen." if c["restrict"]
        else "Aus – Terminal, Benutzerverwaltung und Firewall sind auch aus dem Internet erreichbar (mit Login).")
    if c["password_reset"]:
        try:
            import notify
            n = notify.config()
            ready = bool(n.get("host") and n.get("sender") and n.get("dashboard_url"))
        except Exception:
            ready = False
        with_mail = sum(1 for u in users if u["email"] and not u["disabled"])
        add("reset", "ok" if ready else "info", "Passwort vergessen",
            f"per E-Mail-Link möglich für {with_mail} Konto/Konten mit Adresse" if ready
            else "eingeschaltet, aber ohne Mailserver oder Dashboard-Adresse (Einstellungen → E-Mail) geht keine Mail raus.")
    add("rate", "ok" if c["anon_rate"] else "warning", "Begrenzung ohne Login",
        f"Höchstens {c['anon_rate']} Anfragen pro Minute und Adresse aus dem Internet ohne Login." if c["anon_rate"] else "Aus.")
    try:
        import statuspage
        if statuspage.config().get("enabled"):
            add("status", "info", "Öffentliche Statusseite", "eingeschaltet – zeigt Dienste und Störungen ohne Login unter /status.")
    except Exception:
        pass
    try:
        import mcp_server
        if mcp_server.public().get("enabled"):
            add("mcp", "info", "MCP-Server", "eingeschaltet – /mcp ist mit OAuth geschützt; Freigaben für kritische Werkzeuge prüfen.")
    except Exception:
        pass
    return out
