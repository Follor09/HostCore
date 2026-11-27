"""
HostCore - server & homelab dashboard for Debian, Ubuntu and Raspberry Pi OS.
FastAPI backend with WebSocket real-time live streaming, services monitoring,
drives explorer, Git auto-updater, diagnostics runner, and system monitoring.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
import hmac
import time
from contextlib import asynccontextmanager
import logging
import os
import subprocess
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from urllib.parse import quote
import sys

# The modules live in backend/; the data files (settings.json, auth.json, history.db …) stay here
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

import updater
import history
import auth
import wol
import hostctx
import webutil
import authweb
from routes import remote_servers as remote_servers_routes
from routes import incidents as incidents_routes
from routes import email_notify as email_notify_routes
from routes import retention as retention_routes
from routes import tasks as tasks_routes
from routes import dbmonitor as dbmonitor_routes
from routes import backup_check as backup_check_routes
from routes import imagepolicy as imagepolicy_routes
from routes import maintenance as maintenance_routes
from routes import anomalies as anomalies_routes
from routes import changes as changes_routes
from routes import versions as versions_routes
from routes import processes as processes_routes
from routes import stackcheck as stackcheck_routes
from routes import offsite as offsite_routes
from routes import services as services_routes
from routes import containers_page as containers_page_routes
from routes import secretscan as secretscan_routes
from routes import gitops as gitops_routes
from routes import docs as docs_routes
from routes.docs import _docs_call
from routes import fleet_latency as fleet_latency_routes
from routes import drhandbook as drhandbook_routes
from routes import accessreview as accessreview_routes
from routes import secwatch as secwatch_routes
from routes import servicecatalog as servicecatalog_routes
from routes import alarms as alarms_routes
from routes import compliance as compliance_routes
from routes import files as files_routes
from routes import auth_extra as auth_extra_routes
from routes import passkeys as passkeys_routes
from routes import sso as sso_routes
from routes import account_email as account_email_routes
from routes import systools as systools_routes
from routes import vms as vms_routes
from routes import docker as docker_routes
from routes import terminal as terminal_routes
from routes import vulns as vulns_routes
from routes import settings_page as settings_page_routes
from routes import autoupdate as autoupdate_routes
from routes import wol as wol_routes
from routes.wol import _wol_public, _lan_mark_wol
import systools
import security
import geoip
import agent
import remotes
import mcp_server
import attachments
import wiki
geoip.on_update.append(security.geoblock_reapply)
import audit
import healthchecks
import insights
import access
import docs
import autoupdate
import apidocs
import latency
import notify
import alerting
import tasks
import websec
import versions
import oidc
import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pi_dashboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: a pool big enough that short jobs never wait behind long ones (default: CPUs + 4)
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=SHARED_POOL_SIZE, thread_name_prefix="shared"))
    # Initial git check & background poller
    await asyncio.to_thread(updater.refresh_git_info)
    try:
        insights.record_start(updater.update_state.get("current_commit", ""))
    except Exception as e:
        logger.warning(f"Could not record start: {e}")
    poller_task = asyncio.create_task(updater.background_update_poller())
    await asyncio.to_thread(systools.restore_governor)
    history_task = asyncio.create_task(history.background_sampler())
    health_task = asyncio.create_task(healthchecks.background_checker())
    autoupdate_task = asyncio.create_task(autoupdate.background_runner())
    notify_task = asyncio.create_task(notify.background_runner())
    alerting_task = asyncio.create_task(alerting.background_runner())
    tasks_task = asyncio.create_task(tasks.background_runner())
    scheduler_task = asyncio.create_task(scheduler.background_runner())
    latency_task = None if AGENT else asyncio.create_task(latency.background_runner())
    if not AGENT:
        await mcp_server.reload()
    yield
    await mcp_server.shutdown()
    autoupdate_task.cancel()
    notify_task.cancel()
    alerting_task.cancel()
    tasks_task.cancel()
    scheduler_task.cancel()
    if latency_task:
        latency_task.cancel()
    # Shutdown
    poller_task.cancel()
    history_task.cancel()
    health_task.cancel()


# No /docs, /redoc, /openapi.json: /api-doku (login) documents the API
app = FastAPI(title="Raspberry Pi Server Dashboard", version="1.9.10", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Unexpected errors: say what went wrong instead of a bare "Internal Server Error"
    (the full traceback goes to the journal)."""
    logger.error(f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}", exc_info=exc)
    return JSONResponse({"detail": f"Interner Fehler: {type(exc).__name__}: {str(exc)[:200]}"}, status_code=500)

