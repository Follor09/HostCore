"""
Service catalog - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, Request

import servicecatalog
from webutil import call400 as _call400

router = APIRouter()


CATALOG_ERRORS = (servicecatalog.CatalogError,)


@router.get("/api/service-catalog")
async def api_catalog(request: Request):
    return await asyncio.to_thread(servicecatalog.overview, request.state.user or "")


@router.post("/api/service-catalog/items")
async def api_catalog_item_create(payload: dict, request: Request):
    return await asyncio.to_thread(_call400, CATALOG_ERRORS, servicecatalog.save_item, None, payload, request.state.user or "")


@router.post("/api/service-catalog/items/{iid}")
async def api_catalog_item_update(iid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, CATALOG_ERRORS, servicecatalog.save_item, iid, payload, request.state.user or "")


@router.post("/api/service-catalog/items/{iid}/delete")
async def api_catalog_item_delete(iid: str, request: Request):
    return await asyncio.to_thread(_call400, CATALOG_ERRORS, servicecatalog.delete_item, iid, request.state.user or "")


@router.post("/api/service-catalog/items/{iid}/request")
async def api_catalog_submit(iid: str, payload: dict, request: Request):
    return await asyncio.to_thread(_call400, CATALOG_ERRORS, servicecatalog.submit, iid, payload.get("values") or {}, str(payload.get("note", "")), request.state.user or "")


@router.post("/api/service-catalog/requests/{rid}/{act}")
async def api_catalog_decide(rid: str, act: str, payload: dict, request: Request):
    request.state.audit_detail = f"{rid} · {act}"
    return await asyncio.to_thread(_call400, CATALOG_ERRORS, servicecatalog.decide, rid, act, str(payload.get("note", "")), request.state.user or "")
