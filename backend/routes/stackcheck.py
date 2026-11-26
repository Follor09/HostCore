"""
Compose security check (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import stackcheck
from webutil import heavy

router = APIRouter()


def _stackcheck_call(fn, *args):
    try:
        return fn(*args)
    except stackcheck.StackCheckError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/containers/stack-check")
async def api_stack_check():
    return await asyncio.to_thread(stackcheck.overview)


@router.post("/api/containers/stack-check/run")
async def api_stack_check_run():
    return await heavy(stackcheck.run)


@router.post("/api/containers/stack-check/ignore")
async def api_stack_check_ignore(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('stack', '')} · {payload.get('finding', '')}"[:120]
    return await asyncio.to_thread(_stackcheck_call, stackcheck.set_ignored, str(payload.get("stack", "")),
                                   str(payload.get("finding", "")), bool(payload.get("ignore", True)))