SHARED_POOL_SIZE = 32


# Shared with backend/routes/: which server a request is for, the client for remote agents, page and pool helpers
HEAVY_POOL, heavy = webutil.HEAVY_POOL, webutil.heavy
_serve_page, _md_response, _call400, _same_origin = webutil.serve_page, webutil.md_response, webutil.call400, webutil.same_origin
HOST_HEADER, HOST_CHANGED_CLOSE, AGENT = hostctx.HOST_HEADER, hostctx.HOST_CHANGED_CLOSE, hostctx.AGENT
_proxy, _remote_for = hostctx.proxy, hostctx.remote_for
_host_changed, _host_changed_response = hostctx.host_changed, hostctx.host_changed_response
auth.AGENT_MODE = AGENT
_ws_relay = hostctx._ws_relay
INTERNAL_HEADER = hostctx.INTERNAL_HEADER
INTERNAL_USER_HEADER = hostctx.INTERNAL_USER_HEADER
INTERNAL_KEY = hostctx.INTERNAL_KEY
_os_name = webutil._os_name
_safe_next = authweb._safe_next
_me = authweb._me
_login_failure = authweb._login_failure
_set_session_cookie = authweb._set_session_cookie
_session_is_remembered = authweb._session_is_remembered
_need_elevated = authweb._need_elevated
_auth_call = authweb._auth_call


@app.middleware("http")
async def remote_proxy(request: Request, call_next):
    """Innermost middleware (after login, permissions and the activity log): API calls for a
    selected remote server go to its agent, with this dashboard's permissions already checked."""
    path = request.url.path
    remote = _remote_for(request) if path.startswith("/api/") else None
    # The selection is one cookie for the whole browser: a tab opened for server A still sends
    # actions after another tab switched to server B. Pages say which server they show
    # (X-PiCore-Host); an action that would land on a different one is refused instead.
    if (path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and (path.startswith("/api/terminal/") or (path != "/api/remotes/select" and not remotes.LOCAL_ONLY.match(path)))):
        now = _host_changed(request, request.headers.get(HOST_HEADER))
        if now:
            return _host_changed_response(now)
    if not remote or remotes.LOCAL_ONLY.match(path):
        return await call_next(request)
    request.state.audit_detail = f"@{remote['name']}"
    url = remote["url"] + path + (f"?{request.url.query}" if request.url.query else "")
    headers = {"Authorization": f"Bearer {remote['token']}", "X-PiCore-User": request.state.user or ""}
    for h in ("content-type", "content-length", "accept", "range"):
        if h in request.headers:
            headers[h] = request.headers[h]
    body = request.stream() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None
    try:
        req = _proxy(remote).build_request(request.method, url, headers=headers, content=body)
        resp = await _proxy(remote).send(req, stream=True)
    except httpx.HTTPError as e:
        remotes.mark_offline(remote["id"], f"Nicht erreichbar ({type(e).__name__})")
        return JSONResponse({"detail": f"{remote['name']} nicht erreichbar ({type(e).__name__}).", "remote": remote["name"]}, status_code=502)
    if path in ("/api/settings", "/api/tools/lan") and request.method == "GET" and resp.status_code == 200:
        # The account part belongs to this dashboard (the agent has no users), and so do the
        # Wake-on-LAN devices (one list for all servers)
        try:
            await resp.aread()
            data = resp.json()
            if path == "/api/settings":
                data["account"] = {**_me(request.state.user), "username": request.state.user, "public_view": auth.public_view()}
                data["wol_devices"] = [_wol_public(d) for d in wol.list_devices()]
            else:
                _lan_mark_wol(data)
            return JSONResponse(data)
        except ValueError:
            return JSONResponse({"detail": "Ungültige Antwort vom Agent."}, status_code=502)
        finally:
            await resp.aclose()
    keep = {k: v for k, v in resp.headers.items()
            if k.lower() in ("content-type", "content-disposition", "content-length", "cache-control", "content-security-policy",
                             "x-content-type-options", "content-range", "accept-ranges")}
    async def body_iter():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
    return StreamingResponse(body_iter(), status_code=resp.status_code, headers=keep)

