"""
Tasks: long-running work with its own log (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import tasks

router = APIRouter()


def _task_call(fn, *args):
    try:
        return fn(*args)
    except tasks.TaskError as e:
        raise HTTPException(status_code=404 if "nicht gefunden" in str(e) else 400, detail=str(e))


@router.get("/api/tasks")
async def api_tasks(limit: int = 100, kind: str = ""):
    return await asyncio.to_thread(tasks.overview, max(1, min(limit, 300)), kind)


@router.get("/api/tasks/{task_id}")
async def api_task(task_id: str):
    return await asyncio.to_thread(_task_call, tasks.get, task_id)


@router.get("/api/tasks/{task_id}/log")
async def api_task_log(task_id: str, offset: int = 0):
    return await asyncio.to_thread(_task_call, tasks.log, task_id, max(0, offset))


@router.post("/api/tasks/{task_id}/cancel")
async def api_task_cancel(task_id: str, request: Request):
    request.state.audit_detail = task_id
    return await asyncio.to_thread(_task_call, tasks.cancel, task_id, request.state.user or "")
