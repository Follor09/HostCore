"""
Remote Pis for the main dashboard: agents (see agent.py) that the dashboard forwards
API calls, live data and the terminal to. Stored in settings.json - tokens never leave
the server again after they were entered.
"""

import re
import secrets
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

import settings_store

COOKIE = "picore_host"
_status: Dict[str, Dict[str, Any]] = {}

# Never forwarded: logins, users/permissions, the remote list itself and things that
# only make sense for the main dashboard
LOCAL_ONLY = re.compile(r"^/api/(auth/|me$|users|groups|permissions|remotes|webhook/|netbox/|boards|calendar|exchange|x/|search|fleet|wiki|costs|about$|changelog$|links$|notes$|kanban|reminders|docs/|terminal/ticket$"
                        r"|settings/(public-view|logout-all|logout-others|account|links.*|language|status-page|netbox.*|mcp.*|webhooks.*|email.*)$|mcp/|files/shares?(/|$)|public/)")


class RemoteError(Exception):
    pass


def _all() -> List[Dict[str, Any]]:
    return list(settings_store.get("remotes", []) or [])


def get(remote_id: str) -> Optional[Dict[str, Any]]:
    return next((r for r in _all() if r["id"] == remote_id), None)


def public(r: Dict[str, Any]) -> Dict[str, Any]:
    st = _status.get(r["id"], {})
    return {"id": r["id"], "name": r["name"], "url": r["url"], "token_hint": f"…{r['token'][-4:]}",
            "online": st.get("online"), "info": st.get("info"), "error": st.get("error", ""), "checked": st.get("checked", 0)}


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
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
        raise RemoteError("Dort läuft kein PiCore-Agent (oder eine zu alte Version).")
    if res.status_code != 200:
        raise RemoteError(f"Antwort {res.status_code}.")
    info = res.json()
    if not info.get("agent"):
        raise RemoteError("Dort läuft ein normales Dashboard, kein Agent: auf dem Pi ./install.sh --agent ausführen.")
    return info


async def add(name: str, url: str, token: str) -> Dict[str, Any]:
    name = (name or "").strip()[:40]
    if not name:
        raise RemoteError("Name angeben.")
    token = (token or "").strip()
    if len(token) < 32:
        raise RemoteError("Token fehlt oder ist zu kurz (auf dem Remote-Pi: ./agent.sh token).")
    url = normalize_url(url)
    items = _all()
    if any(r["url"] == url for r in items):
        raise RemoteError("Diesen Pi gibt es schon.")
    if len(items) >= 20:
        raise RemoteError("Maximal 20 Remote-Pis.")
    info = await probe(url, token)
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
    import asyncio
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

    await asyncio.gather(*(one(r) for r in _all()))
    return [public(r) for r in _all()]


def mark_offline(remote_id: str, error: str) -> None:
    """A forwarded call failed: show it as offline right away (next status check may revive it)."""
    st = _status.get(remote_id, {})
    _status[remote_id] = {"online": False, "info": st.get("info"), "error": error, "checked": time.time()}


def selected(cookie_value: Optional[str]) -> Optional[Dict[str, Any]]:
    return get(cookie_value) if cookie_value else None
