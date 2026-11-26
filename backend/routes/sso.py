"""
Single sign-on (OpenID Connect, see oidc.py) - routes moved out of app.py.
"""

import asyncio
import re
from html import escape as html_escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import audit
import auth
import oidc
import websec
import webutil
from authweb import _safe_next, _set_session_cookie
from webutil import logger

router = APIRouter()


def _sso_callback_url(request: Request) -> str:
    host = (request.headers.get("host") or "").strip()
    if not host or not re.match(r"^[A-Za-z0-9.\-\[\]:]{1,260}$", host):
        raise HTTPException(status_code=400, detail="Unbekannte Adresse.")
    return f"{websec.scheme(request)}://{host}/auth/oidc/callback"


def _sso_page(title: str, message: str, target: str = "", close: bool = False, status: int = 200) -> HTMLResponse:
    """Small page after the provider's redirect. Continuing with a same-site navigation (meta refresh)
    instead of a redirect: the SameSite=Strict session cookie is not sent within a chain that started
    on the provider's site, so the dashboard would look logged out."""
    refresh = f'<meta http-equiv="refresh" content="0;url={html_escape(target)}">' if target else ""
    link = f'<a class="login-public" href="{html_escape(target or "/login")}">Weiter →</a>' if not close else ""
    script = '<script src="/static/sso-close.js"></script>' if close else ""
    html = (f'<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f'{refresh}<title>{html_escape(title)} | HostCore Server</title><script src="/static/theme.js"></script>'
            f'{webutil.stylesheet_links()}</head><body class="theme-cyber login-page"><main class="login-wrap">'
            f'<div class="glass-card login-card"><h1>{html_escape(title)}</h1><p class="card-subtitle">{html_escape(message)}</p>{link}</div>'
            f'</main>{script}</body></html>')
    return HTMLResponse(html.replace("<script src=", '<script data-cfasync="false" src='), status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.get("/auth/oidc/start")
async def sso_start(request: Request, next: str = "/", remember: int = 0, reauth: int = 0):
    session, user = "", ""
    if reauth:
        token = request.cookies.get(auth.COOKIE_NAME) or ""
        user = request.state.user or ""
        if not user:
            return _sso_page("Nicht angemeldet", "Zum Bestätigen zuerst im Dashboard anmelden.", close=True, status=401)
        session = websec.session_key(token)
    try:
        url, binding = await oidc.begin(_sso_callback_url(request), _safe_next(next), bool(remember), session, user)
    except oidc.OidcError as e:
        if reauth:
            return _sso_page("Bestätigung nicht möglich", str(e), close=True, status=400)
        return RedirectResponse(f"/login?sso_error={quote(str(e), safe='')}", status_code=303)
    response = RedirectResponse(url, status_code=303)
    # Lax: comes back with the provider's top-level redirect (Strict would not)
    response.set_cookie(oidc.STATE_COOKIE, binding, max_age=oidc.FLOW_TTL, httponly=True, samesite="lax",
                        path="/auth/oidc/", secure=websec.scheme(request) == "https")
    return response


@router.get("/auth/oidc/callback")
async def sso_callback(request: Request, state: str = "", code: str = "", error: str = "", error_description: str = ""):
    ip = websec.client_ip(request)
    flow = None
    try:
        flow = oidc.take_flow(state, request.cookies.get(oidc.STATE_COOKIE) or "")
        if error:
            raise oidc.OidcError(f"Anbieter: {error_description or error}"[:200])
        claims = await oidc.finish(flow, code)
        if flow["reauth_session"]:
            username = await asyncio.to_thread(auth.find_sso, claims["iss"].rstrip("/"), str(claims["sub"]))
            if not username or username != flow["reauth_user"]:
                raise oidc.OidcError("Beim Anbieter ist ein anderes Konto angemeldet.")
        else:
            username, how = await asyncio.to_thread(oidc.resolve_user, claims)
    except (oidc.OidcError, auth.AuthError) as e:
        await asyncio.to_thread(audit.record, (flow or {}).get("reauth_user", ""), ip, "GET", "/auth/oidc/callback", 401, str(e))
        websec.record_event("warning", "SSO-Anmeldung abgelehnt", f"{ip} · {e}")
        if flow and flow["reauth_session"]:
            return _sso_page("Bestätigung fehlgeschlagen", str(e), close=True, status=401)
        response = RedirectResponse(f"/login?sso_error={quote(str(e), safe='')}", status_code=303)
        response.delete_cookie(oidc.STATE_COOKIE, path="/auth/oidc/")
        return response
    if flow["reauth_session"]:
        websec.elevate(flow["reauth_session"])
        await asyncio.to_thread(audit.record, username, ip, "GET", "/auth/oidc/callback", 200, "Erneut bestätigt (SSO)")
        response = _sso_page("Bestätigt", "Dieses Fenster schließt sich – die Aktion läuft weiter.", close=True)
        response.delete_cookie(oidc.STATE_COOKIE, path="/auth/oidc/")
        return response
    logger.info(f"SSO login '{username}' from {ip} ({how})")
    known = auth.known_ips(username)
    response = _sso_page("Angemeldet", f"Willkommen, {username}.", target=flow["next"])
    token = _set_session_cookie(response, request, username, flow["remember"], "sso")
    websec.elevate(websec.session_key(token))
    response.delete_cookie(oidc.STATE_COOKIE, path="/auth/oidc/")
    await asyncio.to_thread(audit.record, username, ip, "GET", "/auth/oidc/callback", 200, how)
    await asyncio.to_thread(websec.check_new_login, username, ip, known, request.headers.get("user-agent", ""))
    return response


def _sso_call(fn, *args):
    try:
        return fn(*args)
    except (oidc.OidcError, auth.AuthError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/settings/sso")
async def api_settings_sso(request: Request):
    return oidc.public(_sso_callback_url(request))


@router.post("/api/settings/sso")
async def api_settings_sso_save(payload: dict, request: Request):
    await asyncio.to_thread(_sso_call, oidc.save, payload)
    if oidc.enabled():
        try:
            await oidc.discovery(force=True)
        except oidc.OidcError as e:
            raise HTTPException(status_code=400, detail=f"Gespeichert, aber: {e}")
    return oidc.public(_sso_callback_url(request))


@router.post("/api/settings/sso/test")
async def api_settings_sso_test(payload: dict):
    try:
        return await oidc.test(payload)
    except oidc.OidcError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/sso/unlink")
async def api_settings_sso_unlink(payload: dict, request: Request):
    user = str(payload.get("user") or "")
    await asyncio.to_thread(_sso_call, auth.unlink_sso, user)
    request.state.audit_detail = user[:32]
    return oidc.public(_sso_callback_url(request))
