"""
Anomalies, DNS monitoring - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import anomalies
import dnswatch

router = APIRouter()


@router.get("/api/anomalies")
async def api_anomalies():
    return await asyncio.to_thread(anomalies.overview)


@router.post("/api/anomalies/settings")
async def api_anomalies_settings(payload: dict):
    try:
        return await asyncio.to_thread(anomalies.save, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _dns_call(fn, *args):
    try:
        return fn(*args)
    except dnswatch.DnsWatchError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/dns-watch")
async def api_dns_watch():
    return await asyncio.to_thread(dnswatch.overview)


@router.post("/api/dns-watch")
async def api_dns_watch_create(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('type', '')} {payload.get('name', '')}"[:120]
    return await asyncio.to_thread(_dns_call, dnswatch.save, None, payload, request.state.user or "")


@router.post("/api/dns-watch/check")
async def api_dns_watch_check():
    return await asyncio.to_thread(dnswatch.check_all)


@router.post("/api/dns-watch/{item_id}")
async def api_dns_watch_update(item_id: str, payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('type', '')} {payload.get('name', '')}"[:120]
    return await asyncio.to_thread(_dns_call, dnswatch.save, item_id, payload, request.state.user or "")


@router.post("/api/dns-watch/{item_id}/delete")
async def api_dns_watch_delete(item_id: str):
    return await asyncio.to_thread(dnswatch.delete, item_id)
