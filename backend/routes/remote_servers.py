"""
Remote servers (agents) this dashboard controls: list, host switcher, add, test, rename, new
token, remove. Never forwarded (remotes.LOCAL_ONLY); managing them needs users.manage,
the switcher every logged-in user (backend/access.py).
"""

import asyncio
import platform

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import remotes

router = APIRouter()


class RemoteRequest(BaseModel):
    name: str = ""
    url: str = ""
    token: str = ""
    trust: str = ""      # certificate fingerprint the user compared and confirmed (agent with https)


def _untrusted(e: remotes.UntrustedCert) -> JSONResponse:
    """409 with the fingerprint: the page asks the user to compare it with ./agent.sh tls."""
    return JSONResponse({"detail": str(e), "fingerprint": e.fingerprint}, status_code=409)


class SelectRemoteRequest(BaseModel):
    id: str = ""


def _remote_call(fn, *args):
    try:
        return fn(*args)
    except remotes.RemoteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/remotes")
async def api_remotes():
    return {"remotes": await remotes.refresh_status(), "local": platform.node()}


@router.get("/api/remotes/current")
async def api_remotes_current(request: Request):
    """For the host switcher (every logged-in user)."""
    items = await remotes.refresh_status(max_age=30)
    cur = request.cookies.get(remotes.COOKIE, "")
    current = next((r for r in items if r["id"] == cur), None)
    return {"local": platform.node(), "current": current,
            "hosts": [{k: r[k] for k in ("id", "name", "online", "info")} for r in items]}


@router.post("/api/remotes/select")
async def api_remotes_select(req: SelectRemoteRequest, request: Request):
    resp = JSONResponse({"ok": True, "id": req.id})
    if req.id and remotes.get(req.id):
        resp.set_cookie(remotes.COOKIE, req.id, max_age=90 * 86400, httponly=True, samesite="lax")
        request.state.audit_detail = remotes.get(req.id)["name"]
    else:
        resp.delete_cookie(remotes.COOKIE)
    return resp


@router.post("/api/remotes")
async def api_remotes_add(req: RemoteRequest, request: Request):
    request.state.audit_detail = f"{req.name} ({req.url})"[:250]
    try:
        return await remotes.add(req.name, req.url, req.token, req.trust)
    except remotes.UntrustedCert as e:
        return _untrusted(e)
    except remotes.RemoteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/remotes/test")
async def api_remotes_test(req: RemoteRequest):
    try:
        url = remotes.normalize_url(req.url)
        info, cert = await remotes._connect(url, req.token.strip(), req.trust)
        return {**info, "insecure": await asyncio.to_thread(remotes.transport_risk, url),
                "fingerprint": remotes.cert_fingerprint(cert) if cert else ""}
    except remotes.UntrustedCert as e:
        return _untrusted(e)
    except remotes.RemoteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/remotes/{remote_id}/update")
async def api_remotes_update(remote_id: str, req: RemoteRequest):
    return _remote_call(remotes.update, remote_id, req.name or None, req.token or None)


@router.post("/api/remotes/{remote_id}/address")
async def api_remotes_address(remote_id: str, req: RemoteRequest, request: Request):
    """New address or certificate (agent switched to https with ./agent.sh tls on, moved, renewed)."""
    r = remotes.get(remote_id)
    request.state.audit_detail = f"{r['name'] if r else remote_id}: {req.url}"[:250]
    try:
        return await remotes.change_address(remote_id, req.url, req.trust)
    except remotes.UntrustedCert as e:
        return _untrusted(e)
    except remotes.RemoteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/remotes/{remote_id}/delete")
async def api_remotes_delete(remote_id: str, request: Request):
    r = remotes.get(remote_id)
    request.state.audit_detail = r["name"] if r else remote_id
    remotes.remove(remote_id)
    return {"remotes": await remotes.refresh_status()}
