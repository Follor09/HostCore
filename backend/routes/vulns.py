"""
Vulnerabilities, end of life, self-test (per server) - routes moved out of app.py.
"""

import asyncio
import subprocess
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

import agent
import aptchangelog
import audit
import auth
import eol
import fleet
import hardening
import recordings
import sbom
import selftest
import terminal
import vulns
import websec
from hostctx import AGENT, HOST_CHANGED_CLOSE, _ws_relay, host_changed as _host_changed, proxy as _proxy, remote_for as _remote_for
from routes.terminal import TerminalResumeRequest, TerminalTicketRequest
from webutil import heavy, logger

router = APIRouter()


@router.get("/api/vulns")
async def api_vulns():
    return await asyncio.to_thread(vulns.overview)


@router.post("/api/vulns/scan")
async def api_vulns_scan():
    return await asyncio.to_thread(vulns.start_scan)


@router.post("/api/vulns/ignore")
async def api_vulns_ignore(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('id', '')} {'aus' if payload.get('ignore') is False else 'akzeptiert'}"[:120]
    try:
        return await asyncio.to_thread(vulns.ignore, payload.get("id", ""), payload.get("note", ""), request.state.user or "",
                                       payload.get("ignore") is not False)
    except vulns.VulnError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/eol")
async def api_eol():
    return await asyncio.to_thread(eol.overview)


@router.post("/api/eol/check")
async def api_eol_check():
    return await asyncio.to_thread(eol.start_check)


@router.get("/api/selftest")
async def api_selftest():
    return await asyncio.to_thread(selftest.overview)


@router.post("/api/selftest/run")
async def api_selftest_run():
    return await asyncio.to_thread(selftest.start)


@router.get("/api/hardening")
async def api_hardening():
    return await asyncio.to_thread(hardening.overview)


@router.post("/api/hardening/run")
async def api_hardening_run():
    return await asyncio.to_thread(hardening.start)


@router.get("/api/updates/changelog")
async def api_updates_changelog(name: str, old: str = "", new: str = ""):
    try:
        return await asyncio.to_thread(aptchangelog.changelog, name, old, new)
    except aptchangelog.ChangelogError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/sbom")
async def api_sbom():
    return await asyncio.to_thread(sbom.overview)


@router.get("/api/sbom/search")
async def api_sbom_search(q: str = "", kind: str = ""):
    return await asyncio.to_thread(sbom.search, q, kind)


@router.get("/api/sbom/export")
async def api_sbom_export(format: str = "cyclonedx"):
    name, media, body = await heavy(sbom.export, "csv" if format == "csv" else "cyclonedx")
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/fleet/sbom")
async def api_fleet_sbom(q: str = "", kind: str = ""):
    return await fleet.sbom_search(q, kind)


@router.post("/api/selftest/repair")
async def api_selftest_repair(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("id", ""))[:40]
    try:
        return await asyncio.to_thread(selftest.repair, str(payload.get("id", "")))
    except (selftest.RepairError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/terminal/ticket")
async def api_terminal_ticket(req: TerminalTicketRequest, request: Request):
    """Re-check the password before handing out a one-time terminal ticket.
    For a remote server the password is checked here and the agent issues the ticket."""
    if AGENT:
        # the main dashboard already checked the password; the token proves it's the main dashboard
        return await asyncio.to_thread(_terminal_issue, request.state.user, req.container, req.session)
    ip = websec.client_ip(request)
    wait = auth.lockout_remaining(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche. Bitte {wait} s warten.")
    if not await asyncio.to_thread(auth.verify_login, request.state.user, req.password):
        auth.record_failure(ip)
        logger.warning(f"Terminal: wrong password for '{request.state.user}' from {ip}")
        raise HTTPException(status_code=403, detail="Passwort ist falsch.")
    auth.record_success(ip)
    remote = _remote_for(request)
    if remote:
        try:
            res = await _proxy(remote).post(f"{remote['url']}/api/terminal/ticket", json={"container": req.container, "session": req.session},
                                      headers={"Authorization": f"Bearer {remote['token']}", "X-PiCore-User": request.state.user or ""})
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"{remote['name']} nicht erreichbar ({type(e).__name__}).")
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=res.json().get("detail", "Fehler") if res.headers.get("content-type", "").startswith("application/json") else "Fehler")
        return res.json()
    request.state.audit_detail = f"Sitzung {req.session}" if req.session else f"Container {req.container}" if req.container else ""
    return await asyncio.to_thread(_terminal_issue, request.state.user, req.container, req.session)


