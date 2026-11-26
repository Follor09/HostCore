"""
Login and session helpers shared by app.py and the route modules in backend/routes/:
session cookie, "remember me", re-confirmation, the current user's profile, safe redirects.
"""

import logging
import time
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

import auth
import oidc
import websec

logger = logging.getLogger("pi_dashboard")


def _safe_next(target: str) -> str:
    """Only paths of this dashboard - never another site (open redirect)."""
    target = target or "/"
    if not target.startswith("/") or target.startswith("//") or "\\" in target or target.startswith("/login"):
        return "/"
    return target


def _me(user: Optional[str]) -> dict:
    info = auth.user_info(user) if user else None
    group = auth.all_groups().get(info["group"]) if info else None
    return {"user": user, "group": info["group"] if info else None, "group_name": group["name"] if group else None,
            "permissions": sorted(auth.permissions(user)) if user else [],
            "totp": bool(info and info["totp"]), "totp_required": bool(info and websec.twofa_required(info["group"])),
            "passkeys": info["passkeys"] if info else 0, "email": info["email"] if info else "",
            "sso": bool(info and info["sso"] and oidc.enabled()), "sso_only": bool(info and info["sso_only"])}


def _login_failure(ip: str, username: str) -> None:
    auth.record_failure(ip)
    auth.record_account_failure(username)
    logger.warning(f"Failed login for '{username[:32]}' from {ip}")


def _set_session_cookie(response, request: Request, username: str, remember: bool, method: str = "password") -> str:
    old = request.cookies.get(auth.COOKIE_NAME)
    if old and auth.user_from_token(old) == username:
        auth.revoke_session(auth.session_id(old))      # replaced by the new cookie
    token, ttl = auth.make_session(username, remember, websec.client_ip(request), request.headers.get("user-agent", ""), method)
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=ttl if remember else None,  # without "remember": ends with the browser
        httponly=True, samesite="strict", path="/",
        secure=websec.scheme(request) == "https",
    )
    return token


def _session_is_remembered(request: Request) -> bool:
    try:
        expiry = int(request.cookies.get(auth.COOKIE_NAME, "").split("|")[1])
    except (IndexError, ValueError):
        return False
    return expiry - time.time() > auth.SESSION_TTL


def _need_elevated(request: Request) -> Optional[JSONResponse]:
    """Adding login methods or the reset address needs a fresh confirmation (the page's dialog asks)."""
    if not websec.elevated_until(websec.session_key(request.cookies.get(auth.COOKIE_NAME) or "")):
        return JSONResponse({"detail": "Bitte zur Bestätigung das Passwort erneut eingeben.", "reauth": True}, status_code=403)
    return None


def _auth_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except auth.AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
