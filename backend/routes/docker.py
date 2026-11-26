"""
Docker container control - routes moved out of app.py.
"""

import asyncio
import platform
import subprocess

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import boards
import changelog
import containers
import homelab
import logdigest
import netbox
import packages
import power
import security
import statuspage
import system_info
import updater
from hostctx import AGENT
from webutil import _os_name, logger, serve_page as _serve_page

router = APIRouter()


@router.post("/api/workloads/docker/{name}/{action}")
async def api_docker_action(name: str, action: str, request: Request):
    result = await asyncio.to_thread(homelab.docker_action, name, action)
    containers.invalidate()
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"docker {action} {name} by '{request.state.user}'")
    request.state.audit_detail = f"{action} {name}"
    return result


@router.get("/api/workloads/docker/{name}/logs")
async def api_docker_logs(name: str, lines: int = 200):
    result = await asyncio.to_thread(homelab.docker_logs, name, lines)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


class CommandRequest(BaseModel):
    command: str


class WeatherLocationRequest(BaseModel):
    city: str


@router.get("/")
async def get_index():
    return _serve_page("index.html")


@router.get("/report")
async def get_report_page():
    return _serve_page("report.html")


@router.get("/settings")
async def get_settings_page():
    return _serve_page("settings.html")


@router.get("/terminal")
async def get_terminal_page():
    return _serve_page("terminal.html")


@router.get("/system")
async def get_system_page():
    return _serve_page("system.html")


@router.get("/status")
async def get_status_page():
    return _serve_page("status.html")


@router.get("/api/public/status")
async def api_public_status():
    data = await asyncio.to_thread(statuspage.public_status)
    if data is None:
        return JSONResponse({"detail": "Die Statusseite ist ausgeschaltet."}, status_code=404)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


class StatusPageRequest(BaseModel):
    enabled: bool = False
    title: str = ""
    description: str = ""
    show_history: bool = True
    items: list = []


@router.get("/api/settings/status-page")
async def api_status_page_settings():
    return {"config": statuspage.config(), "available": await asyncio.to_thread(statuspage.available)}


@router.post("/api/settings/status-page")
async def api_status_page_save(req: StatusPageRequest, request: Request):
    try:
        cfg = await asyncio.to_thread(statuspage.save, req.enabled, req.title, req.description, req.show_history, req.items)
    except statuspage.StatusPageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{'an' if cfg['enabled'] else 'aus'} · {len(cfg['items'])} Einträge"
    return cfg


@router.get("/api/changelog")
async def api_changelog():
    return await asyncio.to_thread(changelog.changelog)


class PowerSettingsRequest(BaseModel):
    price_ct: float
    extra_w: float = 0
    currency: str = "€"


@router.get("/api/power")
async def api_power():
    return await asyncio.to_thread(power.overview)


@router.post("/api/power/settings")
async def api_power_settings(req: PowerSettingsRequest):
    try:
        return await asyncio.to_thread(power.save_settings, req.price_ct, req.extra_w, req.currency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/tools/connections")
async def api_connections():
    return await asyncio.to_thread(security.connections)


@router.get("/api/tools/errors")
async def api_error_digest(days: int = 7):
    return await asyncio.to_thread(logdigest.digest, days)


class PackagesRequest(BaseModel):
    names: list
    action: str = "install"


def _pkg_call(fn, *args):
    try:
        return fn(*args)
    except packages.PackageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="apt antwortet nicht.")
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="apt ist auf diesem Gerät nicht verfügbar.")


@router.get("/api/packages")
async def api_packages():
    return await asyncio.to_thread(packages.installed)


@router.get("/api/packages/search")
async def api_packages_search(q: str):
    return await asyncio.to_thread(_pkg_call, packages.search, q)


@router.get("/api/packages/show")
async def api_packages_show(name: str):
    return await asyncio.to_thread(_pkg_call, packages.show, name)


@router.get("/api/packages/status")
async def api_packages_status():
    return await asyncio.to_thread(packages.status)


@router.post("/api/packages/simulate")
async def api_packages_simulate(req: PackagesRequest):
    return await asyncio.to_thread(_pkg_call, packages.simulate, req.action, [str(n) for n in req.names])


@router.post("/api/packages/run")
async def api_packages_run(req: PackagesRequest, request: Request):
    request.state.audit_detail = f"{req.action} {' '.join(str(n) for n in req.names)}"[:250]
    result = await asyncio.to_thread(_pkg_call, packages.start, req.action, [str(n) for n in req.names])
    logger.info(f"apt {req.action} {req.names} by '{request.state.user}'")
    return result


