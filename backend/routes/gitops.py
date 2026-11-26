"""
GitOps: compose stacks from a Git repository (gitops.py) - routes moved out of app.py.
"""

import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import appstore
import containers
import gitops
import history
import imagecheck
import kanban
import reminders
import system_info
from webutil import logger

router = APIRouter()


def _gitops_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except gitops.GitOpsError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/gitops")
async def api_gitops():
    return await asyncio.to_thread(gitops.overview)


@router.post("/api/gitops")
async def api_gitops_create(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('name', '')} · {payload.get('url', '')}"[:200]
    return await asyncio.to_thread(_gitops_call, gitops.create, payload, request.state.user or "")


@router.get("/api/gitops/{name}/diff")
async def api_gitops_diff(name: str):
    return await asyncio.to_thread(_gitops_call, gitops.diff, name)


@router.get("/api/gitops/{name}/env")
async def api_gitops_env(name: str):
    return {"env": await asyncio.to_thread(_gitops_call, gitops.env_text, name)}


@router.post("/api/gitops/{name}/check")
async def api_gitops_check(name: str):
    return await asyncio.to_thread(_gitops_call, gitops.check, name, False)


@router.post("/api/gitops/{name}/deploy")
async def api_gitops_deploy(name: str, payload: dict, request: Request):
    request.state.audit_detail = f"{name} @ {str(payload.get('commit') or 'neueste')[:12]}"
    return await asyncio.to_thread(_gitops_call, gitops.deploy, name, request.state.user or "", str(payload.get("commit") or ""))


@router.post("/api/gitops/{name}/update")
async def api_gitops_update(name: str, payload: dict, request: Request):
    request.state.audit_detail = name
    return await asyncio.to_thread(_gitops_call, gitops.edit, name, payload)


@router.post("/api/gitops/{name}/hook")
async def api_gitops_new_hook(name: str, request: Request):
    request.state.audit_detail = name
    return await asyncio.to_thread(_gitops_call, gitops.new_hook, name)


@router.post("/api/gitops/{name}/delete")
async def api_gitops_delete(name: str, payload: dict, request: Request):
    request.state.audit_detail = name + (" · gestoppt" if payload.get("stop") else "") + (" · Ordner gelöscht" if payload.get("delete_files") else "")
    return await asyncio.to_thread(_gitops_call, gitops.remove, name, bool(payload.get("delete_files")), bool(payload.get("stop")),
                                   request.state.user or "")


@router.post("/hook/gitops/{token}")
async def public_gitops_hook(token: str):
    """Deploy webhook (GitHub, Gitea, GitLab ...): the token in the path names the stack; only a check
    runs - deploying follows the stack's own setting."""
    if len(token) > 100 or not await asyncio.to_thread(gitops.hook, token):
        return JSONResponse({"detail": "Unbekannt."}, status_code=404)
    return {"ok": True}


@router.get("/api/containers/images")
async def api_container_images():
    return imagecheck.status()


@router.post("/api/containers/images/check")
async def api_container_images_check():
    return imagecheck.start_check()


@router.get("/api/containers/images/releases")
async def api_container_image_releases(ref: str, refresh: bool = False):
    try:
        return await asyncio.to_thread(imagecheck.releases, ref, refresh)
    except (ValueError, httpx.HTTPError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "GitHub nicht erreichbar.")


