"""
MCP server built into the dashboard (Streamable HTTP at /mcp), secured with OAuth 2.1 via
FastMCP's OIDCProxy federated to Pocket ID - the same setup as the *-no-kari servers.

- Configured under Einstellungen -> MCP-Server (settings.json "mcp"); the client secret and the
  JWT signing key never leave the Pi again.
- Every Pocket ID identity (e-mail, username or subject) is mapped to a dashboard user. Tools call
  the dashboard's own API in-process as that user, so its permissions, the activity log and the
  forwarding to remote Pis apply exactly as in the browser.
- The MCP app is (re)built at startup and whenever the settings change; requests for its paths are
  routed to it by McpDispatch (outermost ASGI layer). Under the public MCP host name nothing else of
  the dashboard is reachable.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import settings_store

logger = logging.getLogger("pi_dashboard.mcp")

KEY = "mcp"
_state: Dict[str, Any] = {"asgi": None, "task": None, "stop": None, "paths": [], "host": "", "error": "", "started": 0}
_lock = asyncio.Lock()
_call = None  # app.internal_call, set by setup()


class McpConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return c if isinstance(c, dict) else {}


def public() -> Dict[str, Any]:
    c = config()
    base = c.get("base_url", "")
    appr = approval_config()
    return {"enabled": bool(c.get("enabled")), "base_url": base, "issuer": c.get("issuer", ""),
            "approval": appr, "approval_tools": [{"name": n, "label": l} for n, l in APPROVAL_TOOLS.items()],
            "client_id": c.get("client_id", ""), "has_secret": bool(c.get("client_secret")),
            "users": c.get("users", []), "consent": c.get("consent", True),
            "callback_url": f"{base}/auth/callback" if base else "", "mcp_url": f"{base}/mcp" if base else "",
            "running": _state["asgi"] is not None, "error": _state["error"], "started": _state["started"],
            "available": _fastmcp_available()}


def _fastmcp_available() -> bool:
    try:
        import fastmcp  # noqa: F401
        return True
    except Exception:
        return False


def _clean_url(url: str, what: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    if not p.hostname or p.query or p.fragment or p.username:
        raise McpConfigError(f"{what}: ungültige Adresse.")
    return url


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    import auth
    c = dict(config())
    base_url = _clean_url(fields.get("base_url", ""), "Öffentliche Adresse")
    if base_url:
        p = urlparse(base_url)
        if p.path not in ("", "/"):
            raise McpConfigError("Öffentliche Adresse ohne Pfad angeben, z.B. https://picore-mcp.example.com")
        # Under this host name only the MCP is served - an IP or the dashboard's own address would lock it out
        if p.scheme != "https" or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", p.hostname or "", re.I) or p.hostname == "localhost":
            raise McpConfigError("Öffentliche Adresse: eine eigene https-Domain (z.B. über Cloudflare Tunnel), keine IP-Adresse.")
    issuer = _clean_url(fields.get("issuer", ""), "Pocket-ID-Adresse")
    issuer = re.sub(r"/\.well-known/openid-configuration$", "", issuer)
    users = []
    for u in fields.get("users") or []:
        match = str((u or {}).get("match", "")).strip()[:120]
        target = auth.active_user(str((u or {}).get("user", "")).strip())
        if not match:
            continue
        if not target:
            raise McpConfigError(f"Dashboard-Benutzer für „{match}“ nicht gefunden oder deaktiviert.")
        users.append({"match": match, "user": target})
    if len(users) > 50:
        raise McpConfigError("Höchstens 50 Zuordnungen.")
    secret = str(fields.get("client_secret") or "").strip()
    c.update(enabled=bool(fields.get("enabled")), base_url=base_url, issuer=issuer,
             client_id=str(fields.get("client_id") or "").strip()[:200], users=users,
             consent=fields.get("consent", True) is not False)
    if secret:
        c["client_secret"] = secret
    if not c.get("jwt_key"):
        c["jwt_key"] = secrets.token_hex(32)
    if c["enabled"]:
        missing = [n for n, v in (("Öffentliche Adresse", base_url), ("Pocket-ID-Adresse", issuer),
                                  ("Client-ID", c["client_id"]), ("Client-Secret", c.get("client_secret"))) if not v]
        if missing:
            raise McpConfigError(f"Zum Einschalten fehlt: {', '.join(missing)}.")
        if not users:
            raise McpConfigError("Mindestens eine Zuordnung Pocket ID → Dashboard-Benutzer anlegen.")
    settings_store.set(KEY, c)
    return c


def save_approval(a: Dict[str, Any]) -> Dict[str, Any]:
    try:
        wait = int(a.get("wait", 60))
    except (TypeError, ValueError):
        raise McpConfigError("Wartezeit: Anzahl Sekunden.")
    if not 10 <= wait <= 240:
        raise McpConfigError("Wartezeit: 10 bis 240 Sekunden.")
    c = dict(config())
    c["approval"] = {"enabled": bool(a.get("enabled")), "wait": wait,
                     "tools": [t for t in (a.get("tools") or []) if t in APPROVAL_TOOLS]}
    settings_store.set(KEY, c)
    return approval_config()


# ---------------------------------------------------------------------------
# Lifecycle and routing
# ---------------------------------------------------------------------------
def setup(call) -> None:
    global _call
    _call = call


async def _run(asgi, ready: asyncio.Event, stop: asyncio.Event, errors: list) -> None:
    """The MCP app's lifespan (session manager) lives in this task from start to stop, so its
    task group is entered and left in the same task."""
    try:
        async with asgi.router.lifespan_context(asgi):
            ready.set()
            await stop.wait()
    except Exception as e:
        errors.append(e)
        ready.set()


async def _stop() -> None:
    if _state["task"] is not None:
        _state["stop"].set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(_state["task"], timeout=10)
    _state.update(asgi=None, task=None, stop=None, paths=[], host="", started=0)


async def reload() -> Dict[str, Any]:
    """Stop the running MCP app (if any) and start it again with the current settings."""
    async with _lock:
        await _stop()
        _state["error"] = ""
        c = config()
        if not c.get("enabled"):
            return public()
        missing = [k for k in ("base_url", "issuer", "client_id", "client_secret", "jwt_key") if not c.get(k)]
        if missing:
            _state["error"] = f"Einstellungen unvollständig ({', '.join(missing)})"
            return public()
        try:
            mcp = await asyncio.to_thread(_build, c)
            asgi = mcp.http_app(path="/mcp")
            ready, stop, errors = asyncio.Event(), asyncio.Event(), []
            task = asyncio.create_task(_run(asgi, ready, stop, errors))
            await asyncio.wait_for(ready.wait(), timeout=30)
            if errors:
                raise errors[0]
            paths = sorted({getattr(r, "path", "") for r in asgi.routes if getattr(r, "path", "")})
            _state.update(asgi=asgi, task=task, stop=stop, paths=paths,
                          host=(urlparse(c["base_url"]).hostname or "").lower(), started=int(time.time()))
            logger.info(f"MCP server running at {c['base_url']}/mcp ({len(paths)} routes)")
        except Exception as e:
            _state["error"] = f"{type(e).__name__}: {e}"[:300]
            logger.warning(f"MCP server not started: {_state['error']}")
    return public()


async def shutdown() -> None:
    async with _lock:
        await _stop()


def _is_mcp_path(path: str) -> bool:
    for p in _state["paths"]:
        if path == p or (p.startswith("/.well-known/") and path.startswith(p)):
            return True
    return path == "/mcp/" or path.startswith("/.well-known/oauth-")


class McpDispatch:
    """Outermost ASGI layer: MCP and OAuth paths go to the MCP app; under the public MCP host name
    nothing else of the dashboard is served."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            asgi = _state["asgi"]
            path = scope.get("path", "")
            if asgi is not None and scope["type"] == "http" and _is_mcp_path(path):
                return await asgi(scope, receive, send)
            host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "").split(":")[0].lower()
            if _state["host"] and host == _state["host"]:
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"Not found"})
                return
        return await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Approvals: chosen actions wait until a person confirms them in the dashboard