@router.get("/api/agent/info")
async def api_agent_info():
    """For the main dashboard: is this an agent, and which server is it."""
    if not AGENT:
        raise HTTPException(status_code=404, detail="Kein Agent.")
    snap = await asyncio.to_thread(system_info.get_system_snapshot)
    up = snap.get("uptime") or {}
    caps = await asyncio.to_thread(system_info.get_capabilities)
    return {"agent": True, "hostname": snap.get("host_name") or platform.node(), "model": snap.get("model", ""),
            "os": _os_name(), "kind": caps.get("kind", ""), "arch": platform.machine(),
            "ip": (snap.get("network_ip") or {}).get("primary_ip", "") if isinstance(snap.get("network_ip"), dict) else snap.get("network_ip", ""), "commit": (updater.update_state or {}).get("current_commit", ""),
            "uptime": up.get("formatted", "") if isinstance(up, dict) else up}


class BoardsRequest(BaseModel):
    rev: int
    boards: list


@router.get("/api/boards")
async def api_boards():
    return {**(await boards.ensure()), "cards": boards.CARDS, "engines": boards.ENGINES}


@router.post("/api/boards")
async def api_boards_save(req: BoardsRequest, request: Request):
    try:
        cfg = await asyncio.to_thread(boards.save, {"boards": req.boards}, req.rev, request.state.user or "")
    except boards.BoardConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except boards.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cfg


@router.post("/api/boards/reset")
async def api_boards_reset():
    return await asyncio.to_thread(boards.reset)


@router.get("/api/boards/history")
async def api_boards_history():
    return await asyncio.to_thread(boards.history)


class RestoreRequest(BaseModel):
    rev: int
    base_rev: int


@router.post("/api/boards/restore")
async def api_boards_restore(req: RestoreRequest, request: Request):
    request.state.audit_detail = f"Version {req.rev}"
    try:
        return await asyncio.to_thread(boards.restore, req.rev, req.base_rev, request.state.user or "")
    except boards.BoardConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except boards.BoardError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/boards/status")
async def api_boards_status(board: str):
    try:
        return await boards.status(board)
    except boards.BoardError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/boards/rss")
async def api_boards_rss(board: str, item: str):
    try:
        return await boards.rss(board, item)
    except boards.BoardError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/boards/remote/{remote_id}")
async def api_boards_remote(remote_id: str):
    try:
        return await boards.remote(remote_id)
    except boards.BoardError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/boards/discover")
async def api_boards_discover():
    return await boards.discover()


class ProbeRequest(BaseModel):
    host: str


@router.post("/api/boards/probe")
async def api_boards_probe(req: ProbeRequest, request: Request):
    request.state.audit_detail = req.host[:120]
    try:
        return await boards.probe(req.host)
    except boards.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/calendar")
async def api_calendar(start: str, days: int = 42, sources: str = "reminders,tasks,maintenance"):
    try:
        return await asyncio.to_thread(boards.calendar, start, days, sources.split(","))
    except boards.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/boards/icons")
async def api_boards_icons(q: str = ""):
    try:
        return {"icons": await boards.search_icons(q)}
    except boards.BoardError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/boards/icon/{file}")
async def api_boards_icon(file: str):
    path = await boards.icon(file)
    if not path:
        raise HTTPException(status_code=404, detail="Icon nicht gefunden.")
    media = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp"}[file.rsplit(".", 1)[1]]
    # Never a document on this origin: an SVG opened directly could otherwise run scripts
    return FileResponse(path, media_type=media, headers={
        "Cache-Control": "public, max-age=604800",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "X-Content-Type-Options": "nosniff"})


class NetBoxSettingsRequest(BaseModel):
    url: str = ""
    token: str = ""
    verify: bool = True


class NetBoxIpRequest(BaseModel):
    ip: str
    status: str = "active"
    dns_name: str = ""
    description: str = ""


@router.get("/api/netbox/status")
async def api_netbox_status():
    return netbox.public()


@router.get("/api/netbox/data")
async def api_netbox_data(refresh: bool = False):
    try:
        return await netbox.data(refresh)
    except netbox.NetBoxError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/netbox/ip")
async def api_netbox_ip(req: NetBoxIpRequest, request: Request):
    request.state.audit_detail = f"{req.ip} {req.dns_name}".strip()[:250]
    try:
        return await netbox.create_ip(req.ip, req.status, req.dns_name, req.description)
    except netbox.NetBoxError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/netbox")
async def api_settings_netbox(req: NetBoxSettingsRequest, request: Request):
    request.state.audit_detail = req.url[:250]
    try:
        return await netbox.save(req.url, req.token, req.verify)
    except netbox.NetBoxError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/netbox/test")
async def api_settings_netbox_test(req: NetBoxSettingsRequest):
    try:
        url = netbox.normalize_url(req.url)
        token = req.token.strip()
        cur = netbox._cfg()
        if not token and cur and cur["url"] == url:
            token = cur["token"]
        return await netbox.probe(url, token, req.verify)
    except netbox.NetBoxError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/settings/netbox/delete")
async def api_settings_netbox_delete():
    netbox.remove()
    return netbox.public()


@router.get("/containers")
async def get_containers_page():
    return _serve_page("containers.html")
