"""
Files page (/dateien): the own cloud - see backend/cloud.py. Always on the main dashboard
(local routes), whichever server is selected.
"""

import asyncio
import contextlib
import os
import secrets
import time
from html import escape as html_escape
from typing import List
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

import audit
import auth
import cloud
import files
import websec
from webutil import logger, serve_page

router = APIRouter()


def _who(request: Request):
    user = request.state.user or ""
    return user, auth.can(user, "users.manage")


def _call(fn, *args):
    try:
        return fn(*args)
    except cloud.Conflict as e:
        raise HTTPException(status_code=409, detail=f"„{e}“ gibt es dort schon.")
    except cloud.CloudError as e:
        raise HTTPException(status_code=404 if "nicht gefunden" in str(e).lower() or "Nicht im" in str(e) else 400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=e.strerror or str(e))


async def _key(request: Request, space: str) -> str:
    user, admin = _who(request)
    return await asyncio.to_thread(_call, cloud.space_key, space, user, admin)


@router.get("/dateien")
async def get_files_page():
    return serve_page("cloud.html")


@router.get("/api/cloud")
async def api_cloud_overview(request: Request):
    user, admin = _who(request)
    return await asyncio.to_thread(_call, cloud.overview, user, admin)


@router.get("/api/cloud/list")
async def api_cloud_list(request: Request, space: str = "me", path: str = ""):
    user, _ = _who(request)
    key = await _key(request, space)
    return await asyncio.to_thread(_call, cloud.list_dir, key, path, user)


@router.get("/api/cloud/recent")
async def api_cloud_recent(request: Request):
    user, admin = _who(request)
    return {"items": await asyncio.to_thread(_call, cloud.recent, user, admin)}


@router.get("/api/cloud/favorites")
async def api_cloud_favorites(request: Request):
    user, admin = _who(request)
    return {"items": await asyncio.to_thread(_call, cloud.favorites, user, admin)}


@router.get("/api/cloud/search")
async def api_cloud_search(request: Request, q: str = ""):
    user, admin = _who(request)
    return await asyncio.to_thread(_call, cloud.search, user, admin, q)


@router.get("/api/cloud/uploads")
async def api_cloud_uploads(request: Request):
    """Everything uploaded outside the cloud: exchange links and documentation attachments."""
    user, _ = _who(request)
    return await asyncio.to_thread(cloud.uploads_elsewhere, auth.can(user, "files.share"), True)


