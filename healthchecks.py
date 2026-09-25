"""
Health checks for your own web services (Home Assistant, NAS, Grafana, ...).

Each check is a URL on the home network or VPN that is requested every 1 / 5 / 15
minutes. Any HTTP answer below 400 counts as up (redirects and login pages included).
Addresses outside the home network / VPN are refused - this is not an internet
monitor. Results are kept 30 days in history.db.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

import history
import settings_store

logger = logging.getLogger("pi_dashboard.healthchecks")

INTERVALS = (1, 5, 15)            # minutes
TIMEOUT = 8
RETENTION = 30 * 86400
MAX_CHECKS = 30
VPN_NET = ipaddress.ip_network("100.64.0.0/10")

_last_run: Dict[str, float] = {}


def _conn():
    conn = history._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS health_results (
        ts INTEGER, check_id TEXT, ok INTEGER, status INTEGER, ms REAL, error TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS health_results_idx ON health_results (check_id, ts)")
    return conn


def checks() -> List[Dict[str, Any]]:
    return settings_store.get("health_checks", []) or []


def _local_only(host: str) -> None:
    """Every address the host resolves to must be local (LAN, VPN, loopback)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ValueError(f"„{host}“ lässt sich nicht auflösen.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in VPN_NET)):
            raise ValueError(f"{host} ({ip}) liegt nicht im Heimnetz oder VPN – nur eigene Dienste werden geprüft.")


def _validate(name: str, url: str, interval: int) -> Dict[str, Any]:
    name = name.strip()[:40]
    url = url.strip()
    if not name:
        raise ValueError("Bitte einen Namen angeben.")
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise ValueError("Nur http:// und https:// werden unterstützt.")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 300:
        raise ValueError("Ungültige Adresse – z.B. http://192.168.178.45:8123")
    if interval not in INTERVALS:
        raise ValueError("Intervall muss 1, 5 oder 15 Minuten sein.")
    _local_only(parsed.hostname)
    return {"name": name, "url": url, "interval": interval}


def add_check(name: str, url: str, interval: int = 5, verify_tls: bool = False) -> Dict[str, Any]:
    items = checks()
    if len(items) >= MAX_CHECKS:
        raise ValueError(f"Höchstens {MAX_CHECKS} Prüfungen.")
    item = {"id": uuid.uuid4().hex[:10], **_validate(name, url, interval), "verify_tls": bool(verify_tls)}
    settings_store.set("health_checks", items + [item])
    return item


def remove_check(check_id: str) -> None:
    settings_store.set("health_checks", [c for c in checks() if c["id"] != check_id])
    with _conn() as conn:
        conn.execute("DELETE FROM health_results WHERE check_id = ?", (check_id,))


async def run_check(check: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    result = {"ok": False, "status": None, "ms": None, "error": ""}
    try:
        await asyncio.to_thread(_local_only, urlparse(check["url"]).hostname)
        async with httpx.AsyncClient(verify=check.get("verify_tls", False), timeout=TIMEOUT,
                                     follow_redirects=False) as client:
            res = await client.get(check["url"], headers={"User-Agent": "PiCore-Healthcheck"})
        result.update(status=res.status_code, ms=round((time.time() - t0) * 1000, 1), ok=res.status_code < 400)
        if res.status_code >= 400:
            result["error"] = f"HTTP {res.status_code}"
    except ValueError as e:
        result["error"] = str(e)
    except httpx.ConnectError:
        result["error"] = "Verbindung abgelehnt / nicht erreichbar"
    except httpx.TimeoutException:
        result["error"] = f"Keine Antwort nach {TIMEOUT} s"
    except httpx.HTTPError as e:
        msg = str(e) or e.__class__.__name__
        result["error"] = "Zertifikat ungültig" if "CERTIFICATE" in msg.upper() else msg[:150]
    _last_run[check["id"]] = time.time()
    with _conn() as conn:
        conn.execute("INSERT INTO health_results (ts, check_id, ok, status, ms, error) VALUES (?, ?, ?, ?, ?, ?)",
                     (int(time.time()), check["id"], int(result["ok"]), result["status"], result["ms"], result["error"]))
    return result


async def background_checker() -> None:
    logger.info("Health checks started")
    last_cleanup = 0.0
    while True:
        await asyncio.sleep(20)
        now = time.time()
        due = [c for c in checks() if now - _last_run.get(c["id"], 0) >= c.get("interval", 5) * 60 - 5]
        if due:
            await asyncio.gather(*(run_check(c) for c in due), return_exceptions=True)
        if now - last_cleanup > 3600:
            last_cleanup = now
            with _conn() as conn:
                conn.execute("DELETE FROM health_results WHERE ts < ?", (int(now) - RETENTION,))


def status(hours: int = 24) -> Dict[str, Any]:
    now = int(time.time())
    since = now - hours * 3600
    bucket = 1800
    buckets = list(range((since // bucket) * bucket, now + 1, bucket))
    out = []
    with _conn() as conn:
        for c in checks():
            rows = conn.execute("SELECT ts, ok, status, ms, error FROM health_results WHERE check_id = ? AND ts >= ? ORDER BY ts",
                                (c["id"], since)).fetchall()
            last = rows[-1] if rows else conn.execute(
                "SELECT ts, ok, status, ms, error FROM health_results WHERE check_id = ? ORDER BY ts DESC LIMIT 1", (c["id"],)).fetchone()
            last_fail = next((r for r in reversed(rows) if not r[1]), None)
            per_bucket: Dict[int, List[int]] = {}
            for ts, ok, *_ in rows:
                per_bucket.setdefault((ts // bucket) * bucket, []).append(ok)
            ms = [r[3] for r in rows if r[3] is not None]
            out.append({
                **c,
                "last": {"ts": last[0], "ok": bool(last[1]), "status": last[2], "ms": last[3], "error": last[4]} if last else None,
                "uptime": round(sum(r[1] for r in rows) / len(rows) * 100, 1) if rows else None,
                "avg_ms": round(sum(ms) / len(ms), 1) if ms else None,
                "last_failure": {"ts": last_fail[0], "error": last_fail[4]} if last_fail else None,
                # per 30 min: share of successful checks (None = not checked)
                "bars": [round(sum(per_bucket[b]) / len(per_bucket[b]), 2) if b in per_bucket else None for b in buckets],
            })
    return {"checks": out, "hours": hours, "intervals": INTERVALS}
