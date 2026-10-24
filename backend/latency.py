"""
Latency between the servers (Flotte -> Latenz): every 5 minutes each server pings every other one
(3 pings, round-trip time and packet loss). The main dashboard runs it: its own pings
directly, the remote servers' pings through their agent (/api/tools/ping-peers). Results live in
history.db (table latency) as long as the metrics (retention "metrics").
"""

import concurrent.futures
import ipaddress
import logging
import platform
import re
import subprocess
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx

import history

logger = logging.getLogger("pi_dashboard.latency")

INTERVAL = 300
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _conn():
    conn = history._connect()
    conn.execute("CREATE TABLE IF NOT EXISTS latency (ts INTEGER, src TEXT, dst TEXT, rtt REAL, loss REAL)")
    conn.execute("CREATE INDEX IF NOT EXISTS latency_idx ON latency (src, dst, ts)")
    return conn


def _valid(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(HOST_RE.match(host or ""))


def ping(host: str, count: int = 3) -> Dict[str, Any]:
    """Round-trip time (ms, average) and packet loss (%) to one host."""
    if not _valid(host):
        return {"host": host, "rtt": None, "loss": 100.0, "error": "Ungültiger Host"}
    wait = ["-W", "1000"] if platform.system() == "Darwin" else ["-W", "1"]
    try:
        res = subprocess.run(["ping", "-n", "-q", "-c", str(count), "-i", "0.2", *wait, host],
                             capture_output=True, text=True, timeout=count * 2 + 4)
    except (OSError, subprocess.TimeoutExpired):
        return {"host": host, "rtt": None, "loss": 100.0, "error": "ping antwortet nicht"}
    out = res.stdout + res.stderr
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", out)
    return {"host": host, "rtt": float(rtt.group(1)) if rtt else None, "loss": float(loss.group(1)) if loss else 100.0,
            "error": "" if rtt else "keine Antwort"}


def ping_many(hosts: List[str]) -> Dict[str, Any]:
    hosts = [h for h in dict.fromkeys(hosts) if h][:20]
    with concurrent.futures.ThreadPoolExecutor(max(1, min(8, len(hosts)))) as pool:
        return {"results": list(pool.map(ping, hosts))}


# ---------------------------------------------------------------------------
# Measuring (main dashboard only)
# ---------------------------------------------------------------------------
def _nodes() -> List[Dict[str, str]]:
    import remotes
    import system_info
    snap = system_info.get_system_snapshot()
    net = snap.get("network_ip") or {}
    own = net.get("primary_ip", "") if isinstance(net, dict) else str(net)
    nodes = [{"id": "", "name": platform.node() or "Dieser Server", "host": own}]
    for r in remotes._all():
        nodes.append({"id": r["id"], "name": r["name"], "host": urlparse(r["url"]).hostname or "", "url": r["url"], "token": r["token"]})
    return [n for n in nodes if n["host"]]


def measure() -> None:
    nodes = _nodes()
    if len(nodes) < 2:
        return
    now = int(time.time())
    rows = []

    def from_node(n: Dict[str, str]) -> List[tuple]:
        targets = [m for m in nodes if m["id"] != n["id"]]
        hosts = [m["host"] for m in targets]
        if n["id"] == "":
            results = ping_many(hosts)["results"]
        else:
            try:
                res = httpx.get(f"{n['url']}/api/tools/ping-peers", params={"targets": ",".join(hosts)},
                                headers={"Authorization": f"Bearer {n['token']}"}, timeout=30)
                if res.status_code != 200:
                    return []   # offline or an older agent without this endpoint
                results = res.json().get("results", [])
            except (httpx.HTTPError, ValueError):
                return []
        by_host = {r["host"]: r for r in results}
        return [(now, n["id"], m["id"], by_host.get(m["host"], {}).get("rtt"), by_host.get(m["host"], {}).get("loss", 100.0))
                for m in targets if m["host"] in by_host]

    with concurrent.futures.ThreadPoolExecutor(min(8, len(nodes))) as pool:
        for part in pool.map(from_node, nodes):
            rows.extend(part)
    if rows:
        with _conn() as conn:
            conn.executemany("INSERT INTO latency (ts, src, dst, rtt, loss) VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute("DELETE FROM latency WHERE ts < ?", (now - history.retention_seconds(),))


def matrix(hours: int = 24) -> Dict[str, Any]:
    nodes = [{k: n[k] for k in ("id", "name", "host")} for n in _nodes()]
    since = int(time.time()) - hours * 3600
    with _conn() as conn:
        rows = conn.execute("""SELECT src, dst, AVG(rtt), MAX(rtt), AVG(loss), COUNT(*), MAX(ts) FROM latency
                               WHERE ts >= ? GROUP BY src, dst""", (since,)).fetchall()
        last_ts = conn.execute("SELECT MAX(ts) FROM latency").fetchone()[0]
        latest = conn.execute("SELECT src, dst, rtt, loss FROM latency WHERE ts = ?", (last_ts or 0,)).fetchall()
        bucket = 3600 if hours <= 48 else 6 * 3600
        series_rows = conn.execute(f"""SELECT src, dst, (ts / {bucket}) * {bucket} AS b, AVG(rtt), AVG(loss) FROM latency
                                       WHERE ts >= ? GROUP BY src, dst, b ORDER BY b""", (since,)).fetchall()
    now_map = {(s, d): {"rtt": r, "loss": l} for s, d, r, l in latest}
    series: Dict[str, List] = {}
    for s, d, b, r, l in series_rows:
        series.setdefault(f"{s}>{d}", []).append([b, None if r is None else round(r, 2), round(l, 1)])
    pairs = []
    for s, d, avg, mx, loss, n, ts in rows:
        cur = now_map.get((s, d), {})
        pairs.append({"src": s, "dst": d, "rtt": cur.get("rtt"), "loss": cur.get("loss"),
                      "avg": None if avg is None else round(avg, 2), "max": None if mx is None else round(mx, 2),
                      "avg_loss": round(loss or 0, 1), "samples": n, "series": series.get(f"{s}>{d}", [])})
    return {"nodes": nodes, "pairs": pairs, "measured": last_ts, "hours": hours, "interval": INTERVAL}


async def background_runner() -> None:
    import asyncio
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(measure)
        except Exception as e:
            logger.warning(f"Latency measurement failed: {e}")
        await asyncio.sleep(INTERVAL)
