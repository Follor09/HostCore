"""
Passkeys - routes moved out of app.py.
"""

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import oidc
import webauthn
import websec
from authweb import _auth_call, _need_elevated, _set_session_cookie
from webutil import same_origin as _same_origin

router = APIRouter()


def _rp(request: Request) -> tuple:
    """Relying party ID (host name) and origin as the browser sees the page."""
    host = (request.headers.get("host") or "").strip()
    if not host or not re.match(r"^[A-Za-z0-9.\-\[\]:]{1,260}$", host):
        raise HTTPException(status_code=400, detail="Unbekannte Adresse.")
    rp_id = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0].strip("[")
    # Prefer the Origin the browser sends: behind a proxy that is not listed as trusted the
    # scheme (or port) seen here differs from the page's, and the signed origin would never match.
    # Only taken when it names the same host, so it cannot widen what the passkey is bound to.
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    m = re.match(r"^(https?)://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+)(:\d{1,5})?$", origin)
    if m and m.group(2).strip("[]").lower() == rp_id.lower():
        return rp_id, origin
    return rp_id, f"{websec.scheme(request)}://{host}"


class PasskeyRequest(BaseModel):
    credential: dict
    name: str = ""
    remember: bool = False


@router.get("/api/auth/passkeys")
async def api_auth_passkeys(request: Request):
    return {"passkeys": auth.passkeys(request.state.user)}


@router.post("/api/auth/passkey/register/options")
async def api_passkey_register_options(request: Request):
    denied = _need_elevated(request)
    if denied:
        return denied
    rp_id, origin = _rp(request)
    user = request.state.user
    handle = await asyncio.to_thread(auth.passkey_handle, user)
    return webauthn.registration_options(user, handle, rp_id, origin, [p["id"] for p in auth.passkeys(user)])


@router.post("/api/auth/passkey/register")
async def api_passkey_register(req: PasskeyRequest, request: Request):
    denied = _need_elevated(request)
    if denied:
        return denied
    try:
        cred = await asyncio.to_thread(webauthn.verify_registration, request.state.user, req.credential)
    except webauthn.WebAuthnError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await asyncio.to_thread(_auth_call, auth.add_passkey, request.state.user, cred, req.name)
    request.state.audit_detail = f"Passkey angelegt: {req.name[:40]}"
    websec.record_event("info", f"Passkey angelegt: {request.state.user}", f"{req.name[:40]} · {websec.client_ip(request)}")
    return {"passkeys": auth.passkeys(request.state.user)}


@router.post("/api/auth/passkeys/{cred_id}/delete")
async def api_passkey_delete(cred_id: str, request: Request):
    denied = _need_elevated(request)
    if denied:
        return denied
    await asyncio.to_thread(_auth_call, auth.remove_passkey, request.state.user, cred_id)
    request.state.audit_detail = "Passkey entfernt"
    websec.record_event("warning", f"Passkey entfernt: {request.state.user}", websec.client_ip(request))
    return {"passkeys": auth.passkeys(request.state.user)}


@router.post("/api/auth/passkey/login/options")
async def api_passkey_login_options(request: Request):
    rp_id, origin = _rp(request)
    user = request.state.user          # set when confirming again; empty on the login page
    allow = [p["id"] for p in auth.passkeys(user)] if user else []
    return webauthn.authentication_options(rp_id, origin, allow, user or "")


@router.post("/api/auth/passkey/login")
async def api_passkey_login(req: PasskeyRequest, request: Request):
    ip = websec.client_ip(request)
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")
    wait = auth.lockout_remaining(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    try:
        username, stored, count = await asyncio.to_thread(webauthn.verify_authentication, req.credential, auth.find_passkey)
    except webauthn.WebAuthnError as e:
        auth.record_failure(ip)
        request.state.audit_detail = f"Passkey abgelehnt: {e}"
        raise HTTPException(status_code=401, detail=str(e))
    await asyncio.to_thread(auth.passkey_used, username, stored["id"], count)
    auth.record_success(ip)
    request.state.audit_user = username
    if request.state.user == username:
        # Confirm again (already logged in): elevate this session instead of a new login
        until = websec.elevate(websec.session_key(request.cookies.get(auth.COOKIE_NAME) or ""))
        request.state.audit_detail = "Erneut bestätigt (Passkey)"
        return {"ok": True, "until": until}
    if not oidc.local_login_allowed(auth.user_info(username)["group"]):
        request.state.audit_detail = "Lokale Anmeldung gesperrt (nur SSO)"
        raise HTTPException(status_code=403, detail=f"Bitte über „{oidc.config()['label']}“ anmelden – die Anmeldung mit Passkey ist Administratoren vorbehalten.")
    request.state.audit_detail = f"Mit Passkey: {stored.get('name', '')[:40]}"
    known = auth.known_ips(username)
    response = JSONResponse({"ok": True, "user": username})
    token = _set_session_cookie(response, request, username, req.remember, "passkey")
    websec.elevate(websec.session_key(token))
    await asyncio.to_thread(websec.check_new_login, username, ip, known, request.headers.get("user-agent", ""))
    return response
