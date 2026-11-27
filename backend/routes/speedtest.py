"""
Speed test of the selected server's internet connection (backend/speedtest.py). Forwarded like
every tool: with a remote server selected, its agent measures its own line.

/api/speedtest/fleet (this dashboard only, never forwarded): the last result of every server,
and a test on several servers - one after the other, so servers behind the same line do not
share it during the measurement.
"""

import asyncio
import os
import platform
import sys
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

import fleet
import remotes
import speedtest
import tasks

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "speedtest.py")

router = APIRouter()


@router.get("/api/tools/speedtest")
async def api_speedtest_history():
    return speedtest.history()


def _start_task(title: str, args: list, user: str, key: str) -> dict:
    try:
        return tasks.start("speedtest", title, [{"argv": [sys.executable, SCRIPT, *args], "show": title}], user=user, key=key,
                           timeout=60 + 60 * max(1, len(args) - 1), env={"HC_TASK_USER": user})
    except tasks.TaskError as e:
        raise HTTPException(status_code=400, detail="Es läuft schon ein Speedtest." if "läuft schon" in str(e) else str(e))


@router.post("/api/tools/speedtest")
async def api_speedtest_run(request: Request, payload: Optional[dict] = None):
    """As a task (the dock at the bottom shows it, the log the progress) - or, with {"wait": true},
    measured right away (the main dashboard's test on several servers asks its agents like that)."""
    if not (payload or {}).get("wait"):
        if speedtest.is_running():
            raise HTTPException(status_code=400, detail="Es läuft schon ein Speedtest.")
        request.state.audit_detail = "als Aufgabe gestartet"
        return {"task": _start_task("Speedtest", ["run"], request.state.user or "", "speedtest")}
    try:
        r = await asyncio.to_thread(speedtest.run)
    except speedtest.SpeedtestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = f"{r['down']} / {r['up']} Mbit/s, {r['ping']} ms"
    return {**speedtest.history(), "result": r}


LOCAL_ID = "local"


def _short(r: dict) -> dict:
    return {k: r.get(k) for k in ("ts", "down", "up", "ping", "jitter", "server", "isp")} if r else None


async def _remote_history(r: dict) -> dict:
    base = {"id": r["id"], "name": r["name"]}
    try:
        d = await fleet._call(r["id"], "GET", "/api/tools/speedtest", timeout=10.0)
        return {**base, "online": True, "last": _short((d.get("results") or [None])[0]), "running": bool(d.get("running"))}
    except ValueError as e:
        msg = str(e)
        return {**base, "online": True, "last": None, "error": "Agent zu alt – dort HostCore aktualisieren." if "404" in msg or "Not Found" in msg else msg}
    except httpx.HTTPError as e:
        return {**base, "online": False, "last": None, "error": f"Nicht erreichbar ({type(e).__name__})"}


@router.get("/api/speedtest/fleet")
async def api_speedtest_fleet():
    local = speedtest.history()
    rows = await asyncio.gather(*(_remote_history(r) for r in remotes.all_remotes()))
    here = {"id": LOCAL_ID, "name": platform.node() or "Dieser Server", "online": True,
            "last": _short((local["results"] or [None])[0]), "running": local["running"]}
    return {"servers": [here, *rows]}


@router.post("/api/speedtest/fleet")
async def api_speedtest_fleet_run(payload: dict, request: Request):
    """One task on this dashboard that measures the chosen servers one after the other."""
    known = {r["id"]: r["name"] for r in remotes.all_remotes()}
    known[LOCAL_ID] = platform.node() or "Dieser Server"
    ids = [str(i) for i in dict.fromkeys(payload.get("servers") or []) if str(i) in known]
    if not ids:
        raise HTTPException(status_code=400, detail="Bitte mindestens einen Server wählen.")
    title = f"Speedtest auf {len(ids)} Servern" if len(ids) > 1 else f"Speedtest: {known[ids[0]]}"
    request.state.audit_detail = ", ".join(known[i] for i in ids)[:250]
    return {"task": _start_task(title, ["fleet", *ids], request.state.user or "", "speedtest-fleet"), "servers": [known[i] for i in ids]}
