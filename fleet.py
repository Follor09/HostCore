"""
Fleet overview (page /flotte): this Pi and every remote Pi in one table - reachability,
dashboard version, apt updates, temperature, load, memory and storage - and apt update /
upgrade for several Pis at once (each Pi runs it in its own systemd unit, as on its own page).
"""

import asyncio
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx

import homelab
import remotes
import system_info
import updater


def _compact(snap: Dict[str, Any], updates: Dict[str, Any], version: Dict[str, Any], apt: Dict[str, Any],
             pkg: Dict[str, Any]) -> Dict[str, Any]:
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
        "commits_behind": version.get("commits_behind") or 0, "latest_commit": (version.get("latest_remote_commit") or "")[:7],
        "updating": bool(version.get("is_updating")),
        "apt_running": bool(apt.get("running")), "apt_state": apt.get("state", ""), "apt_mode": apt.get("mode") or "",
        "packages": updates.get("packages", []) if updates.get("supported") else [],
        "lists_updated": updates.get("lists_updated"),
        "pkg_running": bool(pkg.get("running")), "pkg_action": pkg.get("action", ""), "pkg_state": pkg.get("state", ""),
    }


def _local() -> Dict[str, Any]:
    data = _compact(system_info.get_system_snapshot(), homelab.get_updates(), updater.update_state or {}, homelab.get_apt_run(),
                    _pkg_status())
    return {"id": "", "name": "Dieser Pi", "url": "", "online": True, **data}