@app.middleware("http")
async def version_history(request: Request, call_next):
    """Documentation and wiki: compare the collection before and after a change and keep the versions."""
    if request.method != "POST" or _remote_for(request):
        return await call_next(request)
    before = await asyncio.to_thread(versions.track_before, request.url.path)
    response = await call_next(request)
    if before and response.status_code < 400:
        try:
            await asyncio.to_thread(versions.track_after, before, getattr(request.state, "user", "") or "")
        except Exception as e:
            logger.warning(f"Version history failed: {e}")
    return response


# Serve static assets
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Login: everything requires a session, except the login page itself and static assets.
# With "public view" enabled, GET requests are open and only actions need a login.
# ---------------------------------------------------------------------------
PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/logout", "/api/auth/status", "/api/about", "/favicon.ico",
                "/status", "/api/public/status", "/api/auth/passkey/login/options", "/api/auth/passkey/login",
                "/api/auth/forgot", "/api/auth/reset", "/passwort", "/alarm", "/api/alarm",
                "/auth/oidc/start", "/auth/oidc/callback"}   # the public status page answers 404 while switched off


@app.middleware("http")
async def audit_log(request: Request, call_next):
    """Record changing requests in the activity log (runs inside require_login, so the user is known)."""
    response = await call_next(request)
    path = request.url.path
    if request.method in ("POST", "PUT") or path == "/api/files/download" or path.startswith("/api/files/zip/"):
        user = getattr(request.state, "audit_user", None) or getattr(request.state, "user", None) or ""
        detail = getattr(request.state, "audit_detail", "")
        if path == "/api/files/download":
            detail = request.query_params.get("path", "")
        ip = websec.client_ip(request)
        try:
            await asyncio.to_thread(audit.record, user, ip, request.method, path, response.status_code, detail)
        except Exception as e:
            logger.warning(f"Activity log failed: {e}")
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if AGENT:
        # Agent: only the token-authenticated main dashboard, no pages, no users
        if path.startswith("/static/"):
            return await call_next(request)
        if path.startswith("/api/"):
            if not agent.check(request.headers.get("authorization", "")):
                return JSONResponse({"detail": "Agent: Token fehlt oder falsch."}, status_code=401)
            if not agent.allowed(websec.client_ip(request)):
                return JSONResponse({"detail": "Agent: diese Adresse ist nicht freigegeben (./agent.sh allow)."}, status_code=403)
            request.state.user = agent.remote_user(request.headers)
            request.state.audit_user = request.state.user
            return await call_next(request)
        return HTMLResponse(agent.landing_html())
    token = request.cookies.get(auth.COOKIE_NAME)
    user = auth.user_from_token(token)
    ip = websec.client_ip(request)
    if user:
        auth.touch_session(auth.session_id(token), ip)
    # In-process calls from the MCP server: the key only exists in this process's memory
    internal = request.headers.get(INTERNAL_HEADER)
    if internal and hmac.compare_digest(internal, INTERNAL_KEY):
        user = auth.active_user(request.headers.get(INTERNAL_USER_HEADER, ""))
        request.state.via_mcp = True
        request.state.audit_user = f"{user} (MCP)" if user else ""
    request.state.user = user

    # Share and exchange links are public on purpose (token, expiry and limits are checked in their routes)
    if path.startswith(("/static/", "/s/", "/x/", "/api/x/", "/hook/")) or path in PUBLIC_PATHS:
        return await call_next(request)
    # The GitHub webhook authenticates itself via HMAC - but only if a secret is configured
    if path == "/api/webhook/github" and updater.WEBHOOK_SECRET:
        return await call_next(request)

    is_read = request.method in ("GET", "HEAD")
    perm = access.required_permission(request.method, path)
    if user is None:
        # Not logged in: only plain reads of the dashboard, and only with "view without login"
        if not (perm is None and is_read and auth.public_view()):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Nicht angemeldet."}, status_code=401)
            # Back to this page after signing in (the browser keeps the #section across the redirect)
            target = path + (f"?{request.url.query}" if request.url.query else "")
            if request.method == "GET" and path not in ("/", "/login"):
                return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
            return RedirectResponse("/login", status_code=303)
    elif perm not in (None, "login") and not auth.can(user, perm):
        label = next((p[1] for p in auth.PERMISSIONS if p[0] == perm), perm)
        if path.startswith("/api/"):
            return JSONResponse({"detail": f"Keine Berechtigung: {label}", "permission": perm}, status_code=403)
        return RedirectResponse(f"/?denied={perm}", status_code=303)

    if not is_read and not _same_origin(request):
        return JSONResponse({"detail": "Ungültige Herkunft der Anfrage."}, status_code=403)

    if user and not getattr(request.state, "via_mcp", False):
        # Two-factor login required for this group, but not set up yet: only the setup is open
        info = auth.user_info(user)
        if (info and not info["totp"] and not info["passkeys"] and websec.twofa_required(info["group"]) and not _twofa_setup_path(path)
                and not (oidc.counts_as_2fa() and auth.session_method(auth.session_id(token)) == "sso")):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Bitte zuerst die Zwei-Faktor-Anmeldung einrichten.", "setup_2fa": True}, status_code=403)
            return RedirectResponse("/2fa", status_code=303)
        # Areas that are closed for the internet, whoever asks
        if websec.blocked_from_outside(perm, ip):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Nur aus dem Heimnetz oder VPN erreichbar.", "restricted": True}, status_code=403)
            return RedirectResponse(f"/?denied={perm}", status_code=303)
        # Critical changes: password (and code) again, valid for a few minutes
        if websec.needs_reauth(perm, request.method) and not websec.elevated_until(websec.session_key(token or "")):
            return JSONResponse({"detail": "Bitte zur Bestätigung das Passwort erneut eingeben.", "reauth": True}, status_code=403)

    return await call_next(request)


