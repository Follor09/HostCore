"""
Virtual machines (KVM / libvirt) - page /vms, console /vm-konsole - routes moved out of app.py.
"""

import asyncio
import contextlib
import os
import subprocess
import time
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import agent
import audit
import auth
import tasks
import terminal
import vmops
import vms
import websec
from hostctx import AGENT, HOST_CHANGED_CLOSE, _ws_relay, host_changed as _host_changed, remote_for as _remote_for
from webutil import logger, serve_page as _serve_page

router = APIRouter()


@router.get("/vms")
async def get_vms_page(request: Request):
    # With a remote server selected its API decides (the page then shows its answer)
    if not vms.enabled() and not _remote_for(request):
        return RedirectResponse("/settings#vms" if vms.available() else "/", status_code=303)
    return _serve_page("vms.html")


@router.get("/vm-konsole")
async def get_vm_console_page():
    return _serve_page("vmconsole.html")


def _vm_call(fn, *args):
    try:
        vms.require_enabled()
        return fn(*args)
    except vms.VmDisabled as e:
        raise HTTPException(status_code=409, detail=str(e))
    except vms.VmError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except tasks.TaskError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Zeitüberschreitung.")


async def _vm(fn, *args):
    return await asyncio.to_thread(_vm_call, fn, *args)


class VmSettingsRequest(BaseModel):
    vcpus: Optional[int] = None
    memory_mb: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None


class VmNameRequest(BaseModel):
    name: str


class VmFlagRequest(BaseModel):
    enabled: bool = False


class VmDiskRequest(BaseModel):
    pool: str = "default"
    size_gb: int = 10
    bus: str = "virtio"


class VmMediaRequest(BaseModel):
    path: str = ""


class VmInterfaceRequest(BaseModel):
    kind: str = "network"
    source: str = "default"
    model: str = "virtio"


class VmSnapshotRequest(BaseModel):
    name: str
    description: str = ""


class VmNetworkRequest(BaseModel):
    name: str
    cidr: str
    mode: str = "nat"
    dhcp: bool = True


class VmPoolRequest(BaseModel):
    name: str
    path: str


class VmImageRequest(BaseModel):
    id: str = ""
    url: str = ""
    sha: str = ""
    kind: str = "iso"
    pool: str = "default"
    volume: str = ""


class VmFeatureRequest(BaseModel):
    enabled: bool


@router.get("/api/settings/vms")
async def api_settings_vms():
    return vms.feature()


@router.post("/api/settings/vms")
async def api_settings_vms_save(req: VmFeatureRequest, request: Request):
    request.state.audit_detail = "an" if req.enabled else "aus"
    try:
        return await asyncio.to_thread(vms.set_enabled, req.enabled)
    except vms.VmError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/vms")
async def api_vms():
    return await _vm(vms.overview)


@router.get("/api/vms/catalog")
async def api_vms_catalog():
    return await _vm(vms.catalog)


@router.get("/api/vms/networks")
async def api_vms_networks():
    return await _vm(vms.networks)


@router.get("/api/vms/pools")
async def api_vms_pools():
    return await _vm(vms.pools)


@router.post("/api/vms/setup/{step}")
async def api_vms_setup(step: str, request: Request):
    request.state.audit_detail = step
    return await _vm(vms.setup, step)


@router.post("/api/vms/create")
async def api_vms_create(payload: dict, request: Request):
    request.state.audit_detail = f"{str(payload.get('name', ''))[:63]} ({payload.get('source', '')}, {payload.get('vcpus')} vCPU, {payload.get('memory_mb')} MB)"
    return await _vm(vms.create, payload, request.state.user or "")


@router.post("/api/vms/images/fetch")
async def api_vms_fetch(req: VmImageRequest, request: Request):
    request.state.audit_detail = req.id or req.url[:200]
    return await _vm(vms.fetch_image, req.id, req.url, req.sha, req.kind, req.pool, req.volume, request.state.user or "")


@router.post("/api/vms/networks")
async def api_vms_network_create(req: VmNetworkRequest, request: Request):
    request.state.audit_detail = f"{req.name} {req.cidr} ({req.mode})"
    return await _vm(vms.network_create, req.name, req.cidr, req.mode, req.dhcp)


