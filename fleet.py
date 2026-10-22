"""
Fleet overview (page /flotte): this Pi and every remote Pi in one table - reachability,
dashboard version, apt updates, temperature, load, memory and storage - and apt update /
upgrade for several Pis at once (each Pi runs it in its own systemd unit, as on its own page).
"""

import asyncio
import platform
import time
from typing import Any, Dict, List

import httpx

import homelab
import remotes
import system_info
import updater


def _compact(snap: Dict[str, Any], updates: Dict[str, Any], version: Dict[str, Any], apt: Dict[str, Any]) -> Dict[str, Any]:
    up = snap.get("uptime") or {}
    return {
        "hostname": snap.get("host_name", ""), "model": snap.get("model", ""),
        "cpu": (snap.get("cpu") or {}).get("percent"), "temp": (snap.get("temperature") or {}).get("celsius"),
        "ram": (snap.get("memory") or {}).get("percent"), "disk": (snap.get("disk") or {}).get("percent"),
        "uptime": up.get("formatted", "") if isinstance(up, dict) else "",
        "updates": updates.get("count") if updates.get("supported") else None,
        "security": updates.get("security_count") if updates.get("supported") else None,
        "reboot_required": bool(updates.get("reboot_required")),
        "commit": (version.get("current_commit") or "")[:7], "update_available": bool(version.get("update_available")),
        "apt_running": bool(apt.get("running")), "apt_state": apt.get("state", ""), "apt_mode": apt.get("mode") or "",
    }


def _local() -> Dict[str, Any]:
    data = _compact(system_info.get_system_snapshot(), homelab.get_updates(), updater.update_state or {}, homelab.get_apt_run())
    return {"id": "", "name": "Dieser Pi", "url": "", "online": True, **data}


async def _remote(client: httpx.AsyncClient, r: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {r['token']}"}
    base = {"id": r["id"], "name": r["name"], "url": r["url"]}
    try:
        snap, hl, ver, apt = await asyncio.gather(*(client.get(f"{r['url']}{p}", headers=headers) for p in
                                                    ("/api/system", "/api/homelab", "/api/updates/status", "/api/updates/apt")))
        for res in (snap, hl, ver, apt):
            res.raise_for_status()
        return {**base, "online": True, **_compact(snap.json(), hl.json().get("updates") or {}, ver.json(), apt.json())}
    except (httpx.HTTPError, ValueError) as e:
        remotes.mark_offline(r["id"], f"Nicht erreichbar ({type(e).__name__})")
        return {**base, "online": False, "error": f"Nicht erreichbar ({type(e).__name__})"}


async def overview() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        local_task = asyncio.to_thread(_local)
        rows = await asyncio.gather(local_task, *(_remote(client, r) for r in remotes._all()))
    rows[0]["name"] = platform.node() or "Dieser Pi"
    return {"pis": rows, "checked": int(time.time())}


async def apt(ids: List[str], mode: str, user: str = "") -> Dict[str, Any]:
    """Start apt update/upgrade on the given Pis ("" = this Pi); returns one result per Pi."""
    if mode not in homelab.APT_MODES:
        raise ValueError("Unbekannte Aktion.")
    by_id = {r["id"]: r for r in remotes._all()}

    async def one(pid: str) -> Dict[str, Any]:
        if pid == "":
            res = await asyncio.to_thread(homelab.start_apt_run, mode)
            return {"id": "", "ok": res["ok"], "message": res["message"]}
        r = by_id.get(pid)
        if not r:
            return {"id": pid, "ok": False, "message": "Unbekannter Pi."}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                res = await client.post(f"{r['url']}/api/updates/apt", json={"mode": mode},
                                        headers={"Authorization": f"Bearer {r['token']}", "X-PiCore-User": user})
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
            return {"id": pid, "ok": res.status_code == 200, "message": data.get("message") or data.get("detail") or f"Antwort {res.status_code}"}
        except httpx.HTTPError as e:
            return {"id": pid, "ok": False, "message": f"Nicht erreichbar ({type(e).__name__})"}

    return {"results": await asyncio.gather(*(one(i) for i in dict.fromkeys(ids)))}
