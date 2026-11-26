"""
Latency between the servers, dashboard versions and capacity of the fleet - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

import capacity
import fleet
import latency
import updater
from webutil import heavy

router = APIRouter()


@router.get("/api/tools/ping-peers")
async def api_ping_peers(targets: str = ""):
    """Called by the main dashboard on every server: ping the other servers."""
    return await asyncio.to_thread(latency.ping_many, [t.strip() for t in targets.split(",") if t.strip()][:20])


@router.get("/api/fleet/latency")
async def api_fleet_latency(hours: int = 24):
    return await asyncio.to_thread(latency.matrix, max(1, min(hours, 24 * 30)))


@router.post("/api/fleet/latency/measure")
async def api_fleet_latency_measure():
    await asyncio.to_thread(latency.measure)
    return await asyncio.to_thread(latency.matrix, 24)


@router.post("/api/updates/run-now")
async def api_updates_run_now():
    return await asyncio.to_thread(updater.run_update_now)


class FleetIdsRequest(BaseModel):
    ids: list


@router.post("/api/fleet/dashboard-update")
async def api_fleet_dashboard_update(req: FleetIdsRequest, request: Request):
    request.state.audit_detail = f"{len(req.ids)} Server"
    return await fleet.dashboard_update([str(i) for i in req.ids][:50], request.state.user or "")


@router.get("/api/fleet/capacity")
async def api_fleet_capacity():
    return await fleet.capacity()


@router.get("/api/capacity")
async def api_capacity():
    return await heavy(capacity.overview)


@router.get("/api/capacity/summary")
async def api_capacity_summary():
    return await asyncio.to_thread(capacity.summary)
