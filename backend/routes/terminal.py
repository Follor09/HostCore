"""
Web terminal - routes moved out of app.py.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import mcp_server
import webhooks
import websec

router = APIRouter()


class TerminalTicketRequest(BaseModel):
    password: str = ""
    container: str = ""      # shell inside this Docker container instead of on the server
    session: str = ""        # take over this running (persistent) session instead of a new shell


class TerminalResumeRequest(BaseModel):
    session: str
    token: str


class WebhookRequest(BaseModel):
    name: str
    level: str = "auto"


@router.get("/api/settings/webhooks")
async def api_webhooks():
    return await asyncio.to_thread(webhooks.overview)


@router.post("/api/settings/webhooks")
async def api_webhooks_create(req: WebhookRequest, request: Request):
    request.state.audit_detail = req.name[:120]
    try:
        return await asyncio.to_thread(webhooks.create, req.name, req.level, request.state.user or "")
    except webhooks.WebhookError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/webhooks/{hook_id}/delete")
async def api_webhooks_delete(hook_id: str):
    return await asyncio.to_thread(webhooks.delete, hook_id)


@router.api_route("/hook/{token}", methods=["POST", "PUT"])
async def public_webhook(token: str, request: Request):
    """Public webhook receiver (token in the path): the message becomes an event."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > webhooks.MAX_BODY:
            break
    ip = websec.client_ip(request)
    try:
        return await asyncio.to_thread(webhooks.receive, token, body, request.headers.get("content-type", ""),
                                       {k.lower(): v for k, v in request.headers.items()}, ip)
    except webhooks.WebhookError as e:
        return JSONResponse({"detail": str(e)}, status_code=404 if "Unbekannt" in str(e) else 429)


@router.get("/api/settings/mcp")
async def api_settings_mcp():
    return mcp_server.public()


@router.post("/api/settings/mcp")
async def api_settings_mcp_save(payload: dict, request: Request):
    request.state.audit_detail = f"{'an' if payload.get('enabled') else 'aus'} · {payload.get('base_url', '')}"[:250]
    try:
        await asyncio.to_thread(mcp_server.save, payload)
    except mcp_server.McpConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await mcp_server.reload()


@router.post("/api/settings/mcp/approval")
async def api_settings_mcp_approval(payload: dict, request: Request):
    """Which MCP actions wait for a confirmation (no restart of the MCP server needed)."""
    request.state.audit_detail = f"{'an' if payload.get('enabled') else 'aus'} · {len(payload.get('tools') or [])} Werkzeuge"
    try:
        await asyncio.to_thread(mcp_server.save_approval, payload)
    except mcp_server.McpConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return mcp_server.public()


@router.get("/api/settings/mcp/calls")
async def api_settings_mcp_calls(tool: str = "", user: str = "", status: str = "", q: str = "", limit: int = 200):
    return await asyncio.to_thread(mcp_server.calls, tool, user, status, q, limit)


@router.get("/api/mcp/approvals")
async def api_mcp_approvals(request: Request):
    user = request.state.user or ""
    return mcp_server.approvals(user, auth.can(user, "users.manage"))


@router.post("/api/mcp/approvals/{approval_id}/{decision}")
async def api_mcp_approval_decide(approval_id: str, decision: str, request: Request):
    if getattr(request.state, "via_mcp", False):
        raise HTTPException(status_code=403, detail="Freigaben nur im Dashboard, nicht über den MCP.")
    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=404, detail="Unbekannte Entscheidung.")
    user = request.state.user or ""
    try:
        a = mcp_server.decide(approval_id, decision == "approve", user, auth.can(user, "users.manage"))
    except mcp_server.McpConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{a['tool']} · {a['user']} · {a['summary']}"[:250]
    return mcp_server.approvals(user, auth.can(user, "users.manage"))
