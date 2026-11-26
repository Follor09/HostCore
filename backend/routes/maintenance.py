"""
Maintenance windows - routes moved out of app.py.
"""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import maintenance

router = APIRouter()


class MaintenanceRequest(BaseModel):
    title: str
    start: int
    end: int
    scope: Any = "all"
    note: str = ""


def _maintenance_call(fn, *args):
    try:
        return fn(*args)
    except maintenance.MaintenanceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/maintenance")
async def api_maintenance():
    return await asyncio.to_thread(maintenance.overview)


@router.post("/api/maintenance")
async def api_maintenance_create(req: MaintenanceRequest, request: Request):
    request.state.audit_detail = f"{req.title[:60]} · {time.strftime('%d.%m. %H:%M', time.localtime(req.start))}–{time.strftime('%d.%m. %H:%M', time.localtime(req.end))}"
    return await asyncio.to_thread(_maintenance_call, maintenance.create, req.title, req.start, req.end, req.scope, req.note,
                                   request.state.user or "")


@router.post("/api/maintenance/{window_id}/end")
async def api_maintenance_end(window_id: str):
    return await asyncio.to_thread(_maintenance_call, maintenance.end_now, window_id)


@router.post("/api/maintenance/{window_id}/delete")
async def api_maintenance_delete(window_id: str):
    return await asyncio.to_thread(_maintenance_call, maintenance.delete, window_id)
