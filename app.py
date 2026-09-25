"""
Raspberry Pi All-in-One Dashboard Server (Server & Homelab Edition).
FastAPI backend with WebSocket real-time live streaming, services monitoring,
drives explorer, Git auto-updater, diagnostics runner, and system monitoring.
"""

import asyncio
import platform
import re
import secrets
import contextlib
from html import escape as html_escape
import time
from contextlib import asynccontextmanager
import logging
import os
import subprocess
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

import system_info
import services_manager
import updater
import history
import homelab
import auth
import settings_store
import wol
import files
import terminal
import systools
import security
import monitor
import netscan
import fancurve
import audit
import sensors
import actions
import nettools
import healthchecks
import storage
import insights
import access

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pi_dashboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initial git check & background poller
    updater.refresh_git_info()
    try:
        insights.record_start(updater.update_state.get("current_commit", ""))
    except Exception as e:
        logger.warning(f"Could not record start: {e}")
    poller_task = asyncio.create_task(updater.background_update_poller())
    await asyncio.to_thread(systools.restore_governor)
    history_task = asyncio.create_task(history.background_sampler())
    health_task = asyncio.create_task(healthchecks.background_checker())
    yield
    # Shutdown
    poller_task.cancel()
    history_task.cancel()
    health_task.cancel()


app = FastAPI(title="Raspberry Pi Server Dashboard", version="1.2.0", lifespan=lifespan)

# Serve static assets
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Login: everything requires a session, except the login page itself and static assets.
# With "public view" enabled, GET requests are open and only actions need a login.
# ---------------------------------------------------------------------------
PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/logout", "/api/auth/status", "/api/about", "/favicon.ico"}


def _same_origin(request: Request) -> bool:
    """Reject cross-site form posts / fetches (CSRF) on state-changing requests."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin navigation / non-browser clients
    return origin.split("://", 1)[-1] == request.headers.get("host", "")


@app.middleware("http")
async def audit_log(request: Request, call_next):
    """Record changing requests in the activity log (runs inside require_login, so the user is known)."""
    response = await call_next(request)
    path = request.url.path
    if request.method in ("POST", "PUT") or path == "/api/files/download":
        user = getattr(request.state, "audit_user", None) or getattr(request.state, "user", None) or ""
        detail = getattr(request.state, "audit_detail", "")
        if path == "/api/files/download":
            detail = request.query_params.get("path", "")
        ip = request.client.host if request.client else ""
        try:
            await asyncio.to_thread(audit.record, user, ip, request.method, path, response.status_code, detail)
        except Exception as e:
            logger.warning(f"Activity log failed: {e}")
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    user = auth.user_from_token(request.cookies.get(auth.COOKIE_NAME))
    request.state.user = user

    if path.startswith(("/static/", "/s/")) or path in PUBLIC_PATHS:
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
            return RedirectResponse("/login", status_code=303)
    elif perm not in (None, "login") and not auth.can(user, perm):
        label = next((p[1] for p in auth.PERMISSIONS if p[0] == perm), perm)
        if path.startswith("/api/"):
            return JSONResponse({"detail": f"Keine Berechtigung: {label}", "permission": perm}, status_code=403)
        return RedirectResponse(f"/?denied={perm}", status_code=303)

    if not is_read and not _same_origin(request):
        return JSONResponse({"detail": "Ungültige Herkunft der Anfrage."}, status_code=403)

    return await call_next(request)


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


@app.get("/login")
async def login_page(request: Request):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
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
    data = {"name": "PiCore Server", "commit": commit if re.fullmatch(r"[0-9a-f]{4,40}", commit) else "",
            "date": date, "repo": url, "year": time.localtime().tm_year}
    _about_cache.update(ts=now, data=data)
    return data


@app.get("/api/about")
async def api_about():
    return await asyncio.to_thread(_about)


def _me(user: Optional[str]) -> dict:
    info = auth.user_info(user) if user else None
    group = auth.all_groups().get(info["group"]) if info else None
    return {"user": user, "group": info["group"] if info else None, "group_name": group["name"] if group else None,
            "permissions": sorted(auth.permissions(user)) if user else []}


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    return {"configured": auth.is_configured(), "public_view": auth.public_view(), **_me(request.state.user)}


@app.get("/api/me")
async def api_me(request: Request):
    return _me(request.state.user)


@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not auth.is_configured():
        raise HTTPException(status_code=503, detail="Noch kein Passwort gesetzt. Auf dem Pi ausführen: ~/pi-dashboard/set-password.sh")
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")

    wait = auth.lockout_remaining(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")

    request.state.audit_user = req.username.strip()[:32]
    username = await asyncio.to_thread(auth.verify_login, req.username.strip(), req.password)
    if not username:
        request.state.audit_detail = "Falsches Passwort"
        auth.record_failure(ip)
        logger.warning(f"Failed login for '{req.username[:32]}' from {ip}")
        wait = auth.lockout_remaining(ip)
        raise HTTPException(status_code=401 if not wait else 429,
                            detail="Benutzername oder Passwort falsch." if not wait else f"Zu viele Fehlversuche. Bitte {wait} s warten.")

    auth.record_success(ip)
    request.state.audit_detail = "Angemeldet bleiben" if req.remember else ""
    request.state.audit_user = username   # typed in any case, stored in one spelling
    logger.info(f"Login '{username}' from {ip}")
    response = JSONResponse({"ok": True, "user": username})
    _set_session_cookie(response, request, username, req.remember)
    return response


def _set_session_cookie(response, request: Request, username: str, remember: bool) -> None:
    token, ttl = auth.make_session(username, remember)
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=ttl if remember else None,  # without "remember": ends with the browser
        httponly=True, samesite="strict", path="/",
        secure=request.url.scheme == "https",
    )


def _session_is_remembered(request: Request) -> bool:
    try:
        expiry = int(request.cookies.get(auth.COOKIE_NAME, "").split("|")[1])
    except (IndexError, ValueError):
        return False
    return expiry - time.time() > auth.SESSION_TTL


@app.post("/api/auth/logout")
async def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Settings page API
# ---------------------------------------------------------------------------
class AccountRequest(BaseModel):
    current_password: str
    username: str = ""          # ignored: usernames are changed by an administrator
    new_password: str = ""


class PublicViewRequest(BaseModel):
    enabled: bool


class HiddenServicesRequest(BaseModel):
    hidden: list[str]


def _os_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def _settings_payload(user: str) -> dict:
    import services_manager as sm
    updater.refresh_git_info()
    states = sm.get_unit_states([sm.UPDATER_TIMER, sm.UPDATER_SERVICE])
    return {
        "account": {**_me(user), "username": user, "public_view": auth.public_view()},
        "weather": load_weather_location(),
        "services": {"available": sm.available_services(),
                     "hidden": settings_store.get("hidden_services", [])},
        "updater": {
            **{k: updater.update_state.get(k) for k in
               ("is_git_repo", "branch", "current_commit", "commit_message", "remote_url",
                "update_available", "commits_behind", "last_check_timestamp")},
            "timer": sm._updater_status(states),
            "token_set": os.path.exists(os.path.join(BASE_DIR, ".git", "github-credentials")),
        },
        "history": history.get_stats(),
        "wol_devices": wol.list_devices(),
        "system": {
            "hostname": platform.node(),
            "model": system_info.get_pi_model(),
            "os": _os_name(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "uptime": system_info.get_uptime()["formatted"],
            "ip": system_info.get_ip_addresses(),
        },
    }


@app.get("/api/settings")
async def api_settings(request: Request):
    return await asyncio.to_thread(_settings_payload, request.state.user)


@app.post("/api/settings/account")
async def api_settings_account(req: AccountRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    wait = auth.lockout_remaining(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    if not await asyncio.to_thread(auth.verify_login, request.state.user, req.current_password):
        auth.record_failure(ip)
        raise HTTPException(status_code=403, detail="Aktuelles Passwort ist falsch.")
    auth.record_success(ip)

    if not req.new_password:
        raise HTTPException(status_code=400, detail="Bitte ein neues Passwort eingeben.")
    try:
        await asyncio.to_thread(auth.update_user, request.state.user, password=req.new_password)
    except auth.AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Password changed by '{request.state.user}' from {ip}")
    request.state.audit_detail = "Eigenes Passwort geändert"
    response = JSONResponse({"ok": True, "message": "Gespeichert. Deine anderen Geräte wurden abgemeldet.", "user": request.state.user})
    _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@app.post("/api/settings/public-view")
async def api_settings_public_view(req: PublicViewRequest):
    auth.set_public_view(req.enabled)
    return {"ok": True, "public_view": req.enabled}


@app.post("/api/settings/logout-others")
async def api_settings_logout_others(request: Request):
    """Ends this user's other sessions (the current one gets a fresh cookie)."""
    await asyncio.to_thread(auth.update_user, request.state.user, logout=True)
    response = JSONResponse({"ok": True, "message": "Alle anderen Geräte wurden abgemeldet."})
    _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@app.post("/api/settings/logout-all")