TWOFA_SETUP_PATHS = ("/2fa", "/api/auth/2fa", "/api/auth/logout", "/api/auth/status", "/api/me", "/api/about", "/api/auth/reauth",
                     "/api/auth/passkey")


def _twofa_setup_path(path: str) -> bool:
    return path.startswith(TWOFA_SETUP_PATHS) or path.startswith("/static/")


@app.middleware("http")
async def security_layer(request: Request, call_next):
    """Outermost: rate limit for requests from the internet without login (a wall display in the
    home network with "view without login" polls a lot), security headers on every answer."""
    path = request.url.path
    if not AGENT and not path.startswith("/static/") and not request.headers.get(INTERNAL_HEADER):
        ip = websec.client_ip(request)
        if not websec.is_internal(ip) and not auth.user_from_token(request.cookies.get(auth.COOKIE_NAME)) and not websec.rate_ok(ip):
            return JSONResponse({"detail": "Zu viele Anfragen. Bitte kurz warten."}, status_code=429, headers={"Retry-After": "30"})
    response = await call_next(request)
    websec.apply_headers(request, response)
    return response


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False
    code: str = ""


@app.get("/login")
async def login_page(request: Request, next: str = ""):
    if request.state.user:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _serve_page("login.html")


_about_cache: dict = {"ts": 0.0, "data": None}


