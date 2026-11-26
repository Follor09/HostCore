"""
Confirm again, two-factor login, sessions - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import auth
import websec
from authweb import _auth_call, _login_failure

router = APIRouter()


class ConfirmRequest(BaseModel):
    password: str = ""
    code: str = ""


async def _confirm(request: Request, password: str, code: str, need_code: bool = True) -> None:
    """Password (and the 2FA code, if set up) of the logged-in user - with the login lockout."""
    user = request.state.user
    ip = websec.client_ip(request)
    wait = max(auth.lockout_remaining(ip), auth.account_lockout_remaining(user or ""))
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    if not user or not await asyncio.to_thread(auth.verify_login, user, password):
        _login_failure(ip, user or "")
        raise HTTPException(status_code=403, detail="Passwort ist falsch.")
    if need_code and auth.totp_enabled(user):
        if not await asyncio.to_thread(auth.totp_check, user, code):
            _login_failure(ip, user)
            raise HTTPException(status_code=403, detail="Code falsch oder schon benutzt.", headers={"X-Need-Code": "1"})
    auth.record_success(ip)


@router.post("/api/auth/reauth")
async def api_auth_reauth(req: ConfirmRequest, request: Request):
    await _confirm(request, req.password, req.code)
    until = websec.elevate(websec.session_key(request.cookies.get(auth.COOKIE_NAME) or ""))
    request.state.audit_detail = "Erneut bestätigt"
    return {"ok": True, "until": until}


@router.get("/api/auth/2fa")
async def api_auth_2fa(request: Request):
    user = request.state.user
    info = auth.user_info(user)
    return {"enabled": info["totp"], "recovery_left": auth.totp_recovery_left(user) if info["totp"] else 0,
            "required": websec.twofa_required(info["group"]),
            "elevated": bool(websec.elevated_until(websec.session_key(request.cookies.get(auth.COOKIE_NAME) or "")))}


@router.post("/api/auth/2fa/setup")
async def api_auth_2fa_setup(req: ConfirmRequest, request: Request):
    # Shortly after the login (or a confirmation) no password: that is the forced first setup
    if not websec.elevated_until(websec.session_key(request.cookies.get(auth.COOKIE_NAME) or "")):
        if not req.password:
            raise HTTPException(status_code=403, detail="Bitte das Passwort eingeben.", headers={"X-Need-Password": "1"})
        await _confirm(request, req.password, req.code)
    return await asyncio.to_thread(_auth_call, auth.totp_begin, request.state.user)


@router.post("/api/auth/2fa/enable")
async def api_auth_2fa_enable(req: ConfirmRequest, request: Request):
    codes = await asyncio.to_thread(_auth_call, auth.totp_enable, request.state.user, req.code)
    request.state.audit_detail = "Zwei-Faktor-Anmeldung eingerichtet"
    websec.record_event("info", f"Zwei-Faktor-Anmeldung eingerichtet: {request.state.user}", websec.client_ip(request))
    return {"ok": True, "recovery_codes": codes}


@router.post("/api/auth/2fa/disable")
async def api_auth_2fa_disable(req: ConfirmRequest, request: Request):
    info = auth.user_info(request.state.user)
    if websec.twofa_required(info["group"]):
        raise HTTPException(status_code=400, detail="Für deine Gruppe ist die Zwei-Faktor-Anmeldung Pflicht.")
    await _confirm(request, req.password, req.code)
    await asyncio.to_thread(auth.totp_disable, request.state.user)
    request.state.audit_detail = "Zwei-Faktor-Anmeldung abgeschaltet"
    websec.record_event("warning", f"Zwei-Faktor-Anmeldung abgeschaltet: {request.state.user}", websec.client_ip(request))
    return {"ok": True}


@router.post("/api/auth/2fa/recovery")
async def api_auth_2fa_recovery(req: ConfirmRequest, request: Request):
    await _confirm(request, req.password, req.code)
    codes = await asyncio.to_thread(_auth_call, auth.totp_new_recovery, request.state.user)
    request.state.audit_detail = "Neue Wiederherstellungscodes"
    return {"ok": True, "recovery_codes": codes}