def _terminal_issue(user: str, container: str, sid: str) -> dict:
    """Ticket for a new shell, or for taking over an own running session."""
    if sid:
        role = terminal.access(sid, user)
        s = terminal.session(sid)
        if not role or not s:
            raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
        return {"ticket": terminal.issue_ticket(user, s["container"], sid, role == "view"), "expires_in": terminal.TICKET_TTL}
    if terminal.session_count() >= terminal.MAX_SESSIONS:
        raise HTTPException(status_code=429, detail=f"Maximal {terminal.MAX_SESSIONS} Terminals gleichzeitig – "
                                                    "eine laufende Sitzung beenden oder übernehmen.")
    return {"ticket": terminal.issue_ticket(user, _terminal_target(container)), "expires_in": terminal.TICKET_TTL}


@router.post("/api/terminal/resume")
async def api_terminal_resume(req: TerminalResumeRequest, request: Request):
    """Reconnect after a dropped connection: the session's resume token instead of the password
    (it only exists in the tab that had the session open; the login cookie is still needed)."""
    if not await asyncio.to_thread(terminal.check_resume, req.session, request.state.user or "", req.token):
        raise HTTPException(status_code=403, detail="Sitzung nicht mehr verfügbar.")
    s = await asyncio.to_thread(terminal.session, req.session)
    role = await asyncio.to_thread(terminal.access, req.session, request.state.user or "")
    return {"ticket": terminal.issue_ticket(request.state.user, s["container"], req.session, role == "view"), "expires_in": terminal.TICKET_TTL}