async def _remote(client: httpx.AsyncClient, r: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {r['token']}"}
    base = {"id": r["id"], "name": r["name"], "url": r["url"]}
    try:
        snap, hl, ver, apt, pkg = await asyncio.gather(*(client.get(f"{r['url']}{p}", headers=headers) for p in
                                                         ("/api/system", "/api/homelab", "/api/updates/status", "/api/updates/apt",
                                                          "/api/packages/status")))
        for res in (snap, hl, ver, apt):
            res.raise_for_status()
        pkg_data = pkg.json() if pkg.status_code == 200 else {}  # older agents: no package jobs
        return {**base, "online": True, **_compact(snap.json(), hl.json().get("updates") or {}, ver.json(), apt.json(), pkg_data)}
    except (httpx.HTTPError, ValueError) as e:
        remotes.mark_offline(r["id"], f"Nicht erreichbar ({type(e).__name__})")
        return {**base, "online": False, "error": f"Nicht erreichbar ({type(e).__name__})"}


async def overview() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        local_task = asyncio.to_thread(_local)
        rows = await asyncio.gather(local_task, *(_remote(client, r) for r in remotes._all()))
    rows[0]["name"] = platform.node() or "Dieser Pi"
    return {"pis": rows, "checked": int(time.time())}


def _pkg_status() -> Dict[str, Any]:
    import packages
    try:
        return packages.status()
    except Exception:
        return {}


async def _call(pid: str, method: str, path: str, body: Any = None, user: str = "") -> Dict[str, Any]:
    """One request to a remote Pi's agent (the main Pi is handled by the caller)."""
    r = next((x for x in remotes._all() if x["id"] == pid), None)
    if not r:
        raise ValueError("Unbekannter Pi.")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        res = await client.request(method, f"{r['url']}{path}", json=body,
                                   headers={"Authorization": f"Bearer {r['token']}", "X-PiCore-User": user})
    data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
    if res.status_code != 200:
        raise ValueError(data.get("detail") or f"Antwort {res.status_code}")
    return data


async def upgrade_packages(targets: Dict[str, List[str]], user: str = "") -> Dict[str, Any]:
    """Upgrade chosen packages per Pi ({pi id: [package names]}, "" = this Pi) - only newer
    versions of installed packages, each Pi in its own apt unit."""
    import packages

    async def one(pid: str, names: List[str]) -> Dict[str, Any]:
        names = [n for n in dict.fromkeys(names) if packages.NAME.match(n)][:200]
        if not names:
            return {"id": pid, "ok": False, "message": "Keine gültigen Pakete."}
        try:
            if pid == "":
                await asyncio.to_thread(packages.start, "upgrade", names)
            else:
                await _call(pid, "POST", "/api/packages/run", {"action": "upgrade", "names": names}, user)
            return {"id": pid, "ok": True, "message": f"{len(names)} {'Paket' if len(names) == 1 else 'Pakete'} werden aktualisiert"}
        except (packages.PackageError, ValueError, PermissionError, RuntimeError) as e:
            return {"id": pid, "ok": False, "message": str(e)}
        except (OSError, subprocess.TimeoutExpired):
            return {"id": pid, "ok": False, "message": "apt ist hier nicht verfügbar oder antwortet nicht."}
        except httpx.HTTPError as e:
            return {"id": pid, "ok": False, "message": f"Nicht erreichbar ({type(e).__name__})"}

    return {"results": await asyncio.gather(*(one(pid, names) for pid, names in list(targets.items())[:50]))}


async def job_log(pid: str) -> Dict[str, Any]:
    """Output of the running / last apt job of one Pi (system upgrade or package upgrade)."""
    if pid == "":
        apt, pkg = await asyncio.to_thread(homelab.get_apt_run), await asyncio.to_thread(_pkg_status)
    else:
        apt = await _call(pid, "GET", "/api/updates/apt")
        try:
            pkg = await _call(pid, "GET", "/api/packages/status")
        except (ValueError, httpx.HTTPError):
            pkg = {}
    # The more recent of the two jobs
    use_pkg = bool(pkg.get("running")) or (not apt.get("running") and (pkg.get("started") or 0) > (apt.get("started") or 0))
    src = pkg if use_pkg else apt
    return {"kind": "packages" if use_pkg else "apt", "running": bool(src.get("running")), "state": src.get("state", ""),
            "title": (f"apt {src.get('action', '')} {src.get('packages', '')}" if use_pkg else f"apt {src.get('mode', '')}").strip(),
            "started": src.get("started"), "finished": src.get("finished"), "exit_code": src.get("exit_code"),
            "log": (src.get("log") or "")[-20000:]}


async def dashboard_update(ids: List[str], user: str = "") -> Dict[str, Any]:
    """Let the given Pis pull the newest dashboard version right away (instead of at the next timer run)."""
    import updater

    async def one(pid: str) -> Dict[str, Any]:
        try:
            if pid == "":
                res = await asyncio.to_thread(updater.run_update_now)
            else:
                res = await _call(pid, "POST", "/api/updates/run-now", {}, user)
            return {"id": pid, "ok": bool(res.get("ok")), "message": res.get("message", "")}
        except ValueError as e:
            msg = str(e)
            if "404" in msg or "Not Found" in msg:
                msg = "Dieser Pi kennt das noch nicht – er aktualisiert sich innerhalb einer Minute selbst."
            return {"id": pid, "ok": False, "message": msg}
        except httpx.HTTPError as e:
            return {"id": pid, "ok": False, "message": f"Nicht erreichbar ({type(e).__name__})"}

    return {"results": await asyncio.gather(*(one(i) for i in dict.fromkeys(ids)))}


async def capacity() -> Dict[str, Any]:
    """Capacity summary of every Pi (for the fleet page)."""
    import capacity as cap

    async def one(r: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if r is None:
            try:
                return {"id": "", "name": platform.node() or "Dieser Pi", **(await asyncio.to_thread(cap.summary))}
            except Exception as e:
                return {"id": "", "name": platform.node() or "Dieser Pi", "error": f"{type(e).__name__}"}
        try:
            return {"id": r["id"], "name": r["name"], **(await _call(r["id"], "GET", "/api/capacity/summary"))}
        except (ValueError, httpx.HTTPError) as e:
            return {"id": r["id"], "name": r["name"], "error": "Nicht verfügbar" if "404" in str(e) else f"Nicht erreichbar ({type(e).__name__})"}

    return {"pis": await asyncio.gather(one(None), *(one(r) for r in remotes._all()))}


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
