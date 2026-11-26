"""
Secret scan (secretscan.py) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import secretscan
from webutil import heavy

router = APIRouter()


def _secretscan_call(fn, *args):
    try:
        return fn(*args)
    except secretscan.SecretScanError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/secret-scan")
async def api_secret_scan():
    return await asyncio.to_thread(secretscan.overview)


@router.post("/api/secret-scan/run")
async def api_secret_scan_run():
    return await heavy(_secretscan_call, secretscan.run)


@router.post("/api/secret-scan/ignore")
async def api_secret_scan_ignore(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('fingerprint', '')} · {'akzeptiert' if payload.get('ignore', True) else 'wieder beachtet'}"
    return await asyncio.to_thread(_secretscan_call, secretscan.set_ignored, str(payload.get("fingerprint", "")), bool(payload.get("ignore", True)))


@router.post("/api/secret-scan/settings")
async def api_secret_scan_settings(payload: dict):
    return await asyncio.to_thread(_secretscan_call, secretscan.save_settings, payload)
