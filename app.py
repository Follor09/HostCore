"""
Raspberry Pi All-in-One Dashboard Server (Server & Homelab Edition).
FastAPI backend with WebSocket real-time live streaming, services monitoring,
drives explorer, Git auto-updater, diagnostics runner, and system monitoring.
"""

import asyncio
import json
from contextlib import asynccontextmanager
import logging
import os
import subprocess
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

import system_info
import services_manager
import updater
import history
import homelab

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pi_dashboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initial git check & background poller
    updater.refresh_git_info()
    poller_task = asyncio.create_task(updater.background_update_poller())
    history_task = asyncio.create_task(history.background_sampler())
    net_task = asyncio.create_task(homelab.background_net_monitor())
    yield
    # Shutdown
    poller_task.cancel()
    history_task.cancel()
    net_task.cancel()


app = FastAPI(title="Raspberry Pi Server Dashboard", version="1.2.0", lifespan=lifespan)

# Serve static assets
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CommandRequest(BaseModel):
    command: str


class WeatherLocationRequest(BaseModel):
    city: str


# Weather location, persisted in settings.json (untracked, so it survives git updates)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
DEFAULT_WEATHER_LOCATION = {"city": "Berlin", "lat": 52.52, "lon": 13.41}


def load_weather_location() -> dict:
    try:
        with open(SETTINGS_FILE, "r") as f:
            loc = json.load(f).get("weather_location")
            if loc and {"city", "lat", "lon"} <= loc.keys():
                return loc
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass
    return dict(DEFAULT_WEATHER_LOCATION)


def save_weather_location(loc: dict) -> None:
    try:
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}
    settings["weather_location"] = loc
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_FILE)


# Cached weather response to prevent rate limiting
_cached_weather = {
    "temp": 20.0,
    "humidity": 50,
    "wind_speed": 10.0,
    "description": "Klar",
    "weather_code": 0,
    "city": "Berlin (Default)",
    "timestamp": 0
}


def interpret_wmo_code(code: int) -> tuple[str, str]:
    """Translate WMO weather code to German description and icon."""
    mapping = {
        0: ("Klarer Himmel", "sun"),
        1: ("Überwiegend klar", "sun-dim"),
        2: ("Teilweise bewölkt", "cloud-sun"),
        3: ("Bedeckt", "cloud"),
        45: ("Neblig", "cloud-fog"),
        48: ("Raureifnebel", "cloud-fog"),
        51: ("Leichter Nieselregen", "cloud-drizzle"),
        53: ("Nieselregen", "cloud-drizzle"),
        55: ("Starker Nieselregen", "cloud-drizzle"),
        61: ("Leichter Regen", "cloud-rain"),
        63: ("Mäßiger Regen", "cloud-rain"),
        65: ("Starker Regen", "cloud-rain-heavy"),
        71: ("Leichter Schneefall", "cloud-snow"),
        73: ("Schneefall", "cloud-snow"),
        75: ("Starker Schneefall", "cloud-snow"),
        80: ("Regenschauer", "cloud-showers"),
        81: ("Starke Schauer", "cloud-showers"),
        82: ("Heftige Schauer", "cloud-showers-heavy"),
        95: ("Gewitter", "cloud-lightning"),
        96: ("Gewitter mit Hagel", "cloud-lightning-hail"),
    }
    return mapping.get(code, ("Heiter", "cloud-sun"))


@app.get("/")
async def get_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"status": "Dashboard frontend loading..."})

    # Append each asset's mtime as ?v=... so browsers never mix a new page
    # with a cached old style.css / app.js after an auto-update.
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    for asset in ("style.css", "app.js"):
        path = os.path.join(STATIC_DIR, asset)
        if os.path.exists(path):
            html = html.replace(f"/static/{asset}\"", f"/static/{asset}?v={int(os.path.getmtime(path))}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/system")
async def api_system():
    """Return full system snapshot."""
    return system_info.get_system_snapshot()


