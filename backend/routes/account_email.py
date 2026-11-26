"""
E-mail address and "Passwort vergessen" - routes moved out of app.py.
"""

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import auth
import websec
from authweb import _auth_call, _need_elevated
from webutil import logger, same_origin as _same_origin, serve_page as _serve_page

router = APIRouter()


class EmailRequest(BaseModel):
    email: str = ""


class ForgotRequest(BaseModel):
    login: str


class ResetRequest(BaseModel):
    token: str
    password: str


_forgot_hits: dict = {}


@router.post("/api/auth/email")
async def api_auth_email(req: EmailRequest, request: Request):
    denied = _need_elevated(request)
    if denied:
        return denied
    await asyncio.to_thread(_auth_call, auth.set_email, request.state.user, req.email)
    request.state.audit_detail = "E-Mail-Adresse geändert"
    return {"ok": True, "email": auth.user_info(request.state.user)["email"]}


def _send_reset(username: str, ip: str) -> None:
    import notify
    info = auth.user_info(username)
    base = notify.config().get("dashboard_url", "")
    if not info or not info["email"] or not base:
        logger.info(f"Password reset for '{username}' not sent (no e-mail address or dashboard address)")
        return
    token = auth.create_reset(username)
    link = f"{base}/passwort?token={token}"
    text = (f"Hallo {username},\n\nfür dein Konto am HostCore-Dashboard ({base}) wurde ein neues Passwort angefordert "
            f"(von {ip}).\n\nNeues Passwort festlegen (30 Minuten gültig, einmal benutzbar):\n{link}\n\n"
            "Warst du das nicht, ignoriere diese Mail – dein Passwort bleibt unverändert.\n")
    try:
        notify.send("[HostCore] Passwort zurücksetzen", text, None, to=[info["email"]])
    except notify.NotifyError as e:
        logger.warning(f"Password reset mail failed: {e}")


@router.post("/api/auth/forgot")
async def api_auth_forgot(req: ForgotRequest, request: Request):
    """Same answer whether or not the account exists (nobody can probe for usernames)."""
    ip = websec.client_ip(request)
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")
    if not websec.config()["password_reset"]:
        raise HTTPException(status_code=403, detail="Zurücksetzen per E-Mail ist ausgeschaltet – bitte einen Administrator fragen.")
    now = time.time()
    hits = [t for t in _forgot_hits.get(ip, []) if now - t < 3600]
    if len(hits) >= 5:
        raise HTTPException(status_code=429, detail="Zu viele Anfragen. Bitte später noch einmal versuchen.")
    _forgot_hits[ip] = hits + [now]
    username = auth.find_by_login(req.login[:200])
    request.state.audit_user = username or req.login.strip()[:32]
    if username:
        last = _forgot_hits.get(f"user:{username}", [0])[-1]
        if now - last > 300:
            _forgot_hits[f"user:{username}"] = [now]
            await asyncio.to_thread(_send_reset, username, ip)
            websec.record_event("info", f"Passwort-Zurücksetzen angefordert: {username}", ip)
    return {"ok": True, "message": "Wenn es dieses Konto mit E-Mail-Adresse gibt, ist eine Mail mit einem Link unterwegs (30 Minuten gültig)."}


@router.get("/api/auth/reset")
async def api_auth_reset_check(token: str = ""):
    name = auth.reset_user(token)
    return {"valid": bool(name), "user": name or ""}


@router.post("/api/auth/reset")
async def api_auth_reset(req: ResetRequest, request: Request):
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")
    name = await asyncio.to_thread(_auth_call, auth.consume_reset, req.token, req.password)
    ip = websec.client_ip(request)
    request.state.audit_user = name
    request.state.audit_detail = "Passwort per E-Mail-Link zurückgesetzt"
    websec.record_event("warning", f"Passwort per E-Mail zurückgesetzt: {name}", ip)
    import notify
    info = auth.user_info(name)
    if info and info["email"]:
        def tell():
            try:
                notify.send("[HostCore] Dein Passwort wurde geändert",
                            f"Hallo {name},\n\ndein Passwort am HostCore-Dashboard wurde gerade über den E-Mail-Link geändert (von {ip}). "
                            "Alle Geräte wurden abgemeldet.\n\nWarst du das nicht, sofort einen Administrator informieren.\n", None,
                            to=[info["email"]])
            except notify.NotifyError:
                pass
        threading.Thread(target=tell, daemon=True).start()
    return {"ok": True, "message": "Neues Passwort gespeichert – jetzt anmelden."}


@router.get("/passwort")
async def reset_page():
    return _serve_page("reset.html")
