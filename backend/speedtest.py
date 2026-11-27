"""
Speed test of this server's internet connection against Cloudflare's speed test endpoints
(the ones speed.cloudflare.com uses) - nothing to install, no account.

Ping and jitter from small requests on one kept-open connection; download and upload with
several connections in parallel (one alone rarely fills a fast line), each direction at most
DURATION seconds and CAP_BYTES, the first second (TCP slow start) not counted. With a remote
server selected the agent measures its own line. The last results are kept in settings.json.
"""

import logging
import os
import statistics
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

import settings_store

logger = logging.getLogger("pi_dashboard")

BASE = "https://speed.cloudflare.com"
DURATION = 8.0                 # seconds per direction
RAMP = 1.0                     # first second not counted (TCP slow start)
CAP_BYTES = 250 * 1000 * 1000  # per direction
STREAMS = 4
PING_COUNT = 12
KEEP = 30
KEY = "speedtest_results"
HEADERS = {"User-Agent": "HostCore-Speedtest", "Referer": "https://speed.cloudflare.com/"}

_running = threading.Lock()


class SpeedtestError(Exception):
    pass


class _Meter:
    """Bytes moved by all streams over time; the rate counts from RAMP seconds on."""

    def __init__(self, duration: float, cap: int):
        self.lock = threading.Lock()
        self.start = time.monotonic()
        self.duration, self.cap = duration, cap
        self.total = 0
        self.at_ramp: Optional[tuple] = None
        self.last = (self.start, 0)

    def add(self, n: int) -> None:
        now = time.monotonic()
        with self.lock:
            self.total += n
            if self.at_ramp is None and now - self.start >= RAMP:
                self.at_ramp = (now, self.total)
            self.last = (now, self.total)

    def done(self) -> bool:
        return time.monotonic() - self.start >= self.duration or self.total >= self.cap

    def mbps(self) -> Optional[float]:
        t0, b0 = self.at_ramp or (self.start, 0)
        t1, b1 = self.last
        if t1 - t0 < 0.2 or b1 <= b0:
            t0, b0 = self.start, 0            # short test or slow line: the whole run counts
        if t1 - t0 <= 0 or b1 <= 0:
            return None
        return round((b1 - b0) * 8 / (t1 - t0) / 1e6, 1)


def _parallel(fn: Callable[[], None], streams: int) -> List[BaseException]:
    errors: List[BaseException] = []

    def wrap():
        try:
            fn()
        except BaseException as e:     # noqa: BLE001 - collected, reported once below
            errors.append(e)
    threads = [threading.Thread(target=wrap, daemon=True) for _ in range(streams)]
    [t.start() for t in threads]
    [t.join(DURATION + 30) for t in threads]
    return errors


def _client(transport=None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(15.0), headers=HEADERS, transport=transport, follow_redirects=False)


def _ping(client: httpx.Client, base: str) -> Dict[str, Optional[float]]:
    times = []
    for _ in range(PING_COUNT):
        t = time.monotonic()
        client.get(f"{base}/__down", params={"bytes": 0}).raise_for_status()
        times.append((time.monotonic() - t) * 1000)
    times = times[1:] if len(times) > 2 else times      # the first one includes connecting (TLS)
    jitter = statistics.mean(abs(a - b) for a, b in zip(times, times[1:])) if len(times) > 1 else 0.0
    return {"ping": round(statistics.median(times), 1), "jitter": round(jitter, 1)}


def _download(base: str, transport, duration: float, cap: int, streams: int) -> _Meter:
    meter = _Meter(duration, cap)

    def stream():
        with _client(transport) as c:
            while not meter.done():
                want = min(25_000_000, max(65536, (meter.cap - meter.total) // streams))
                with c.stream("GET", f"{base}/__down", params={"bytes": want}) as r:
                    r.raise_for_status()
                    for chunk in r.iter_bytes(65536):
                        meter.add(len(chunk))
                        if meter.done():
                            break
    errors = _parallel(stream, streams)
    if errors and meter.total == 0:
        raise errors[0]
    return meter


def _upload(base: str, transport, duration: float, cap: int, streams: int) -> _Meter:
    """Bytes count once Cloudflare confirmed them (counting what was handed to the socket would
    measure its buffer, not the line). Each stream starts with 1 MB and doubles while a request
    takes under half a second - fine steps on a slow line, little overhead on a fast one."""
    meter = _Meter(duration, cap)
    block = os.urandom(16 * 1024 * 1024)       # random: nothing on the way can compress it

    def stream():
        size = 1024 * 1024
        with _client(transport) as c:
            while not meter.done():
                n = min(size, max(0, meter.cap - meter.total) // streams or 65536)    # stay near the data limit
                t = time.monotonic()
                c.post(f"{base}/__up", content=block[:n], headers={"Content-Type": "application/octet-stream"}).raise_for_status()
                meter.add(n)
                if time.monotonic() - t < 0.5 and size < len(block):
                    size *= 2
    errors = _parallel(stream, streams)
    if errors and meter.total == 0:
        raise errors[0]
    return meter


def _meta(client: httpx.Client, base: str) -> Dict[str, str]:
    try:
        m = client.get(f"{base}/meta").json()
    except (httpx.HTTPError, ValueError):
        return {}
    colo = m.get("colo")
    # "colo" is the Cloudflare site that answered: {"iata": "FRA", "city": "Frankfurt", …} (older: just "FRA")
    city = str((colo.get("city") if isinstance(colo, dict) else "") or m.get("city") or "")[:40]
    code = str((colo.get("iata") if isinstance(colo, dict) else colo) or "")[:8]
    return {"server": "Cloudflare" + (f" {city}" if city else "") + (f" ({code})" if code else ""),
            "isp": str(m.get("asOrganization") or "")[:80]}


def run(base: str = BASE, transport=None, duration: float = DURATION, cap: int = CAP_BYTES, streams: int = STREAMS) -> Dict[str, Any]:
    """Measure and keep the result. One test at a time."""
    if not _running.acquire(blocking=False):
        raise SpeedtestError("Es läuft schon ein Speedtest.")
    started = time.time()
    try:
        with _client(transport) as c:
            meta = _meta(c, base)
            lat = _ping(c, base)
        down = _download(base, transport, duration, cap, streams)
        up = _upload(base, transport, duration, cap, streams)
    except httpx.HTTPStatusError as e:
        raise SpeedtestError(f"Der Speedtest-Server antwortet mit {e.response.status_code}.")
    except httpx.HTTPError as e:
        raise SpeedtestError(f"Keine Verbindung zum Speedtest-Server ({type(e).__name__}) – ist der Server online?")
    finally:
        _running.release()
    result = {"ts": int(started), "ping": lat["ping"], "jitter": lat["jitter"], "down": down.mbps(), "up": up.mbps(),
              "down_mb": round(down.total / 1e6, 1), "up_mb": round(up.total / 1e6, 1),
              "seconds": round(time.time() - started, 1), **meta}
    settings_store.update(KEY, lambda items: ([result] + list(items or []))[:KEEP], [])
    logger.info(f"Speedtest: {result['down']} Mbit/s down, {result['up']} Mbit/s up, {result['ping']} ms")
    return result


def history() -> Dict[str, Any]:
    items = settings_store.get(KEY, []) or []
    return {"results": items, "running": _running.locked(), "limits": {"seconds": DURATION, "mb": CAP_BYTES // 1_000_000}}
