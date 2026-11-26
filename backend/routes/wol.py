"""
Wake-on-LAN routes: one device list for all servers (kept on the main dashboard whichever
server is selected), each device woken from the server in its network - this dashboard, or a
remote server whose agent sends the magic packet (POST /api/agent/wol there).
Permissions: backend/access.py, activity log labels: backend/audit.py, never forwarded:
remotes.LOCAL_ONLY.
"""

import asyncio
import logging
import platform
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import hostctx
import netscan
import remotes
import settings_store
import wol

logger = logging.getLogger("pi_dashboard")
router = APIRouter()


class WolDeviceRequest(BaseModel):
    name: str
    mac: str
    broadcast: str = ""
    via: str = ""          # "" = this dashboard sends the packet, else a remote id


class WolUpdateRequest(BaseModel):
    via: Optional[str] = None
    broadcast: Optional[str] = None


class WolWakeRequest(BaseModel):
    via: Optional[str] = None    # send from this server this once instead of the device's own


class AgentWolRequest(BaseModel):
    mac: str
    broadcast: str = ""


def _wol_via_ok(via: str) -> str:
    via = (via or "").strip()
    if via and not remotes.get(via):
        raise HTTPException(status_code=400, detail="Diesen Server gibt es nicht (mehr).")
    return via


def _wol_hosts() -> list:
    return [{"id": "", "name": f"{platform.node()} (Dashboard)"}] + [{"id": r["id"], "name": r["name"]} for r in remotes.all_remotes()]


def _wol_public(d: dict) -> dict:
    via = d.get("via", "")
    r = remotes.get(via) if via else None
    return {**d, "via": via, "via_name": r["name"] if r else ("" if not via else "Server entfernt"), "via_missing": bool(via and not r)}


async def _wol_import_remote_lists() -> None:
    """Before 1.9.1 every server kept its own list: take each remote's over once (sent from there)."""
    done = set(settings_store.get("wol_imported_remotes", []) or [])
    todo = [r for r in remotes.all_remotes() if r["id"] not in done and remotes.status_of(r["id"]).get("online") is not False]
    if not todo:
        return

    async def one(r):
        try:
            res = await hostctx.proxy(r).get(f"{r['url']}/api/wol", headers={"Authorization": f"Bearer {r['token']}"}, timeout=4.0)
        except httpx.HTTPError:
            return None
        if res.status_code != 200:
            return None
        try:
            items = res.json().get("devices") or []
        except ValueError:
            return None
        n = await asyncio.to_thread(wol.import_devices, [i for i in items if isinstance(i, dict) and not i.get("via")], r["id"])
        if n:
            logger.info(f"Wake-on-LAN: {n} device(s) taken over from {r['name']}")
        return r["id"]

    ok = [x for x in await asyncio.gather(*(one(r) for r in todo)) if x]
    if ok:
        settings_store.set("wol_imported_remotes", sorted(done | set(ok)))


@router.get("/api/wol")
async def api_wol_devices():
    if not hostctx.AGENT:
        await _wol_import_remote_lists()
    return {"devices": [_wol_public(d) for d in wol.list_devices()], "hosts": [] if hostctx.AGENT else _wol_hosts()}