def _about() -> dict:
    """Version info for the footer: commit, its date and the repo link (never with credentials)."""
    now = time.time()
    if _about_cache["data"] and now - _about_cache["ts"] < 60:
        return _about_cache["data"]
    url = updater.update_state.get("remote_url") or ""
    url = re.sub(r"^https?://[^/@]*@", "https://", url)              # drop user:token@
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    url = re.sub(r"\.git$", "", url) if url.startswith("https://") else ""
    date = ""
    try:
        date = subprocess.run(["git", "-C", BASE_DIR, "log", "-1", "--format=%cs"],
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    commit = updater.update_state.get("current_commit") or ""
    data = {"name": "HostCore Server", "commit": commit if re.fullmatch(r"[0-9a-f]{4,40}", commit) else "",
            "date": date, "repo": url, "year": time.localtime().tm_year, "version": updater.update_state.get("version") or ""}
    _about_cache.update(ts=now, data=data)
    return data


@app.get("/api/about")
async def api_about(request: Request):
    data = await asyncio.to_thread(_about)
    if not request.state.user:
        # Without login no version: it would tell an attacker which fixes are missing
        return {"name": data["name"], "year": data["year"], "commit": "", "date": "", "repo": "", "version": ""}
    return data


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    return {"configured": auth.is_configured(), "public_view": auth.public_view(), "sso_login": oidc.login_info(),
            **_me(request.state.user)}


@app.get("/api/me")
async def api_me(request: Request):
    return _me(request.state.user)


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    ip = websec.client_ip(request)
    if not auth.is_configured():
        raise HTTPException(status_code=503, detail="Noch kein Passwort gesetzt. Auf dem Server ausführen: ~/pi-dashboard/set-password.sh")
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")

    wait = max(auth.lockout_remaining(ip), auth.account_lockout_remaining(req.username.strip()))
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")

    request.state.audit_user = req.username.strip()[:32]
    username = await asyncio.to_thread(auth.verify_login, req.username.strip(), req.password)
    if not username:
        request.state.audit_detail = "Falsches Passwort"
        _login_failure(ip, req.username.strip())
        wait = auth.lockout_remaining(ip)
        raise HTTPException(status_code=401 if not wait else 429,
                            detail="Benutzername oder Passwort falsch." if not wait else f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    if not oidc.local_login_allowed(auth.user_info(username)["group"]):
        request.state.audit_detail = "Lokale Anmeldung gesperrt (nur SSO)"
        raise HTTPException(status_code=403, detail=f"Bitte über „{oidc.config()['label']}“ anmelden – die Anmeldung mit Passwort ist Administratoren vorbehalten.")
    recovery_left = None
    if auth.totp_enabled(username):
        if not req.code.strip():
            return JSONResponse({"ok": False, "twofa": True})      # password right: now the code
        kind = await asyncio.to_thread(auth.totp_check, username, req.code)
        if not kind:
            request.state.audit_detail = "Falscher Zwei-Faktor-Code"
            _login_failure(ip, username)
            raise HTTPException(status_code=401, detail="Code falsch oder schon benutzt.")
        if kind == "recovery":
            recovery_left = auth.totp_recovery_left(username)
            request.state.audit_detail = "Mit Wiederherstellungscode"
            websec.record_event("warning", f"Anmeldung mit Wiederherstellungscode: {username}", f"{ip} · noch {recovery_left} Codes")

    auth.record_success(ip)
    auth.record_account_success(username)
    request.state.audit_detail = getattr(request.state, "audit_detail", "") or ("Angemeldet bleiben" if req.remember else "")
    request.state.audit_user = username   # typed in any case, stored in one spelling
    logger.info(f"Login '{username}' from {ip}")
    known = auth.known_ips(username)
    agent_str = request.headers.get("user-agent", "")
    response = JSONResponse({"ok": True, "user": username, "recovery_left": recovery_left})
    token = _set_session_cookie(response, request, username, req.remember)
    websec.elevate(websec.session_key(token))      # a fresh login counts as confirmation
    await asyncio.to_thread(websec.check_new_login, username, ip, known, agent_str)
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    auth.revoke_session(auth.session_id(request.cookies.get(auth.COOKIE_NAME)))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Confirm again, two-factor login, sessions
# ---------------------------------------------------------------------------
app.include_router(auth_extra_routes.router)   # backend/routes/auth_extra.py


# ---------------------------------------------------------------------------
# Passkeys
# ---------------------------------------------------------------------------
app.include_router(passkeys_routes.router)   # backend/routes/passkeys.py


# ---------------------------------------------------------------------------
# Single sign-on (OpenID Connect, see oidc.py)
# ---------------------------------------------------------------------------
app.include_router(sso_routes.router)   # backend/routes/sso.py


# ---------------------------------------------------------------------------
# E-mail address and "Passwort vergessen"
# ---------------------------------------------------------------------------
app.include_router(account_email_routes.router)   # backend/routes/account_email.py


# ---------------------------------------------------------------------------
# Version history (documentation, wiki)
# ---------------------------------------------------------------------------
app.include_router(versions_routes.router)   # backend/routes/versions.py


# ---------------------------------------------------------------------------
# Settings page API
# ---------------------------------------------------------------------------
app.include_router(settings_page_routes.router)   # backend/routes/settings_page.py


# ---------------------------------------------------------------------------
# Processes: search + end (own processes only)
# ---------------------------------------------------------------------------
app.include_router(processes_routes.router)   # backend/routes/processes.py


# ---------------------------------------------------------------------------
# File explorer + storage analysis (read-only, always requires login)
# ---------------------------------------------------------------------------
app.include_router(files_routes.router)   # backend/routes/files.py


# ---------------------------------------------------------------------------
# System tools page
# ---------------------------------------------------------------------------
app.include_router(systools_routes.router)   # backend/routes/systools.py


async def internal_call(user: str, method: str, path: str, params: Optional[dict] = None, json_body: Any = None,
                        host: str = "", timeout: float = 120) -> Any:
    """Call this dashboard's own API in-process as `user` (permissions, activity log and remote-Pi
    forwarding apply as for a browser). host = remote server id, '' = this server."""
    transport = httpx.ASGITransport(app=app)
    headers = {INTERNAL_HEADER: INTERNAL_KEY, INTERNAL_USER_HEADER: user}
    cookies = {remotes.COOKIE: host} if host else None
    async with httpx.AsyncClient(transport=transport, base_url="http://picore.internal", timeout=timeout, cookies=cookies) as client:
        res = await client.request(method, path, params={k: v for k, v in (params or {}).items() if v is not None},
                                   json=json_body, headers=headers)
    try:
        data = res.json()
    except ValueError:
        data = {"text": res.text[:60000]}
    if res.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise InternalCallError(res.status_code, str(detail or f"Fehler {res.status_code}"))
    return data


class InternalCallError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


mcp_server.setup(internal_call)


# ---------------------------------------------------------------------------
# Web terminal
# ---------------------------------------------------------------------------
app.include_router(terminal_routes.router)   # backend/routes/terminal.py


# ---------------------------------------------------------------------------
# E-mail notifications
# ---------------------------------------------------------------------------
app.include_router(email_notify_routes.router)   # backend/routes/email_notify.py


# ---------------------------------------------------------------------------
# Data retention
# ---------------------------------------------------------------------------
app.include_router(retention_routes.router)   # backend/routes/retention.py


# ---------------------------------------------------------------------------
# Alarms: acknowledgement and escalation (per server)
# ---------------------------------------------------------------------------
app.include_router(alarms_routes.router)   # backend/routes/alarms.py


# ---------------------------------------------------------------------------
# Tasks: long-running work with its own log (per server)
# ---------------------------------------------------------------------------
app.include_router(tasks_routes.router)   # backend/routes/tasks.py


# ---------------------------------------------------------------------------
# Compose security check (per server)
# ---------------------------------------------------------------------------
app.include_router(stackcheck_routes.router)   # backend/routes/stackcheck.py


# ---------------------------------------------------------------------------
# Database monitor (per server)
# ---------------------------------------------------------------------------
app.include_router(dbmonitor_routes.router)   # backend/routes/dbmonitor.py


# ---------------------------------------------------------------------------
# Backup check (per server)
# ---------------------------------------------------------------------------
app.include_router(backup_check_routes.router)   # backend/routes/backup_check.py


# ---------------------------------------------------------------------------
# Off-site backup with restic (per server)
# ---------------------------------------------------------------------------
app.include_router(offsite_routes.router)   # backend/routes/offsite.py


# ---------------------------------------------------------------------------
# Vulnerabilities, end of life, self-test (per server)
# ---------------------------------------------------------------------------
app.include_router(vulns_routes.router)   # backend/routes/vulns.py


# ---------------------------------------------------------------------------
# Virtual machines (KVM / libvirt) - page /vms, console /vm-konsole
# ---------------------------------------------------------------------------
app.include_router(vms_routes.router)   # backend/routes/vms.py


# ---------------------------------------------------------------------------
# systemd service control (tiles in the "Server-Dienste" widget)
# ---------------------------------------------------------------------------
app.include_router(services_routes.router)   # backend/routes/services.py


# ---------------------------------------------------------------------------
# Wake-on-LAN
# ---------------------------------------------------------------------------
app.include_router(wol_routes.router)   # backend/routes/wol.py


# ---------------------------------------------------------------------------
# Docker container control
# ---------------------------------------------------------------------------
app.include_router(docker_routes.router)   # backend/routes/docker.py
app.include_router(remote_servers_routes.router)   # backend/routes/remote_servers.py


# ---------------------------------------------------------------------------
# Containers page: Kubernetes, Docker, compose stacks, image updates
# ---------------------------------------------------------------------------
app.include_router(containers_page_routes.router)   # backend/routes/containers_page.py


# ---------------------------------------------------------------------------
# Secret scan (secretscan.py)
# ---------------------------------------------------------------------------
app.include_router(secretscan_routes.router)   # backend/routes/secretscan.py


# ---------------------------------------------------------------------------
# Image policies (imagepolicy.py)
# ---------------------------------------------------------------------------
app.include_router(imagepolicy_routes.router)   # backend/routes/imagepolicy.py


# ---------------------------------------------------------------------------
# GitOps: compose stacks from a Git repository (gitops.py)
# ---------------------------------------------------------------------------
app.include_router(gitops_routes.router)   # backend/routes/gitops.py


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------
app.include_router(maintenance_routes.router)   # backend/routes/maintenance.py


# ---------------------------------------------------------------------------
# Documentation: inventory, journal, runbooks, service docs
# ---------------------------------------------------------------------------
app.include_router(docs_routes.router)   # backend/routes/docs.py


# ---------------------------------------------------------------------------
# Latency between the servers, dashboard versions and capacity of the fleet
# ---------------------------------------------------------------------------
app.include_router(fleet_latency_routes.router)   # backend/routes/fleet_latency.py


# ---------------------------------------------------------------------------
# Anomalies, DNS monitoring
# ---------------------------------------------------------------------------
app.include_router(anomalies_routes.router)   # backend/routes/anomalies.py


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
app.include_router(incidents_routes.router)   # backend/routes/incidents.py


# ---------------------------------------------------------------------------
# API documentation
# ---------------------------------------------------------------------------
@app.get("/api-doku")
async def get_api_docs_page():
    return _serve_page("apidocs.html")


@app.get("/api/api-docs")
async def api_api_docs():
    return apidocs.build(app.openapi())


@app.get("/wissen")
async def get_wiki_page():
    return _serve_page("wiki.html")


def _wiki_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except wiki.WikiError as e:
        raise HTTPException(status_code=404 if "nicht gefunden" in str(e) else 400, detail=str(e))


@app.get("/api/wiki")
async def api_wiki_pages():
    return await asyncio.to_thread(wiki.pages)


@app.get("/api/wiki/templates")
async def api_wiki_templates():
    return await asyncio.to_thread(wiki.templates)


@app.get("/api/wiki/{page_id}")
async def api_wiki_page(page_id: str):
    return await asyncio.to_thread(_wiki_call, wiki.page, page_id)


@app.get("/api/wiki/{page_id}/version")
async def api_wiki_version(page_id: str, ts: int):
    return await asyncio.to_thread(_wiki_call, wiki.version, page_id, ts)


@app.post("/api/wiki")
async def api_wiki_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_wiki_call, wiki.save, None, payload, request.state.user or "")


@app.post("/api/wiki/{page_id}")
async def api_wiki_update(page_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_wiki_call, wiki.save, page_id, payload, request.state.user or "", payload.get("base"))


@app.post("/api/wiki/{page_id}/delete")
async def api_wiki_delete(page_id: str):
    result = await asyncio.to_thread(_wiki_call, wiki.delete, page_id)
    await asyncio.to_thread(attachments.delete_all, "wiki", page_id)
    return result


@app.get("/api/docs/contracts")
async def api_docs_contracts():
    return await asyncio.to_thread(docs.contracts)


@app.post("/api/docs/contracts")
async def api_docs_contract_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_docs_call, docs.contract_save, None, payload, request.state.user or "")