@router.post("/api/vms/net/{name}/{act}")
async def api_vms_network_action(name: str, act: str, request: Request):
    request.state.audit_detail = f"{name}: {act}"
    return await _vm(vms.network_action, name, act)


@router.post("/api/vms/pools")
async def api_vms_pool_create(req: VmPoolRequest, request: Request):
    request.state.audit_detail = f"{req.name} ({req.path})"
    return await _vm(vms.pool_create, req.name, req.path)


@router.post("/api/vms/pool/{name}/volumes/{vol}/delete")
async def api_vms_volume_delete(name: str, vol: str, request: Request):
    request.state.audit_detail = f"{name}/{vol}"
    return await _vm(vms.volume_delete, name, vol)


@router.post("/api/vms/pool/{name}/{act}")
async def api_vms_pool_action(name: str, act: str, request: Request):
    request.state.audit_detail = f"{name}: {act}"
    return await _vm(vms.pool_action, name, act)


# Templates, backups, schedules and live migration (vmops.py)
@router.get("/api/vms/ops")
async def api_vms_ops():
    def load():
        return {"templates": vmops.templates(), **vmops.backups(), **vmops.schedules()}
    return await _vm(load)


@router.post("/api/vms/templates")
async def api_vms_template_create(payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:80]
    return await _vm(vmops.template_save, None, payload, request.state.user or "")


@router.post("/api/vms/templates/{tid}")
async def api_vms_template_update(tid: str, payload: dict, request: Request):
    return await _vm(vmops.template_save, tid, payload, request.state.user or "")


@router.post("/api/vms/templates/{tid}/delete")
async def api_vms_template_delete(tid: str):
    return await _vm(vmops.template_delete, tid)


@router.post("/api/vms/backups/config")
async def api_vms_backup_config(payload: dict):
    return await _vm(vmops.backup_config_save, str(payload.get("pool", "")))


@router.post("/api/vms/backups/{bid}/restore")
async def api_vms_backup_restore(bid: str, payload: dict, request: Request):
    request.state.audit_detail = str(payload.get("name", ""))[:64]
    return await _vm(vmops.backup_restore, bid, str(payload.get("name", "")), str(payload.get("pool", "")), request.state.user or "")


@router.post("/api/vms/backups/{bid}/delete")
async def api_vms_backup_delete(bid: str):
    return await _vm(vmops.backup_delete, bid)


@router.post("/api/vms/schedules")
async def api_vms_schedule_create(payload: dict, request: Request):
    return await _vm(vmops.schedule_save, None, payload, request.state.user or "")


@router.post("/api/vms/schedules/{sid}")
async def api_vms_schedule_update(sid: str, payload: dict, request: Request):
    return await _vm(vmops.schedule_save, sid, payload, request.state.user or "")


@router.post("/api/vms/schedules/{sid}/delete")
async def api_vms_schedule_delete(sid: str):
    return await _vm(vmops.schedule_delete, sid)


@router.post("/api/vms/migration/key")
async def api_vms_migration_key():
    return await _vm(vmops.source_key)


@router.post("/api/vms/migration/target")
async def api_vms_migration_target(payload: dict):
    return await _vm(vmops.prepare_target, str(payload.get("key", "")), str(payload.get("source", "")))


@router.post("/api/vms/vm/{name}/backup")
async def api_vms_backup(name: str, request: Request):
    request.state.audit_detail = name
    return await _vm(vmops.backup_start, name, request.state.user or "")


@router.post("/api/vms/vm/{name}/migrate-check")
async def api_vms_migrate_check(name: str, payload: dict):
    return await _vm(vmops.check, name, payload.get("info") or {}, str(payload.get("address", "")), bool(payload.get("shared")))


@router.post("/api/vms/vm/{name}/migrate")
async def api_vms_migrate(name: str, payload: dict, request: Request):
    return await _vm(vmops.migrate, name, payload.get("info") or {}, str(payload.get("address", "")), bool(payload.get("shared")),
                     str(payload.get("target_name", ""))[:80], request.state.user or "")


async def _migration(request: Request, fn, *args):
    try:
        return await fn(_remote_for(request), *args)
    except vms.VmError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except tasks.TaskError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/api/vm-migration/targets")
