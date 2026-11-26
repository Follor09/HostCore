"""
Auto-Update Endpoints - routes moved out of app.py.
"""

import asyncio
import json
import subprocess
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Header, Request, WebSocket, WebSocketDisconnect

import agent
import auth
import services_manager
import storage
import system_info
import updater
import weather
import websec
from hostctx import AGENT, HOST_CHANGED_CLOSE, _ws_relay, host_changed as _host_changed, remote_for as _remote_for
from routes.docker import CommandRequest, WeatherLocationRequest
from weather import interpret_wmo_code, save_weather_location
from webutil import logger

router = APIRouter()


@router.get("/api/updates/status")
async def api_update_status():
    """Get current Git version info and pending update status."""
    return updater.update_state


@router.post("/api/settings/update-channel")
async def api_settings_update_channel(payload: dict, request: Request):
    try:
        value = await asyncio.to_thread(updater.set_channel, str(payload.get("channel", "")))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.state.audit_detail = value
    return await asyncio.to_thread(updater.check_for_updates)


@router.post("/api/updates/check")
async def api_update_check():
    """Trigger manual check against GitHub remote."""
    return await asyncio.to_thread(updater.check_for_updates)


@router.post("/api/updates/apply")
async def api_update_apply():
    """Install the update now: through the update unit like the timer (update.sh, also updates root's copy
    of the helpers), in this process only where that unit doesn't exist (development)."""
    res = await asyncio.to_thread(updater.run_update_now)
    if res.get("ok"):
        return {"status": "started", "message": res["message"]}
    return await asyncio.to_thread(updater.perform_update)


@router.post("/api/webhook/github")
async def api_github_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """
    GitHub Webhook receiver.
    Triggered instantly whenever a new commit is pushed to the GitHub repository.
    """
    body = await request.body()
    if not updater.verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid GitHub webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "push")
    logger.info(f"Received GitHub webhook event: {event_type}")

    if event_type in ("push", "ping"):
        # Run update in background task
        asyncio.create_task(asyncio.to_thread(updater.perform_update))
        return {"status": "ok", "message": "GitHub push event empfangen. Auto-Update gestartet!"}

    return {"status": "ignored", "event": event_type}


def _next_hours(hourly: dict, current_time: str, count: int) -> list:
    """Hourly forecast starting at the current hour (Open-Meteo times are local ISO strings)."""
    times = hourly.get("time", [])
    current_hour = current_time[:13]  # "2026-09-24T14"
    start = next((i for i, t in enumerate(times) if t[:13] >= current_hour), 0)
    return [
        {
            "time": times[i][11:16],
            "temp": round(hourly["temperature_2m"][i]),
            "weather_code": hourly["weather_code"][i],
            "precip_prob": hourly["precipitation_probability"][i] or 0,
        }
        for i in range(start, min(start + count, len(times)))
    ]


def _daily_forecast(daily: dict) -> list:
    days = []
    for i, date in enumerate(daily.get("time", [])):
        code = daily["weather_code"][i]
        days.append({
            "date": date,
            "weather_code": code,
            "description": interpret_wmo_code(code)[0],
            "temp_max": round(daily["temperature_2m_max"][i]),
            "temp_min": round(daily["temperature_2m_min"][i]),
            "precip_prob": daily["precipitation_probability_max"][i] or 0,
            "precip_sum": round(daily["precipitation_sum"][i] or 0, 1),
            "wind_max": round(daily["wind_speed_10m_max"][i] or 0),
            "uv_max": round(daily["uv_index_max"][i] or 0, 1),
            "sunrise": daily["sunrise"][i][11:16],
            "sunset": daily["sunset"][i][11:16],
        })
    return days


