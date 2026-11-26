"""
Remote servers for the main dashboard: agents (see agent.py) that the dashboard forwards
API calls, live data and the terminal to. Stored in settings.json - tokens never leave
the server again after they were entered.
"""

import asyncio
import hashlib
import ipaddress
import re
import secrets
import socket
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

import settings_store

COOKIE = "picore_host"
_status: Dict[str, Dict[str, Any]] = {}

# Never forwarded: logins, users/permissions, the remote list itself and things that
# only make sense for the main dashboard. Wake-on-LAN devices are one list for all servers
# (each says which server sends its packet), so they stay here too.
LOCAL_ONLY = re.compile(r"^/api/(service-catalog|nis2|policies|suppliers|internal-audits|vm-migration/|sudo-log/fleet|auth/|me$|users|groups|security|versions|permissions|remotes|webhook/|netbox/|boards|calendar|exchange|x/|search|fleet|wiki|costs|stack-templates|incidents|api-docs|about$|changelog$|links$|notes$|kanban|reminders|docs/|terminal/(ticket|share-users)$"
                        r"|settings/(public-view|logout-all|logout-others|account|links.*|language|status-page|netbox.*|mcp.*|webhooks.*|email.*)$|mcp/|files/shares?(/|$)|public/|offsite/(fleet|copy)(/|$)"
                        r"|wol(/|$)|settings/wol(/|$)|agent/wol$|tools/lan/[^/]+/wol$)")


class RemoteError(Exception):
    pass


class UntrustedCert(RemoteError):
    """The agent answers with https and a certificate no authority vouches for (./agent.sh tls on).
    It is trusted only after the user compared the fingerprint with the one the agent shows."""

    def __init__(self, fingerprint: str, pem: str):
        super().__init__(f"Unbekanntes Zertifikat – Fingerabdruck mit ./agent.sh tls auf dem Server vergleichen: {fingerprint}")
        self.fingerprint, self.pem = fingerprint, pem


def cert_fingerprint(pem: str) -> str:
    h = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


_ctx_cache: Dict[str, ssl.SSLContext] = {}


def ssl_context(pem: str) -> ssl.SSLContext:
    """Trust exactly this certificate (pinning): no certificate authorities, no host name check -
    the agent is identified by its certificate, whatever address it is reached at."""
    ctx = _ctx_cache.get(pem)
    if ctx is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=pem)
        _ctx_cache[pem] = ctx
    return ctx


def verify_for(r: Dict[str, Any]):
    """httpx "verify" for a remote: its pinned certificate, else the system's authorities."""
    return ssl_context(r["cert"]) if r.get("cert") else True


def ws_ssl(r: Dict[str, Any]):
    """ssl argument for a WebSocket to the agent (None for ws://)."""
    if not r["url"].startswith("https://"):
        return None
    return ssl_context(r["cert"]) if r.get("cert") else ssl.create_default_context()


