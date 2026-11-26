"""
systemd service control (tiles in the "Server-Dienste" widget) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import services_manager
from webutil import logger

router = APIRouter()


@router.post("/api/services/{service_id}/{action}")
async def api_service_action(service_id: str, action: str, request: Request):
    result = await asyncio.to_thread(services_manager.service_action, service_id, action)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"systemctl {action} {result['unit']} by '{request.state.user}'")
    request.state.audit_detail = f"{action} {result['unit']}"
    return result


@router.get("/api/services/{service_id}/journal")
async def api_service_journal(service_id: str, lines: int = 150):
    result = await asyncio.to_thread(services_manager.service_journal, service_id, lines)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result
