"""
Processes: search + end (own processes only) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import system_info
from webutil import logger

router = APIRouter()


class SignalRequest(BaseModel):
    create_time: float
    force: bool = False


@router.get("/api/processes/search")
async def api_process_search(q: str = ""):
    return {"processes": await asyncio.to_thread(system_info.search_processes, q)}


@router.post("/api/processes/{pid}/signal")
async def api_process_signal(pid: int, req: SignalRequest, request: Request):
    result = await asyncio.to_thread(system_info.signal_process, pid, req.create_time, req.force)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"{'SIGKILL' if req.force else 'SIGTERM'} pid {pid} by '{request.state.user}': {result['message']}")
    request.state.audit_detail = f"PID {pid} · {'SIGKILL' if req.force else 'SIGTERM'} · {result['message']}"
    return result