@router.get("/api/cloud/file")
async def api_cloud_file(request: Request, space: str, path: str, inline: bool = False):
    """Download - or, for images, PDFs, audio and video, inline for the preview (sandboxed)."""
    key = await _key(request, space)
    real = await asyncio.to_thread(_call, cloud.file_path, key, path)
    media = cloud.inline_type(real) if inline else None
    if media:
        return FileResponse(real, media_type=media, content_disposition_type="inline", filename=os.path.basename(real), headers={
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"})
    return FileResponse(real, filename=os.path.basename(real), headers={"X-Content-Type-Options": "nosniff"})


@router.get("/api/cloud/text")
async def api_cloud_text(request: Request, space: str, path: str):
    key = await _key(request, space)
    return await asyncio.to_thread(_call, cloud.text_preview, key, path)


@router.put("/api/cloud/upload")
async def api_cloud_upload(request: Request, space: str, name: str, path: str = "", sub: str = "", overwrite: bool = False):
    key = await _key(request, space)
    length = request.headers.get("content-length")
    size = int(length) if length and length.isdigit() else None
    request.state.audit_detail = f"{space}:{path}/{sub + '/' if sub else ''}{name}"[:250]
    target = await asyncio.to_thread(_call, cloud.upload_target, key, path, name, overwrite, size, sub)
    left = await asyncio.to_thread(cloud.quota_left, key)
    tmp = os.path.join(os.path.dirname(target), f".cloud-upload-{secrets.token_hex(6)}")
    written = 0
    try:
        with open(tmp, "xb") as f:
            os.chmod(tmp, 0o600)
            async for chunk in request.stream():
                written += len(chunk)
                if written > cloud.MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Datei zu groß (max. 4 GB).")
                if left is not None and written > left:
                    raise HTTPException(status_code=413, detail="Dein Speicher ist voll – das Kontingent ist erreicht.")
                await asyncio.to_thread(f.write, chunk)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    logger.info(f"Cloud upload {target} ({written} bytes) by '{request.state.user}'")
    return {"ok": True, "name": os.path.basename(target), "size": written}


class FolderRequest(BaseModel):
    space: str
    path: str = ""
    name: str


class EntryRequest(BaseModel):
    space: str
    path: str


class EntriesRequest(BaseModel):
    space: str
    paths: List[str]


class RenameRequest(BaseModel):
    space: str
    path: str
    name: str


class TransferRequest(BaseModel):
    space: str
    paths: List[str]
    target_space: str
    target: str = ""
    mode: str = "move"


class FavoriteRequest(BaseModel):
    space: str
    path: str
    on: bool = True


class ShareRequest(BaseModel):
    space: str
    path: str
    hours: int = 168
    max_downloads: int = 0
    password: str = ""
    notify: bool = False


class TrashRequest(BaseModel):
    space: str
    id: str


@router.post("/api/cloud/folder")
async def api_cloud_folder(req: FolderRequest, request: Request):
    key = await _key(request, req.space)
    request.state.audit_detail = f"{req.space}:{req.path}/{req.name}"[:250]
    return await asyncio.to_thread(_call, cloud.mkdir, key, req.path, req.name)


@router.post("/api/cloud/rename")
async def api_cloud_rename(req: RenameRequest, request: Request):
    key = await _key(request, req.space)
    request.state.audit_detail = f"{req.space}:{req.path} → {req.name}"[:250]
    return await asyncio.to_thread(_call, cloud.rename, key, req.path, req.name)


@router.post("/api/cloud/transfer")
async def api_cloud_transfer(req: TransferRequest, request: Request):
    src = await _key(request, req.space)
    dst = await _key(request, req.target_space)
    request.state.audit_detail = f"{len(req.paths)} {req.space} → {req.target_space}:{req.target} ({req.mode})"[:250]
    return await asyncio.to_thread(_call, cloud.transfer, src, req.paths, dst, req.target, req.mode)


@router.post("/api/cloud/delete")
async def api_cloud_delete(req: EntriesRequest, request: Request):
    key = await _key(request, req.space)
    user = request.state.user or ""
    request.state.audit_detail = f"{req.space}: {', '.join(req.paths)}"[:250]
    for p in req.paths[:500]:
        await asyncio.to_thread(_call, cloud.trash_move, key, p, user)
    return {"count": len(req.paths[:500])}


@router.post("/api/cloud/favorite")
async def api_cloud_favorite(req: FavoriteRequest, request: Request):
    key = await _key(request, req.space)
    await asyncio.to_thread(_call, cloud.set_favorite, request.state.user or "", key, req.path, req.on)
    return {"ok": True}


_zip_tickets: dict = {}


@router.post("/api/cloud/zip")
async def api_cloud_zip_ticket(req: EntriesRequest, request: Request):
    """Step 1: check the selection and hand out a short-lived ticket; step 2 (GET) streams the ZIP."""
    key = await _key(request, req.space)
    entries = await asyncio.to_thread(_call, cloud.zip_entries, key, req.paths)
    now = time.time()
    for k in [k for k, v in _zip_tickets.items() if v["expires"] < now]:
        _zip_tickets.pop(k, None)
    ticket = secrets.token_urlsafe(18)
    _zip_tickets[ticket] = {**entries, "user": request.state.user, "expires": now + 300}
    return {"ticket": ticket, "name": entries["name"], "count": len(entries["files"])}


@router.get("/api/cloud/zip/{ticket}")
async def api_cloud_zip(ticket: str, request: Request):
    entry = _zip_tickets.pop(ticket, None)
    if not entry or entry["expires"] < time.time() or entry["user"] != request.state.user:
        raise HTTPException(status_code=404, detail="Download-Link abgelaufen.")
    return StreamingResponse(files.zip_stream(entry["files"]), media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry['name'])}", "Cache-Control": "no-store"})


@router.get("/api/cloud/trash")
async def api_cloud_trash(request: Request):
    user, admin = _who(request)
    return await asyncio.to_thread(_call, cloud.trash_list, cloud.keys_for(user, admin), user)


@router.post("/api/cloud/trash/restore")
async def api_cloud_trash_restore(req: TrashRequest, request: Request):
    key = await _key(request, req.space)
    request.state.audit_detail = f"{req.space}:{req.id}"
    return await asyncio.to_thread(_call, cloud.trash_restore, key, req.id)


@router.post("/api/cloud/trash/delete")
async def api_cloud_trash_delete(req: TrashRequest, request: Request):
    key = await _key(request, req.space)
    request.state.audit_detail = f"{req.space}:{req.id}"
    await asyncio.to_thread(_call, cloud.trash_delete, key, req.id)
    return {"ok": True}


@router.post("/api/cloud/trash/empty")
async def api_cloud_trash_empty(request: Request):
    user, admin = _who(request)
    n = await asyncio.to_thread(_call, cloud.trash_empty, cloud.keys_for(user, admin))
    request.state.audit_detail = str(n)
    return {"count": n}


@router.get("/api/cloud/shares")
async def api_cloud_shares(request: Request):
    user, admin = _who(request)
    return {"shares": await asyncio.to_thread(cloud.list_shares, user, admin)}


@router.post("/api/cloud/shares")
async def api_cloud_share_create(req: ShareRequest, request: Request):
    key = await _key(request, req.space)
    extra = [f"max. {req.max_downloads}×" if req.max_downloads else "", "Passwort" if req.password else "", "Mail" if req.notify else ""]
    request.state.audit_detail = " · ".join([f"{req.space}:{req.path}", f"{req.hours} Std.", *[x for x in extra if x]])[:250]
    return await asyncio.to_thread(_call, cloud.create_share, key, req.path, req.hours, request.state.user or "",
                                   req.max_downloads, req.password, req.notify)


@router.post("/api/cloud/shares/{token}/delete")
async def api_cloud_share_delete(token: str, request: Request):
    user, admin = _who(request)
    await asyncio.to_thread(_call, cloud.delete_share, token, user, admin)
    return {"shares": await asyncio.to_thread(cloud.list_shares, user, admin)}


@router.post("/api/settings/cloud")
async def api_cloud_settings(payload: dict, request: Request):
    request.state.audit_detail = f"{payload.get('root') or cloud.DEFAULT_ROOT} · {payload.get('quota_mb', 0)} MB"[:250]
    return await asyncio.to_thread(_call, cloud.save, payload)


_SHARE_FAILS: dict = {}          # ip -> [timestamps of wrong passwords]
FAIL_WINDOW, FAIL_MAX = 900, 10


def _share_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=de><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html_escape(title)}</title><meta name=robots content=noindex>"
        "<style>body{font-family:system-ui,sans-serif;background:#0b0e14;color:#e5e7eb;display:flex;min-height:100vh;"
        "align-items:center;justify-content:center;margin:0;padding:20px}main{background:#141925;border:1px solid #262d3d;"
        "border-radius:16px;padding:28px;max-width:420px;width:100%}h1{font-size:1.15rem;margin:0 0 4px;word-break:break-word}"
        "p{color:#94a3b8;font-size:.9rem;margin:6px 0 16px}input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;"
        "border:1px solid #334155;background:#0b0e14;color:#e5e7eb;font-size:1rem;margin-bottom:12px}button{width:100%;padding:11px;"
        "border:0;border-radius:10px;background:#00c8d7;color:#04121a;font-weight:600;font-size:1rem;cursor:pointer}"
        ".err{color:#ff6b8a}</style><main>" + body + "</main></html>", status_code=status)


