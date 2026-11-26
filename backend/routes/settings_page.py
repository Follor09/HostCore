"""
Settings page API - routes moved out of app.py.
"""

import asyncio
import os
import platform

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import history
import settings_store
import system_info
import updater
import websec
import wol
from authweb import _me, _session_is_remembered, _set_session_cookie
from routes.wol import _wol_public
from weather import load_weather_location
from webutil import BASE_DIR, _os_name, logger

router = APIRouter()


class AccountRequest(BaseModel):
    current_password: str
    username: str = ""          # ignored: usernames are changed by an administrator
    new_password: str = ""


class PublicViewRequest(BaseModel):
    enabled: bool


class HiddenServicesRequest(BaseModel):
    hidden: list[str]


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
                "update_available", "commits_behind", "last_check_timestamp", "channel", "version", "latest_release")},
            "channels": updater.CHANNELS,
            "timer": sm._updater_status(states),
            "token_set": os.path.exists(os.path.join(BASE_DIR, ".git", "github-credentials")),
        },
        "history": history.get_stats(),
        "wol_devices": [_wol_public(d) for d in wol.list_devices()],
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


@router.get("/api/settings")
async def api_settings(request: Request):
    return await asyncio.to_thread(_settings_payload, request.state.user)


@router.post("/api/settings/account")
async def api_settings_account(req: AccountRequest, request: Request):
    ip = websec.client_ip(request)
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


@router.post("/api/settings/public-view")
async def api_settings_public_view(req: PublicViewRequest):
    auth.set_public_view(req.enabled)
    return {"ok": True, "public_view": req.enabled}


@router.post("/api/settings/logout-others")
async def api_settings_logout_others(request: Request):
    """Ends this user's other sessions (the current one gets a fresh cookie)."""
    await asyncio.to_thread(auth.update_user, request.state.user, logout=True)
    response = JSONResponse({"ok": True, "message": "Alle anderen Geräte wurden abgemeldet."})
    _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@router.post("/api/settings/logout-all")
async def api_settings_logout_all(request: Request):
    """Admins: every session of every user ends (except this one)."""
    await asyncio.to_thread(auth.rotate_secret)
    response = JSONResponse({"ok": True, "message": "Alle Benutzer wurden auf allen Geräten abgemeldet."})
    _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@router.post("/api/settings/services")
async def api_settings_services(req: HiddenServicesRequest):
    import services_manager as sm
    valid = {s["id"] for s in sm.available_services()}
    settings_store.set("hidden_services", [s for s in req.hidden if s in valid])
    return {"ok": True}


@router.post("/api/settings/history/clear")
async def api_settings_history_clear():
    await asyncio.to_thread(history.clear)
    return {"ok": True, "message": "Verlauf gelöscht."}
