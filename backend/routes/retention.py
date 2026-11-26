"""
Data retention - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import retention

router = APIRouter()


@router.get("/api/settings/retention")
async def api_settings_retention():
    return await asyncio.to_thread(retention.overview)


@router.post("/api/settings/retention")
async def api_settings_retention_save(payload: dict, request: Request):
    request.state.audit_detail = ", ".join(f"{k}={v}" for k, v in payload.items())[:250]
    try:
        return await asyncio.to_thread(retention.save, payload)
    except retention.RetentionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/retention/cleanup")
async def api_settings_retention_cleanup():
    return await asyncio.to_thread(retention.cleanup_now)