def _invalid(e: Exception) -> HTMLResponse:
    return _share_page("Link ungültig", f"<h1>{html_escape(str(e))}</h1>"
                                        "<p>Bitte bei der Person nachfragen, die dir den Link geschickt hat.</p>", 404)


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


def _password_form(s: dict, error: str = "", status: int = 200) -> HTMLResponse:
    size = f" · {_fmt(s['size'])}" if s.get("size") is not None else " · Ordner als ZIP"
    err = f"<p class=err>{html_escape(error)}</p>" if error else ""
    return _share_page(s["name"], f"<h1>{html_escape(s['name'])}</h1><p>Geschützter Download{size}</p>{err}"
                                  "<form method=post><input type=password name=password placeholder='Passwort' autofocus required "
                                  "autocomplete=current-password aria-label=Passwort><button type=submit>Herunterladen</button></form>", status)


async def _send_share(token: str, request: Request):
    ip = websec.client_ip(request)
    agent = request.headers.get("user-agent", "")
    try:
        s = await asyncio.to_thread(cloud.take_download, token, ip, agent)
    except cloud.CloudError as e:
        return _invalid(e)
    await asyncio.to_thread(audit.record, "Freigabe-Link", ip, "GET", "/s/c/", 200, s["path"])
    if s["type"] == "dir":
        return StreamingResponse(files.zip_stream(s["files"]), media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(s['name'])}", "Cache-Control": "no-store"})
    return FileResponse(s["path"], filename=s["name"], headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/s/c/{token}")
async def public_cloud_share(token: str, request: Request):
    """Public link (no login): downloads at once - or, with a password, shows the form first."""
    try:
        s = await asyncio.to_thread(cloud.share_info, token)
    except cloud.CloudError as e:
        return _invalid(e)
    if s.get("pw_hash"):
        return _password_form(s)
    return await _send_share(token, request)


@router.post("/s/c/{token}")
async def public_cloud_share_password(token: str, request: Request):
    try:
        s = await asyncio.to_thread(cloud.share_info, token)
    except cloud.CloudError as e:
        return _invalid(e)
    ip = websec.client_ip(request)
    now = time.time()
    fails = [t for t in _SHARE_FAILS.get(ip, []) if now - t < FAIL_WINDOW]
    _SHARE_FAILS[ip] = fails
    if len(fails) >= FAIL_MAX:
        return _password_form(s, "Zu viele falsche Versuche – bitte in 15 Minuten erneut.", 429)
    body = (await request.body())[:4096].decode("utf-8", "replace")
    password = (parse_qs(body).get("password") or [""])[0]
    if not await asyncio.to_thread(cloud.check_password, s, password):
        fails.append(now)
        await asyncio.to_thread(cloud.record_failed, token, ip, request.headers.get("user-agent", ""))
        await asyncio.to_thread(audit.record, "Freigabe-Link", ip, "POST", "/s/c/", 403, f"Falsches Passwort · {s['name']}")
        return _password_form(s, "Falsches Passwort.", 403)
    return await _send_share(token, request)
