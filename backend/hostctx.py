"""
Which server a request is for: this dashboard or the remote server the browser switched to
(cookie), and the shared HTTP client that talks to remote agents. Used by app.py and by the
route modules in backend/routes/.
"""

from typing import Optional

import httpx
from fastapi.responses import JSONResponse

import agent
import remotes

HOST_HEADER = "x-picore-host"   # which server the page in the browser shows ("local" or a remote id)
HOST_CHANGED_CLOSE = 4409       # WebSocket close code: the page shows another server than the one selected now
AGENT = agent.enabled()
_proxy_client: Optional[httpx.AsyncClient] = None
_pinned_clients: dict = {}   # (remote id, certificate) -> client trusting exactly that certificate
_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=10.0)


def proxy(remote: Optional[dict] = None) -> httpx.AsyncClient:
    """The client for calls to remote agents. A remote with a pinned certificate (agent with
    https, ./agent.sh tls on) gets its own client that trusts only that certificate."""
    global _proxy_client
    if remote and remote.get("cert"):
        key = (remote["id"], remote["cert"])
        client = _pinned_clients.get(key)
        if client is None:
            for old in [k for k in _pinned_clients if k[0] == remote["id"]]:   # certificate changed
                _pinned_clients.pop(old, None)
            client = _pinned_clients[key] = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False,
                                                              verify=remotes.ssl_context(remote["cert"]))
        return client
    if _proxy_client is None:
        _proxy_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    return _proxy_client


def reset_proxy() -> None:
    """Forget the clients (tests: every TestClient request runs in its own event loop)."""
    global _proxy_client
    _proxy_client = None
    _pinned_clients.clear()


def remote_for(request_or_ws) -> Optional[dict]:
    """The remote server this browser has switched to (cookie), if any - never in agent mode."""
    if AGENT:
        return None
    return remotes.selected(request_or_ws.cookies.get(remotes.COOKIE))


def host_changed(request_or_ws, want: Optional[str]) -> Optional[str]:
    """The selection is one cookie for the whole browser, so a tab opened for server A still talks after
    another tab switched to B. Pages say which server they show ("local" or a remote id); returns the
    name of the server selected now if that is a different one, else None."""
    if want is None or AGENT:
        return None
    remote = remote_for(request_or_ws)
    if want == (remote["id"] if remote else "local"):
        return None
    return remote["name"] if remote else "dieser Server"


def host_changed_response(now: str) -> JSONResponse:
    return JSONResponse({"detail": f"Inzwischen ist ein anderer Server ausgewählt ({now}). Die Seite wird neu geladen – "
                                   "danach bitte noch einmal versuchen.", "host_changed": True}, status_code=409)


def agent_headers(remote: dict, user: str = "") -> dict:
    """Headers for a call to a remote agent on behalf of a dashboard user."""
    headers = {"Authorization": f"Bearer {remote['token']}"}
    if user:
        headers["X-PiCore-User"] = user
    return headers
