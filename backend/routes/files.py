"""
File explorer + storage analysis (read-only, always requires login) - routes moved out of app.py.
"""

import asyncio
import contextlib
import os
import secrets
import time
from html import escape as html_escape
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import audit
import auth
import exchange
import files
import fleet
import history
import search
import websec
from hostctx import remote_for as _remote_for
from webutil import logger, same_origin as _same_origin, serve_page as _serve_page

router = APIRouter()


async def _files_call(fn, path: str):
    try:
        return await asyncio.to_thread(fn, path)
    except files.FileAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/files/list")
async def api_files_list(path: str = "/"):
    return await _files_call(files.list_dir, path)


@router.get("/api/files/sizes")
async def api_files_sizes(path: str):
    return await _files_call(files.dir_sizes, path)


@router.get("/api/files/largest")
async def api_files_largest(path: str):
    return await _files_call(files.largest_files, path)


@router.get("/api/files/download")
async def api_files_download(path: str, request: Request):
    real = await _files_call(files.download_path, path)
    logger.info(f"Download {real} by '{request.state.user}'")
    return FileResponse(real, filename=os.path.basename(real))


class ShareRequest(BaseModel):
    path: str
    hours: int = 24


@router.get("/api/files/preview")
async def api_files_preview(path: str):
    return await _files_call(files.preview, path)


