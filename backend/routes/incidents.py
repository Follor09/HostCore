"""
Incidents - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import incidents

router = APIRouter()


def _incident_call(fn, *args):
    try:
        return fn(*args)
    except incidents.IncidentError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/incidents")
async def api_incidents(state: str = ""):
    return await asyncio.to_thread(incidents.overview, state)


@router.post("/api/incidents")
async def api_incidents_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_incident_call, incidents.save, None, payload, request.state.user or "")


@router.post("/api/incidents/{incident_id}")
async def api_incidents_update(incident_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("title", ""))[:120]
    return await asyncio.to_thread(_incident_call, incidents.save, incident_id, payload, request.state.user or "")


@router.post("/api/incidents/{incident_id}/update")
async def api_incidents_add_update(incident_id: str, payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('status', '')}: {str(payload.get('text', ''))[:100]}"
    return await asyncio.to_thread(_incident_call, incidents.add_update, incident_id, str(payload.get("status", "")),
                                   str(payload.get("text", "")), request.state.user or "")


@router.post("/api/incidents/{incident_id}/delete")
async def api_incidents_delete(incident_id: str):
    return await asyncio.to_thread(incidents.delete, incident_id)


@router.post("/api/incidents/{incident_id}/postmortem")
async def api_incidents_postmortem(incident_id: str, payload: dict, request: Request):
    return await asyncio.to_thread(_incident_call, incidents.save_postmortem, incident_id, payload, request.state.user or "")


@router.post("/api/incidents/{incident_id}/postmortem/tasks")
async def api_incidents_postmortem_tasks(incident_id: str, request: Request):
    return await asyncio.to_thread(_incident_call, incidents.create_tasks, incident_id, request.state.user or "")


@router.get("/api/incidents/{incident_id}/postmortem.md")
async def api_incidents_postmortem_md(incident_id: str):
    text = await asyncio.to_thread(_incident_call, incidents.markdown, incident_id)
    return Response(text, media_type="text/markdown; charset=utf-8")