async def api_vm_migration_targets(request: Request):
    return await _migration(request, vmops.targets)


@router.post("/api/vm-migration/check")
async def api_vm_migration_check(payload: dict, request: Request):
    return await _migration(request, vmops.run_check, vms.check_name(str(payload.get("vm", ""))), str(payload.get("target", "")), str(payload.get("address", "")),
                            bool(payload.get("shared")), request.state.user or "")


@router.post("/api/vm-migration/start")
async def api_vm_migration_start(payload: dict, request: Request):
    name = vms.check_name(str(payload.get("vm", "")))
    request.state.audit_detail = f"{name} → {payload.get('target', '') or 'Hauptserver'}"
    return await _migration(request, vmops.run_migrate, name, str(payload.get("target", "")), str(payload.get("address", "")),
                            bool(payload.get("shared")), request.state.user or "")


@router.get("/api/vms/vm/{name}")
async def api_vm_detail(name: str):
    return await _vm(vms.detail, name)


@router.post("/api/vms/vm/{name}/autostart")
async def api_vm_autostart(name: str, req: VmFlagRequest, request: Request):
    request.state.audit_detail = f"{name}: Autostart {'an' if req.enabled else 'aus'}"
    return await _vm(vms.set_autostart, name, req.enabled)


@router.post("/api/vms/vm/{name}/delete")
async def api_vm_delete(name: str, req: VmFlagRequest, request: Request):
    request.state.audit_detail = f"{name}{' samt Laufwerken' if req.enabled else ''}"
    return await _vm(vms.delete, name, req.enabled)


@router.post("/api/vms/vm/{name}/settings")
async def api_vm_settings(name: str, req: VmSettingsRequest, request: Request):
    request.state.audit_detail = f"{name}: vCPU {req.vcpus or '–'}, RAM {req.memory_mb or '–'} MB"
    return await _vm(vms.settings, name, req.vcpus, req.memory_mb, req.title, req.description)


@router.post("/api/vms/vm/{name}/rename")
async def api_vm_rename(name: str, req: VmNameRequest, request: Request):
    request.state.audit_detail = f"{name} → {req.name}"
    return await _vm(vms.rename, name, req.name)


@router.post("/api/vms/vm/{name}/clone")
async def api_vm_clone(name: str, req: VmNameRequest, request: Request):
    request.state.audit_detail = f"{name} → {req.name}"
    return await _vm(vms.clone, name, req.name, request.state.user or "")


@router.post("/api/vms/vm/{name}/disks")
async def api_vm_disk_add(name: str, req: VmDiskRequest, request: Request):
    request.state.audit_detail = f"{name}: {req.size_gb} GB ({req.pool})"
    return await _vm(vms.add_disk, name, req.pool, req.size_gb, req.bus)


@router.post("/api/vms/vm/{name}/disks/{target}/detach")
async def api_vm_disk_detach(name: str, target: str, req: VmFlagRequest, request: Request):
    request.state.audit_detail = f"{name}: {target}{' gelöscht' if req.enabled else ''}"
    return await _vm(vms.detach_disk, name, target, req.enabled)


@router.post("/api/vms/vm/{name}/media")
async def api_vm_media(name: str, req: VmMediaRequest, request: Request):
    request.state.audit_detail = f"{name}: {os.path.basename(req.path) or 'auswerfen'}"
    return await _vm(vms.change_media, name, req.path)


@router.post("/api/vms/vm/{name}/interfaces")
async def api_vm_iface_add(name: str, req: VmInterfaceRequest, request: Request):
    request.state.audit_detail = f"{name}: {req.kind} {req.source}"
    return await _vm(vms.add_interface, name, req.kind, req.source, req.model)


@router.post("/api/vms/vm/{name}/interfaces/{mac}/{act}")
async def api_vm_iface_action(name: str, mac: str, act: str, request: Request):
    request.state.audit_detail = f"{name}: {mac} {act}"
    return await _vm(vms.interface_action, name, mac, act)


@router.post("/api/vms/vm/{name}/snapshots")
async def api_vm_snapshot_create(name: str, req: VmSnapshotRequest, request: Request):
    request.state.audit_detail = f"{name}: {req.name}"
    return await _vm(vms.snapshot_create, name, req.name, req.description)


