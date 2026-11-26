"""
E-mail notifications - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import notify

router = APIRouter()


@router.get("/api/settings/email")
async def api_settings_email():
    return notify.public()


@router.post("/api/settings/email")
async def api_settings_email_save(payload: dict, request: Request):
    request.state.audit_detail = f"{'an' if payload.get('enabled') else 'aus'} · {payload.get('host', '')}"[:250]
    try:
        return await asyncio.to_thread(notify.save, payload)
    except notify.NotifyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/email/test")
async def api_settings_email_test(payload: dict = None):
    kind = (payload or {}).get("kind", "test")
    try:
        if kind == "weekly":
            subject, text, body = await asyncio.to_thread(notify.weekly_content)
            await asyncio.to_thread(notify.send, subject, text, body)
        elif kind == "daily":
            items = await asyncio.to_thread(notify.deadlines)
            text, body = notify._render("Tagesübersicht (Vorschau)", "So sieht die tägliche Übersicht heute aus.",
                                        [("Fällig und bald fällig", items or [("good", "Heute ist nichts fällig.", "")])], "/")
            await asyncio.to_thread(notify.send, "[HostCore] Tagesübersicht (Vorschau)", text, body)
        else:
            await asyncio.to_thread(notify.send_test)
    except notify.NotifyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return notify.public()