def http_client(r: Dict[str, Any], timeout: float = 20.0) -> httpx.AsyncClient:
    """A client for calls to this remote (with its pinned certificate, if any)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout), verify=verify_for(r))


async def with_client(fn, r: Dict[str, Any], timeout: float = 20.0):
    """await fn(client, r) with a client of its own for this remote (its pinned certificate)."""
    async with http_client(r, timeout) as client:
        return await fn(client, r)


def _fetch_cert(url: str) -> str:
    p = urlparse(url)
    return ssl.get_server_certificate((p.hostname, p.port or 443), timeout=5)


def _all() -> List[Dict[str, Any]]:
    return list(settings_store.get("remotes", []) or [])


def all_remotes() -> List[Dict[str, Any]]:
    """All configured remote servers (with tokens - for server-side use only)."""
    return _all()


def status_of(remote_id: str) -> Dict[str, Any]:
    """Last known status: {"online": bool|None, "info": ..., "error": ..., "checked": ts}."""
    return dict(_status.get(remote_id, {}))


def get(remote_id: str) -> Optional[Dict[str, Any]]:
    return next((r for r in _all() if r["id"] == remote_id), None)


# Networks where plain http stays inside: LAN, loopback, link-local, and the 100.64.0.0/10
# range of VPNs like Tailscale / NetBird (CGNAT), IPv6 unique-local
_PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                                                   "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10")]
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".localdomain", ".fritz.box", ".netbird.cloud", ".ts.net")
_risk_cache: Dict[str, tuple] = {}
INSECURE_HINT = ("Unverschlüsselt über das Internet: Token, Terminal und Dateien sind unterwegs mitlesbar. "
                 "Den Agent über ein VPN (WireGuard, NetBird, Tailscale) oder per https hinter einem Reverse-Proxy anbinden.")


def _is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in n for n in _PRIVATE_NETS)


def transport_risk(url: str, resolve: bool = True) -> bool:
    """True when the agent is reached with plain http at a public address - the token and
    everything else would cross the internet unencrypted. Names are resolved (cached 10 min);
    a name that does not resolve counts as no risk (nothing to warn about yet). With
    resolve=False only the cache is used (never blocks - for code on the event loop)."""
    p = urlparse(url)
    if p.scheme != "http" or not p.hostname:
        return False
    host = p.hostname.lower().rstrip(".")
    if host == "localhost" or "." not in host and ":" not in host or host.endswith(_LOCAL_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host)
        return not _is_private(host)
    except ValueError:
        pass
    hit = _risk_cache.get(host)
    if hit and (not resolve or time.time() - hit[0] < 600):
        return hit[1]
    if not resolve:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
        risky = bool(addrs) and not all(_is_private(a) for a in addrs)
    except (OSError, UnicodeError):
        risky = False
    _risk_cache[host] = (time.time(), risky)
    return risky


def public(r: Dict[str, Any]) -> Dict[str, Any]:
    st = _status.get(r["id"], {})
    return {"id": r["id"], "name": r["name"], "url": r["url"], "token_hint": f"…{r['token'][-4:]}",
            "online": st.get("online"), "info": st.get("info"), "error": st.get("error", ""), "checked": st.get("checked", 0),
            "insecure": transport_risk(r["url"], resolve=False),
            "tls": "pinned" if r.get("cert") else "ca" if r["url"].startswith("https://") else "",
            "fingerprint": cert_fingerprint(r["cert"]) if r.get("cert") else ""}


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url or re.fullmatch(r"(?i)https?:?/*", url):
        raise RemoteError("Adresse wie http://192.168.178.50:8000 angeben.")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    p = urlparse(url)
    if not p.hostname or p.path not in ("", "/") or p.query or p.username:
        raise RemoteError("Adresse wie http://192.168.178.50:8000 angeben.")
    if not p.port:
        url = f"{p.scheme}://{p.hostname}:8000" if p.scheme == "http" else f"{p.scheme}://{p.hostname}"
    return url


async def probe(url: str, token: str, cert: str = "") -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), verify=ssl_context(cert) if cert else True) as client:
            res = await client.get(f"{url}/api/agent/info", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) and url.startswith("https://"):
            if cert:
                msg = "Das Zertifikat des Agents hat sich geändert – unter Adresse neu bestätigen und den Fingerabdruck vergleichen."
                raise RemoteError(msg)
            try:
                pem = await asyncio.to_thread(_fetch_cert, url)
            except (OSError, ValueError):
                raise RemoteError("Zertifikat des Agents nicht lesbar.")
            raise UntrustedCert(cert_fingerprint(pem), pem)
        if "WRONG_VERSION_NUMBER" in str(e) or "record layer failure" in str(e):
            raise RemoteError("Der Agent spricht kein https – Adresse mit http:// angeben (oder auf dem Server ./agent.sh tls on).")
        if isinstance(e, httpx.RemoteProtocolError) and url.startswith("http://"):
            raise RemoteError("Keine Antwort über http – spricht der Agent https? Dann https:// angeben.")
        raise RemoteError(f"Nicht erreichbar ({type(e).__name__}) – Adresse, Port und Firewall prüfen.")
    if res.status_code in (401, 403):
        raise RemoteError("Token falsch.")
    if res.status_code == 404:
        raise RemoteError("Dort läuft kein HostCore-Agent (oder eine zu alte Version).")
    if res.status_code != 200:
        raise RemoteError(f"Antwort {res.status_code}.")
    info = res.json()
    if not info.get("agent"):
        raise RemoteError("Dort läuft ein normales Dashboard, kein Agent: auf dem Server ./install.sh --agent ausführen.")
    return info


async def _connect(url: str, token: str, trust: str = "", cert: str = "") -> tuple:
    """Probe the agent; an unknown certificate is taken only when its fingerprint is the one the
    user confirmed (trust). Returns (info, pinned certificate or "")."""
    try:
        return await probe(url, token, cert), cert
    except UntrustedCert as e:
        if trust and trust.replace(" ", "").upper() == e.fingerprint:
            return await probe(url, token, e.pem), e.pem
        raise


async def add(name: str, url: str, token: str, trust: str = "") -> Dict[str, Any]:
    name = (name or "").strip()[:40]
    if not name:
        raise RemoteError("Name angeben.")
    token = (token or "").strip()
    if len(token) < 32:
        raise RemoteError("Token fehlt oder ist zu kurz (auf dem Remote-Server: ./agent.sh token).")
    url = normalize_url(url)
    items = _all()
    if any(r["url"] == url for r in items):
        raise RemoteError("Diesen Server gibt es schon.")
    if len(items) >= 20:
        raise RemoteError("Maximal 20 Remote-Server.")
    info, cert = await _connect(url, token, trust)
    await asyncio.to_thread(transport_risk, url)
    rid = secrets.token_hex(4)
    items.append({"id": rid, "name": name, "url": url, "token": token, "added": int(time.time())})
    if cert:
        items[-1]["cert"] = cert
    settings_store.set("remotes", items)
    _status[rid] = {"online": True, "info": info, "checked": time.time()}
    return public(items[-1])


def update(remote_id: str, name: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    items = _all()
    r = next((x for x in items if x["id"] == remote_id), None)
    if not r:
        raise RemoteError("Nicht gefunden.")
    if name is not None and name.strip():
        r["name"] = name.strip()[:40]
    if token:
        if len(token.strip()) < 32:
            raise RemoteError("Token zu kurz.")
        r["token"] = token.strip()
    settings_store.set("remotes", items)
    return public(r)


async def change_address(remote_id: str, url: str, trust: str = "") -> Dict[str, Any]:
    """New address and/or certificate (agent switched to https, moved, new certificate)."""
    r = get(remote_id)
    if not r:
        raise RemoteError("Nicht gefunden.")
    url = normalize_url(url)
    if any(x["url"] == url and x["id"] != remote_id for x in _all()):
        raise RemoteError("Diesen Server gibt es schon.")
    info, cert = await _connect(url, r["token"], trust)
    await asyncio.to_thread(transport_risk, url)
    items = _all()
    for x in items:
        if x["id"] == remote_id:
            x["url"] = url
            if cert:
                x["cert"] = cert
            else:
                x.pop("cert", None)
            r = x
    settings_store.set("remotes", items)
    _status[remote_id] = {"online": True, "info": info, "checked": time.time()}
    return public(r)


def remove(remote_id: str) -> None:
    settings_store.set("remotes", [r for r in _all() if r["id"] != remote_id])
    _status.pop(remote_id, None)


async def refresh_status(max_age: float = 20) -> List[Dict[str, Any]]:
    now = time.time()

    async def one(r):
        st = _status.get(r["id"])
        if st and now - st.get("checked", 0) < max_age:
            return
        try:
            info = await probe(r["url"], r["token"], r.get("cert", ""))
            _status[r["id"]] = {"online": True, "info": info, "checked": now}
        except RemoteError as e:
            _status[r["id"]] = {"online": False, "info": (st or {}).get("info"), "error": str(e), "checked": now}

    await asyncio.gather(*(one(r) for r in _all()), *(asyncio.to_thread(transport_risk, r["url"]) for r in _all()))
    return [public(r) for r in _all()]


def mark_offline(remote_id: str, error: str) -> None:
    """A forwarded call failed: show it as offline right away (next status check may revive it)."""
    st = _status.get(remote_id, {})
    _status[remote_id] = {"online": False, "info": st.get("info"), "error": error, "checked": time.time()}


def selected(cookie_value: Optional[str]) -> Optional[Dict[str, Any]]:
    return get(cookie_value) if cookie_value else None