@app.post("/api/docs/contracts/{contract_id}")
async def api_docs_contract_update(contract_id: str, payload: dict):
    return await asyncio.to_thread(_docs_call, docs.contract_save, contract_id, payload)


@app.post("/api/docs/contracts/{contract_id}/delete")
async def api_docs_contract_delete(contract_id: str):
    result = await asyncio.to_thread(_docs_call, docs.contract_delete, contract_id)
    await asyncio.to_thread(attachments.delete_all, "contracts", contract_id)
    return result


@app.get("/api/docs/snippets")
async def api_docs_snippets():
    return await asyncio.to_thread(docs.snippets)


@app.post("/api/docs/snippets")
async def api_docs_snippet_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_docs_call, docs.snippet_save, None, payload, request.state.user or "")


@app.post("/api/docs/snippets/{snippet_id}")
async def api_docs_snippet_update(snippet_id: str, payload: dict):
    return await asyncio.to_thread(_docs_call, docs.snippet_save, snippet_id, payload)


@app.post("/api/docs/snippets/{snippet_id}/delete")
async def api_docs_snippet_delete(snippet_id: str):
    return await asyncio.to_thread(_docs_call, docs.snippet_delete, snippet_id)


@app.get("/api/docs/journal")
async def api_docs_journal(q: str = "", category: str = "", limit: int = 200):
    return docs.journal(q[:100], category, limit)


