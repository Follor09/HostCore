"""
Documentation: inventory, journal, runbooks, service docs - routes moved out of app.py.
"""

import asyncio
import contextlib
import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import attachments
import costs
import docs
import domains
from webutil import serve_page as _serve_page

router = APIRouter()


def _docs_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except docs.DocsError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/doku")
async def get_docs_page():
    return _serve_page("docs.html")


def _domain_call(fn, *args):
    try:
        return fn(*args)
    except domains.DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/docs/domains")
async def api_docs_domains():
    return await asyncio.to_thread(domains.overview)


@router.post("/api/docs/domains")
async def api_docs_domains_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_domain_call, domains.save, None, payload, request.state.user or "")


@router.post("/api/docs/domains/check")
async def api_docs_domains_check():
    return await asyncio.to_thread(domains.start_check)


@router.post("/api/docs/domains/{domain_id}")
async def api_docs_domains_update(domain_id: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:120]
    return await asyncio.to_thread(_domain_call, domains.save, domain_id, payload, request.state.user or "")


@router.post("/api/docs/domains/{domain_id}/delete")
async def api_docs_domains_delete(domain_id: str):
    return await asyncio.to_thread(domains.delete, domain_id)


@router.get("/api/docs/inventory")
async def api_docs_inventory():
    return await asyncio.to_thread(docs.inventory)


@router.post("/api/docs/inventory")
async def api_docs_inventory_create(payload: dict):
    return await asyncio.to_thread(_docs_call, docs.inventory_save, None, payload)


@router.post("/api/docs/inventory/{item_id}")
async def api_docs_inventory_update(item_id: str, payload: dict):
    return await asyncio.to_thread(_docs_call, docs.inventory_save, item_id, payload)


@router.post("/api/docs/inventory/{item_id}/delete")
async def api_docs_inventory_delete(item_id: str):
    result = await asyncio.to_thread(_docs_call, docs.inventory_delete, item_id)
    await asyncio.to_thread(attachments.delete_all, "inventory", item_id)
    return result


@router.get("/api/docs/attachments/{kind}")
async def api_attachment_counts(kind: str):
    return {"counts": await asyncio.to_thread(attachments.counts, kind)}


@router.get("/api/docs/attachments/{kind}/{item_id}")
async def api_attachments(kind: str, item_id: str):
    try:
        return {"files": await asyncio.to_thread(attachments.list_files, kind, item_id)}
    except attachments.AttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/docs/attachments/{kind}/{item_id}")
async def api_attachment_upload(kind: str, item_id: str, name: str, request: Request):
    length = request.headers.get("content-length")
    try:
        target = await asyncio.to_thread(attachments.target, kind, item_id, name, int(length) if length and length.isdigit() else None)
    except attachments.AttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{kind}/{item_id}: {os.path.basename(target)}"[:250]
    tmp = f"{target}.upload-{secrets.token_hex(4)}"
    written = 0
    try:
        with open(tmp, "xb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > attachments.MAX_FILE:
                    raise HTTPException(status_code=413, detail="Datei zu groß (höchstens 25 MB).")
                await asyncio.to_thread(f.write, chunk)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    return {"files": await asyncio.to_thread(attachments.list_files, kind, item_id)}


@router.get("/api/docs/attachments/{kind}/{item_id}/file")
async def api_attachment_file(kind: str, item_id: str, name: str, download: bool = False):
    try:
        f = await asyncio.to_thread(attachments.file_path, kind, item_id, name)
    except attachments.AttachmentError as e:
        raise HTTPException(status_code=404, detail=str(e))
    inline = f["inline"] and not download
    return FileResponse(f["path"], media_type=f["media"], filename=name, content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox",
                                 "Cache-Control": "private, max-age=300"})


@router.post("/api/docs/attachments/{kind}/{item_id}/delete")
async def api_attachment_delete(kind: str, item_id: str, name: str, request: Request):
    request.state.audit_detail = f"{kind}/{item_id}: {name}"[:250]
    try:
        return {"files": await asyncio.to_thread(attachments.delete, kind, item_id, name)}
    except attachments.AttachmentError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/costs")
async def api_costs():
    return await asyncio.to_thread(costs.overview)
