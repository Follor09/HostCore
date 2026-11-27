"""
Off-site backup with restic (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import hostctx
import offsite
from webutil import heavy

router = APIRouter()


def _offsite(fn, *args):
    try:
        return fn(*args)
    except offsite.OffsiteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/offsite")
async def api_offsite():
    return await asyncio.to_thread(offsite.overview)


@router.post("/api/offsite/install")
async def api_offsite_install():
    return await asyncio.to_thread(_offsite, offsite.install)


@router.post("/api/offsite/auth/start")
async def api_offsite_auth_start(payload: dict):
    return await asyncio.to_thread(_offsite, offsite.auth_start, str(payload.get("kind", "")), str(payload.get("client_id", "")),
                                   str(payload.get("client_secret", "")), str(payload.get("job", "")))


@router.post("/api/offsite/auth/finish")
async def api_offsite_auth_finish(payload: dict):
    return await asyncio.to_thread(_offsite, offsite.auth_finish, str(payload.get("address", "")))


@router.get("/api/offsite/fleet")
async def api_offsite_fleet(request: Request):
    remote = hostctx.remote_for(request)          # the page shows this server first
    return await offsite.fleet(remote["id"] if remote else "")


@router.post("/api/offsite/copy")
async def api_offsite_copy(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('job', '')} -> {', '.join(map(str, payload.get('servers') or []))}"[:200]
    try:
        return await offsite.copy_to(str(payload.get("job", "")), [str(x) for x in payload.get("servers") or []], request.state.user or "")
    except offsite.OffsiteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offsite/fleet/{server_id}/{job_id}/{action}")
async def api_offsite_fleet_action(server_id: str, job_id: str, action: str, request: Request):
    try:
        return await offsite.remote_action(server_id, job_id, action, request.state.user or "")
    except offsite.OffsiteError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/offsite")
async def api_offsite_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_offsite, offsite.save, None, payload, request.state.user or "")


@router.get("/api/offsite/{job_id}/snapshots")
async def api_offsite_snapshots(job_id: str):
    return await heavy(_offsite, offsite.snapshots, job_id)


@router.post("/api/offsite/{job_id}/run")
async def api_offsite_run(job_id: str, request: Request):
    return await asyncio.to_thread(_offsite, offsite.run, job_id, request.state.user or "")


@router.post("/api/offsite/{job_id}/check")
async def api_offsite_check(job_id: str, request: Request):
    return await asyncio.to_thread(_offsite, offsite.check, job_id, request.state.user or "")


@router.post("/api/offsite/{job_id}/restore")
async def api_offsite_restore(job_id: str, payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('snapshot', '')} {payload.get('path', '')}".strip()[:200]
    return await asyncio.to_thread(_offsite, offsite.restore, job_id, str(payload.get("snapshot", "")), str(payload.get("path", "")),
                                   request.state.user or "")


@router.post("/api/offsite/{job_id}/password")
async def api_offsite_password(job_id: str):
    return await asyncio.to_thread(_offsite, offsite.password, job_id)


@router.post("/api/offsite/{job_id}/delete")
async def api_offsite_delete(job_id: str):
    return await asyncio.to_thread(_offsite, offsite.delete, job_id)


@router.post("/api/offsite/{job_id}")
async def api_offsite_update(job_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_offsite, offsite.save, job_id, payload, request.state.user or "")