@router.get("/api/weather")
async def api_weather(force: bool = False):
    """Fetch live weather conditions for the configured location."""
    import time
    now = time.time()
    loc = weather.load_weather_location()
    cached = weather.cache

    if not force and now - cached["timestamp"] < 600 and cached.get("city") == loc["city"]:
        return cached

    try:
        params = {
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max,sunrise,sunset,uv_index_max",
            "forecast_days": 7,
            "timezone": "auto",
        }
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            if resp.status_code == 200:
                data = resp.json()
                cur = data.get("current", {})
                code = cur.get("weather_code", 0)
                desc, icon = weather.interpret_wmo_code(code)

                fresh = {
                    "temp": round(cur.get("temperature_2m", 20.0), 1),
                    "humidity": cur.get("relative_humidity_2m", 50),
                    "wind_speed": round(cur.get("wind_speed_10m", 0.0), 1),
                    "description": desc,
                    "icon": icon,
                    "weather_code": code,
                    "city": loc["city"],
                    "hourly": _next_hours(data.get("hourly", {}), cur.get("time", ""), 12),
                    "daily": _daily_forecast(data.get("daily", {})),
                    "timestamp": now
                }
                cached.clear()
                cached.update(fresh)
                return cached
    except Exception as e:
        logger.warning(f"Failed to fetch weather: {e}")

    return cached


@router.post("/api/weather/location")
async def api_set_weather_location(req: WeatherLocationRequest, request: Request):
    """Look up a city via Open-Meteo geocoding, save it, and return its weather."""
    name = req.city.strip()
    request.state.audit_detail = name[:60]
    if not name:
        raise HTTPException(status_code=400, detail="Bitte einen Ort eingeben.")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": name, "count": 1, "language": "de", "format": "json"},
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
    except Exception as e:
        logger.warning(f"Geocoding failed: {e}")
        raise HTTPException(status_code=502, detail="Ortssuche fehlgeschlagen. Internetverbindung prüfen.")

    if not results:
        raise HTTPException(status_code=404, detail=f"Ort \"{name}\" nicht gefunden.")

    hit = results[0]
    loc = {"city": hit["name"], "lat": hit["latitude"], "lon": hit["longitude"]}
    save_weather_location(loc)
    logger.info(f"Weather location set to {loc['city']} ({loc['lat']}, {loc['lon']})")
    return await api_weather(force=True)


@router.post("/api/action/command")
async def api_exec_command(req: CommandRequest, request: Request):
    request.state.audit_detail = req.command[:40]
    """Execute whitelisted safe diagnostic commands."""
    allowed_commands = {
        "ping": ["ping", "-c", "3", "1.1.1.1"],
        "uptime": ["uptime"],
        "memory": ["free", "-h"],
        "disk": ["df", "-h"],
        "temp": ["vcgencmd", "measure_temp"],
        "throttled": ["vcgencmd", "get_throttled"],
        "uname": ["uname", "-a"],
        "docker": ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"]
    }

    cmd_key = req.command.strip().lower()
    if cmd_key not in allowed_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Kommando nicht erlaubt. Verfügbare Kommandos: {list(allowed_commands.keys())}"
        )

    cmd_args = allowed_commands[cmd_key]
    try:
        if cmd_key == "memory" and system_info.platform.system() == "Darwin":
            cmd_args = ["vm_stat"]
        elif cmd_key in ("temp", "throttled") and not system_info.is_raspberry_pi():
            return {
                "command": cmd_key,
                "stdout": f"[Dev-Mode]: '{cmd_key}' ist für echten Raspberry Pi reserviert. Aktuelle CPU-Temperatur: {system_info.get_cpu_temp()['celsius']}°C",
                "returncode": 0
            }

        proc = await asyncio.to_thread(subprocess.run, cmd_args, capture_output=True, text=True, timeout=8)
        return {
            "command": cmd_key,
            "stdout": proc.stdout or proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"command": cmd_key, "error": str(e), "returncode": 1}


@router.post("/api/action/reboot")
async def api_reboot():
    """Trigger system reboot (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "reboot"])
            return {"status": "success", "message": "Server wird jetzt neu gestartet..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Neustart-Befehl erfolgreich empfangen. Auf einem echten Server wird 'sudo reboot' ausgeführt."
        }


@router.post("/api/action/shutdown")
async def api_shutdown():
    """Trigger system shutdown (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "poweroff"])
            return {"status": "success", "message": "Server wird heruntergefahren..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Herunterfahren-Befehl erfolgreich empfangen. Auf einem echten Server wird 'sudo poweroff' ausgeführt."
        }