@router.post("/api/containers/images/source")
async def api_container_image_source(payload: dict):
    try:
        return await asyncio.to_thread(imagecheck.set_source, str(payload.get("repo", "")), str(payload.get("github", "")))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _appstore_call(fn, *args):
    try:
        return fn(*args)
    except (appstore.AppStoreError, containers.ContainerError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Ordner/Datei: {e.strerror or e}")


class AppInstallRequest(BaseModel):
    name: str
    ports: dict = {}
    folders: dict = {}
    env: dict = {}

    def values(self) -> dict:
        return {"name": self.name, "ports": self.ports, "folders": self.folders, "env": self.env}


@router.get("/api/appstore")
async def api_appstore():
    try:
        return await asyncio.to_thread(appstore.catalog)
    except Exception as e:
        logger.exception("App store catalog failed")
        raise HTTPException(status_code=500, detail=f"App-Store: {type(e).__name__}: {e}"[:300])


@router.post("/api/appstore/refresh")
async def api_appstore_refresh():
    await asyncio.to_thread(appstore.refresh)
    return await asyncio.to_thread(appstore.catalog)


@router.get("/api/appstore/logo/{app_id}")
async def api_appstore_logo(app_id: str):
    found = await asyncio.to_thread(appstore.logo, app_id)
    if not found:
        raise HTTPException(status_code=404, detail="Kein Logo.")
    body, ctype = found
    # SVGs may carry scripts: never run them in the dashboard's origin
    return Response(body, media_type=ctype, headers={"Cache-Control": "public, max-age=604800",
                                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                                                     "X-Content-Type-Options": "nosniff"})


@router.get("/api/appstore/{app_id}/form")
async def api_appstore_form(app_id: str):
    return await asyncio.to_thread(_appstore_call, appstore.form, app_id)


@router.post("/api/appstore/{app_id}/preview")
async def api_appstore_preview(app_id: str, req: AppInstallRequest):
    return await asyncio.to_thread(_appstore_call, appstore.preview, app_id, req.values())


@router.post("/api/appstore/{app_id}/install")
async def api_appstore_install(app_id: str, req: AppInstallRequest, request: Request):
    request.state.audit_detail = f"{app_id} als {req.name[:40]}"
    job = await asyncio.to_thread(_appstore_call, appstore.install, app_id, req.values(), request.state.user or "")
    logger.info(f"App {app_id} installed as {req.name} by '{request.state.user}'")
    return job


@router.get("/api/capabilities")
async def api_capabilities():
    """Hardware features of this server (or of the selected remote server, via the proxy)."""
    return await asyncio.to_thread(system_info.get_capabilities)


@router.get("/api/system")
async def api_system():
    """Return full system snapshot."""
    return await asyncio.to_thread(system_info.get_system_snapshot)


@router.get("/api/processes")
async def api_processes(limit: int = 8):
    """Return top active processes."""
    return {"processes": await asyncio.to_thread(system_info.get_top_processes, limit=limit)}


@router.get("/api/history")
async def api_history(range: str = "24h", iface: str = ""):
    """Downsampled metrics history: range=24h or 7d; iface = network values of one interface."""
    return await asyncio.to_thread(history.get_history, range, iface[:32])


@router.get("/api/replay")
async def api_replay(start: int, end: int = 0):
    """Every minute sample of a time window (max. 2 days) for the day replay."""
    return await asyncio.to_thread(history.get_raw, start, end or start + 86400)


class KanbanCardRequest(BaseModel):
    title: Optional[str] = None
    desc: Optional[str] = None
    labels: Optional[list] = None
    due: Optional[str] = None
    column: Optional[str] = None
    index: Optional[int] = None
    top: bool = False

    def fields(self) -> dict:
        return {k: v for k, v in {"title": self.title, "desc": self.desc, "labels": self.labels, "due": self.due,
                                  "column": self.column, "index": self.index, "top": self.top}.items() if v is not None}


def _kanban_call(fn, *args):
    try:
        return fn(*args)
    except kanban.KanbanError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/kanban")
async def api_kanban():
    return kanban.board()


@router.post("/api/kanban/cards")
async def api_kanban_create(req: KanbanCardRequest, request: Request):
    return await asyncio.to_thread(_kanban_call, kanban.create, req.fields(), request.state.user or "")


@router.post("/api/kanban/cards/{card_id}")
async def api_kanban_update(card_id: str, req: KanbanCardRequest, request: Request):
    return await asyncio.to_thread(_kanban_call, kanban.update, card_id, req.fields(), request.state.user or "")


@router.post("/api/kanban/cards/{card_id}/delete")
async def api_kanban_delete(card_id: str):
    return await asyncio.to_thread(_kanban_call, kanban.delete, card_id)


@router.post("/api/kanban/clear-done")
async def api_kanban_clear_done():
    return await asyncio.to_thread(_kanban_call, kanban.clear_done)


class ReminderRequest(BaseModel):
    title: str
    due: str
    repeat: str = "none"
    every: int = 1


def _reminder_call(fn, *args):
    try:
        return fn(*args)
    except reminders.ReminderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/reminders")
async def api_reminders():
    return reminders.overview()


@router.post("/api/reminders")
async def api_reminders_create(req: ReminderRequest, request: Request):
    return await asyncio.to_thread(_reminder_call, reminders.create, {"title": req.title, "due": req.due, "repeat": req.repeat,
                                                                      "every": req.every}, request.state.user or "")


@router.post("/api/reminders/{item_id}")
async def api_reminders_update(item_id: str, req: ReminderRequest):
    return await asyncio.to_thread(_reminder_call, reminders.update, item_id, {"title": req.title, "due": req.due,
                                                                               "repeat": req.repeat, "every": req.every})


@router.post("/api/reminders/{item_id}/done")
async def api_reminders_done(item_id: str, request: Request):
    return await asyncio.to_thread(_reminder_call, reminders.done, item_id, request.state.user or "")


@router.post("/api/reminders/{item_id}/snooze")
async def api_reminders_snooze(item_id: str, days: int = 1):
    return await asyncio.to_thread(_reminder_call, reminders.snooze, item_id, days)


@router.post("/api/reminders/{item_id}/delete")
async def api_reminders_delete(item_id: str):
    return await asyncio.to_thread(_reminder_call, reminders.delete, item_id)