# ---------------------------------------------------------------------------
APPROVAL_TOOLS = {
    "service_action": "Dienste starten/stoppen", "container_action": "Container starten/stoppen",
    "stack_action": "Compose-Stacks", "kube_restart": "Kubernetes neu starten", "kube_scale": "Kubernetes skalieren",
    "apt_run": "apt-Updates", "fleet_apt": "apt auf mehreren Pis", "fleet_upgrade_packages": "Einzelne Pakete aktualisieren", "packages": "Pakete installieren/entfernen",
    "system_power": "Neustart & Herunterfahren", "firewall": "Firewall ändern", "run_quick_action": "Schnellaktionen",
    "wake_on_lan": "Wake-on-LAN", "maintenance_window": "Wartungsfenster", "write_file": "Dateien schreiben",
    "move_to_trash": "Dateien löschen", "delete_task": "Aufgaben löschen", "write_notes": "Notizen ändern",
    "save_wiki_page": "Wiki-Seiten ändern", "add_app_to_board": "Boards ändern",
}
DEFAULT_APPROVAL = ["stack_action", "kube_scale", "apt_run", "fleet_apt", "packages", "system_power", "firewall",
                    "write_file", "move_to_trash"]
APPROVAL_TTL = 15 * 60
_approvals: Dict[str, Dict[str, Any]] = {}


def approval_config() -> Dict[str, Any]:
    a = config().get("approval") or {}
    return {"enabled": bool(a.get("enabled")), "wait": int(a.get("wait", 60)),
            "tools": list(a.get("tools", DEFAULT_APPROVAL))}


def _summary(args: Dict[str, Any], limit: int = 300) -> str:
    def short(v):
        if isinstance(v, str) and len(v) > 80:
            return v[:77] + "…"
        return v
    text = ", ".join(f"{k}={json.dumps(short(v), ensure_ascii=False)}" for k, v in (args or {}).items() if v not in (None, "", [], {}))
    return text[:limit] or "–"


