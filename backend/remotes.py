"""
Remote servers for the main dashboard: agents (see agent.py) that the dashboard forwards
API calls, live data and the terminal to. Stored in settings.json - tokens never leave
the server again after they were entered.
"""

import asyncio
import ipaddress
import re
import secrets
import socket
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
            "insecure": transport_risk(r["url"], resolve=False)}


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


async def probe(url: str, token: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            res = await client.get(f"{url}/api/agent/info", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
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


async def add(name: str, url: str, token: str) -> Dict[str, Any]:
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
    info = await probe(url, token)
    await asyncio.to_thread(transport_risk, url)
    rid = secrets.token_hex(4)
    items.append({"id": rid, "name": name, "url": url, "token": token, "added": int(time.time())})
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
            info = await probe(r["url"], r["token"])
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
