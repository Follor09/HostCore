"""
Access review (accessreview.py) - routes moved out of app.py.
"""

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import accessreview
from webutil import heavy

router = APIRouter()


def _review_call(fn, *args):
    try:
        return fn(*args)
    except accessreview.ReviewError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/access-review")
async def api_access_review():
    return await asyncio.to_thread(accessreview.overview)


@router.post("/api/access-review/settings")
async def api_access_review_settings(payload: dict):
    return await asyncio.to_thread(_review_call, accessreview.save_settings, payload)


@router.post("/api/access-review/sign-off")
async def api_access_review_sign_off(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("note", ""))[:200]
    return await asyncio.to_thread(_review_call, accessreview.sign_off, str(payload.get("note", "")), request.state.user or "")


@router.get("/api/access-review/export.csv")
async def api_access_review_csv():
    text = await asyncio.to_thread(accessreview.csv_export)
    return Response("\ufeff" + text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rechte-{time.strftime("%Y-%m-%d")}.csv"'})


@router.get("/api/access-review/{review_id}/markdown")
async def api_access_review_markdown(review_id: str):
    text = await heavy(_review_call, accessreview.review_markdown, review_id)
    return Response(text, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rechte-review-{review_id}.md"'})