@app.get("/api/processes")
async def api_processes(limit: int = 8):
    """Return top active processes."""
    return {"processes": system_info.get_top_processes(limit=limit)}


@app.get("/api/history")
async def api_history(range: str = "24h"):
    """Downsampled metrics history: range=24h or 7d."""
    return await asyncio.to_thread(history.get_history, range)


@app.get("/api/homelab")
async def api_homelab():
    """Internet monitor, apt updates, NVMe health, Docker containers and Kubernetes pods."""
    return await asyncio.to_thread(homelab.get_all)


@app.get("/api/services")
async def api_services():
    """Return status of homelab services."""
    return {"services": services_manager.get_services_status()}


@app.get("/api/drives")
async def api_drives():
    """Return mounted storage partitions and external USB drives."""
    return {"drives": services_manager.get_mounted_drives()}


@app.get("/api/logs")
async def api_logs(lines: int = 30):
    """Return recent system journal logs."""
    return {"logs": services_manager.get_recent_logs(lines=lines)}


# ---------------------------------------------------------------------------
# Auto-Update Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/updates/status")
async def api_update_status():
    """Get current Git version info and pending update status."""
    return updater.update_state


@app.post("/api/updates/check")
async def api_update_check():
    """Trigger manual check against GitHub remote."""
    status = updater.check_for_updates()
    return status


@app.post("/api/updates/apply")
async def api_update_apply():
    """Pull latest Git commits, install requirements, and restart server."""
    result = updater.perform_update()
    return result


@app.post("/api/webhook/github")
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


@app.get("/api/weather")
async def api_weather(force: bool = False):
    """Fetch live weather conditions for the configured location."""
    global _cached_weather
    import time
    now = time.time()
    loc = load_weather_location()

    if not force and now - _cached_weather["timestamp"] < 600 and _cached_weather.get("city") == loc["city"]:
        return _cached_weather

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
                desc, icon = interpret_wmo_code(code)

                _cached_weather = {
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
                return _cached_weather
    except Exception as e:
        logger.warning(f"Failed to fetch weather: {e}")

    return _cached_weather


@app.post("/api/weather/location")
async def api_set_weather_location(req: WeatherLocationRequest):
    """Look up a city via Open-Meteo geocoding, save it, and return its weather."""
    name = req.city.strip()
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


@app.post("/api/action/command")
async def api_exec_command(req: CommandRequest):
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

        proc = subprocess.run(cmd_args, capture_output=True, text=True, timeout=8)
        return {
            "command": cmd_key,
            "stdout": proc.stdout or proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"command": cmd_key, "error": str(e), "returncode": 1}


@app.post("/api/action/reboot")
async def api_reboot():
    """Trigger system reboot (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "reboot"])
            return {"status": "success", "message": "Raspberry Pi wird jetzt neu gestartet..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Neustart-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo reboot' ausgeführt."
        }


@app.post("/api/action/shutdown")
async def api_shutdown():
    """Trigger system shutdown (guarded on dev hosts)."""
    if system_info.is_raspberry_pi():
        try:
            subprocess.Popen(["sudo", "poweroff"])
            return {"status": "success", "message": "Raspberry Pi wird heruntergefahren..."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {
            "status": "simulated",
            "message": "[Simulation] Herunterfahren-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo poweroff' ausgeführt."
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time live push updates.
    Sends system snapshot, top processes, services, drives, and updater state every 1.5 seconds.
    """
    await websocket.accept()
    logger.info("WebSocket Client connected.")
    try:
        while True:
            snapshot = system_info.get_system_snapshot()
            processes = system_info.get_top_processes(limit=6)
            services = services_manager.get_services_status()
            drives = services_manager.get_mounted_drives()

            payload = {
                "type": "live_update",
                "system": snapshot,
                "processes": processes,
                "services": services,
                "drives": drives,
                "updater": updater.update_state
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        logger.info("WebSocket Client disconnected.")
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")