@router.post("/api/vms/vm/{name}/snapshots/{snap}/{act}")
async def api_vm_snapshot_action(name: str, snap: str, act: str, request: Request):
    request.state.audit_detail = f"{name}: {snap} {act}"
    return await _vm(vms.snapshot_action, name, snap, act)


@router.post("/api/vms/vm/{name}/console")
async def api_vm_console(name: str, request: Request):
    """One-time ticket (60 s) for the console WebSocket - issued where the VM runs (an agent for a remote host)."""
    request.state.audit_detail = name
    info = await _vm(vms.console_info, name)
    return {**info, "ticket": vms.issue_ticket(request.state.user or "", name), "expires_in": vms.TICKET_TTL}


@router.post("/api/vms/vm/{name}/{act}")
async def api_vm_action(name: str, act: str, request: Request):
    request.state.audit_detail = f"{name}: {act}"
    logger.info(f"VM {name} {act} by '{request.state.user}'")
    return await _vm(vms.action, name, act)


@router.websocket("/ws/vm-console")
async def ws_vm_console(websocket: WebSocket, ticket: str = "", kind: str = "serial", rows: int = 24, cols: int = 80):
    """kind=serial: text console (virsh console, same protocol as the web terminal);
    kind=vnc: raw VNC bytes for noVNC, to the VM's VNC port on 127.0.0.1."""
    if kind not in ("serial", "vnc"):
        await websocket.close(code=4400)
        return
    if AGENT:
        await websocket.accept()
        if not agent.check(websocket.headers.get("authorization", "")) or not agent.allowed(websec.client_ip(websocket)):
            await websocket.close(code=4401)
            return
        user = agent.remote_user(websocket.headers)
    else:
        user = auth.user_from_token(websocket.cookies.get(auth.COOKIE_NAME))
        origin = websocket.headers.get("origin", "")
        same_origin = origin.split("://", 1)[-1] == websocket.headers.get("host", "")
        await websocket.accept()
        if not user or not auth.can(user, "vms.manage") or not same_origin:
            await websocket.close(code=4401)
            return
        if websec.blocked_from_outside("vms.manage", websec.client_ip(websocket)):
            await websocket.close(code=4403)
            return
        if _host_changed(websocket, websocket.query_params.get("host")):
            await websocket.close(code=HOST_CHANGED_CLOSE)
            return
        remote = _remote_for(websocket)
        if remote:
            await _ws_relay(websocket, remote, f"/ws/vm-console?ticket={quote(ticket)}&kind={kind}&rows={int(rows)}&cols={int(cols)}", user)
            return
    name = vms.redeem_ticket(ticket, user or "")
    if not name or not vms.enabled():
        await websocket.close(code=4401)
        return
    client = websec.client_ip(websocket)
    label = "serielle Konsole" if kind == "serial" else "grafische Konsole"
    await asyncio.to_thread(audit.record, user, client, "WS", "/ws/vm-console", 200, f"VM {name}: {label} geöffnet")
    started = time.time()
    try:
        if kind == "serial":
            await terminal._bridge(websocket, user, client, rows, cols, argv=vms.serial_argv(name))
        else:
            await _vnc_bridge(websocket, name)
    except vms.VmError as e:
        with contextlib.suppress(Exception):
            await websocket.close(code=4404, reason=str(e)[:120])
    finally:
        minutes = max(1, round((time.time() - started) / 60))
        await asyncio.to_thread(audit.record, user, client, "WS", "/ws/vm-console", 200, f"VM {name}: {label} getrennt nach {minutes} Min.")


async def _vnc_bridge(websocket: WebSocket, name: str) -> None:
    port = await asyncio.to_thread(vms.vnc_port, name)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    async def down():
        while True:
            data = await reader.read(65536)
            if not data:
                return
            await websocket.send_bytes(data)

    async def up():
        while True:
            m = await websocket.receive()
            if m["type"] == "websocket.disconnect":
                return
            data = m.get("bytes")
            if data is None and m.get("text") is not None:
                data = m["text"].encode()
            if data:
                writer.write(data)
                await writer.drain()

    pumps = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in pumps:
            t.cancel()
        writer.close()
        with contextlib.suppress(Exception):
            await websocket.close()
