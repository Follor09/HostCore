"""
Speed test of the selected server's internet connection (backend/speedtest.py). Forwarded like
every tool: with a remote server selected, its agent measures its own line.

/api/speedtest/fleet (this dashboard only, never forwarded): the last result of every server,
and a test on several servers - one after the other, so servers behind the same line do not
share it during the measurement.
"""

import asyncio
import platform

import httpx
from fastapi import APIRouter, HTTPException, Request

import fleet
import remotes
import speedtest

router = APIRouter()


@router.get("/api/tools/speedtest")
async def api_speedtest_history():
    return speedtest.history()


@router.post("/api/tools/speedtest")
async def api_speedtest_run(request: Request):
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
    known = {r["id"]: r["name"] for r in remotes.all_remotes()}
    known[LOCAL_ID] = platform.node() or "Dieser Server"
    ids = [str(i) for i in dict.fromkeys(payload.get("servers") or []) if str(i) in known]
    if not ids:
        raise HTTPException(status_code=400, detail="Bitte mindestens einen Server wählen.")
    results = []
    for sid in ids:                      # one after the other: servers behind one line would share it
        try:
            if sid == LOCAL_ID:
                r = await asyncio.to_thread(speedtest.run)
            else:
                r = (await fleet._call(sid, "POST", "/api/tools/speedtest", {}, request.state.user or "", timeout=180.0)).get("result")
            results.append({"id": sid, "name": known[sid], "ok": True, "result": _short(r)})
        except speedtest.SpeedtestError as e:
            results.append({"id": sid, "name": known[sid], "ok": False, "message": str(e)})
        except ValueError as e:
            msg = str(e)
            results.append({"id": sid, "name": known[sid], "ok": False,
                            "message": "Agent zu alt – dort HostCore aktualisieren." if "404" in msg or "Not Found" in msg else msg})
        except httpx.HTTPError as e:
            results.append({"id": sid, "name": known[sid], "ok": False, "message": f"Nicht erreichbar ({type(e).__name__})"})
    request.state.audit_detail = ", ".join(f"{x['name']}: {x['result']['down']}/{x['result']['up']} Mbit/s" if x["ok"] else f"{x['name']}: Fehler"
                                           for x in results)[:250]
    return {"results": results}