# Live data shared by all open dashboards: measured once per interval, not once per tab.
# Each group refreshes at its own pace and gets a version that only moves when the content changed.
_LIVE_EVERY = {"system": 1.5, "processes": 3.0, "services": 10.0, "storage": 15.0}
_LIVE_GROUPS = {"system": ("system",), "processes": ("processes",), "services": ("services",),
                "storage": ("storage", "drives")}
_LIVE: dict[str, dict] = {"data": {}, "ts": {}, "ver": {}, "sig": {}}
_LIVE_LOCK = asyncio.Lock()


def _live_compute(due: list) -> dict:
    out: dict[str, Any] = {}
    for group in due:
        try:
            if group == "system":
                out[group] = system_info.get_system_snapshot()
            elif group == "processes":
                out[group] = system_info.get_top_processes(limit=6)
            elif group == "services":
                out[group] = services_manager.get_services_status()
            elif group == "storage":
                out[group] = {"storage": storage.overview(), "drives": services_manager.get_mounted_drives()}
        except Exception as e:
            logger.warning(f"Live data {group}: {e}")
    return out


async def _live_data() -> tuple[dict, dict]:
    async with _LIVE_LOCK:
        now = time.monotonic()
        due = [g for g, every in _LIVE_EVERY.items() if now - _LIVE["ts"].get(g, -1e9) >= every - 0.25]
        if due:
            fresh = await asyncio.to_thread(_live_compute, due)
            for g, value in fresh.items():
                _LIVE["ts"][g] = now
                sig = json.dumps(value, sort_keys=True, default=str) if g != "system" else None
                if g == "system" or sig != _LIVE["sig"].get(g):
                    _LIVE["sig"][g] = sig
                    _LIVE["ver"][g] = _LIVE["ver"].get(g, 0) + 1
                    _LIVE["data"][g] = value
        return _LIVE["data"], dict(_LIVE["ver"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time live push updates.
    Every 1.5 s: system snapshot, plus processes, services, storage and updater state when they changed.
    """
    # Middleware doesn't cover WebSockets: check the session here
    await websocket.accept()
    if AGENT:
        if not agent.check(websocket.headers.get("authorization", "")) or not agent.allowed(websec.client_ip(websocket)):
            await websocket.close(code=4401)
            return
    else:
        user = auth.user_from_token(websocket.cookies.get(auth.COOKIE_NAME))
        if not user and not auth.public_view():
            await websocket.close(code=4401)  # the page redirects to /login on this code
            return
        if _host_changed(websocket, websocket.query_params.get("host")):
            await websocket.close(code=HOST_CHANGED_CLOSE)
            return
        remote = _remote_for(websocket)
        if remote:
            await _ws_relay(websocket, remote, "/ws", user or "")
            return
    logger.info("WebSocket Client connected.")
    sent: dict[str, int] = {}
    last_updater = None
    try:
        while True:
            data, versions = await _live_data()
            # Only parts that changed since this client's last message; "system" always changes
            payload: dict[str, Any] = {"type": "live_update"}
            for group, keys in _LIVE_GROUPS.items():
                if group not in data or sent.get(group) == versions.get(group):
                    continue
                sent[group] = versions[group]
                for k in keys:
                    payload[k] = data[group][k] if group == "storage" else data[group]
            upd = json.dumps(updater.update_state, sort_keys=True, default=str)
            if upd != last_updater:
                last_updater = upd
                payload["updater"] = updater.update_state
            await websocket.send_json(payload)
            await asyncio.sleep(_LIVE_EVERY["system"])
    except WebSocketDisconnect:
        logger.info("WebSocket Client disconnected.")
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")


# Outermost layer: the MCP server's own paths (/mcp, OAuth) bypass the dashboard's login