@app.post("/api/docs/journal")
async def api_docs_journal_create(payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.journal_save, None, payload, request.state.user or "")


@app.post("/api/docs/journal/{entry_id}")
async def api_docs_journal_update(entry_id: str, payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.journal_save, entry_id, payload, request.state.user or "")


@app.post("/api/docs/journal/{entry_id}/delete")
async def api_docs_journal_delete(entry_id: str):
    return await asyncio.to_thread(_docs_call, docs.journal_delete, entry_id)


@app.get("/api/docs/runbooks")
async def api_docs_runbooks():
    return docs.runbooks()


@app.post("/api/docs/runbooks")
async def api_docs_runbook_create(payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.runbook_save, None, payload, request.state.user or "")


@app.post("/api/docs/runbooks/{runbook_id}")
async def api_docs_runbook_update(runbook_id: str, payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.runbook_save, runbook_id, payload, request.state.user or "")


@app.post("/api/docs/runbooks/{runbook_id}/delete")
async def api_docs_runbook_delete(runbook_id: str):
    return await asyncio.to_thread(_docs_call, docs.runbook_delete, runbook_id)


@app.post("/api/docs/runbooks/{runbook_id}/run")
async def api_docs_runbook_run(runbook_id: str, payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.run_action, runbook_id, str(payload.get("action", "")), request.state.user or "",
                                   str(payload.get("step", "")), bool(payload.get("checked", True)), str(payload.get("note", "")))


