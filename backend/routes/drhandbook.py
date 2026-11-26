"""
Emergency handbook (drhandbook.py) - routes moved out of app.py.
"""

import asyncio
import platform
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import auth
import drhandbook
from webutil import serve_page as _serve_page

router = APIRouter()


def _handbook_call(fn, *args):
    try:
        return fn(*args)
    except drhandbook.HandbookError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/docs/handbook")
async def api_docs_handbook(refresh: int = 0):
    return await asyncio.to_thread(drhandbook.build, bool(refresh))


@router.post("/api/docs/handbook/general")
async def api_docs_handbook_general(payload: dict, request: Request):
    return await asyncio.to_thread(_handbook_call, drhandbook.save_general, payload, request.state.user or "")


@router.post("/api/docs/handbook/service")
async def api_docs_handbook_service(payload: dict, request: Request):
    return await asyncio.to_thread(_handbook_call, drhandbook.save_service, str(payload.get("target", "")), payload,
                                   request.state.user or "")


@router.post("/api/docs/handbook/exported")
async def api_docs_handbook_exported(request: Request):
    return await asyncio.to_thread(drhandbook.mark_exported, request.state.user or "")


@router.get("/api/docs/handbook.md")
async def api_docs_handbook_markdown(request: Request):
    text = await asyncio.to_thread(drhandbook.markdown)
    if auth.can(request.state.user, "notes.edit"):
        await asyncio.to_thread(drhandbook.mark_exported, request.state.user or "")
    name = f"notfallhandbuch-{platform.node()}-{time.strftime('%Y-%m-%d')}.md"
    return Response(text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/notfallhandbuch")
async def get_handbook_page():
    return _serve_page("handbook.html")
