"""
Alarms: acknowledgement and escalation (per server) - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import alerting
import notify
from webutil import same_origin as _same_origin, serve_page as _serve_page

router = APIRouter()


def _alert_call(fn, *args):
    try:
        return fn(*args)
    except alerting.AlertError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except notify.NotifyError as e:
        raise HTTPException(status_code=502, detail=str(e))


class AlertAck(BaseModel):
    note: str = ""


class AlertTokenAck(BaseModel):
    token: str
    note: str = ""


@router.get("/api/alerts")
async def api_alerts():
    return await asyncio.to_thread(alerting.overview)


@router.post("/api/alerts/resolve-all")
async def api_alerts_resolve_all(request: Request):
    n = await asyncio.to_thread(alerting.resolve_all, request.state.user or "")
    request.state.audit_detail = f"{n} Alarme"
    return {**await asyncio.to_thread(alerting.overview), "resolved": n}


@router.post("/api/alerts/{alert_id}/ack")
async def api_alert_ack(alert_id: str, req: AlertAck, request: Request):
    request.state.audit_detail = alert_id
    await asyncio.to_thread(_alert_call, alerting.acknowledge, alert_id, request.state.user or "", req.note)
    return await asyncio.to_thread(alerting.overview)


@router.post("/api/alerts/{alert_id}/resolve")
async def api_alert_resolve(alert_id: str, request: Request):
    request.state.audit_detail = alert_id
    await asyncio.to_thread(_alert_call, alerting.resolve, alert_id, request.state.user or "")
    return await asyncio.to_thread(alerting.overview)


@router.get("/api/settings/alerting")
async def api_alerting_settings():
    return await asyncio.to_thread(alerting.public)


@router.post("/api/settings/alerting")
async def api_alerting_save(payload: dict):
    return await asyncio.to_thread(_alert_call, alerting.save, payload)


@router.post("/api/settings/alerting/test")
async def api_alerting_test():
    return await asyncio.to_thread(_alert_call, alerting.send_test)


@router.get("/alarm")
async def alarm_page():
    return _serve_page("alarm.html")


@router.get("/api/alarm")
async def api_alarm_info(token: str = ""):
    return await asyncio.to_thread(alerting.token_info, token)


@router.post("/api/alarm")
async def api_alarm_ack(req: AlertTokenAck, request: Request):
    """Acknowledge with the link from the alarm mail (no login: the link is the permission)."""
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültige Herkunft der Anfrage.")
    a = await asyncio.to_thread(_alert_call, alerting.acknowledge_token, req.token, req.note)
    request.state.audit_user = a.get("acked_by", "")
    request.state.audit_detail = a["id"]
    return {"ok": True, "acked_by": a.get("acked_by", ""), "acked_at": a.get("acked_at", 0)}