@router.post("/api/wol/{device_id}/wake")
async def api_wol_wake(device_id: str, request: Request, req: Optional[WolWakeRequest] = None):
    try:
        device = wol.get_device(device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    via = device.get("via", "") if req is None or req.via is None else req.via
    via = "" if hostctx.AGENT else via
    remote = remotes.get(via) if via else None
    if via and not remote:
        raise HTTPException(status_code=400, detail=f"Der Server, über den {device['name']} geweckt wird, existiert nicht mehr – "
                                                    "in den Einstellungen unter Wake-on-LAN einen anderen wählen.")
    request.state.audit_detail = f"{device['name']} ({device['mac']})" + (f" über {remote['name']}" if remote else "")
    if remote:
        try:
            res = await hostctx.proxy(remote).post(f"{remote['url']}/api/agent/wol", json={"mac": device["mac"], "broadcast": device["broadcast"]},
                                      headers={"Authorization": f"Bearer {remote['token']}", "X-PiCore-User": request.state.user or ""},
                                      timeout=10.0)
        except httpx.HTTPError as e:
            remotes.mark_offline(remote["id"], f"Nicht erreichbar ({type(e).__name__})")
            raise HTTPException(status_code=502, detail=f"{remote['name']} nicht erreichbar ({type(e).__name__}) – Weckruf nicht gesendet.")
        if res.status_code == 404:
            raise HTTPException(status_code=502, detail=f"Der Agent auf {remote['name']} ist zu alt für Wake-on-LAN über ihn – bitte dort aktualisieren.")
        if res.status_code != 200:
            detail = res.json().get("detail", "") if res.headers.get("content-type", "").startswith("application/json") else ""
            raise HTTPException(status_code=502, detail=f"{remote['name']}: {detail or f'Antwort {res.status_code}'}")
    else:
        try:
            await asyncio.to_thread(wol.send, device["mac"], device["broadcast"])
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Wake-on-LAN sent to {device['name']} ({device['mac']})" + (f" via {remote['name']}" if remote else ""))
    return {"ok": True, "message": f"Weckruf an {device['name']} gesendet" + (f" (über {remote['name']})." if remote else ".")}


@router.post("/api/agent/wol")
async def api_agent_wol(req: AgentWolRequest, request: Request):
    """Agent: the main dashboard asks this server to send a magic packet into its own network."""
    if not hostctx.AGENT:
        raise HTTPException(status_code=404, detail="Kein Agent.")
    try:
        await asyncio.to_thread(wol.send, req.mac, req.broadcast)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{req.mac[:20]} (vom Haupt-Dashboard)"
    logger.info(f"Wake-on-LAN sent to {req.mac} for the main dashboard")
    return {"ok": True}


@router.post("/api/settings/wol")
async def api_settings_wol_add(req: WolDeviceRequest, request: Request):
    via = _wol_via_ok(req.via)
    try:
        device = wol.add_device(req.name, req.mac, req.broadcast, via)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "device": _wol_public(device)}


@router.post("/api/settings/wol/{device_id}/update")
async def api_settings_wol_update(device_id: str, req: WolUpdateRequest, request: Request):
    via = _wol_via_ok(req.via) if req.via is not None else None
    try:
        device = wol.update_device(device_id, via=via, broadcast=req.broadcast)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = _wol_public(device)
    request.state.audit_detail = f"{device['name']}: über {out['via_name'] or platform.node()}"
    return {"ok": True, "device": out}


@router.post("/api/settings/wol/{device_id}/delete")
async def api_settings_wol_delete(device_id: str):
    wol.remove_device(device_id)
    return {"ok": True}


# --- LAN scan: "Zu Wake-on-LAN" -------------------------------------------------
class LanWolRequest(BaseModel):
    name: str


def _lan_mark_wol(data: dict) -> dict:
    """A remote's LAN scan: is the device in this dashboard's (global) Wake-on-LAN list?"""
    macs = {d["mac"].lower() for d in wol.list_devices()}
    for d in data.get("devices") or []:
        if isinstance(d, dict):
            d["in_wol"] = str(d.get("mac", "")).lower() in macs
    return data


@router.post("/api/tools/lan/{mac}/wol")
async def api_tools_lan_wol(mac: str, req: LanWolRequest, request: Request):
    # Not forwarded (one list for all servers): a device found by a remote's scan is woken from there
    now = hostctx.host_changed(request, request.headers.get(hostctx.HOST_HEADER))
    if now:
        return hostctx.host_changed_response(now)
    remote = hostctx.remote_for(request)
    request.state.audit_detail = f"{req.name[:40]} ({mac[:20]})" + (f" @{remote['name']}" if remote else "")
    try:
        await asyncio.to_thread(wol.add_device, req.name, mac, "", remote["id"] if remote else "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not remote:
        return await asyncio.to_thread(netscan.devices)
    try:
        res = await hostctx.proxy(remote).get(f"{remote['url']}/api/tools/lan", headers={"Authorization": f"Bearer {remote['token']}",
                                                                            "X-PiCore-User": request.state.user or ""})
        res.raise_for_status()
        return _lan_mark_wol(res.json())
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"Hinzugefügt, aber {remote['name']} nicht erreichbar ({type(e).__name__}).")