@router.get("/api/files/raw")
async def api_files_raw(path: str):
    """Images for the preview only - served inline, sandboxed, never HTML/SVG."""
    real = await _files_call(files.download_path, path)
    media = files.image_type(real)
    if not media:
        raise HTTPException(status_code=400, detail="Keine Bilddatei.")
    return FileResponse(real, media_type=media, headers={
        "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "no-store"})


@router.put("/api/files/upload")
async def api_files_upload(request: Request, dir: str, name: str, overwrite: bool = False):
    length = request.headers.get("content-length")
    size = int(length) if length and length.isdigit() else None
    request.state.audit_detail = f"{dir.rstrip('/')}/{name}"[:250]
    try:
        target = await asyncio.to_thread(files.upload_target, dir, name, overwrite, size)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Eine Datei mit diesem Namen gibt es dort schon.")
    except files.FileAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Write next to the target first, rename when complete: no half files on abort
    tmp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.upload-{secrets.token_hex(4)}")
    written = 0
    try:
        with open(tmp, "xb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > files.MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Datei zu groß (max. 4 GB).")
                await asyncio.to_thread(f.write, chunk)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    logger.info(f"Upload {target} ({written} bytes) by '{request.state.user}'")
    return {"ok": True, "path": target, "size": written}


class TextSaveRequest(BaseModel):
    path: str
    content: str
    mtime: Optional[str] = None


class NewEntryRequest(BaseModel):
    dir: str
    name: str
    kind: str = "file"            # file | folder


class PathRequest(BaseModel):
    path: str


@router.get("/api/files/text")
async def api_files_text(path: str):
    return await _files_call(files.read_text, path)


@router.post("/api/files/text")
async def api_files_text_save(req: TextSaveRequest, request: Request):
    request.state.audit_detail = req.path[:250]
    try:
        result = await asyncio.to_thread(files.write_text, req.path, req.content, req.mtime)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Die Datei wurde inzwischen geändert – neu laden oder überschreiben.")
    except (files.FileAccessError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Edited {result['path']} by '{request.state.user}'")
    return result


@router.post("/api/files/new")
async def api_files_new(req: NewEntryRequest, request: Request):
    request.state.audit_detail = f"{req.dir.rstrip('/')}/{req.name}"[:250]
    fn = files.create_folder if req.kind == "folder" else files.create_file
    try:
        return await asyncio.to_thread(fn, req.dir, req.name)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Den Namen gibt es dort schon.")
    except (files.FileAccessError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/files/delete")
async def api_files_delete(req: PathRequest, request: Request):
    request.state.audit_detail = req.path[:250]
    result = await _files_call(lambda p: files.trash_move(p, request.state.user), req.path)
    logger.info(f"Moved {req.path} to the recycle bin by '{request.state.user}'")
    return result


@router.get("/api/files/trash")
async def api_files_trash():
    return await asyncio.to_thread(files.trash_list)


@router.post("/api/files/trash/{item_id}/restore")
async def api_files_trash_restore(item_id: str, request: Request):
    result = await _files_call(files.trash_restore, item_id)
    request.state.audit_detail = result["path"][:250]
    return {**result, **await asyncio.to_thread(files.trash_list)}


@router.post("/api/files/trash/{item_id}/delete")
async def api_files_trash_delete(item_id: str):
    await _files_call(files.trash_delete, item_id)
    return await asyncio.to_thread(files.trash_list)


@router.post("/api/files/trash/empty")
async def api_files_trash_empty(request: Request):
    count = await asyncio.to_thread(files.trash_empty)
    request.state.audit_detail = f"{count}"
    return await asyncio.to_thread(files.trash_list)


@router.get("/api/files/search")
async def api_files_search(path: str, q: str = "", kind: str = "", min_size: int = 0, days: int = 0):
    return await asyncio.to_thread(_files_sync, files.search, path, q, kind, min_size, days)


def _files_sync(fn, *args):
    try:
        return fn(*args)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Den Namen gibt es dort schon.")
    except files.FileAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=e.strerror or str(e))


class RenameRequest(BaseModel):
    path: str
    name: str


class TransferRequest(BaseModel):
    paths: list
    target: str
    mode: str                     # move | copy


class PathsRequest(BaseModel):
    paths: list


class DuplicatesRequest(BaseModel):
    path: str
    min_size: int = files.DUPE_MIN_SIZE


@router.post("/api/files/rename")
async def api_files_rename(req: RenameRequest, request: Request):
    request.state.audit_detail = f"{req.path} → {req.name}"[:250]
    return await asyncio.to_thread(_files_sync, files.rename, req.path, req.name)


@router.post("/api/files/transfer")
async def api_files_transfer(req: TransferRequest, request: Request):
    request.state.audit_detail = f"{len(req.paths)} → {req.target}"[:250]
    return await asyncio.to_thread(_files_sync, files.transfer, [str(p) for p in req.paths], req.target, req.mode)


@router.get("/api/files/jobs/{job_id}")
async def api_files_job(job_id: str):
    return _files_sync(files.get_job, job_id)


@router.post("/api/files/jobs/{job_id}/cancel")
async def api_files_job_cancel(job_id: str):
    files.cancel_job(job_id)
    return _files_sync(files.get_job, job_id)


@router.post("/api/files/duplicates")
async def api_files_duplicates(req: DuplicatesRequest):
    return await asyncio.to_thread(_files_sync, files.start_duplicates, req.path, max(1, req.min_size))


_zip_tickets: dict = {}


@router.post("/api/files/zip")
async def api_files_zip_ticket(req: PathsRequest, request: Request):
    """Step 1: check the selection, hand out a short-lived ticket; step 2 (GET) streams it.
    Lets the browser download with a normal link instead of holding the ZIP in memory."""
    entries = await asyncio.to_thread(_files_sync, files.zip_entries, [str(p) for p in req.paths])
    now = time.time()
    for k in [k for k, v in _zip_tickets.items() if v["expires"] < now]:
        _zip_tickets.pop(k, None)
    ticket = secrets.token_urlsafe(18)
    _zip_tickets[ticket] = {**entries, "user": request.state.user, "expires": now + 300, "paths": req.paths[:20]}
    return {"ticket": ticket, "name": entries["name"], "count": len(entries["files"]),
            "size": sum(os.path.getsize(f) for f, _ in entries["files"] if os.path.exists(f))}


@router.get("/api/files/zip/{ticket}")
async def api_files_zip(ticket: str, request: Request):
    entry = _zip_tickets.pop(ticket, None)
    if not entry or entry["expires"] < time.time() or entry["user"] != request.state.user:
        raise HTTPException(status_code=404, detail="Download-Link abgelaufen.")
    request.state.audit_detail = f"ZIP {entry['name']} ({len(entry['files'])})"
    logger.info(f"ZIP download {entry['name']} ({len(entry['files'])} files) by '{request.state.user}'")
    from urllib.parse import quote
    return StreamingResponse(files.zip_stream(entry["files"]), media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry['name'])}", "Cache-Control": "no-store"})


@router.post("/api/files/share")
async def api_files_share(req: ShareRequest, request: Request):
    if _remote_for(request):
        # Share links are served by this server - they can't hand out a remote server's files
        raise HTTPException(status_code=400, detail="Freigabe-Links gibt es nur für Dateien dieses Servers.")
    request.state.audit_detail = f"{req.path[:200]} · {req.hours} Std."
    return await _files_call(lambda p: files.create_share(p, req.hours, request.state.user), req.path)


@router.get("/api/files/shares")
async def api_files_shares():
    return {"shares": await asyncio.to_thread(files.list_shares)}


@router.post("/api/files/shares/{token}/delete")
async def api_files_share_delete(token: str):
    await asyncio.to_thread(files.delete_share, token)
    return {"shares": await asyncio.to_thread(files.list_shares)}


class ExchangeRequest(BaseModel):
    folder: str
    mode: str = "upload"
    hours: int = 168
    max_mb: int = 500
    note: str = ""


@router.get("/api/exchange")
async def api_exchange_list():
    return {"links": await asyncio.to_thread(exchange.list_links), "modes": exchange.MODES, "hours": exchange.HOURS}


@router.post("/api/exchange")
async def api_exchange_create(req: ExchangeRequest, request: Request):
    request.state.audit_detail = f"{exchange.MODES.get(req.mode, req.mode)} · {req.folder}"[:250]
    try:
        link = await asyncio.to_thread(exchange.create, req.folder, req.mode, req.hours, req.max_mb, req.note,
                                       request.state.user or "", auth.can(request.state.user, "files.write"))
    except exchange.ExchangeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return link


@router.post("/api/exchange/{token}/delete")
async def api_exchange_delete(token: str, request: Request):
    await asyncio.to_thread(exchange.delete, token)
    return {"links": await asyncio.to_thread(exchange.list_links)}


def _exchange_error(e: Exception, status: int = 404) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=status)


@router.get("/x/{token}")
async def public_exchange_page(token: str):
    return _serve_page("exchange.html")


@router.get("/api/x/{token}")
async def public_exchange_info(token: str):
    try:
        return await asyncio.to_thread(exchange.info, token)
    except exchange.ExchangeError as e:
        return _exchange_error(e)


@router.put("/api/x/{token}/upload")
async def public_exchange_upload(token: str, name: str, request: Request):
    """Public upload into an exchange folder: same-origin only, limit checked while streaming."""
    if not _same_origin(request):
        return JSONResponse({"detail": "Ungültige Herkunft der Anfrage."}, status_code=403)
    length = request.headers.get("content-length")
    size = int(length) if length and length.isdigit() else None
    try:
        t = await asyncio.to_thread(exchange.upload_target, token, name, size)
    except exchange.ExchangeError as e:
        return _exchange_error(e, 400)
    target, left = t["target"], t["left"]
    tmp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.upload-{secrets.token_hex(4)}")
    written = 0
    try:
        with open(tmp, "xb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > left:
                    raise HTTPException(status_code=413, detail="Das Limit dieses Links ist erreicht.")
                await asyncio.to_thread(f.write, chunk)
        if os.path.lexists(target):  # someone else took the name meanwhile
            target = (await asyncio.to_thread(exchange.upload_target, token, os.path.basename(target), 0))["target"]
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    ip = websec.client_ip(request)
    await asyncio.to_thread(exchange.record_upload, token, os.path.basename(target), written, ip)
    request.state.audit_user = "Austausch-Link"   # the activity log middleware records this request
    request.state.audit_detail = target
    return {"ok": True, "name": os.path.basename(target), "size": written}


@router.get("/api/x/{token}/file")
async def public_exchange_download(token: str, name: str, request: Request):
    try:
        path = await asyncio.to_thread(exchange.download_path, token, name)
    except exchange.ExchangeError as e:
        return _exchange_error(e)
    ip = websec.client_ip(request)
    await asyncio.to_thread(audit.record, "Austausch-Link", ip, "GET", "/x/file", 200, path)
    return FileResponse(path, filename=os.path.basename(path), headers={"X-Content-Type-Options": "nosniff"})


@router.get("/flotte")
async def get_fleet_page():
    return _serve_page("fleet.html")


@router.get("/werkzeuge")
async def get_tools_page():
    return _serve_page("tools.html")


@router.get("/api/fleet")
async def api_fleet():
    return await fleet.overview()


class FleetAptRequest(BaseModel):
    ids: list
    mode: str = "upgrade"


class FleetPackagesRequest(BaseModel):
    targets: dict   # {pi id ("" = this server): [package names]}


@router.post("/api/fleet/packages")
async def api_fleet_packages(req: FleetPackagesRequest, request: Request):
    targets = {str(k): [str(n) for n in (v or [])] for k, v in req.targets.items()}
    request.state.audit_detail = f"{sum(len(v) for v in targets.values())} Pakete · {len(targets)} Server"
    return await fleet.upgrade_packages(targets, request.state.user or "")


@router.get("/api/fleet/job")
async def api_fleet_job(id: str = ""):
    try:
        return await fleet.job_log(id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Nicht erreichbar ({type(e).__name__})")


@router.post("/api/fleet/apt")
async def api_fleet_apt(req: FleetAptRequest, request: Request):
    request.state.audit_detail = f"{req.mode} · {len(req.ids)} Server"
    try:
        return await fleet.apt([str(i) for i in req.ids][:50], req.mode, request.state.user or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/search")
async def api_search(q: str = ""):
    return await asyncio.to_thread(search.search, q)


@router.get("/s/{token}")
async def public_share(token: str, request: Request):
    """Public download for a share link (no login) - expiry and file checked on every call."""
    try:
        share = await asyncio.to_thread(files.shared_file, token)
    except files.FileAccessError as e:
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Link ungültig</title>"
                            f"<body style='font-family:sans-serif;padding:40px'><h2>{html_escape(str(e))}</h2>"
                            f"<p>Bitte bei der Person nachfragen, die dir den Link geschickt hat.</p>", status_code=404)
    ip = websec.client_ip(request)
    await asyncio.to_thread(audit.record, "Freigabe-Link", ip, "GET", "/s/", 200, share["path"])
    return FileResponse(share["path"], filename=share["name"])


@router.get("/api/nvme/history")
async def api_nvme_history(days: int = 90):
    return await asyncio.to_thread(history.get_nvme_history, days)