async def api_settings_logout_all(request: Request):
    """Admins: every session of every user ends (except this one)."""
    await asyncio.to_thread(auth.rotate_secret)
    response = JSONResponse({"ok": True, "message": "Alle Benutzer wurden auf allen Geräten abgemeldet."})
    _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@app.post("/api/settings/services")
async def api_settings_services(req: HiddenServicesRequest):
    import services_manager as sm
    valid = {s["id"] for s in sm.available_services()}
    settings_store.set("hidden_services", [s for s in req.hidden if s in valid])
    return {"ok": True}


@app.post("/api/settings/history/clear")
async def api_settings_history_clear():
    await asyncio.to_thread(history.clear)
    return {"ok": True, "message": "Verlauf gelöscht."}


# ---------------------------------------------------------------------------
# Processes: search + end (own processes only)
# ---------------------------------------------------------------------------
class SignalRequest(BaseModel):
    create_time: float
    force: bool = False


@app.get("/api/processes/search")
async def api_process_search(q: str = ""):
    return {"processes": await asyncio.to_thread(system_info.search_processes, q)}


@app.post("/api/processes/{pid}/signal")
async def api_process_signal(pid: int, req: SignalRequest, request: Request):
    result = await asyncio.to_thread(system_info.signal_process, pid, req.create_time, req.force)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"{'SIGKILL' if req.force else 'SIGTERM'} pid {pid} by '{request.state.user}': {result['message']}")
    request.state.audit_detail = f"PID {pid} · {'SIGKILL' if req.force else 'SIGTERM'} · {result['message']}"
    return result


# ---------------------------------------------------------------------------
# File explorer + storage analysis (read-only, always requires login)
# ---------------------------------------------------------------------------
async def _files_call(fn, path: str):
    try:
        return await asyncio.to_thread(fn, path)
    except files.FileAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/files/list")
async def api_files_list(path: str = "/"):
    return await _files_call(files.list_dir, path)


@app.get("/api/files/sizes")
async def api_files_sizes(path: str):
    return await _files_call(files.dir_sizes, path)


@app.get("/api/files/largest")
async def api_files_largest(path: str):
    return await _files_call(files.largest_files, path)


@app.get("/api/files/download")
async def api_files_download(path: str, request: Request):
    real = await _files_call(files.download_path, path)
    logger.info(f"Download {real} by '{request.state.user}'")
    return FileResponse(real, filename=os.path.basename(real))


class ShareRequest(BaseModel):
    path: str
    hours: int = 24


@app.get("/api/files/preview")
async def api_files_preview(path: str):
    return await _files_call(files.preview, path)


