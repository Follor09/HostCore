"""
Speed test of this server's internet connection against Cloudflare's speed test endpoints
(the ones speed.cloudflare.com uses) - nothing to install, no account.

Ping and jitter from small requests on one kept-open connection; download and upload with
several connections in parallel (one alone rarely fills a fast line), each direction at most
DURATION seconds and CAP_BYTES, the first second (TCP slow start) not counted. With a remote
server selected the agent measures its own line. The last results are kept in settings.json.
"""

import contextlib
import fcntl
import json
import logging
import os
import statistics
import sys
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

_running = threading.Lock()                        # within one process
LOCK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".speedtest.lock")   # across processes (tasks)


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


@contextlib.contextmanager
def _exclusive():
    """One test at a time on this server - also between the dashboard and a task (own process)."""
    if not _running.acquire(blocking=False):
        raise SpeedtestError("Es läuft schon ein Speedtest.")
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SpeedtestError("Es läuft schon ein Speedtest.")
        yield
    finally:
        os.close(fd)                 # also releases the lock
        _running.release()


def is_running() -> bool:
    if _running.locked():
        return True
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def run(base: str = BASE, transport=None, duration: float = DURATION, cap: int = CAP_BYTES, streams: int = STREAMS,
        say: Callable[[str], None] = lambda text: None) -> Dict[str, Any]:
    """Measure and keep the result. One test at a time. say: progress lines (the task log)."""
    started = time.time()
    with _exclusive():
        return _measure(base, transport, duration, cap, streams, say, started)


def _measure(base, transport, duration, cap, streams, say, started) -> Dict[str, Any]:
    try:
        with _client(transport) as c:
            meta = _meta(c, base)
            if meta.get("server"):
                say(f"   Gegenstelle: {meta['server']}" + (f" · {meta['isp']}" if meta.get("isp") else ""))
            say("▶ Ping")
            lat = _ping(c, base)
            say(f"   {lat['ping']} ms, Jitter {lat['jitter']} ms")
        say(f"▶ Download ({streams} Verbindungen, höchstens {duration:g} s / {cap // 1_000_000} MB)")
        down = _download(base, transport, duration, cap, streams)
        say(f"   {down.mbps()} Mbit/s ({down.total / 1e6:.0f} MB)")
        say("▶ Upload")
        up = _upload(base, transport, duration, cap, streams)
        say(f"   {up.mbps()} Mbit/s ({up.total / 1e6:.0f} MB)")
    except httpx.HTTPStatusError as e:
        raise SpeedtestError(f"Der Speedtest-Server antwortet mit {e.response.status_code}.")
    except httpx.HTTPError as e:
        raise SpeedtestError(f"Keine Verbindung zum Speedtest-Server ({type(e).__name__}) – ist der Server online?")
    result = {"ts": int(started), "ping": lat["ping"], "jitter": lat["jitter"], "down": down.mbps(), "up": up.mbps(),
              "down_mb": round(down.total / 1e6, 1), "up_mb": round(up.total / 1e6, 1),
              "seconds": round(time.time() - started, 1), **meta}
    settings_store.update(KEY, lambda items: ([result] + list(items or []))[:KEEP], [])
    logger.info(f"Speedtest: {result['down']} Mbit/s down, {result['up']} Mbit/s up, {result['ping']} ms")
    return result


def history() -> Dict[str, Any]:
    items = settings_store.get(KEY, []) or []
    return {"results": items, "running": is_running(), "limits": {"seconds": DURATION, "mb": CAP_BYTES // 1_000_000}}


# ---------------------------------------------------------------------------
# As a task (tasks.py runs this file): progress into the task log
# ---------------------------------------------------------------------------
def _say(text: str) -> None:
    print(text, flush=True)


def _summary(r: Dict[str, Any]) -> str:
    return f"↓ {r.get('down')} Mbit/s · ↑ {r.get('up')} Mbit/s · Ping {r.get('ping')} ms · Jitter {r.get('jitter')} ms"


def _remote_run(rem: Dict[str, Any], transport=None) -> Dict[str, Any]:
    """Ask a remote server's agent to measure its own line right away (it waits for the result)."""
    import remotes
    with httpx.Client(timeout=httpx.Timeout(180.0), verify=remotes.verify_for(rem), transport=transport) as c:
        res = c.post(f"{rem['url']}/api/tools/speedtest", json={"wait": True},
                     headers={"Authorization": f"Bearer {rem['token']}", "X-PiCore-User": os.environ.get("HC_TASK_USER", "")})
    if res.status_code == 404:
        raise SpeedtestError("Agent zu alt – dort HostCore aktualisieren.")
    data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
    if res.status_code != 200:
        raise SpeedtestError(data.get("detail") or f"Antwort {res.status_code}")
    return data.get("result") or {}


def _fleet(ids: List[str]) -> int:
    """Measure on several servers, one after the other: this one in this process, remote servers
    through their agents (each measures its own line). Exit code 1 if one failed."""
    import platform
    import remotes
    known = {r["id"]: r for r in remotes.all_remotes()}
    failed = 0
    for sid in ids:
        name = (platform.node() or "Dieser Server") if sid == "local" else (known.get(sid) or {}).get("name", sid)
        _say(f"━━ {name}")
        try:
            if sid == "local":
                r = run(say=_say)
            elif sid in known:
                r = _remote_run(known[sid])
            else:
                raise SpeedtestError("Unbekannter Server.")
            _say(f"✅ {name}: {_summary(r)}")
        except (SpeedtestError, httpx.HTTPError, ValueError) as e:
            failed += 1
            _say(f"❌ {name}: {e if isinstance(e, SpeedtestError) else f'nicht erreichbar ({type(e).__name__})'}")
    return 1 if failed else 0


def main(argv: List[str]) -> int:
    if argv[:1] == ["run"]:
        try:
            r = run(say=_say)
        except SpeedtestError as e:
            _say(f"❌ {e}")
            return 1
        _say(f"✅ {_summary(r)}")
        _say("HC-SPEEDTEST " + json.dumps(r))
        return 0
    if argv[:1] == ["fleet"] and argv[1:]:
        return _fleet(argv[1:])
    _say("usage: speedtest.py run | fleet <server id> …")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
