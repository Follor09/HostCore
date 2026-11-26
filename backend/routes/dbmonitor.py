"""
Database monitor (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import dbmonitor

router = APIRouter()


def _db_call(fn, *args):
    try:
        return fn(*args)
    except dbmonitor.DbError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/containers/databases")
async def api_databases():
    return await asyncio.to_thread(dbmonitor.overview)


@router.post("/api/containers/databases/refresh")
async def api_databases_refresh():
    return await asyncio.to_thread(dbmonitor.refresh)


@router.get("/api/containers/databases/job/{job_id}")
async def api_databases_job(job_id: str):
    return await asyncio.to_thread(_db_call, dbmonitor.get_job, job_id)


@router.post("/api/containers/databases/{name}/dump")
async def api_databases_dump(name: str, request: Request):
    request.state.audit_detail = name
    return await asyncio.to_thread(_db_call, dbmonitor.start_dump, name, request.state.user or "")


@router.post("/api/containers/databases/{name}/settings")
async def api_databases_settings(name: str, payload: dict, request: Request):
    request.state.audit_detail = name
    return await asyncio.to_thread(_db_call, dbmonitor.save_settings, name, payload)