@app.get("/api/files/raw")
async def api_files_raw(path: str):
    """Images for the preview only - served inline, sandboxed, never HTML/SVG."""
    real = await _files_call(files.download_path, path)
    media = files.image_type(real)
    if not media:
        raise HTTPException(status_code=400, detail="Keine Bilddatei.")
    return FileResponse(real, media_type=media, headers={
        "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "no-store"})


@app.put("/api/files/upload")
async def api_files_upload(request: Request, dir: str, name: str, overwrite: bool = False):
    length = request.headers.get("content-length")
    size = int(length) if length and length.isdigit() else None
    request.state.audit_detail = f"{dir.rstrip('/')}/{name}"[:250]
    try:
        target = await asyncio.to_thread(files.upload_target, dir, name, overwrite, size)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Eine Datei mit diesem Namen gibt es dort schon.")
    except files.FileAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Write next to the target first, rename when complete: no half files on abort
    tmp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.upload-{secrets.token_hex(4)}")
    written = 0
    try:
        with open(tmp, "xb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > files.MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Datei zu groß (max. 4 GB).")
                await asyncio.to_thread(f.write, chunk)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    logger.info(f"Upload {target} ({written} bytes) by '{request.state.user}'")
    return {"ok": True, "path": target, "size": written}


@app.post("/api/files/share")
async def api_files_share(req: ShareRequest, request: Request):
    request.state.audit_detail = f"{req.path[:200]} · {req.hours} Std."
    return await _files_call(lambda p: files.create_share(p, req.hours, request.state.user), req.path)


@app.get("/api/files/shares")
async def api_files_shares():
    return {"shares": await asyncio.to_thread(files.list_shares)}


@app.post("/api/files/shares/{token}/delete")
async def api_files_share_delete(token: str):
    await asyncio.to_thread(files.delete_share, token)
    return {"shares": await asyncio.to_thread(files.list_shares)}


@app.get("/s/{token}")
async def public_share(token: str, request: Request):
    """Public download for a share link (no login) - expiry and file checked on every call."""
    try:
        share = await asyncio.to_thread(files.shared_file, token)
    except files.FileAccessError as e:
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Link ungültig</title>"
                            f"<body style='font-family:sans-serif;padding:40px'><h2>{html_escape(str(e))}</h2>"
                            f"<p>Bitte bei der Person nachfragen, die dir den Link geschickt hat.</p>", status_code=404)
    ip = request.client.host if request.client else ""
    await asyncio.to_thread(audit.record, "Freigabe-Link", ip, "GET", "/s/", 200, share["path"])
    return FileResponse(share["path"], filename=share["name"])


@app.get("/api/nvme/history")
async def api_nvme_history(days: int = 90):
    return await asyncio.to_thread(history.get_nvme_history, days)


# ---------------------------------------------------------------------------
# System tools page
# ---------------------------------------------------------------------------
@app.get("/api/tools/network")
async def api_tools_network():
    return await asyncio.to_thread(systools.network_details)


@app.get("/api/tools/hardware")
async def api_tools_hardware():
    return await asyncio.to_thread(systools.hardware_info)


class GovernorRequest(BaseModel):
    governor: str


class BenchmarkRequest(BaseModel):
    kind: str          # "disk" or "cpu"
    mount: str = "/"


@app.get("/api/tools/benchmark")
async def api_tools_benchmarks():
    return await asyncio.to_thread(systools.benchmark_history)


@app.post("/api/tools/benchmark")
async def api_tools_run_benchmark(req: BenchmarkRequest, request: Request):
    try:
        entry = await asyncio.to_thread(systools.run_benchmark, req.kind, req.mount)
    except (ValueError, PermissionError, files.FileAccessError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Benchmark {req.kind} {req.mount} by '{request.state.user}': {entry}")
    request.state.audit_detail = f"{req.kind} {req.mount}"
    return entry


@app.get("/api/tools/log-units")
async def api_tools_log_units():
    return await asyncio.to_thread(systools.journal_units)


@app.get("/api/tools/logs")
async def api_tools_logs(unit: str = "", priority: str = "all", since: str = "1h", q: str = ""):
    try:
        return await asyncio.to_thread(systools.journal_search, unit, priority, since, q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CleanupRequest(BaseModel):
    item: str


@app.get("/api/tools/cleanup")
async def api_tools_cleanup_status():
    return await asyncio.to_thread(systools.cleanup_status)


@app.post("/api/tools/cleanup")
async def api_tools_cleanup(req: CleanupRequest, request: Request):
    try:
        result = await asyncio.to_thread(systools.cleanup_run, req.item)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Cleanup {req.item} by '{request.state.user}': freed {result['freed']} bytes")
    request.state.audit_detail = f"{req.item} · {round(result['freed'] / 1e6)} MB"
    return result


@app.get("/api/tools/cpu-mode")
async def api_tools_cpu_mode():
    return await asyncio.to_thread(systools.cpu_mode)


@app.post("/api/tools/cpu-mode")
async def api_tools_set_cpu_mode(req: GovernorRequest, request: Request):
    request.state.audit_detail = req.governor[:20]
    result = await asyncio.to_thread(systools.set_governor, req.governor)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"CPU governor -> {req.governor} by '{request.state.user}'")
    return {**result, **await asyncio.to_thread(systools.cpu_mode)}


class UnbanRequest(BaseModel):
    ip: str


class FirewallRequest(BaseModel):
    action: str                  # enable, disable, rule, delete, preset, logging
    params: dict = {}


@app.get("/api/tools/ssh")
async def api_tools_ssh():
    return await asyncio.to_thread(security.ssh_logins)


@app.post("/api/tools/fail2ban/unban")
async def api_tools_unban(req: UnbanRequest, request: Request):
    try:
        await asyncio.to_thread(security.fail2ban_unban, req.ip)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"fail2ban unban {req.ip} by '{request.state.user}'")
    request.state.audit_detail = req.ip
    return await asyncio.to_thread(security.fail2ban_status)


@app.get("/api/tools/firewall")
async def api_tools_firewall():
    return await asyncio.to_thread(security.firewall_status)


@app.get("/api/tools/firewall/traffic")
async def api_tools_firewall_traffic(range: str = "24h", action: str = "block", direction: str = "all", q: str = ""):
    return await asyncio.to_thread(security.firewall_traffic, range, action, direction, q[:64])


@app.post("/api/tools/firewall")
async def api_tools_firewall_action(req: FirewallRequest, request: Request):
    try:
        client_ip = request.client.host if request.client else ""
        result = await asyncio.to_thread(security.firewall_action, req.action, req.params, client_ip)
    except (ValueError, TypeError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Firewall {req.action} {req.params} by '{request.state.user}'")
    request.state.audit_detail = f"{req.action} {' '.join(f'{k}={v}' for k, v in req.params.items())}".strip()
    return result


class LanRenameRequest(BaseModel):
    alias: str = ""


class LanWolRequest(BaseModel):
    name: str


class PortTestRequest(BaseModel):
    host: str
    ports: str


@app.get("/api/tools/lan")
async def api_tools_lan():
    return await asyncio.to_thread(netscan.devices)


@app.post("/api/tools/lan/scan")
async def api_tools_lan_scan(request: Request):
    try:
        result = await asyncio.to_thread(netscan.scan)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"LAN scan by '{request.state.user}': {result['last_scan']}")
    request.state.audit_detail = f"{result['last_scan']['online']} Geräte online"
    return result


@app.post("/api/tools/lan/{mac}/rename")
async def api_tools_lan_rename(mac: str, req: LanRenameRequest, request: Request):
    request.state.audit_detail = f"{mac} → {req.alias[:40]}"
    try:
        return await asyncio.to_thread(netscan.rename, mac, req.alias)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tools/lan/{mac}/forget")
async def api_tools_lan_forget(mac: str, request: Request):
    request.state.audit_detail = mac[:20]
    try:
        return await asyncio.to_thread(netscan.forget, mac)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tools/lan/{mac}/wol")
async def api_tools_lan_wol(mac: str, req: LanWolRequest, request: Request):
    request.state.audit_detail = f"{req.name[:40]} ({mac[:20]})"
    try:
        await asyncio.to_thread(wol.add_device, req.name, mac, "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await asyncio.to_thread(netscan.devices)


class NotesRequest(BaseModel):
    text: str
    base: int = 0         # "updated" of the version the edit started from


@app.get("/api/notes")
async def api_notes():
    return settings_store.get("notes", {"text": "", "updated": 0, "user": ""})


@app.post("/api/notes")
async def api_notes_save(req: NotesRequest, request: Request):
    if len(req.text) > 20000:
        raise HTTPException(status_code=400, detail="Notizen dürfen höchstens 20 000 Zeichen lang sein.")
    current = settings_store.get("notes", {"text": "", "updated": 0, "user": ""})
    if req.base != current.get("updated", 0) and req.text != current.get("text"):
        # Someone else saved in the meantime: don't overwrite their version
        return JSONResponse({"detail": "Die Notizen wurden inzwischen woanders geändert.", "notes": current}, status_code=409)
    notes = {"text": req.text, "updated": int(time.time() * 1000), "user": request.state.user}
    settings_store.set("notes", notes)
    return notes


class DnsRequest(BaseModel):
    name: str
    type: str = "A"
    server: str = "system"


class TracerouteRequest(BaseModel):
    host: str


class HealthCheckRequest(BaseModel):
    name: str
    url: str
    interval: int = 5
    verify_tls: bool = False


@app.post("/api/tools/dns")
async def api_tools_dns(req: DnsRequest, request: Request):
    request.state.audit_detail = f"{req.type} {req.name[:80]} @ {req.server[:40]}"
    try:
        return await asyncio.to_thread(nettools.dns_lookup, req.name, req.type, req.server)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/tools/traceroute")
async def api_tools_traceroute(req: TracerouteRequest, request: Request):
    request.state.audit_detail = req.host[:80]
    try:
        return await asyncio.to_thread(nettools.traceroute, req.host)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/health")
async def api_health():
    return await asyncio.to_thread(healthchecks.status)


@app.post("/api/settings/health")
async def api_settings_health_add(req: HealthCheckRequest, request: Request):
    request.state.audit_detail = f"{req.name[:40]} · {req.url[:100]}"
    try:
        item = await asyncio.to_thread(healthchecks.add_check, req.name, req.url, req.interval, req.verify_tls)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await healthchecks.run_check(item)
    return await asyncio.to_thread(healthchecks.status)


@app.post("/api/settings/health/{check_id}/delete")
async def api_settings_health_delete(check_id: str):
    await asyncio.to_thread(healthchecks.remove_check, check_id)
    return await asyncio.to_thread(healthchecks.status)


@app.post("/api/settings/health/{check_id}/run")
async def api_settings_health_run(check_id: str):
    check = next((c for c in healthchecks.checks() if c["id"] == check_id), None)
    if not check:
        raise HTTPException(status_code=404, detail="Prüfung nicht gefunden.")
    await healthchecks.run_check(check)
    return await asyncio.to_thread(healthchecks.status)


class LinkRequest(BaseModel):
    name: str
    url: str


def _links() -> list:
    return settings_store.get("custom_links", []) or []


@app.get("/api/links")
async def api_links():
    return {"links": _links()}


@app.post("/api/settings/links")
async def api_settings_links_add(req: LinkRequest, request: Request):
    name, url = req.name.strip()[:30], req.url.strip()[:500]
    request.state.audit_detail = f"{name} · {url[:120]}"
    if not name:
        raise HTTPException(status_code=400, detail="Bitte einen Namen angeben.")
    if not re.match(r"^https?://[^\s<>\"']+$", url, re.I):
        if re.match(r"^[\w.-]+(:\d+)?(/[^\s<>\"']*)?$", url):
            url = "http://" + url
        else:
            raise HTTPException(status_code=400, detail="Adresse muss mit http:// oder https:// beginnen.")
    links = _links()
    if len(links) >= 20:
        raise HTTPException(status_code=400, detail="Höchstens 20 Links.")
    links.append({"id": secrets.token_hex(5), "name": name, "url": url})
    settings_store.set("custom_links", links)
    return {"links": links}


@app.post("/api/settings/links/{link_id}/delete")
async def api_settings_links_delete(link_id: str):
    links = [l for l in _links() if l["id"] != link_id]
    settings_store.set("custom_links", links)
    return {"links": links}


@app.post("/api/settings/links/{link_id}/move")
async def api_settings_links_move(link_id: str, direction: int = -1):
    links = _links()
    i = next((n for n, l in enumerate(links) if l["id"] == link_id), None)
    j = None if i is None else i + (1 if direction > 0 else -1)
    if i is not None and 0 <= j < len(links):
        links[i], links[j] = links[j], links[i]
        settings_store.set("custom_links", links)
    return {"links": links}


class UserCreateRequest(BaseModel):
    username: str
    password: str
    group: str


class UserUpdateRequest(BaseModel):
    group: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None
    logout: bool = False


class GroupRequest(BaseModel):
    name: str
    description: str = ""
    permissions: list = []


def _auth_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except auth.AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/permissions")
async def api_permissions():
    return {"permissions": [{"id": p, "label": l, "description": d, "category": c} for p, l, d, c in auth.PERMISSIONS]}


@app.get("/api/users")
async def api_users(request: Request):
    return {"users": auth.list_users(), "groups": auth.list_groups(), "me": request.state.user,
            "permissions": [{"id": p, "label": l, "description": d, "category": c} for p, l, d, c in auth.PERMISSIONS]}


@app.post("/api/users")
async def api_users_create(req: UserCreateRequest, request: Request):
    request.state.audit_detail = f"{req.username[:32]} · {req.group[:32]}"
    user = await asyncio.to_thread(_auth_call, auth.create_user, req.username, req.password, req.group)
    return {"user": user, "users": auth.list_users(), "groups": auth.list_groups()}


@app.post("/api/users/{username}/update")
async def api_users_update(username: str, req: UserUpdateRequest, request: Request):
    changes = [k for k in ("group", "disabled", "password") if getattr(req, k) is not None] + (["logout"] if req.logout else [])
    request.state.audit_detail = f"{username[:32]}: {', '.join(changes)}" + (f" → {req.group}" if req.group else "")
    if username.casefold() == (request.state.user or "").casefold() and (req.disabled or (req.group and req.group != "admin")):
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst deaktivieren oder deine Admin-Rechte entfernen.")
    await asyncio.to_thread(_auth_call, auth.update_user, username, group=req.group, disabled=req.disabled,
                            password=req.password, logout=req.logout)
    response = JSONResponse({"users": auth.list_users(), "groups": auth.list_groups()})
    if username.casefold() == (request.state.user or "").casefold() and (req.password or req.logout):
        _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@app.post("/api/users/{username}/delete")
async def api_users_delete(username: str, request: Request):
    request.state.audit_detail = username[:32]
    if username.casefold() == (request.state.user or "").casefold():
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen.")
    await asyncio.to_thread(_auth_call, auth.delete_user, username)
    return {"users": auth.list_users(), "groups": auth.list_groups()}


@app.post("/api/groups")
async def api_groups_create(req: GroupRequest, request: Request):
    request.state.audit_detail = f"{req.name[:40]}: {', '.join(req.permissions)[:200]}"
    await asyncio.to_thread(_auth_call, auth.save_group, None, req.name, req.description, req.permissions)
    return {"groups": auth.list_groups()}


@app.post("/api/groups/{group_id}/update")
async def api_groups_update(group_id: str, req: GroupRequest, request: Request):
    request.state.audit_detail = f"{group_id}: {', '.join(req.permissions)[:200]}"
    await asyncio.to_thread(_auth_call, auth.save_group, group_id, req.name, req.description, req.permissions)
    return {"groups": auth.list_groups()}


@app.post("/api/groups/{group_id}/reset")
async def api_groups_reset(group_id: str, request: Request):
    request.state.audit_detail = group_id
    await asyncio.to_thread(_auth_call, auth.reset_group, group_id)
    return {"groups": auth.list_groups()}


@app.post("/api/groups/{group_id}/delete")
async def api_groups_delete(group_id: str, request: Request):
    request.state.audit_detail = group_id
    await asyncio.to_thread(_auth_call, auth.delete_group, group_id)
    return {"groups": auth.list_groups()}


@app.get("/api/actions")
async def api_actions():
    return await asyncio.to_thread(actions.list_actions)


@app.post("/api/actions/{action_id}/run")
async def api_actions_run(action_id: str, request: Request):
    request.state.audit_detail = action_id[:100]
    try:
        return await asyncio.to_thread(actions.run_action, action_id, request.state.user)
    except (ValueError, RuntimeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/actions/runs/{run_id}")
async def api_actions_get_run(run_id: str, offset: int = 0):
    try:
        return actions.get_run(run_id, max(0, offset))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/actions/runs/{run_id}/stop")
async def api_actions_stop(run_id: str):
    try:
        return actions.stop_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/tools/activity")
async def api_tools_activity(q: str = "", category: str = "", limit: int = 200, before: int = 0):
    return await asyncio.to_thread(audit.entries, q, category, limit, before)


@app.get("/api/tools/ports")
async def api_tools_ports():
    return await asyncio.to_thread(netscan.port_reachability)


@app.post("/api/tools/port-test")
async def api_tools_port_test(req: PortTestRequest, request: Request):
    request.state.audit_detail = f"{req.host[:60]} · {req.ports[:60]}"
    try:
        return await asyncio.to_thread(netscan.test_ports, req.host, req.ports)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class FanCurveRequest(BaseModel):
    temps: list
    speeds: list          # percent


@app.get("/api/tools/fan-curve")
async def api_tools_fan_curve():
    return await asyncio.to_thread(fancurve.status)


@app.post("/api/tools/fan-curve")
async def api_tools_fan_curve_set(req: FanCurveRequest, request: Request):
    try:
        result = await asyncio.to_thread(fancurve.set_curve, req.temps, req.speeds)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{req.temps} °C · {req.speeds} %"
    return result


@app.post("/api/tools/fan-curve/reset")
async def api_tools_fan_curve_reset():
    try:
        return await asyncio.to_thread(fancurve.reset)
    except (PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Web terminal
# ---------------------------------------------------------------------------
class TerminalTicketRequest(BaseModel):
    password: str


@app.post("/api/terminal/ticket")
async def api_terminal_ticket(req: TerminalTicketRequest, request: Request):
    """Re-check the password before handing out a one-time terminal ticket."""
    ip = request.client.host if request.client else "unknown"
    wait = auth.lockout_remaining(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    if not await asyncio.to_thread(auth.verify_login, request.state.user, req.password):
        auth.record_failure(ip)
        logger.warning(f"Terminal: wrong password for '{request.state.user}' from {ip}")
        raise HTTPException(status_code=403, detail="Passwort ist falsch.")
    auth.record_success(ip)
    if terminal.session_count() >= terminal.MAX_SESSIONS:
        raise HTTPException(status_code=429, detail=f"Maximal {terminal.MAX_SESSIONS} Terminals gleichzeitig.")
    return {"ticket": terminal.issue_ticket(request.state.user), "expires_in": terminal.TICKET_TTL}


@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket, ticket: str = "", rows: int = 24, cols: int = 80):
    # WebSockets bypass the HTTP middleware: check session, origin and ticket here
    user = auth.user_from_token(websocket.cookies.get(auth.COOKIE_NAME))
    origin = websocket.headers.get("origin", "")
    same_origin = origin.split("://", 1)[-1] == websocket.headers.get("host", "")
    await websocket.accept()
    if not user or not auth.can(user, "terminal") or not same_origin or not terminal.redeem_ticket(ticket, user):
        await websocket.close(code=4401)
        return
    client = websocket.client.host if websocket.client else "unknown"
    started = time.time()
    await asyncio.to_thread(audit.record, user, client, "WS", "/ws/terminal", 200, "geöffnet")
    try:
        await terminal.run_session(websocket, user, client, rows, cols)
    finally:
        minutes = max(1, round((time.time() - started) / 60))
        await asyncio.to_thread(audit.record, user, client, "WS", "/ws/terminal", 200, f"beendet nach {minutes} Min.")


# ---------------------------------------------------------------------------
# systemd service control (tiles in the "Server-Dienste" widget)
# ---------------------------------------------------------------------------
@app.post("/api/services/{service_id}/{action}")
async def api_service_action(service_id: str, action: str, request: Request):
    result = await asyncio.to_thread(services_manager.service_action, service_id, action)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"systemctl {action} {result['unit']} by '{request.state.user}'")
    request.state.audit_detail = f"{action} {result['unit']}"
    return result


@app.get("/api/services/{service_id}/journal")
async def api_service_journal(service_id: str, lines: int = 150):
    result = await asyncio.to_thread(services_manager.service_journal, service_id, lines)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ---------------------------------------------------------------------------
# Wake-on-LAN
# ---------------------------------------------------------------------------
class WolDeviceRequest(BaseModel):
    name: str
    mac: str
    broadcast: str = ""


@app.get("/api/wol")
async def api_wol_devices():
    return {"devices": wol.list_devices()}


@app.post("/api/wol/{device_id}/wake")
async def api_wol_wake(device_id: str, request: Request):
    try:
        device = await asyncio.to_thread(wol.wake, device_id)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Wake-on-LAN sent to {device['name']} ({device['mac']})")
    request.state.audit_detail = f"{device['name']} ({device['mac']})"
    return {"ok": True, "message": f"Weckruf an {device['name']} gesendet."}


@app.post("/api/settings/wol")
async def api_settings_wol_add(req: WolDeviceRequest):
    try:
        device = wol.add_device(req.name, req.mac, req.broadcast)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "device": device}


@app.post("/api/settings/wol/{device_id}/delete")
async def api_settings_wol_delete(device_id: str):
    wol.remove_device(device_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Docker container control
# ---------------------------------------------------------------------------
@app.post("/api/workloads/docker/{name}/{action}")
async def api_docker_action(name: str, action: str, request: Request):
    result = await asyncio.to_thread(homelab.docker_action, name, action)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"docker {action} {name} by '{request.state.user}'")
    request.state.audit_detail = f"{action} {name}"
    return result


@app.get("/api/workloads/docker/{name}/logs")
async def api_docker_logs(name: str, lines: int = 200):
    result = await asyncio.to_thread(homelab.docker_logs, name, lines)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


class CommandRequest(BaseModel):
    command: str


class WeatherLocationRequest(BaseModel):
    city: str


# Weather location, persisted in settings.json (untracked, so it survives git updates)
DEFAULT_WEATHER_LOCATION = {"city": "Berlin", "lat": 52.52, "lon": 13.41}


def load_weather_location() -> dict:
    loc = settings_store.get("weather_location")
    if isinstance(loc, dict) and {"city", "lat", "lon"} <= loc.keys():
        return loc
    return dict(DEFAULT_WEATHER_LOCATION)


def save_weather_location(loc: dict) -> None:
    settings_store.set("weather_location", loc)


# Cached weather response to prevent rate limiting
_cached_weather = {
    "temp": 20.0,
    "humidity": 50,
    "wind_speed": 10.0,
    "description": "Klar",
    "weather_code": 0,
    "city": "Berlin (Default)",
    "timestamp": 0
}


def interpret_wmo_code(code: int) -> tuple[str, str]:
    """Translate WMO weather code to German description and icon."""
    mapping = {
        0: ("Klarer Himmel", "sun"),
        1: ("Überwiegend klar", "sun-dim"),
        2: ("Teilweise bewölkt", "cloud-sun"),
        3: ("Bedeckt", "cloud"),
        45: ("Neblig", "cloud-fog"),
        48: ("Raureifnebel", "cloud-fog"),
        51: ("Leichter Nieselregen", "cloud-drizzle"),
        53: ("Nieselregen", "cloud-drizzle"),
        55: ("Starker Nieselregen", "cloud-drizzle"),
        61: ("Leichter Regen", "cloud-rain"),
        63: ("Mäßiger Regen", "cloud-rain"),
        65: ("Starker Regen", "cloud-rain-heavy"),
        71: ("Leichter Schneefall", "cloud-snow"),
        73: ("Schneefall", "cloud-snow"),
        75: ("Starker Schneefall", "cloud-snow"),
        80: ("Regenschauer", "cloud-showers"),
        81: ("Starke Schauer", "cloud-showers"),
        82: ("Heftige Schauer", "cloud-showers-heavy"),
        95: ("Gewitter", "cloud-lightning"),
        96: ("Gewitter mit Hagel", "cloud-lightning-hail"),
    }
    return mapping.get(code, ("Heiter", "cloud-sun"))


def _serve_page(name: str) -> HTMLResponse:
    """Serve an HTML page with each asset's mtime appended as ?v=..., so browsers never
    mix a new page with a cached old style.css / *.js after an auto-update."""
    with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as f:
        html = f.read()
    def versioned(m: "re.Match") -> str:
        path = os.path.join(STATIC_DIR, m.group(1))
        return f"/static/{m.group(1)}?v={int(os.path.getmtime(path))}\"" if os.path.exists(path) else m.group(0)
    html = re.sub(r'/static/([\w.-]+\.(?:js|css))"', versioned, html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def get_index():
    return _serve_page("index.html")


@app.get("/report")
async def get_report_page():
    return _serve_page("report.html")


@app.get("/settings")
async def get_settings_page():
    return _serve_page("settings.html")


@app.get("/terminal")
async def get_terminal_page():
    return _serve_page("terminal.html")


@app.get("/system")
async def get_system_page():
    return _serve_page("system.html")


@app.get("/api/system")
async def api_system():
    """Return full system snapshot."""
    return system_info.get_system_snapshot()


@app.get("/api/processes")
async def api_processes(limit: int = 8):
    """Return top active processes."""
    return {"processes": system_info.get_top_processes(limit=limit)}


@app.get("/api/history")
async def api_history(range: str = "24h", iface: str = ""):
    """Downsampled metrics history: range=24h or 7d; iface = network values of one interface."""
    return await asyncio.to_thread(history.get_history, range, iface[:32])


@app.get("/api/history/interfaces")
async def api_history_interfaces():
    return {"interfaces": await asyncio.to_thread(monitor.interfaces)}


@app.get("/api/monitor/hogs")
async def api_monitor_hogs(hours: int = 24):
    return await asyncio.to_thread(monitor.resource_hogs, 168 if hours > 24 else 24)


@app.get("/api/sensors")
async def api_sensors():
    return await asyncio.to_thread(sensors.read_all)


@app.get("/api/sensors/history")
async def api_sensors_history(hours: int = 24):
    return await asyncio.to_thread(monitor.sensor_history, 168 if hours > 24 else 24)


@app.get("/api/monitor/usage")
async def api_monitor_usage(iface: str = ""):
    return await asyncio.to_thread(monitor.data_usage, iface[:32])


@app.get("/api/events")
async def api_events(days: int = 7):
    return await asyncio.to_thread(insights.events, max(1, min(days, 30)))


@app.get("/api/status-summary")
async def api_status_summary():
    return await asyncio.to_thread(insights.status_summary)


@app.get("/api/disk-forecast")
async def api_disk_forecast():
    return await asyncio.to_thread(insights.disk_forecast)


@app.get("/api/report")
async def api_report():
    return await asyncio.to_thread(insights.weekly_report)


@app.get("/api/monitor/throttle")
async def api_monitor_throttle(days: int = 30):
    return await asyncio.to_thread(monitor.throttle_log, max(1, min(days, monitor.RETENTION_DAYS)))


@app.get("/api/monitor/uptime")
async def api_monitor_uptime(days: int = 7):
    return await asyncio.to_thread(monitor.service_uptime, max(1, min(days, monitor.RETENTION_DAYS)))


@app.get("/api/homelab")
async def api_homelab():
    """Apt updates, NVMe health, Docker containers and Kubernetes pods."""
    return await asyncio.to_thread(homelab.get_all)


class AptRunRequest(BaseModel):
    mode: str  # "update" or "upgrade"


@app.get("/api/updates/apt")
async def api_apt_status():
    """Status and log of the running / last apt update or upgrade."""
    return await asyncio.to_thread(homelab.get_apt_run)


@app.post("/api/updates/apt")
async def api_apt_start(req: AptRunRequest, request: Request):
    request.state.audit_detail = req.mode
    """Start apt update or update+full-upgrade via its systemd unit."""
    result = await asyncio.to_thread(homelab.start_apt_run, req.mode)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@app.get("/api/services")
async def api_services():
    """Return status of homelab services."""
    return {"services": services_manager.get_services_status()}


@app.get("/api/drives")
async def api_drives(refresh: bool = False):
    """Mounted partitions (legacy list) and the lsblk-style storage overview."""
    if refresh:
        storage._cache["data"] = None
    return {"drives": services_manager.get_mounted_drives(), "storage": await asyncio.to_thread(storage.overview)}


@app.get("/api/logs")
async def api_logs(lines: int = 30):
    """Return recent system journal logs."""
    return {"logs": services_manager.get_recent_logs(lines=lines)}


# ---------------------------------------------------------------------------
# Auto-Update Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/updates/status")
async def api_update_status():
    """Get current Git version info and pending update status."""
    return updater.update_state


@app.post("/api/updates/check")
async def api_update_check():
    """Trigger manual check against GitHub remote."""
    status = updater.check_for_updates()
    return status


@app.post("/api/updates/apply")
async def api_update_apply():
    """Pull latest Git commits, install requirements, and restart server."""
    result = updater.perform_update()
    return result


@app.post("/api/webhook/github")
async def api_github_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """
    GitHub Webhook receiver.
    Triggered instantly whenever a new commit is pushed to the GitHub repository.
    """
    body = await request.body()
    if not updater.verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid GitHub webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "push")
    logger.info(f"Received GitHub webhook event: {event_type}")

    if event_type in ("push", "ping"):
        # Run update in background task
        asyncio.create_task(asyncio.to_thread(updater.perform_update))
        return {"status": "ok", "message": "GitHub push event empfangen. Auto-Update gestartet!"}

    return {"status": "ignored", "event": event_type}


def _next_hours(hourly: dict, current_time: str, count: int) -> list:
    """Hourly forecast starting at the current hour (Open-Meteo times are local ISO strings)."""
    times = hourly.get("time", [])
    current_hour = current_time[:13]  # "2026-09-24T14"
    start = next((i for i, t in enumerate(times) if t[:13] >= current_hour), 0)
    return [
        {
            "time": times[i][11:16],
            "temp": round(hourly["temperature_2m"][i]),
            "weather_code": hourly["weather_code"][i],
            "precip_prob": hourly["precipitation_probability"][i] or 0,
        }
        for i in range(start, min(start + count, len(times)))
    ]


def _daily_forecast(daily: dict) -> list:
    days = []
    for i, date in enumerate(daily.get("time", [])):
        code = daily["weather_code"][i]
        days.append({
            "date": date,
            "weather_code": code,
            "description": interpret_wmo_code(code)[0],
            "temp_max": round(daily["temperature_2m_max"][i]),
            "temp_min": round(daily["temperature_2m_min"][i]),
            "precip_prob": daily["precipitation_probability_max"][i] or 0,
            "precip_sum": round(daily["precipitation_sum"][i] or 0, 1),
            "wind_max": round(daily["wind_speed_10m_max"][i] or 0),
            "uv_max": round(daily["uv_index_max"][i] or 0, 1),
            "sunrise": daily["sunrise"][i][11:16],
            "sunset": daily["sunset"][i][11:16],
        })
    return days


@app.get("/api/weather")
async def api_weather(force: bool = False):
    """Fetch live weather conditions for the configured location."""
    global _cached_weather
    import time
    now = time.time()
    loc = load_weather_location()

    if not force and now - _cached_weather["timestamp"] < 600 and _cached_weather.get("city") == loc["city"]:
        return _cached_weather

    try:
        params = {
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max,sunrise,sunset,uv_index_max",
            "forecast_days": 7,
            "timezone": "auto",
        }
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            if resp.status_code == 200:
                data = resp.json()
                cur = data.get("current", {})
                code = cur.get("weather_code", 0)
                desc, icon = interpret_wmo_code(code)

                _cached_weather = {
                    "temp": round(cur.get("temperature_2m", 20.0), 1),
                    "humidity": cur.get("relative_humidity_2m", 50),
                    "wind_speed": round(cur.get("wind_speed_10m", 0.0), 1),
                    "description": desc,
                    "icon": icon,
                    "weather_code": code,
                    "city": loc["city"],
                    "hourly": _next_hours(data.get("hourly", {}), cur.get("time", ""), 12),
                    "daily": _daily_forecast(data.get("daily", {})),
                    "timestamp": now
                }
                return _cached_weather
    except Exception as e:
        logger.warning(f"Failed to fetch weather: {e}")

    return _cached_weather


@app.post("/api/weather/location")
async def api_set_weather_location(req: WeatherLocationRequest, request: Request):
    """Look up a city via Open-Meteo geocoding, save it, and return its weather."""
    name = req.city.strip()
    request.state.audit_detail = name[:60]
    if not name:
        raise HTTPException(status_code=400, detail="Bitte einen Ort eingeben.")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": name, "count": 1, "language": "de", "format": "json"},
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
    except Exception as e:
        logger.warning(f"Geocoding failed: {e}")
        raise HTTPException(status_code=502, detail="Ortssuche fehlgeschlagen. Internetverbindung prüfen.")

    if not results:
        raise HTTPException(status_code=404, detail=f"Ort \"{name}\" nicht gefunden.")

    hit = results[0]
    loc = {"city": hit["name"], "lat": hit["latitude"], "lon": hit["longitude"]}
    save_weather_location(loc)
    logger.info(f"Weather location set to {loc['city']} ({loc['lat']}, {loc['lon']})")
    return await api_weather(force=True)


@app.post("/api/action/command")
async def api_exec_command(req: CommandRequest, request: Request):
    request.state.audit_detail = req.command[:40]
    """Execute whitelisted safe diagnostic commands."""
    allowed_commands = {
        "ping": ["ping", "-c", "3", "1.1.1.1"],
        "uptime": ["uptime"],
        "memory": ["free", "-h"],
        "disk": ["df", "-h"],
        "temp": ["vcgencmd", "measure_temp"],
        "throttled": ["vcgencmd", "get_throttled"],
        "uname": ["uname", "-a"],
        "docker": ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"]
    }

    cmd_key = req.command.strip().lower()
    if cmd_key not in allowed_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Kommando nicht erlaubt. Verfügbare Kommandos: {list(allowed_commands.keys())}"
        )

    cmd_args = allowed_commands[cmd_key]
    try:
        if cmd_key == "memory" and system_info.platform.system() == "Darwin":
            cmd_args = ["vm_stat"]
        elif cmd_key in ("temp", "throttled") and not system_info.is_raspberry_pi():
            return {
                "command": cmd_key,
                "stdout": f"[Dev-Mode]: '{cmd_key}' ist für echten Raspberry Pi reserviert. Aktuelle CPU-Temperatur: {system_info.get_cpu_temp()['celsius']}°C",
                "returncode": 0
            }

        proc = subprocess.run(cmd_args, capture_output=True, text=True, timeout=8)
        return {
            "command": cmd_key,
            "stdout": proc.stdout or proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"command": cmd_key, "error": str(e), "returncode": 1}


@app.post("/api/action/reboot")
async def api_reboot():
    """Trigger system reboot (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "reboot"])
            return {"status": "success", "message": "Raspberry Pi wird jetzt neu gestartet..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Neustart-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo reboot' ausgeführt."
        }


@app.post("/api/action/shutdown")
async def api_shutdown():
    """Trigger system shutdown (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "poweroff"])
            return {"status": "success", "message": "Raspberry Pi wird heruntergefahren..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Herunterfahren-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo poweroff' ausgeführt."
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time live push updates.
    Sends system snapshot, top processes, services, drives, and updater state every 1.5 seconds.
    """
    # Middleware doesn't cover WebSockets: check the session here
    await websocket.accept()
    if not auth.user_from_token(websocket.cookies.get(auth.COOKIE_NAME)) and not auth.public_view():
        await websocket.close(code=4401)  # the page redirects to /login on this code
        return
    logger.info("WebSocket Client connected.")
    try:
        while True:
            snapshot = system_info.get_system_snapshot()
            processes = system_info.get_top_processes(limit=6)
            services = services_manager.get_services_status()
            drives = services_manager.get_mounted_drives()
            storage_overview = storage.overview()

            payload = {
                "type": "live_update",
                "system": snapshot,
                "processes": processes,
                "services": services,
                "drives": drives,
                "storage": storage_overview,
                "updater": updater.update_state
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        logger.info("WebSocket Client disconnected.")
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")