@router.get("/api/terminal/sessions")
async def api_terminal_sessions(request: Request):
    user = request.state.user or ""
    every = auth.can(user, "users.manage") if not AGENT else False
    items = await asyncio.to_thread(terminal.sessions, user)
    if every:
        # Admins also see the other users' sessions (to end them), without access to them
        mine = {s["id"] for s in items}
        items += [{**s, "role": "other", "shares": [], "connected": []} for s in await asyncio.to_thread(terminal.sessions) if s["id"] not in mine]
    rec = recordings.config()["enabled"]
    return {"persistent": terminal.persistent(), "sessions": items, "max": terminal.MAX_SESSIONS,
            "detached_hours": terminal.DETACHED_TTL // 3600, "user": user, "recording": rec}


class TerminalShareRequest(BaseModel):
    user: str
    mode: str = "view"


def _terminal_change(fn, *args):
    try:
        return fn(*args)
    except terminal.TerminalError as e:
        raise HTTPException(status_code=404 if "nicht gefunden" in str(e) else 400, detail=str(e))


@router.get("/api/terminal/share-users")
async def api_terminal_share_users(request: Request):
    """Who a session can be shared with: users with the terminal permission (from the main dashboard)."""
    me = request.state.user or ""
    users = await asyncio.to_thread(auth.list_users)
    return {"users": [u["username"] for u in users if u["username"] != me and not u.get("expired") and auth.can(u["username"], "terminal")]}


@router.post("/api/terminal/sessions/{sid}/share")
async def api_terminal_share(sid: str, req: TerminalShareRequest, request: Request):
    if not AGENT:
        known = {u["username"] for u in await asyncio.to_thread(auth.list_users)}
        if req.user not in known or not auth.can(req.user, "terminal"):
            raise HTTPException(status_code=400, detail="Dieser Benutzer darf das Terminal nicht benutzen.")
    await asyncio.to_thread(_terminal_change, terminal.share, sid, request.state.user or "", req.user, req.mode)
    request.state.audit_detail = f"{sid} → {req.user} ({terminal.MODES.get(req.mode, req.mode)})"
    return await api_terminal_sessions(request)


@router.post("/api/terminal/sessions/{sid}/unshare")
async def api_terminal_unshare(sid: str, req: TerminalShareRequest, request: Request):
    await asyncio.to_thread(_terminal_change, terminal.unshare, sid, request.state.user or "", req.user)
    request.state.audit_detail = f"{sid} → {req.user}"
    return await api_terminal_sessions(request)


def _rec_call(fn, *args):
    try:
        return fn(*args)
    except recordings.RecordingError as e:
        raise HTTPException(status_code=404 if "nicht gefunden" in str(e) else 400, detail=str(e))


@router.get("/api/terminal/recordings")
async def api_recordings():
    return await asyncio.to_thread(recordings.overview)


@router.get("/api/terminal/recordings/search")
async def api_recordings_search(q: str, request: Request):
    res = await asyncio.to_thread(_rec_call, recordings.search, q)
    await asyncio.to_thread(audit.record, request.state.user or "", websec.client_ip(request), "GET", "/api/terminal/recordings/search", 200, q[:200])
    return res


@router.get("/api/terminal/recordings/{sid}")
async def api_recording(sid: str, request: Request):
    text = await asyncio.to_thread(_rec_call, recordings.read, sid)
    await asyncio.to_thread(audit.record, request.state.user or "", websec.client_ip(request), "GET", f"/api/terminal/recordings/{sid}", 200, sid)
    return Response(text, media_type="application/x-asciicast")


@router.post("/api/terminal/recordings/{sid}/delete")
async def api_recording_delete(sid: str, request: Request):
    request.state.audit_detail = sid
    return await asyncio.to_thread(_rec_call, recordings.delete, sid)


@router.post("/api/settings/terminal-recording")
async def api_recording_settings(payload: dict):
    return await asyncio.to_thread(_rec_call, recordings.save, payload)


@router.post("/api/terminal/sessions/{sid}/close")
async def api_terminal_close(sid: str, request: Request):
    user = request.state.user or ""
    s = await asyncio.to_thread(terminal.session, sid)
    if not s or (s["user"] != user and (AGENT or not auth.can(user, "users.manage"))):
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
    await asyncio.to_thread(terminal.close_session, sid)
    request.state.audit_detail = f"{sid} ({s['user']})"
    return await api_terminal_sessions(request)


@router.get("/api/terminal/admin/sessions")
async def api_terminal_admin_sessions():
    """Admins: every running session on this server (all users), with who is connected - to end them."""
    return {"persistent": terminal.persistent(), "sessions": await asyncio.to_thread(terminal.sessions),
            "detached_hours": terminal.DETACHED_TTL // 3600}


@router.post("/api/terminal/admin/sessions/{sid}/kill")
async def api_terminal_admin_kill(sid: str, request: Request):
    """Admins: end any running session at once (the shell and everything running in it)."""
    s = await asyncio.to_thread(_terminal_change, terminal.kill_session, sid, request.state.user or "")
    request.state.audit_detail = f"{sid} ({s['user']}{' · Container ' + s['container'] if s['container'] else ''})"
    return await api_terminal_admin_sessions()


def _terminal_target(container: str) -> str:
    if not container:
        return ""
    try:
        return terminal.check_container(container)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        raise HTTPException(status_code=400, detail=str(e) if isinstance(e, ValueError) else "Docker antwortet nicht.")


@router.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket, ticket: str = "", rows: int = 24, cols: int = 80):
    # WebSockets bypass the HTTP middleware: check session, origin and ticket here
    if AGENT:
        await websocket.accept()
        if not agent.check(websocket.headers.get("authorization", "")) or not agent.allowed(websec.client_ip(websocket)):
            await websocket.close(code=4401)
            return
        user = agent.remote_user(websocket.headers)
        redeemed = terminal.redeem_ticket(ticket, user)
        if redeemed is None:
            await websocket.close(code=4401)
            return
    else:
        user = auth.user_from_token(websocket.cookies.get(auth.COOKIE_NAME))
        origin = websocket.headers.get("origin", "")
        same_origin = origin.split("://", 1)[-1] == websocket.headers.get("host", "")
        await websocket.accept()
        if not user or not auth.can(user, "terminal") or not same_origin:
            await websocket.close(code=4401)
            return
        if _host_changed(websocket, websocket.query_params.get("host")):
            await websocket.close(code=HOST_CHANGED_CLOSE)
            return
        remote = _remote_for(websocket)
        if remote:
            # the ticket was issued by the remote agent (the ticket request went there too)
            await asyncio.to_thread(audit.record, user, websec.client_ip(websocket), "WS", "/ws/terminal", 200, f"@{remote['name']}")
            await _ws_relay(websocket, remote, f"/ws/terminal?ticket={ticket}&rows={int(rows)}&cols={int(cols)}", user)
            return
        redeemed = terminal.redeem_ticket(ticket, user)
        if redeemed is None:
            await websocket.close(code=4401)
            return
    target, sid, read_only = redeemed
    client = websec.client_ip(websocket)
    if websec.blocked_from_outside("terminal", client):
        await websocket.close(code=4403)
        return
    started = time.time()
    where = f"Container {target} · " if target else ""
    await asyncio.to_thread(audit.record, user, client, "WS", "/ws/terminal", 200,
                            f"{where}{('zuschauen bei ' if read_only else 'verbunden mit ') + sid if sid else 'geöffnet'}")
    try:
        await terminal.run_session(websocket, user, client, rows, cols, target, sid, read_only)
    finally:
        minutes = max(1, round((time.time() - started) / 60))
        await asyncio.to_thread(audit.record, user, client, "WS", "/ws/terminal", 200, f"{where}getrennt nach {minutes} Min.")