@app.get("/api/docs/services")
async def api_docs_services():
    return await asyncio.to_thread(docs.servicedocs)


@app.post("/api/docs/services")
async def api_docs_services_save(payload: dict, request: Request):
    return await asyncio.to_thread(_docs_call, docs.servicedoc_save, str(payload.get("target", "")), payload, request.state.user or "")


@app.post("/api/docs/services/delete")
async def api_docs_services_delete(payload: dict):
    return await asyncio.to_thread(_docs_call, docs.servicedoc_delete, str(payload.get("target", "")))


# ---------------------------------------------------------------------------
# Emergency handbook (drhandbook.py)
# ---------------------------------------------------------------------------
app.include_router(drhandbook_routes.router)   # backend/routes/drhandbook.py


# ---------------------------------------------------------------------------
# Change management (changes.py)
# ---------------------------------------------------------------------------
app.include_router(changes_routes.router)   # backend/routes/changes.py


# ---------------------------------------------------------------------------
# Access review (accessreview.py)
# ---------------------------------------------------------------------------
app.include_router(accessreview_routes.router)   # backend/routes/accessreview.py


# ---------------------------------------------------------------------------
# Patch deadlines, problems, baseline check, data protection, evidence package
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Security watchers: file integrity, sudo log, malware scan, Falco
# ---------------------------------------------------------------------------
app.include_router(secwatch_routes.router)   # backend/routes/secwatch.py


# ---------------------------------------------------------------------------
# Service catalog
# ---------------------------------------------------------------------------
app.include_router(servicecatalog_routes.router)   # backend/routes/servicecatalog.py


# ---------------------------------------------------------------------------
# NIS-2 deadlines, policies, suppliers, internal audits
# ---------------------------------------------------------------------------
app.include_router(compliance_routes.router)   # backend/routes/compliance.py


# ---------------------------------------------------------------------------
# Auto-Update Endpoints
# ---------------------------------------------------------------------------
app.include_router(autoupdate_routes.router)   # backend/routes/autoupdate.py
app.add_middleware(mcp_server.McpDispatch)

