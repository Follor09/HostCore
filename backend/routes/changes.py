"""
Change management (changes.py) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import changes

router = APIRouter()


def _change_call(fn, *args):
    try:
        return fn(*args)
    except changes.ChangeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/changes")
async def api_changes(request: Request):
    return await asyncio.to_thread(changes.overview, request.state.user or "")


@router.post("/api/changes")
async def api_changes_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:100]
    return await asyncio.to_thread(_change_call, changes.save, None, payload, request.state.user or "")


@router.post("/api/changes/{change_id}")
async def api_changes_update(change_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:100]
    return await asyncio.to_thread(_change_call, changes.save, change_id, payload, request.state.user or "")


@router.post("/api/changes/{change_id}/action")
async def api_changes_action(change_id: str, payload: dict, request: Request):
    act = str(payload.get("action", ""))
    request.state.audit_detail = f"{change_id} · {act}"
    return await asyncio.to_thread(_change_call, changes.action, change_id, act, request.state.user or "",
                                   str(payload.get("note", "")), str(payload.get("result", "")))


@router.post("/api/changes/{change_id}/delete")
async def api_changes_delete(change_id: str, request: Request):
    return await asyncio.to_thread(_change_call, changes.delete, change_id, request.state.user or "")


@router.get("/api/changes/{change_id}/markdown")
async def api_changes_markdown(change_id: str):
    text = await asyncio.to_thread(_change_call, changes.markdown, change_id)
    return Response(text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="aenderung-{change_id}.md"'})
