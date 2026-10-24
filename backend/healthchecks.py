"""
Health checks for your own services (Home Assistant, NAS, Grafana, router, printer ...).

Check types (every 1 / 5 / 15 minutes):
  http     URL answers below HTTP 400 (redirects and login pages included)
  keyword  like http, and the page contains a text (or must not contain it)
  json     like http, and a value in the JSON answer (path a.b.0.c) equals / contains a text
  tcp      a TCP port accepts connections
  ping     the host answers ping; with a MAC (device from the LAN scan) the current
           IP is looked up in the neighbour table first, so DHCP changes don't matter
  dns      a DNS server (Pi-hole, AdGuard, router) answers a query, optionally with an expected value

Hosts outside the home network / VPN are refused - this is not an internet monitor.
Results are kept 30 days in history.db.
"""

import asyncio
import ipaddress
import json
import logging
import platform
import re
import socket
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

import history
import settings_store

logger = logging.getLogger("pi_dashboard.healthchecks")

INTERVALS = (1, 5, 15)            # minutes
TYPES = ("http", "keyword", "json", "tcp", "ping", "dns")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9A-Fa-f:]{2,39}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
TIMEOUT = 8
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
    """Classic HTTP check (kept for the existing settings form and API)."""
    return _validate_check({"name": name, "url": url, "interval": interval, "type": "http"})


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise ValueError("Nur http:// und https:// werden unterstützt.")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 300:
        raise ValueError("Ungültige Adresse – z.B. http://192.168.178.45:8123")
    _local_only(parsed.hostname)
    return url


def _validate_host(host: str) -> str:
    host = (host or "").strip()
    if not HOST_RE.match(host):
        raise ValueError("Ungültiger Host – Name oder IP-Adresse.")
    _local_only(host)
    return host


