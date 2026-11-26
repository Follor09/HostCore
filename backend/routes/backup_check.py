"""
Backup check (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import backups

router = APIRouter()


def _backup_call(fn, *args):
    try:
        return fn(*args)
    except backups.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/backups")
async def api_backups():
    return await asyncio.to_thread(backups.overview)


@router.post("/api/backups")
async def api_backups_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_backup_call, backups.save, None, payload, request.state.user or "")


@router.post("/api/backups/{job_id}")
async def api_backups_update(job_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_backup_call, backups.save, job_id, payload, request.state.user or "")


@router.post("/api/backups/{job_id}/check")
async def api_backups_check(job_id: str):
    return await asyncio.to_thread(backups.start_check, job_id)


@router.post("/api/backups/{job_id}/delete")
async def api_backups_delete(job_id: str):
    return await asyncio.to_thread(backups.delete, job_id)
