"""
Version history (documentation, wiki) - routes moved out of app.py.
"""

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

import auth
import versions
import websec
from webutil import serve_page as _serve_page

router = APIRouter()


def _version_call(fn, *args):
    try:
        return fn(*args)
    except versions.VersionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/versions")
async def api_versions(kind: str, id: str):
    return await asyncio.to_thread(_version_call, versions.item_versions, kind, id)


@router.get("/api/versions/deleted")
async def api_versions_deleted(kind: str = ""):
    return {"items": await asyncio.to_thread(versions.deleted, kind)}


@router.get("/api/versions/recent")
async def api_versions_recent(limit: int = 50):
    return {"items": await asyncio.to_thread(versions.recent, limit)}


@router.post("/api/versions/{version_id}/restore")
async def api_versions_restore(version_id: int, request: Request):
    request.state.audit_detail = f"Version {version_id}"
    return await asyncio.to_thread(_version_call, versions.restore, version_id, request.state.user or "")


@router.get("/2fa")
async def twofa_page():
    return _serve_page("twofa.html")


def _sessions_view(request: Request, username: Optional[str]) -> dict:
    current = auth.session_id(request.cookies.get(auth.COOKIE_NAME))
    items = auth.list_sessions(username)
    for s in items:
        s["current"] = s["id"] == current
        try:
            import geoip
            s["country"] = geoip.lookup(s["ip"]) if s.get("ip") else ""
        except Exception:
            s["country"] = ""
        s["internal"] = websec.is_internal(s.get("ip") or "")
    return {"sessions": items, "legacy": not current}


@router.get("/api/auth/sessions")
async def api_auth_sessions(request: Request):
    return await asyncio.to_thread(_sessions_view, request, request.state.user)


@router.post("/api/auth/sessions/{sid}/revoke")
async def api_auth_sessions_revoke(sid: str, request: Request):
    if not auth.revoke_session(sid, request.state.user):
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
    return await asyncio.to_thread(_sessions_view, request, request.state.user)


@router.get("/api/security")
async def api_security(request: Request):
    data = await asyncio.to_thread(websec.public_config)
    data["checklist"] = await asyncio.to_thread(websec.checklist, request)
    data["events"] = list(reversed(websec.events_since(time.time() - 30 * 86400)))[:50]
    data["client"] = {"ip": websec.client_ip(request), "internal": websec.is_internal(websec.client_ip(request)),
                      "peer": request.client.host if request.client else "", "scheme": websec.scheme(request)}
    return data


@router.post("/api/security")
async def api_security_save(payload: dict, request: Request):
    try:
        await asyncio.to_thread(websec.save, payload)
    except (websec.WebsecError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Never lock yourself out: the restriction must not hit the admin who sets it
    me = websec.client_ip(request)
    if websec.config()["restrict"] and "users.manage" in websec.config()["restrict_perms"] and not websec.is_internal(me):
        await asyncio.to_thread(websec.save, {"restrict": False})
        raise HTTPException(status_code=400, detail="Du bist gerade nicht im Heimnetz/VPN – die Beschränkung würde dich selbst aussperren. Aus dem Heimnetz einschalten.")
    request.state.audit_detail = "Sicherheitseinstellungen"
    return await api_security(request)


@router.get("/api/security/sessions")
async def api_security_sessions(request: Request):
    return await asyncio.to_thread(_sessions_view, request, None)


@router.post("/api/security/sessions/{sid}/revoke")
async def api_security_sessions_revoke(sid: str, request: Request):
    if not auth.revoke_session(sid):
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
    request.state.audit_detail = sid
    return await asyncio.to_thread(_sessions_view, request, None)