def _approval_key(user: str, tool: str, args: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([user, tool, args], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _prune_approvals() -> None:
    now = time.time()
    for k, a in list(_approvals.items()):
        if now - a["created"] > APPROVAL_TTL * 2 or (a["status"] == "used" and now - a["decided"] > 3600):
            _approvals.pop(k, None)


def approvals(user: str, can_manage: bool) -> Dict[str, Any]:
    _prune_approvals()
    now = time.time()
    items = []
    for a in sorted(_approvals.values(), key=lambda a: -a["created"]):
        if not can_manage and a["user"] != user:
            continue
        status = a["status"]
        if status == "pending" and now > a["expires"]:
            status = "expired"
        items.append({k: a[k] for k in ("id", "tool", "label", "user", "identity", "summary", "args", "created", "expires",
                                         "decided", "decided_by")} | {"status": status})
    return {"enabled": bool(config().get("enabled")) and approval_config()["enabled"], "items": items[:50],
            "pending": sum(1 for i in items if i["status"] == "pending")}


def decide(approval_id: str, approve: bool, user: str, can_manage: bool) -> Dict[str, Any]:
    a = next((x for x in _approvals.values() if x["id"] == approval_id), None)
    if not a:
        raise McpConfigError("Anfrage nicht gefunden (abgelaufen?).")
    if a["user"] != user and not can_manage:
        raise McpConfigError("Nur der zugeordnete Benutzer oder ein Administrator darf das freigeben.")
    if a["status"] != "pending" or time.time() > a["expires"]:
        raise McpConfigError("Diese Anfrage ist nicht mehr offen.")
    a.update(status="approved" if approve else "denied", decided=time.time(), decided_by=user,
             expires=time.time() + APPROVAL_TTL)
    if a.get("event") is not None:
        a["event"].set()  # the dashboard and the MCP app share one event loop
    return {k: v for k, v in a.items() if k not in ("event", "key")}


async def _await_approval(user: str, identity: str, tool: str, args: Dict[str, Any]) -> None:
    from fastmcp.exceptions import ToolError
    cfg = approval_config()
    if not cfg["enabled"] or tool not in cfg["tools"]:
        return
    _prune_approvals()
    key = _approval_key(user, tool, args)
    a = _approvals.get(key)
    now = time.time()
    if a and a["status"] == "approved" and now <= a["expires"]:
        a["status"] = "used"
        return
    if a and a["status"] == "denied" and now <= a["expires"]:
        a["status"] = "used"
        raise ToolError(f"Abgelehnt von {a['decided_by']} im Dashboard.")
    if not a or a["status"] != "pending" or now > a["expires"]:
        a = {"id": secrets.token_hex(6), "key": key, "tool": tool, "label": APPROVAL_TOOLS.get(tool, tool), "user": user,
             "identity": identity, "args": json.loads(json.dumps(args, default=str)), "summary": _summary(args),
             "created": now, "expires": now + APPROVAL_TTL, "status": "pending", "decided": 0, "decided_by": "",
             "event": asyncio.Event()}
        _approvals[key] = a
        try:
            import notify
            notify.approval_requested({**a, "event": None})
        except Exception as e:
            logger.warning(f"Approval mail: {e}")
    try:
        await asyncio.wait_for(a["event"].wait(), timeout=cfg["wait"])
    except asyncio.TimeoutError:
        raise ToolError(f"Diese Aktion wartet auf eine Freigabe im Dashboard (Einstellungen → MCP-Aktivität, Anfrage {a['id']}). "
                        "Bitte die Person um Freigabe bitten und das Werkzeug danach mit denselben Parametern erneut aufrufen – "
                        "die Freigabe gilt 15 Minuten.")
    if a["status"] == "approved":
        a["status"] = "used"
        return
    a["status"] = "used"
    raise ToolError(f"Abgelehnt von {a['decided_by']} im Dashboard.")


# ---------------------------------------------------------------------------
# Call log (Einstellungen -> MCP-Aktivität)
# ---------------------------------------------------------------------------
CALLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_calls.json")
_calls_lock = threading.Lock()


def _load_calls() -> List[Dict[str, Any]]:
    try:
        with open(CALLS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_calls(items: List[Dict[str, Any]]) -> None:
    with open(CALLS_FILE + ".tmp", "w") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(CALLS_FILE + ".tmp", CALLS_FILE)


def _max_calls() -> int:
    import retention
    return retention.get("mcp_calls")


def record_call(entry: Dict[str, Any]) -> None:
    with _calls_lock:
        items = _load_calls()
        items.append(entry)
        try:
            _write_calls(items[-_max_calls():])
        except OSError as e:
            logger.warning(f"MCP call log: {e}")


def trim_calls() -> None:
    with _calls_lock:
        items = _load_calls()
        if len(items) > _max_calls():
            _write_calls(items[-_max_calls():])


def calls(tool: str = "", user: str = "", status: str = "", query: str = "", limit: int = 200) -> Dict[str, Any]:
    items = list(reversed(_load_calls()))
    tools = sorted({c["tool"] for c in items})
    users = sorted({c.get("user") or "" for c in items} - {""})
    if tool:
        items = [c for c in items if c["tool"] == tool]
    if user:
        items = [c for c in items if c.get("user") == user]
    if status:
        items = [c for c in items if c.get("status") == status]
    q = (query or "").strip().lower()
    if q:
        items = [c for c in items if q in json.dumps(c, ensure_ascii=False).lower()]
    day = time.time() - 86400
    recent = [c for c in _load_calls() if c["ts"] >= day]
    by_tool: Dict[str, int] = {}
    for c in recent:
        by_tool[c["tool"]] = by_tool.get(c["tool"], 0) + 1
    return {"calls": items[:max(1, min(limit, 1000))], "total": len(items), "tools": tools, "users": users,
            "last_24h": {"calls": len(recent), "errors": sum(1 for c in recent if c.get("status") == "error"),
                         "top": sorted(by_tool.items(), key=lambda kv: -kv[1])[:8]}}


def _identity() -> str:
    try:
        from fastmcp.server.dependencies import get_access_token
        token = get_access_token()
        claims = (token.claims if token else {}) or {}
        return str(claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "")
    except Exception:
        return ""


def _make_middleware():
    from fastmcp.server.middleware import Middleware

    class Guard(Middleware):
        """Logs every tool call and holds back actions that need an approval."""

        async def on_call_tool(self, context, call_next):
            tool = context.message.name
            args = dict(context.message.arguments or {})
            t0 = time.time()
            identity = _identity()
            entry = {"ts": int(t0), "tool": tool, "identity": identity, "user": "", "args": _summary(args, 500),
                     "status": "ok", "error": "", "approval": ""}
            try:
                try:
                    entry["user"] = _user()
                except Exception:
                    pass
                if entry["user"] and tool in APPROVAL_TOOLS:
                    cfg = approval_config()
                    if cfg["enabled"] and tool in cfg["tools"]:
                        entry["approval"] = "required"
                        await _await_approval(entry["user"], identity, tool, args)
                        entry["approval"] = "approved"
                result = await call_next(context)
                if getattr(result, "is_error", False) or getattr(result, "isError", False):
                    entry["status"] = "error"
                return result
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)[:300]
                if entry["approval"] == "required":
                    entry["approval"] = "denied" if "Abgelehnt" in str(e) else "waiting"
                raise
            finally:
                entry["ms"] = int((time.time() - t0) * 1000)
                await asyncio.to_thread(record_call, entry)

        async def on_list_resources(self, context, call_next):
            items = list(await call_next(context))
            try:
                items += await _dynamic_resources()
            except Exception as e:
                logger.info(f"MCP resources: {e}")
            return items

    return Guard()


# ---------------------------------------------------------------------------
# Building the MCP server
# ---------------------------------------------------------------------------
def _user() -> str:
    """Dashboard user for the Pocket ID identity of the current request."""
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token
    token = get_access_token()
    claims = (token.claims if token else {}) or {}
    idents = [str(claims.get(k, "")).lower() for k in ("email", "preferred_username", "sub") if claims.get(k)]
    for m in config().get("users", []):
        if m["match"].lower() in idents:
            return m["user"]
    raise ToolError(f"Diese Pocket-ID-Identität ({', '.join(idents) or 'unbekannt'}) ist keinem Dashboard-Benutzer "
                    "zugeordnet (Einstellungen → MCP-Server).")


async def _api(method: str, path: str, params: Optional[dict] = None, body: Any = None, host: str = "") -> Any:
    from fastmcp.exceptions import ToolError
    import remotes
    user = _user()
    host_id = ""
    if host:
        r = next((x for x in remotes._all() if host in (x["id"], x["name"])), None)
        if not r:
            raise ToolError(f"Unbekannter Pi „{host}“ – list_hosts zeigt die Namen.")
        host_id = r["id"]
    try:
        return await _call(user, method, path, params=params, json_body=body, host=host_id)
    except Exception as e:
        raise ToolError(str(e))


def _build(c: Dict[str, Any]):
    from fastmcp import FastMCP
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    auth_provider = OIDCProxy(
        config_url=f"{c['issuer']}/.well-known/openid-configuration",
        client_id=c["client_id"],
        client_secret=c["client_secret"],
        base_url=c["base_url"],
        jwt_signing_key=c["jwt_key"],
        required_scopes=["openid", "profile", "email"],
        # Pocket ID rejects RFC 8707 resource indicators (see the *-no-kari servers)
        forward_resource=False,
        require_authorization_consent=c.get("consent", True),
    )
    mcp = FastMCP(
        name="picore",
        instructions=(
            "PiCore Server Dashboard: Raspberry-Pi/homelab monitoring and control. Start with get_overview. "
            "Every tool acts as the mapped dashboard user and has exactly that user's permissions. "
            "Most tools take an optional `host` (a remote Pi name from list_hosts; empty = the main Pi)."),
        auth=auth_provider,
    )
    mcp.add_middleware(_make_middleware())
    _register_tools(mcp)
    _register_prompts(mcp)
    _register_resources(mcp)
    return mcp


Host = Optional[str]
RO = {"readOnlyHint": True}


def _register_tools(mcp) -> None:  # noqa: C901 - one flat list of tools
    # ------------------------------------------------------------------ overview & reading
    @mcp.tool(annotations=RO)
    async def get_overview(host: Host = None) -> dict:
        """Status at a glance: health summary, CPU/temperature/RAM/disk, uptime, pending updates, services."""
        summary, snap, homelab, services = await asyncio.gather(
            _api("GET", "/api/status-summary", host=host or ""), _api("GET", "/api/system", host=host or ""),
            _api("GET", "/api/homelab", host=host or ""), _api("GET", "/api/services", host=host or ""))
        upd = (homelab or {}).get("updates") or {}
        return {"summary": summary, "host": snap.get("host_name"), "model": snap.get("model"),
                "cpu_percent": (snap.get("cpu") or {}).get("percent"), "temperature_c": (snap.get("temperature") or {}).get("celsius"),
                "memory": snap.get("memory"), "disk": snap.get("disk"), "uptime": (snap.get("uptime") or {}).get("formatted"),
                "throttled": snap.get("throttled"), "apt_updates": {k: upd.get(k) for k in ("count", "security_count", "reboot_required")},
                "services": [{k: s.get(k) for k in ("id", "name", "status")} for s in services.get("services", [])]}

    @mcp.tool(annotations=RO)
    async def list_hosts() -> dict:
        """The main Pi and all remote Pis (agents) with online state - names usable as `host`."""
        return await _api("GET", "/api/remotes/current")

    @mcp.tool(annotations=RO)
    async def fleet_overview() -> dict:
        """All Pis in one table: online, version, apt updates, temperature, CPU, RAM, disk, uptime."""
        return await _api("GET", "/api/fleet")

    @mcp.tool(annotations=RO)
    async def get_metrics_history(range: Literal["24h", "7d"] = "24h", host: Host = None) -> dict:
        """Recorded CPU, temperature, RAM, network, disk and power values (one point per minute / aggregated)."""
        return await _api("GET", "/api/history", {"range": range}, host=host or "")

    @mcp.tool(annotations=RO)
    async def get_events(days: int = 7, host: Host = None) -> dict:
        """Event timeline: outages, recoveries, throttling, logins, updates, journal entries, webhooks, new devices."""
        return await _api("GET", "/api/events", {"days": days}, host=host or "")

    @mcp.tool(annotations=RO)
    async def get_logs(unit: str = "", since: str = "1h", priority: Literal["all", "err", "warning"] = "all",
                       query: str = "", host: Host = None) -> dict:
        """Search the systemd journal (unit optional, since e.g. '30min', '6h', '2d', query = text filter)."""
        return await _api("GET", "/api/tools/logs", {"unit": unit, "since": since, "priority": priority, "q": query}, host=host or "")

    @mcp.tool(annotations=RO)
    async def get_error_digest(days: int = 7, host: Host = None) -> dict:
        """Grouped error messages from the journal of the last days."""
        return await _api("GET", "/api/tools/errors", {"days": days}, host=host or "")

    @mcp.tool(annotations=RO)
    async def list_services(host: Host = None) -> dict:
        """Monitored system services with their status."""
        return await _api("GET", "/api/services", host=host or "")

    @mcp.tool(annotations=RO)
    async def service_journal(service: str, lines: int = 150, host: Host = None) -> dict:
        """Journal of one monitored service (id from list_services)."""
        return await _api("GET", f"/api/services/{service}/journal", {"lines": lines}, host=host or "")

    @mcp.tool(annotations=RO)
    async def list_processes(limit: int = 15, host: Host = None) -> dict:
        """Top processes by CPU/RAM."""
        return await _api("GET", "/api/processes", {"limit": limit}, host=host or "")

    @mcp.tool(annotations=RO)
    async def list_containers(host: Host = None) -> dict:
        """Docker containers, compose stacks and Kubernetes workloads/pods/services."""
        return await _api("GET", "/api/containers/overview", host=host or "")

    @mcp.tool(annotations=RO)
    async def container_logs(name: str, lines: int = 200, host: Host = None) -> dict:
        """Logs of a Docker container."""
        return await _api("GET", f"/api/workloads/docker/{name}/logs", {"lines": lines}, host=host or "")

    @mcp.tool(annotations=RO)
    async def get_storage(host: Host = None) -> dict:
        """Drives, partitions, mount points and fill levels."""
        return await _api("GET", "/api/drives", host=host or "")

    @mcp.tool(annotations=RO)
    async def get_network(host: Host = None) -> dict:
        """Interfaces, addresses, gateway, DNS and traffic."""
        return await _api("GET", "/api/tools/network", host=host or "")

    @mcp.tool(annotations=RO)
    async def get_security(host: Host = None) -> dict:
        """SSH logins and failures, fail2ban and firewall (ufw) status and rules."""
        ssh, fw = await asyncio.gather(_api("GET", "/api/tools/ssh", host=host or ""), _api("GET", "/api/tools/firewall", host=host or ""))
        return {"ssh": ssh, "firewall": fw}

    @mcp.tool(annotations=RO)
    async def apt_status(host: Host = None) -> dict:
        """Pending apt updates (security separately), reboot required, and the state of a running apt job."""
        homelab, run = await asyncio.gather(_api("GET", "/api/homelab", host=host or ""), _api("GET", "/api/updates/apt", host=host or ""))
        return {"updates": (homelab or {}).get("updates"), "apt_job": run}

    @mcp.tool(annotations=RO)
    async def list_lan_devices() -> dict:
        """Devices found by the LAN scan (IP, MAC, name, vendor, online)."""
        return await _api("GET", "/api/tools/lan")

    @mcp.tool(annotations=RO)
    async def netbox_comparison(refresh: bool = False) -> dict:
        """NetBox prefixes/IPs/devices compared with the LAN scan: undocumented devices, IP/MAC differences, free addresses."""
        return await _api("GET", "/api/netbox/data", {"refresh": refresh or None})

    @mcp.tool(annotations=RO)
    async def web_services() -> dict:
        """Web service checks (health checks) with their last result and 24h uptime."""
        return await _api("GET", "/api/health")

    @mcp.tool(annotations=RO)
    async def weekly_report() -> dict:
        """The weekly report: availability, incidents, changes, resource trends."""
        return await _api("GET", "/api/report")

    @mcp.tool(annotations=RO)
    async def search_content(query: str) -> dict:
        """Full-text search across docs, contracts, commands, notes, tasks, reminders, wiki and board apps."""
        return await _api("GET", "/api/search", {"q": query})

    @mcp.tool(annotations=RO)
    async def dns_lookup(name: str, type: Literal["A", "AAAA", "MX", "TXT", "CNAME", "NS", "PTR", "SRV"] = "A", host: Host = None) -> dict:
        """DNS lookup from the Pi."""
        return await _api("POST", "/api/tools/dns", body={"name": name, "type": type}, host=host or "")

    @mcp.tool(annotations=RO)
    async def port_test(target: str, ports: str, host: Host = None) -> dict:
        """TCP test from the Pi to target, ports e.g. '22,80,443' or '8000-8010' (max. 100)."""
        return await _api("POST", "/api/tools/port-test", body={"host": target, "ports": ports}, host=host or "")

    # ------------------------------------------------------------------ control
    @mcp.tool
    async def service_action(service: str, action: Literal["start", "stop", "restart"], host: Host = None) -> dict:
        """Start, stop or restart a monitored service (id from list_services)."""
        return await _api("POST", f"/api/services/{service}/{action}", host=host or "")

    @mcp.tool
    async def container_action(name: str, action: Literal["start", "stop", "restart"], host: Host = None) -> dict:
        """Start, stop or restart a Docker container."""
        return await _api("POST", f"/api/workloads/docker/{name}/{action}", host=host or "")

    @mcp.tool
    async def stack_action(name: str, action: Literal["up", "stop", "restart", "update", "down"], host: Host = None) -> dict:
        """Docker Compose stack: up (start), stop, restart, update (pull + up -d) or down (remove) - runs as a background job."""
        return await _api("POST", f"/api/containers/stacks/{name}/{action}", host=host or "")

    @mcp.tool
    async def kube_restart(kind: Literal["deployment", "statefulset", "daemonset"], namespace: str, name: str, host: Host = None) -> dict:
        """Rolling restart of a Kubernetes workload."""
        return await _api("POST", f"/api/containers/kube/{kind}/{namespace}/{name}/restart", host=host or "")

    @mcp.tool
    async def kube_scale(kind: Literal["deployment", "statefulset"], namespace: str, name: str, replicas: int, host: Host = None) -> dict:
        """Scale a Kubernetes workload."""
        return await _api("POST", f"/api/containers/kube/{kind}/{namespace}/{name}/scale", body={"replicas": replicas}, host=host or "")

    @mcp.tool
    async def apt_run(mode: Literal["update", "upgrade"], host: Host = None) -> dict:
        """apt update, or update + full upgrade (runs in its own systemd unit; follow it with apt_status)."""
        return await _api("POST", "/api/updates/apt", body={"mode": mode}, host=host or "")

    @mcp.tool
    async def fleet_apt(hosts: List[str], mode: Literal["update", "upgrade"]) -> dict:
        """Start apt update/upgrade on several Pis at once ('' or 'local' = the main Pi, else remote names)."""
        import remotes
        ids = []
        for h in hosts:
            if h in ("", "local"):
                ids.append("")
                continue
            r = next((x for x in remotes._all() if h in (x["id"], x["name"])), None)
            if r:
                ids.append(r["id"])
        return await _api("POST", "/api/fleet/apt", body={"ids": ids, "mode": mode})

    @mcp.tool
    async def fleet_upgrade_packages(targets: Dict[str, List[str]]) -> dict:
        """Upgrade chosen packages (only newer versions of installed ones) per Pi: {'pi-name' or 'local': ['pkg', ...]}.
        The pending packages per Pi are in fleet_overview (field packages)."""
        import remotes
        ids: Dict[str, List[str]] = {}
        for h, names in targets.items():
            if h in ("", "local"):
                ids[""] = names
                continue
            r = next((x for x in remotes._all() if h in (x["id"], x["name"])), None)
            if r:
                ids[r["id"]] = names
        return await _api("POST", "/api/fleet/packages", body={"targets": ids})

    @mcp.tool
    async def packages(action: Literal["install", "remove", "upgrade"], names: List[str], host: Host = None) -> dict:
        """Install or remove apt packages, or upgrade only these installed ones (protected base packages are refused)."""
        return await _api("POST", "/api/packages/run", body={"names": names, "action": action}, host=host or "")

    @mcp.tool
    async def system_power(action: Literal["reboot", "shutdown"], host: Host = None) -> dict:
        """Reboot or shut down a Pi."""
        return await _api("POST", f"/api/action/{action}", host=host or "")

    @mcp.tool
    async def wake_on_lan(device: str) -> dict:
        """Wake a device configured under Wake-on-LAN (name or id)."""
        devices = (await _api("GET", "/api/wol")).get("devices", [])
        d = next((x for x in devices if device.lower() in (str(x.get("id", "")).lower(), str(x.get("name", "")).lower())), None)
        if not d:
            from fastmcp.exceptions import ToolError
            raise ToolError(f"Gerät nicht gefunden. Vorhanden: {', '.join(x.get('name', '') for x in devices) or 'keine'}")
        return await _api("POST", f"/api/wol/{d['id']}/wake")

    @mcp.tool
    async def scan_lan() -> dict:
        """Run a LAN device scan now."""
        return await _api("POST", "/api/tools/lan/scan")

    @mcp.tool
    async def maintenance_window(title: str, start: int, end: int, scope: List[str] | Literal["all"] = "all", note: str = "") -> dict:
        """Plan a maintenance window (unix timestamps); scope 'all' or ids like 'service:ssh', 'health:abc', 'stack:media'."""
        return await _api("POST", "/api/maintenance", body={"title": title, "start": start, "end": end, "scope": scope, "note": note})

    @mcp.tool
    async def firewall(action: Literal["enable", "disable", "rule", "delete", "preset", "logging"], params: dict | None = None,
                       host: Host = None) -> dict:
        """Change ufw. rule: {'action': 'allow'|'deny'|'reject'|'limit', 'dport': '8080', 'proto': 'tcp'|'udp'|'any',
        'from': '192.168.178.0/24' (optional), 'direction': 'in'|'out', 'comment': '...'}; delete: {'num': 3};
        preset: {'preset': 'k3s'|'netbird'|'web'}; logging: {'level': 'off'|'low'|'medium'}.
        Read the current rules with get_security first; removing the last SSH/dashboard rule is refused."""
        return await _api("POST", "/api/tools/firewall", body={"action": action, "params": params or {}}, host=host or "")

    @mcp.tool(annotations=RO)
    async def list_quick_actions(host: Host = None) -> dict:
        """The dashboard's quick actions (own scripts)."""
        return await _api("GET", "/api/actions", host=host or "")

    @mcp.tool
    async def run_quick_action(action_id: str, host: Host = None) -> dict:
        """Start a quick action; returns a run id (output via the dashboard)."""
        return await _api("POST", f"/api/actions/{action_id}/run", host=host or "")

    # ------------------------------------------------------------------ docs & planning
    @mcp.tool(annotations=RO)
    async def get_notes() -> dict:
        """The dashboard notes (Markdown with - [ ] checklists)."""
        return await _api("GET", "/api/notes")

    @mcp.tool
    async def write_notes(text: str, append: bool = True) -> dict:
        """Replace the notes, or append text as new lines (default)."""
        cur = await _api("GET", "/api/notes")
        old = cur.get("text", "")
        new = f"{old.rstrip()}\n{text}".strip("\n") if append and old.strip() else text
        return await _api("POST", "/api/notes", body={"text": new, "base": cur.get("updated", 0)})

    @mcp.tool(annotations=RO)
    async def list_tasks() -> dict:
        """Kanban tasks with columns, labels and due dates."""
        return await _api("GET", "/api/kanban")

    @mcp.tool
    async def save_task(title: str | None = None, card_id: str | None = None, desc: str | None = None,
                        labels: List[str] | None = None, due: str | None = None,
                        column: Literal["idea", "doing", "done"] | None = None) -> dict:
        """Create a task (without card_id) or change one (due as YYYY-MM-DD; moving = column)."""
        body = {k: v for k, v in {"title": title, "desc": desc, "labels": labels, "due": due, "column": column}.items() if v is not None}
        return await _api("POST", f"/api/kanban/cards/{card_id}" if card_id else "/api/kanban/cards", body=body)

    @mcp.tool
    async def delete_task(card_id: str) -> dict:
        """Delete a task."""
        return await _api("POST", f"/api/kanban/cards/{card_id}/delete")

    @mcp.tool(annotations=RO)
    async def list_reminders() -> dict:
        """Reminders with due dates and repeats."""
        return await _api("GET", "/api/reminders")

    @mcp.tool
    async def add_reminder(title: str, due: str, repeat: Literal["none", "daily", "weekly", "monthly", "yearly"] = "none", every: int = 1) -> dict:
        """Create a reminder (due as YYYY-MM-DD)."""
        return await _api("POST", "/api/reminders", body={"title": title, "due": due, "repeat": repeat, "every": every})

    @mcp.tool
    async def reminder_done(item_id: str) -> dict:
        """Tick off a reminder (a repeating one moves to its next date)."""
        return await _api("POST", f"/api/reminders/{item_id}/done")

    @mcp.tool(annotations=RO)
    async def list_journal(query: str = "", limit: int = 50) -> dict:
        """Operations journal ('what was changed and why')."""
        return await _api("GET", "/api/docs/journal", {"q": query, "limit": limit})

    @mcp.tool
    async def add_journal_entry(title: str, text: str = "",
                                category: Literal["Änderung", "Wartung", "Störung", "Installation", "Notiz"] = "Änderung",
                                ref: str = "") -> dict:
        """Write an operations journal entry - use it to document what you changed."""
        return await _api("POST", "/api/docs/journal", body={"title": title, "text": text, "category": category, "ref": ref})

    @mcp.tool(annotations=RO)
    async def list_runbooks() -> dict:
        """Runbooks (step-by-step procedures) with their runs."""
        return await _api("GET", "/api/docs/runbooks")

    @mcp.tool
    async def save_runbook(title: str, steps: List[str], description: str = "", runbook_id: str | None = None) -> dict:
        """Create or replace a runbook."""
        body = {"title": title, "steps": steps, "description": description}
        return await _api("POST", f"/api/docs/runbooks/{runbook_id}" if runbook_id else "/api/docs/runbooks", body=body)

    @mcp.tool(annotations=RO)
    async def list_service_docs() -> dict:
        """Service docs ("Steckbriefe"): purpose, address, ports, data, backup, owner per service."""
        return await _api("GET", "/api/docs/services")

    @mcp.tool
    async def save_service_doc(target: str, fields: dict) -> dict:
        """Create/update a service doc. target e.g. 'service:ssh', 'stack:media', 'health:<id>' or '' for a new custom one;
        fields: name, purpose, url, ports, data, backup, docs_url, owner, notes."""
        return await _api("POST", "/api/docs/services", body={**fields, "target": target})

    @mcp.tool(annotations=RO)
    async def list_inventory() -> dict:
        """Hardware inventory with purchase date, price, warranty and network status."""
        return await _api("GET", "/api/docs/inventory")

    @mcp.tool
    async def save_inventory_item(fields: dict, item_id: str | None = None) -> dict:
        """Create/update an inventory item: name, category, manufacturer, model, serial, location,
        purchased (YYYY-MM-DD), price, warranty_until, mac, notes."""
        return await _api("POST", f"/api/docs/inventory/{item_id}" if item_id else "/api/docs/inventory", body=fields)

    @mcp.tool(annotations=RO)
    async def list_contracts() -> dict:
        """Contracts & subscriptions with costs, renewal and cancel-by dates."""
        return await _api("GET", "/api/docs/contracts")

    @mcp.tool
    async def save_contract(fields: dict, contract_id: str | None = None) -> dict:
        """Create/update a contract: name, provider, category, cost, interval (monthly/quarterly/yearly/once),
        start, end (YYYY-MM-DD), notice_days, auto_renew, link, notes."""
        return await _api("POST", f"/api/docs/contracts/{contract_id}" if contract_id else "/api/docs/contracts", body=fields)

    @mcp.tool(annotations=RO)
    async def list_snippets() -> dict:
        """Saved command snippets."""
        return await _api("GET", "/api/docs/snippets")

    @mcp.tool
    async def save_snippet(title: str, command: str, description: str = "", tags: List[str] | None = None,
                           snippet_id: str | None = None) -> dict:
        """Save a command snippet."""
        body = {"title": title, "command": command, "description": description, "tags": tags or []}
        return await _api("POST", f"/api/docs/snippets/{snippet_id}" if snippet_id else "/api/docs/snippets", body=body)

    @mcp.tool(annotations=RO)
    async def list_wiki() -> dict:
        """Knowledge base: all pages (title, folder, updated) without content."""
        return await _api("GET", "/api/wiki")

    @mcp.tool(annotations=RO)
    async def read_wiki_page(page_id: str) -> dict:
        """One knowledge base page with its Markdown content."""
        return await _api("GET", f"/api/wiki/{page_id}")

    @mcp.tool
    async def save_wiki_page(title: str, content: str, folder: str = "", page_id: str | None = None) -> dict:
        """Create or update a knowledge base page (Markdown). The previous version is kept."""
        body = {"title": title, "content": content, "folder": folder}
        return await _api("POST", f"/api/wiki/{page_id}" if page_id else "/api/wiki", body=body)

    @mcp.tool(annotations=RO)
    async def get_costs() -> dict:
        """Homelab costs: electricity, contracts/subscriptions and hardware purchases per month and year."""
        return await _api("GET", "/api/costs")

    @mcp.tool(annotations=RO)
    async def get_boards() -> dict:
        """The start page boards with their sections, apps, widgets and cards."""
        return await _api("GET", "/api/boards")

    @mcp.tool
    async def add_app_to_board(name: str, url: str, description: str = "", icon: str = "", board: str = "", section: str = "") -> dict:
        """Add an app tile to a board (default: first board, first section). icon = dashboard-icons file like 'jellyfin.svg'."""
        from fastmcp.exceptions import ToolError
        for attempt in range(3):  # someone else saved in between: read again and retry
            cfg = await _api("GET", "/api/boards")
            b = next((x for x in cfg["boards"] if board and board.lower() in (x["id"], (x.get("name") or "").lower())), cfg["boards"][0])
            s = next((x for x in b["sections"] if section and section.lower() == (x.get("name") or "").lower()), b["sections"][0])
            y = max((it["y"] + it["h"] for it in s["items"]), default=0)
            s["items"].append({"id": secrets.token_hex(5), "kind": "app", "name": name, "url": url, "description": description,
                               "icon": icon, "ping": True, "ping_url": "", "new_tab": True, "x": 0, "y": y, "w": 1, "h": 2})
            try:
                res = await _api("POST", "/api/boards", body={"rev": cfg["rev"], "boards": cfg["boards"]})
                return {"ok": True, "rev": res.get("rev"), "board": b["id"]}
            except ToolError as e:
                if "anderen Gerät" not in str(e) or attempt == 2:
                    raise
                await asyncio.sleep(0.5)

    @mcp.tool(annotations=RO)
    async def image_updates(host: Host = None) -> dict:
        """Container images with a newer version in the registry (Docker, Compose stacks, Kubernetes)."""
        return await _api("GET", "/api/containers/images", host=host or "")

    @mcp.tool(annotations=RO)
    async def image_release_notes(ref: str, host: Host = None) -> dict:
        """GitHub release notes of an image (ref from image_updates, e.g. 'ghcr.io/org/app:latest') - check for breaking changes."""
        return await _api("GET", "/api/containers/images/releases", {"ref": ref}, host=host or "")

    @mcp.tool(annotations=RO)
    async def list_backups(host: Host = None) -> dict:
        """Watched backups (restic, Borg, folders): last backup, age, state (ok/overdue/error), size."""
        return await _api("GET", "/api/backups", host=host or "")

    @mcp.tool
    async def check_backup(backup_id: str, host: Host = None) -> dict:
        """Check one watched backup again now (id from list_backups)."""
        return await _api("POST", f"/api/backups/{backup_id}/check", host=host or "")

    @mcp.tool(annotations=RO)
    async def list_domains() -> dict:
        """Own domains with expiry date, registrar, name servers and warning state."""
        return await _api("GET", "/api/docs/domains")

    # ------------------------------------------------------------------ files
    @mcp.tool(annotations=RO)
    async def list_directory(path: str = "/", host: Host = None) -> dict:
        """List a folder (sensitive paths like logins, tokens and ~/.ssh are blocked)."""
        return await _api("GET", "/api/files/list", {"path": path}, host=host or "")

    @mcp.tool(annotations=RO)
    async def read_file(path: str, host: Host = None) -> dict:
        """Read a text file (up to 2 MB, UTF-8); returns content and mtime."""
        return await _api("GET", "/api/files/text", {"path": path}, host=host or "")

    @mcp.tool
    async def write_file(path: str, content: str, mtime: str | None = None, host: Host = None) -> dict:
        """Write a text file atomically. Pass the mtime from read_file to avoid overwriting someone else's change;
        JSON and compose files are validated before saving."""
        return await _api("POST", "/api/files/text", body={"path": path, "content": content, "mtime": mtime}, host=host or "")

    @mcp.tool(annotations=RO)
    async def search_files(path: str, query: str = "", kind: str = "", min_size: int = 0, days: int = 0, host: Host = None) -> dict:
        """Search files below path by name (wildcards * ?), type, minimum size (bytes) and age (days)."""
        return await _api("GET", "/api/files/search", {"path": path, "q": query, "kind": kind, "min_size": min_size, "days": days}, host=host or "")

    @mcp.tool
    async def move_to_trash(path: str, host: Host = None) -> dict:
        """Move a file or folder to the dashboard's recycle bin (restorable for 30 days)."""
        return await _api("POST", "/api/files/delete", body={"path": path}, host=host or "")


# ---------------------------------------------------------------------------
# Prompts: ready-made procedures for Claude
# ---------------------------------------------------------------------------
def _register_prompts(mcp) -> None:
    def where(host: Optional[str]) -> str:
        return f" auf dem Pi „{host}“ (host=\"{host}\")" if host else ""

    @mcp.prompt(name="morgen_check", title="Morgen-Check")
    def morgen_check(host: Optional[str] = None) -> str:
        """Kurzer Zustandsbericht für den Start in den Tag."""
        return (f"Mach einen Morgen-Check des Homelabs{where(host)}. Nutze get_overview, get_events (days=1), apt_status, "
                "web_services, list_backups, list_domains und list_reminders – bei mehreren Pis zusätzlich fleet_overview. "
                "Fasse knapp zusammen: 1. Was ist auffällig oder kaputt (mit Ursache, falls aus den Logs erkennbar), "
                "2. Was ist heute fällig (Erinnerungen, Fristen, überfällige Backups, ablaufende Domains), "
                "3. Empfohlene nächste Schritte. Ändere nichts ohne Rückfrage.")

    @mcp.prompt(name="stoerung_analysieren", title="Störung analysieren")
    def stoerung_analysieren(dienst: str, host: Optional[str] = None) -> str:
        """Ursache einer Störung eines Dienstes oder Containers finden."""
        return (f"Analysiere die Störung von „{dienst}“{where(host)}. Vorgehen: list_services bzw. list_containers für den "
                "Zustand, service_journal oder container_logs für die letzten Meldungen, get_logs (priority=err, since=6h) und "
                "get_events für den zeitlichen Zusammenhang, get_metrics_history für Last, Temperatur und Speicher. Prüfe "
                "list_service_docs und list_runbooks auf vorhandene Anleitungen. Nenne die wahrscheinlichste Ursache mit Belegen "
                "und einen konkreten Lösungsvorschlag. Erst nach meiner Zustimmung neu starten oder Dateien ändern; danach mit "
                "add_journal_entry (Kategorie Störung) dokumentieren.")

    @mcp.prompt(name="wochenbericht", title="Wochenbericht schreiben")
    def wochenbericht() -> str:
        """Den Wochenbericht verständlich zusammenfassen und als Wiki-Seite ablegen."""
        return ("Schreibe einen Wochenbericht des Homelabs. Grundlage: weekly_report, list_journal (letzte 7 Tage), "
                "get_costs und get_events (days=7). Gliederung: Zusammenfassung in drei Sätzen, Verfügbarkeit, Vorfälle, "
                "Änderungen, Ressourcen und Trends, Kosten, offene Punkte. Zeige mir den Text und speichere ihn nach "
                "Bestätigung mit save_wiki_page im Ordner „Berichte“ unter dem Titel „Wochenbericht KW <Kalenderwoche>“.")

    @mcp.prompt(name="wartung_planen", title="Wartung planen")
    def wartung_planen(aufgabe: str, zeitpunkt: Optional[str] = None) -> str:
        """Eine Wartung vorbereiten: Ablauf, Wartungsfenster, Runbook, Doku."""
        when = f" für {zeitpunkt}" if zeitpunkt else ""
        return (f"Plane die Wartung „{aufgabe}“{when}. Ermittle mit list_services, list_containers und list_service_docs, "
                "welche Dienste betroffen sind, und schlage einen Ablauf mit Rückfallplan vor. Nach meiner Zustimmung: "
                "maintenance_window für die betroffenen Dienste anlegen, den Ablauf als Runbook speichern (save_runbook) und "
                "eine Erinnerung setzen (add_reminder). Nach der Wartung einen Eintrag ins Betriebstagebuch (Kategorie Wartung).")

    @mcp.prompt(name="update_runde", title="Update-Runde")
    def update_runde() -> str:
        """Ausstehende System- und Image-Updates prüfen und geordnet einspielen."""
        return ("Prüfe ausstehende Updates: fleet_overview bzw. apt_status für apt, image_updates für Container-Images und "
                "image_release_notes für die Änderungen der neuen Versionen (achte auf Breaking Changes). Erstelle eine "
                "Liste mit Empfehlung (jetzt / später / nicht), Sicherheitsupdates zuerst. Spiele erst nach meiner Zustimmung "
                "ein und dokumentiere jede Änderung mit add_journal_entry.")


# ---------------------------------------------------------------------------
# Resources: knowledge as context (wiki pages, runbooks, service docs)
# ---------------------------------------------------------------------------
def _runbook_md(r: Dict[str, Any]) -> str:
    lines = [f"# {r.get('title', '')}", ""]
    if r.get("description"):
        lines += [r["description"], ""]
    for i, st in enumerate(r.get("steps", []), 1):
        lines.append(f"{i}. {st.get('text', st) if isinstance(st, dict) else st}")
    return "\n".join(lines)


DOC_FIELDS = [("purpose", "Zweck"), ("url", "Adresse"), ("ports", "Ports"), ("data", "Daten"), ("backup", "Backup"),
              ("docs_url", "Doku"), ("owner", "Zuständig"), ("notes", "Notizen")]


def _servicedoc_md(d: Dict[str, Any], fields: Optional[List[Dict[str, str]]] = None) -> str:
    lines = [f"# {d.get('name') or d.get('target', '')}", ""]
    for k, label in ([(f["key"], f["label"]) for f in fields] if fields else DOC_FIELDS):
        if d.get(k):
            lines.append(f"**{label}:** {d[k]}")
    return "\n".join(lines)


def _service_docs(data: Any) -> List[Dict[str, Any]]:
    items = data.get("docs") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [{**v, "target": k} for k, v in items.items()]
    return [d for d in (items or []) if isinstance(d, dict)]


async def _dynamic_resources() -> list:
    from fastmcp.resources import FunctionResource
    out = []
    wiki, runbooks, docs = await asyncio.gather(_api("GET", "/api/wiki"), _api("GET", "/api/docs/runbooks"),
                                                _api("GET", "/api/docs/services"), return_exceptions=True)
    if isinstance(wiki, dict):
        for p in wiki.get("pages", [])[:300]:
            title = f"{p['folder']}/{p['title']}" if p.get("folder") else p["title"]
            out.append(FunctionResource.from_function(lambda: "", uri=f"picore://wiki/{p['id']}", name=f"wiki-{p['id']}",
                                                      title=f"Wissen: {title}", mime_type="text/markdown",
                                                      description="Seite der Wissensdatenbank"))
    if isinstance(runbooks, dict):
        for r in runbooks.get("runbooks", [])[:200]:
            out.append(FunctionResource.from_function(lambda: "", uri=f"picore://runbook/{r['id']}", name=f"runbook-{r['id']}",
                                                      title=f"Runbook: {r.get('title', '')}", mime_type="text/markdown",
                                                      description="Schritt-für-Schritt-Anleitung"))
    if not isinstance(docs, Exception):
        for d in _service_docs(docs)[:200]:
            target = d.get("target") or d.get("id") or ""
            if target:
                out.append(FunctionResource.from_function(lambda: "", uri=f"picore://servicedoc/{target}",
                                                          name=f"servicedoc-{target}", title=f"Steckbrief: {d.get('name') or target}",
                                                          mime_type="text/markdown", description="Dienst-Steckbrief"))
    return out


def _register_resources(mcp) -> None:
    from fastmcp.exceptions import ResourceError

    @mcp.resource("picore://wiki", name="wiki-index", title="Wissensdatenbank: Übersicht", mime_type="text/markdown")
    async def wiki_index() -> str:
        """All knowledge base pages with their resource addresses."""
        data = await _api("GET", "/api/wiki")
        lines = ["# Wissensdatenbank", ""]
        for p in data.get("pages", []):
            lines.append(f"- [{(p['folder'] + '/') if p.get('folder') else ''}{p['title']}](picore://wiki/{p['id']})")
        return "\n".join(lines) if data.get("pages") else "Noch keine Seiten."

    @mcp.resource("picore://wiki/{page_id}", name="wiki-page", title="Wissensseite", mime_type="text/markdown")
    async def wiki_page(page_id: str) -> str:
        """One knowledge base page (Markdown)."""
        p = await _api("GET", f"/api/wiki/{page_id}")
        return f"# {p['title']}\n\n{p.get('content', '')}"

    @mcp.resource("picore://runbook/{runbook_id}", name="runbook", title="Runbook", mime_type="text/markdown")
    async def runbook(runbook_id: str) -> str:
        """One runbook as a numbered list."""
        data = await _api("GET", "/api/docs/runbooks")
        r = next((x for x in data.get("runbooks", []) if x["id"] == runbook_id), None)
        if not r:
            raise ResourceError("Runbook nicht gefunden.")
        return _runbook_md(r)

    @mcp.resource("picore://servicedoc/{target}", name="servicedoc", title="Dienst-Steckbrief", mime_type="text/markdown")
    async def servicedoc(target: str) -> str:
        """One service doc."""
        data = await _api("GET", "/api/docs/services")
        d = next((x for x in _service_docs(data) if (x.get("target") or x.get("id")) == target), None)
        if not d:
            raise ResourceError("Steckbrief nicht gefunden.")
        return _servicedoc_md(d, data.get("fields") if isinstance(data, dict) else None)
