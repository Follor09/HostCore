"""
System tools page - routes moved out of app.py.
"""

import asyncio
import platform
import re
import secrets
import subprocess
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import actions
import audit
import auth
import fancurve
import files
import geoip
import healthchecks
import netscan
import nettools
import security
import settings_store
import systools
import websec
from authweb import _auth_call, _session_is_remembered, _set_session_cookie
from webutil import heavy, logger

router = APIRouter()


@router.get("/api/tools/network")
async def api_tools_network():
    return await asyncio.to_thread(systools.network_details)


@router.get("/api/tools/hardware")
async def api_tools_hardware():
    return await asyncio.to_thread(systools.hardware_info)


class GovernorRequest(BaseModel):
    governor: str


class BenchmarkRequest(BaseModel):
    kind: str          # "disk" or "cpu"
    mount: str = "/"


@router.get("/api/tools/benchmark")
async def api_tools_benchmarks():
    return await asyncio.to_thread(systools.benchmark_history)


@router.post("/api/tools/benchmark")
async def api_tools_run_benchmark(req: BenchmarkRequest, request: Request):
    try:
        entry = await asyncio.to_thread(systools.run_benchmark, req.kind, req.mount)
    except (ValueError, PermissionError, files.FileAccessError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Benchmark {req.kind} {req.mount} by '{request.state.user}': {entry}")
    request.state.audit_detail = f"{req.kind} {req.mount}"
    return entry


@router.get("/api/tools/log-units")
async def api_tools_log_units():
    return await asyncio.to_thread(systools.journal_units)


@router.get("/api/tools/logs")
async def api_tools_logs(unit: str = "", priority: str = "all", since: str = "1h", q: str = ""):
    try:
        return await asyncio.to_thread(systools.journal_search, unit, priority, since, q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CleanupRequest(BaseModel):
    item: str


@router.get("/api/tools/cleanup")
async def api_tools_cleanup_status():
    return await asyncio.to_thread(systools.cleanup_status)


@router.post("/api/tools/cleanup")
async def api_tools_cleanup(req: CleanupRequest, request: Request):
    try:
        result = await asyncio.to_thread(systools.cleanup_run, req.item)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Cleanup {req.item} by '{request.state.user}': freed {result['freed']} bytes")
    request.state.audit_detail = f"{req.item} · {round(result['freed'] / 1e6)} MB"
    return result


@router.get("/api/tools/cpu-mode")
async def api_tools_cpu_mode():
    return await asyncio.to_thread(systools.cpu_mode)


@router.post("/api/tools/cpu-mode")
async def api_tools_set_cpu_mode(req: GovernorRequest, request: Request):
    request.state.audit_detail = req.governor[:20]
    result = await asyncio.to_thread(systools.set_governor, req.governor)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    logger.info(f"CPU governor -> {req.governor} by '{request.state.user}'")
    return {**result, **await asyncio.to_thread(systools.cpu_mode)}


class UnbanRequest(BaseModel):
    ip: str


class FirewallRequest(BaseModel):
    action: str                  # enable, disable, rule, delete, preset, logging
    params: dict = {}


@router.get("/api/tools/ssh")
async def api_tools_ssh():
    return await asyncio.to_thread(security.ssh_logins)


@router.post("/api/tools/fail2ban/unban")
async def api_tools_unban(req: UnbanRequest, request: Request):
    try:
        await asyncio.to_thread(security.fail2ban_unban, req.ip)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"fail2ban unban {req.ip} by '{request.state.user}'")
    request.state.audit_detail = req.ip
    return await asyncio.to_thread(security.fail2ban_status)


@router.get("/api/tools/firewall")
async def api_tools_firewall():
    return await asyncio.to_thread(security.firewall_status)


class GeoblockRequest(BaseModel):
    countries: list = []


@router.get("/api/tools/geoip")
async def api_geoip():
    return geoip.info()


@router.post("/api/tools/geoip/download")
async def api_geoip_download():
    return await asyncio.to_thread(geoip.start_download)


@router.post("/api/tools/geoip/remove")
async def api_geoip_remove():
    try:
        return await asyncio.to_thread(geoip.remove)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/api/tools/geoblock")
async def api_geoblock():
    return await asyncio.to_thread(security.geoblock_status)


@router.post("/api/tools/geoblock")
async def api_geoblock_apply(req: GeoblockRequest, request: Request):
    client_ip = websec.client_ip(request)
    request.state.audit_detail = ", ".join(str(c) for c in req.countries[:40]) or "–"
    try:
        result = await asyncio.to_thread(security.geoblock_apply, req.countries, client_ip)
    except (ValueError, PermissionError, RuntimeError, subprocess.TimeoutExpired) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Geoblock {req.countries} by '{request.state.user}'")
    return result


@router.get("/api/tools/firewall/traffic")
async def api_tools_firewall_traffic(range: str = "24h", action: str = "block", direction: str = "all", q: str = ""):
    return await asyncio.to_thread(security.firewall_traffic, range, action, direction, q[:64])


@router.post("/api/tools/firewall")
async def api_tools_firewall_action(req: FirewallRequest, request: Request):
    try:
        client_ip = websec.client_ip(request)
        result = await asyncio.to_thread(security.firewall_action, req.action, req.params, client_ip)
    except (ValueError, TypeError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Firewall {req.action} {req.params} by '{request.state.user}'")
    request.state.audit_detail = f"{req.action} {' '.join(f'{k}={v}' for k, v in req.params.items())}".strip()
    return result


class LanRenameRequest(BaseModel):
    alias: str = ""


class PortTestRequest(BaseModel):
    host: str
    ports: str


@router.get("/api/tools/lan")
async def api_tools_lan():
    return await asyncio.to_thread(netscan.devices)


@router.post("/api/tools/lan/scan")
async def api_tools_lan_scan(request: Request):
    try:
        result = await asyncio.to_thread(netscan.scan)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"LAN scan by '{request.state.user}': {result['last_scan']}")
    request.state.audit_detail = f"{result['last_scan']['online']} Geräte online"
    return result


@router.post("/api/tools/lan/{mac}/rename")
async def api_tools_lan_rename(mac: str, req: LanRenameRequest, request: Request):
    request.state.audit_detail = f"{mac} → {req.alias[:40]}"
    try:
        return await asyncio.to_thread(netscan.rename, mac, req.alias)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/tools/lan/{mac}/forget")
async def api_tools_lan_forget(mac: str, request: Request):
    request.state.audit_detail = mac[:20]
    try:
        return await asyncio.to_thread(netscan.forget, mac)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class NotesRequest(BaseModel):
    text: str
    base: int = 0         # "updated" of the version the edit started from


@router.get("/api/notes")
async def api_notes():
    return settings_store.get("notes", {"text": "", "updated": 0, "user": ""})


@router.post("/api/notes")
async def api_notes_save(req: NotesRequest, request: Request):
    if len(req.text) > 20000:
        raise HTTPException(status_code=400, detail="Notizen dürfen höchstens 20 000 Zeichen lang sein.")
    current = settings_store.get("notes", {"text": "", "updated": 0, "user": ""})
    if req.base != current.get("updated", 0) and req.text != current.get("text"):
        # Someone else saved in the meantime: don't overwrite their version
        return JSONResponse({"detail": "Die Notizen wurden inzwischen woanders geändert.", "notes": current}, status_code=409)
    notes = {"text": req.text, "updated": int(time.time() * 1000), "user": request.state.user}
    settings_store.set("notes", notes)
    return notes


class DnsRequest(BaseModel):
    name: str
    type: str = "A"
    server: str = "system"


class TracerouteRequest(BaseModel):
    host: str


@router.post("/api/tools/dns")
async def api_tools_dns(req: DnsRequest, request: Request):
    request.state.audit_detail = f"{req.type} {req.name[:80]} @ {req.server[:40]}"
    try:
        return await asyncio.to_thread(nettools.dns_lookup, req.name, req.type, req.server)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/tools/traceroute")
async def api_tools_traceroute(req: TracerouteRequest, request: Request):
    request.state.audit_detail = req.host[:80]
    try:
        return await asyncio.to_thread(nettools.traceroute, req.host)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/health")
async def api_health():
    return await asyncio.to_thread(healthchecks.status)


@router.post("/api/settings/health")
async def api_settings_health_add(payload: dict, request: Request):
    """payload: name, type (http, keyword, json, tcp, ping, dns), interval and the type's fields."""
    request.state.audit_detail = f"{str(payload.get('name', ''))[:40]} · {payload.get('type', 'http')}"
    try:
        item = await asyncio.to_thread(healthchecks.add, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await healthchecks.run_check(item)
    return await asyncio.to_thread(healthchecks.status)


@router.post("/api/settings/health/{check_id}/update")
async def api_settings_health_update(check_id: str, payload: dict, request: Request):
    request.state.audit_detail = f"{str(payload.get('name', ''))[:40]} · {payload.get('type', 'http')}"
    try:
        item = await asyncio.to_thread(healthchecks.update, check_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await healthchecks.run_check(item)
    return await asyncio.to_thread(healthchecks.status)


@router.get("/api/health/{check_id}/history")
async def api_health_history(check_id: str, hours: int = 24):
    try:
        return await asyncio.to_thread(healthchecks.history_detail, check_id, hours)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/tools/lan/{mac}/monitor")
async def api_tools_lan_monitor(mac: str, request: Request):
    """Watch a device from the LAN scan with a ping check (follows its IP by MAC)."""
    dev = (settings_store.get("lan_devices", {}) or {}).get(mac.lower())
    if not dev or not dev.get("ip"):
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden.")
    if any(c.get("mac") == mac.lower() for c in healthchecks.checks()):
        raise HTTPException(status_code=400, detail="Dieses Gerät wird schon überwacht.")
    name = (dev.get("alias") or dev.get("hostname") or dev["ip"])[:40]
    request.state.audit_detail = f"{name} · {mac}"
    try:
        item = await asyncio.to_thread(healthchecks.add, {"name": name, "type": "ping", "host": dev["ip"], "mac": mac.lower(), "interval": 5})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await healthchecks.run_check(item)
    return {"message": f"{name} wird jetzt alle 5 Minuten angepingt.", "check": item}


@router.post("/api/settings/health/{check_id}/delete")
async def api_settings_health_delete(check_id: str):
    await asyncio.to_thread(healthchecks.remove_check, check_id)
    return await asyncio.to_thread(healthchecks.status)


@router.post("/api/settings/health/{check_id}/run")
async def api_settings_health_run(check_id: str):
    check = next((c for c in healthchecks.checks() if c["id"] == check_id), None)
    if not check:
        raise HTTPException(status_code=404, detail="Prüfung nicht gefunden.")
    await healthchecks.run_check(check)
    return await asyncio.to_thread(healthchecks.status)


class LinkRequest(BaseModel):
    name: str
    url: str


def _links() -> list:
    return settings_store.get("custom_links", []) or []


@router.get("/api/links")
async def api_links():
    return {"links": _links()}


@router.post("/api/settings/links")
async def api_settings_links_add(req: LinkRequest, request: Request):
    name, url = req.name.strip()[:30], req.url.strip()[:500]
    request.state.audit_detail = f"{name} · {url[:120]}"
    if not name:
        raise HTTPException(status_code=400, detail="Bitte einen Namen angeben.")
    if not re.match(r"^https?://[^\s<>\"']+$", url, re.I):
        if re.match(r"^[\w.-]+(:\d+)?(/[^\s<>\"']*)?$", url):
            url = "http://" + url
        else:
            raise HTTPException(status_code=400, detail="Adresse muss mit http:// oder https:// beginnen.")
    links = _links()
    if len(links) >= 20:
        raise HTTPException(status_code=400, detail="Höchstens 20 Links.")
    links.append({"id": secrets.token_hex(5), "name": name, "url": url})
    settings_store.set("custom_links", links)
    return {"links": links}


@router.post("/api/settings/links/{link_id}/delete")
async def api_settings_links_delete(link_id: str):
    links = [l for l in _links() if l["id"] != link_id]
    settings_store.set("custom_links", links)
    return {"links": links}


@router.post("/api/settings/links/{link_id}/move")
async def api_settings_links_move(link_id: str, direction: int = -1):
    links = _links()
    i = next((n for n, l in enumerate(links) if l["id"] == link_id), None)
    j = None if i is None else i + (1 if direction > 0 else -1)
    if i is not None and 0 <= j < len(links):
        links[i], links[j] = links[j], links[i]
        settings_store.set("custom_links", links)
    return {"links": links}


class UserCreateRequest(BaseModel):
    username: str
    password: str
    group: str
    expires: Optional[int] = None    # guest account until (epoch seconds)


class UserUpdateRequest(BaseModel):
    group: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None
    logout: bool = False
    expires: Optional[int] = None    # 0 = no expiry
    email: Optional[str] = None


class GroupRequest(BaseModel):
    name: str
    description: str = ""
    permissions: list = []


@router.get("/api/permissions")
async def api_permissions():
    return {"permissions": [{"id": p, "label": l, "description": d, "category": c} for p, l, d, c in auth.PERMISSIONS]}


@router.get("/api/users")
async def api_users(request: Request):
    return {"users": auth.list_users(), "groups": auth.list_groups(), "me": request.state.user,
            "permissions": [{"id": p, "label": l, "description": d, "category": c} for p, l, d, c in auth.PERMISSIONS]}


@router.post("/api/users")
async def api_users_create(req: UserCreateRequest, request: Request):
    request.state.audit_detail = f"{req.username[:32]} · {req.group[:32]}" + (f" · Gast bis {time.strftime('%d.%m.%Y', time.localtime(req.expires))}" if req.expires else "")
    user = await asyncio.to_thread(_auth_call, auth.create_user, req.username, req.password, req.group, req.expires)
    return {"user": user, "users": auth.list_users(), "groups": auth.list_groups()}


@router.post("/api/users/{username}/update")
async def api_users_update(username: str, req: UserUpdateRequest, request: Request):
    changes = [k for k in ("group", "disabled", "password", "expires", "email") if getattr(req, k) is not None] + (["logout"] if req.logout else [])
    request.state.audit_detail = f"{username[:32]}: {', '.join(changes)}" + (f" → {req.group}" if req.group else "")
    if username.casefold() == (request.state.user or "").casefold() and (req.disabled or (req.group and req.group != "admin")):
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst deaktivieren oder deine Admin-Rechte entfernen.")
    await asyncio.to_thread(_auth_call, auth.update_user, username, group=req.group, disabled=req.disabled,
                            password=req.password, logout=req.logout, expires=req.expires)
    if req.email is not None:
        await asyncio.to_thread(_auth_call, auth.set_email, username, req.email)
    response = JSONResponse({"users": auth.list_users(), "groups": auth.list_groups()})
    if username.casefold() == (request.state.user or "").casefold() and (req.password or req.logout):
        _set_session_cookie(response, request, request.state.user, _session_is_remembered(request))
    return response


@router.post("/api/users/{username}/2fa-reset")
async def api_users_2fa_reset(username: str, request: Request):
    """Lost phone: an admin switches the user's two-factor login off (set up again at the next login)."""
    name = auth.canonical_username(username)
    if not name:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden.")
    await asyncio.to_thread(auth.reset_factors, name)
    request.state.audit_detail = f"{name}: Zwei-Faktor und Passkeys zurückgesetzt"
    websec.record_event("warning", f"Zwei-Faktor-Anmeldung zurückgesetzt: {name}", f"von {request.state.user}")
    return {"users": auth.list_users(), "groups": auth.list_groups()}


@router.post("/api/users/{username}/delete")
async def api_users_delete(username: str, request: Request):
    request.state.audit_detail = username[:32]
    if username.casefold() == (request.state.user or "").casefold():
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen.")
    await asyncio.to_thread(_auth_call, auth.delete_user, username)
    return {"users": auth.list_users(), "groups": auth.list_groups()}


@router.post("/api/groups")
async def api_groups_create(req: GroupRequest, request: Request):
    request.state.audit_detail = f"{req.name[:40]}: {', '.join(req.permissions)[:200]}"
    await asyncio.to_thread(_auth_call, auth.save_group, None, req.name, req.description, req.permissions)
    return {"groups": auth.list_groups()}


@router.post("/api/groups/{group_id}/update")
async def api_groups_update(group_id: str, req: GroupRequest, request: Request):
    request.state.audit_detail = f"{group_id}: {', '.join(req.permissions)[:200]}"
    await asyncio.to_thread(_auth_call, auth.save_group, group_id, req.name, req.description, req.permissions)
    return {"groups": auth.list_groups()}


@router.post("/api/groups/{group_id}/reset")
async def api_groups_reset(group_id: str, request: Request):
    request.state.audit_detail = group_id
    await asyncio.to_thread(_auth_call, auth.reset_group, group_id)
    return {"groups": auth.list_groups()}


@router.post("/api/groups/{group_id}/delete")
async def api_groups_delete(group_id: str, request: Request):
    request.state.audit_detail = group_id
    await asyncio.to_thread(_auth_call, auth.delete_group, group_id)
    return {"groups": auth.list_groups()}


@router.get("/api/actions")
async def api_actions():
    return await asyncio.to_thread(actions.list_actions)


@router.post("/api/actions/{action_id}/run")
async def api_actions_run(action_id: str, request: Request):
    request.state.audit_detail = action_id[:100]
    try:
        return await asyncio.to_thread(actions.run_action, action_id, request.state.user)
    except (ValueError, RuntimeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/actions/runs/{run_id}")
async def api_actions_get_run(run_id: str, offset: int = 0):
    try:
        return actions.get_run(run_id, max(0, offset))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/actions/runs/{run_id}/stop")
async def api_actions_stop(run_id: str, request: Request):
    try:
        return actions.stop_run(run_id, request.state.user or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/tools/activity")
async def api_tools_activity(q: str = "", category: str = "", limit: int = 200, before: int = 0):
    return await asyncio.to_thread(audit.entries, q, category, limit, before)


@router.get("/api/tools/activity/verify")
async def api_tools_activity_verify():
    return await heavy(audit.verify)


@router.get("/api/tools/activity/export")
async def api_tools_activity_export():
    body = await heavy(audit.export_csv)
    name = f"aktivitaet-{platform.node() or 'pi'}-{time.strftime('%Y-%m-%d')}.csv"
    return Response(body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/tools/ports")
async def api_tools_ports():
    return await asyncio.to_thread(netscan.port_reachability)


@router.post("/api/tools/port-test")
async def api_tools_port_test(req: PortTestRequest, request: Request):
    request.state.audit_detail = f"{req.host[:60]} · {req.ports[:60]}"
    try:
        return await asyncio.to_thread(netscan.test_ports, req.host, req.ports)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class FanCurveRequest(BaseModel):
    temps: list
    speeds: list          # percent


@router.get("/api/tools/fan-curve")
async def api_tools_fan_curve():
    return await asyncio.to_thread(fancurve.status)


@router.post("/api/tools/fan-curve")
async def api_tools_fan_curve_set(req: FanCurveRequest, request: Request):
    try:
        result = await asyncio.to_thread(fancurve.set_curve, req.temps, req.speeds)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{req.temps} °C · {req.speeds} %"
    return result


@router.post("/api/tools/fan-curve/reset")
async def api_tools_fan_curve_reset():
    try:
        return await asyncio.to_thread(fancurve.reset)
    except (PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
