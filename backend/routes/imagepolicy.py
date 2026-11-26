"""
Image policies (imagepolicy.py) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import imagepolicy

router = APIRouter()


@router.get("/api/containers/image-policy")
async def api_image_policy():
    return await asyncio.to_thread(imagepolicy.audit)


@router.post("/api/containers/image-policy")
async def api_image_policy_save(payload: dict, request: Request):
    try:
        await asyncio.to_thread(imagepolicy.save, payload)
    except imagepolicy.PolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"Modus {payload.get('mode', '')}"
    return await asyncio.to_thread(imagepolicy.audit)