def _validate_check(f: Dict[str, Any]) -> Dict[str, Any]:
    name = str(f.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("Bitte einen Namen angeben.")
    ctype = str(f.get("type") or "http")
    if ctype not in TYPES:
        raise ValueError("Unbekannte Prüfart.")
    try:
        interval = int(f.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 0
    if interval not in INTERVALS:
        raise ValueError("Intervall muss 1, 5 oder 15 Minuten sein.")
    out: Dict[str, Any] = {"name": name, "type": ctype, "interval": interval}
    if ctype in ("http", "keyword", "json"):
        out["url"] = _validate_url(str(f.get("url") or ""))
        out["verify_tls"] = bool(f.get("verify_tls"))
    if ctype == "keyword":
        kw = str(f.get("keyword") or "")
        if not kw or len(kw) > 200:
            raise ValueError("Stichwort: 1–200 Zeichen.")
        out.update(keyword=kw, invert=bool(f.get("invert")))
    if ctype == "json":
        path = str(f.get("json_path") or "").strip()
        if not re.match(r"^[\w-]+(\.[\w-]+)*$", path):
            raise ValueError("JSON-Pfad z. B. status oder data.0.state")
        expected = str(f.get("expected") or "")
        if len(expected) > 200:
            raise ValueError("Erwarteter Wert: höchstens 200 Zeichen.")
        out.update(json_path=path, expected=expected)
    if ctype in ("tcp", "ping", "dns"):
        out["host"] = _validate_host(str(f.get("host") or ""))
    if ctype == "tcp":
        try:
            port = int(f.get("port"))
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            raise ValueError("Port: 1–65535.")
        out["port"] = port
    if ctype == "ping":
        mac = str(f.get("mac") or "").strip().lower()
        if mac and not MAC_RE.match(mac):
            raise ValueError("Ungültige MAC-Adresse.")
        if mac:
            out["mac"] = mac
    if ctype == "dns":
        query = str(f.get("query") or "").strip()
        if not re.match(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$", query):
            raise ValueError("Abzufragender Name, z. B. example.org")
        expected = str(f.get("expected") or "").strip()[:100]
        out.update(query=query, expected=expected)
    if ctype in ("http", "keyword", "json"):
        out["target"] = out["url"]
    elif ctype == "tcp":
        out["target"] = f"{out['host']}:{out['port']}"
    elif ctype == "dns":
        out["target"] = f"{out['query']} @ {out['host']}"
    else:
        out["target"] = out["host"]
    return out


def add_check(name: str, url: str, interval: int = 5, verify_tls: bool = False) -> Dict[str, Any]:
    return add(dict(name=name, url=url, interval=interval, verify_tls=verify_tls, type="http"))


def add(fields: Dict[str, Any]) -> Dict[str, Any]:
    items = checks()
    if len(items) >= MAX_CHECKS:
        raise ValueError(f"Höchstens {MAX_CHECKS} Prüfungen.")
    item = {"id": uuid.uuid4().hex[:10], **_validate_check(fields)}
    settings_store.set("health_checks", items + [item])
    return item


def update(check_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    items = checks()
    item = next((c for c in items if c["id"] == check_id), None)
    if not item:
        raise ValueError("Prüfung nicht gefunden.")
    clean = _validate_check(fields)
    item.clear()
    item.update(id=check_id, **clean)
    settings_store.set("health_checks", items)
    return item


def remove_check(check_id: str) -> None:
    settings_store.set("health_checks", [c for c in checks() if c["id"] != check_id])
    with _conn() as conn:
        conn.execute("DELETE FROM health_results WHERE check_id = ?", (check_id,))


def _json_value(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise ValueError(f"JSON-Pfad {path} nicht gefunden")
    return data


def _ping(host: str) -> Optional[float]:
    """Round-trip time in ms, None if no answer."""
    flag = ["-W", "2000"] if platform.system() == "Darwin" else ["-W", "2"]
    try:
        res = subprocess.run(["ping", "-c", "1", "-n", *flag, host], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    m = re.search(r"time[=<]([\d.]+)\s*ms", res.stdout)
    return float(m.group(1)) if m else 0.0


def _device_ip(mac: str) -> Optional[str]:
    """Current IP of a LAN device by its MAC (neighbour table, then the last scan)."""
    try:
        import netscan
        for ip, info in netscan._neighbours().items():
            if info.get("mac") == mac:
                return ip
        dev = (settings_store.get("lan_devices", {}) or {}).get(mac)
        return dev.get("ip") if dev else None
    except Exception:
        return None


async def _run_http(check: Dict[str, Any], result: Dict[str, Any], t0: float) -> None:
    await asyncio.to_thread(_local_only, urlparse(check["url"]).hostname)
    async with httpx.AsyncClient(verify=check.get("verify_tls", False), timeout=TIMEOUT,
                                 follow_redirects=check.get("type", "http") != "http") as client:
        res = await client.get(check["url"], headers={"User-Agent": "HostCore-Healthcheck"})
    result.update(status=res.status_code, ms=round((time.time() - t0) * 1000, 1), ok=res.status_code < 400)
    if res.status_code >= 400:
        result["error"] = f"HTTP {res.status_code}"
        return
    ctype = check.get("type", "http")
    if ctype == "keyword":
        found = check["keyword"].lower() in res.text.lower()
        if found == bool(check.get("invert")):
            result.update(ok=False, error=f"„{check['keyword']}“ {'gefunden' if found else 'nicht gefunden'}")
    elif ctype == "json":
        try:
            value = _json_value(res.json(), check["json_path"])
        except (ValueError, json.JSONDecodeError) as e:
            result.update(ok=False, error=str(e) if isinstance(e, ValueError) and "JSON-Pfad" in str(e) else "Keine gültige JSON-Antwort")
            return
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) or value is None else str(value)
        result["value"] = text[:100]
        if check.get("expected") and check["expected"].lower() not in text.lower():
            result.update(ok=False, error=f"{check['json_path']} = {text[:60]} (erwartet: {check['expected']})")


async def run_check(check: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    result: Dict[str, Any] = {"ok": False, "status": None, "ms": None, "error": ""}
    ctype = check.get("type", "http")
    try:
        if ctype in ("http", "keyword", "json"):
            await _run_http(check, result, t0)
        elif ctype == "tcp":
            await asyncio.to_thread(_local_only, check["host"])
            reader, writer = await asyncio.wait_for(asyncio.open_connection(check["host"], check["port"]), TIMEOUT)
            result.update(ok=True, ms=round((time.time() - t0) * 1000, 1))
            writer.close()
        elif ctype == "ping":
            host = (await asyncio.to_thread(_device_ip, check["mac"]) if check.get("mac") else None) or check["host"]
            await asyncio.to_thread(_local_only, host)
            ms = await asyncio.to_thread(_ping, host)
            if ms is None:
                result["error"] = f"Keine Antwort von {host}"
            else:
                result.update(ok=True, ms=round(ms, 1))
        elif ctype == "dns":
            import nettools
            await asyncio.to_thread(_local_only, check["host"])
            ip = (await asyncio.to_thread(socket.getaddrinfo, check["host"], None))[0][4][0]
            ans = await asyncio.to_thread(nettools.dns_lookup, check["query"], "A", ip)
            values = [a["value"] for a in ans["answers"]]
            result["ms"] = ans["ms"]
            if not ans["ok"]:
                result["error"] = f"DNS-Antwort {ans['rcode']}"
            elif check.get("expected") and not any(check["expected"] in v for v in values):
                result["error"] = f"{', '.join(values[:3]) or 'keine Antwort'} (erwartet: {check['expected']})"
            else:
                result["ok"] = True
    except ValueError as e:
        result["error"] = str(e)
    except httpx.ConnectError:
        result["error"] = "Verbindung abgelehnt / nicht erreichbar"
    except (httpx.TimeoutException, asyncio.TimeoutError):
        result["error"] = f"Keine Antwort nach {TIMEOUT} s"
    except httpx.HTTPError as e:
        msg = str(e) or e.__class__.__name__
        result["error"] = "Zertifikat ungültig" if "CERTIFICATE" in msg.upper() else msg[:150]
    except OSError as e:
        result["error"] = "Verbindung abgelehnt" if isinstance(e, ConnectionRefusedError) else (e.strerror or str(e))[:150]
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
            cleanup()


def cleanup() -> None:
    import retention
    with _conn() as conn:
        conn.execute("DELETE FROM health_results WHERE ts < ?", (int(time.time()) - retention.seconds("health"),))


def status(hours: int = 24) -> Dict[str, Any]:
    """Results per check; failures inside a maintenance window don't lower the uptime."""
    import maintenance
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
            spans = maintenance.intervals(f"health:{c['id']}", since, now)
            if spans:
                rows = [r for r in rows if r[1] or not any(a <= r[0] < b for a, b in spans)]
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


def history_detail(check_id: str, hours: int = 24) -> Dict[str, Any]:
    """Response times (bucketed) and incidents (consecutive failures) of one check."""
    import maintenance
    check = next((c for c in checks() if c["id"] == check_id), None)
    if not check:
        raise ValueError("Prüfung nicht gefunden.")
    hours = max(1, min(int(hours), 24 * 30))
    now = int(time.time())
    since = now - hours * 3600
    bucket = max(60, hours * 3600 // 240)
    with _conn() as conn:
        rows = conn.execute("SELECT ts, ok, ms, error FROM health_results WHERE check_id = ? AND ts >= ? ORDER BY ts",
                            (check_id, since)).fetchall()
    points: Dict[int, Dict[str, Any]] = {}
    for ts, ok, ms, _err in rows:
        p = points.setdefault((ts // bucket) * bucket, {"ms": [], "ok": 0, "n": 0})
        p["n"] += 1
        p["ok"] += ok
        if ms is not None:
            p["ms"].append(ms)
    series = [{"ts": b, "avg_ms": round(sum(p["ms"]) / len(p["ms"]), 1) if p["ms"] else None,
               "max_ms": max(p["ms"]) if p["ms"] else None, "up": round(p["ok"] / p["n"], 3)} for b, p in sorted(points.items())]
    incidents: List[Dict[str, Any]] = []
    cur = None
    for ts, ok, _ms, err in rows:
        if not ok and cur is None:
            cur = {"start": ts, "end": ts, "error": err or "", "checks": 1}
        elif not ok:
            cur.update(end=ts, checks=cur["checks"] + 1)
        elif cur is not None:
            cur["end"] = ts
            incidents.append(cur)
            cur = None
    if cur is not None:
        cur["ongoing"] = True
        cur["end"] = now
        incidents.append(cur)
    spans = maintenance.intervals(f"health:{check_id}", since, now)
    for i in incidents:
        i["duration"] = i["end"] - i["start"]
        i["maintenance"] = maintenance.overlap(spans, i["start"], i["end"]) > 0
    ms = [r[2] for r in rows if r[2] is not None]
    return {"check": check, "hours": hours, "bucket": bucket, "series": series, "incidents": list(reversed(incidents))[:50],
            "stats": {"checks": len(rows), "uptime": round(sum(r[1] for r in rows) / len(rows) * 100, 2) if rows else None,
                      "avg_ms": round(sum(ms) / len(ms), 1) if ms else None, "max_ms": max(ms) if ms else None,
                      "min_ms": min(ms) if ms else None}}
