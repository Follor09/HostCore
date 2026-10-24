"""
IP -> country, fully offline: the free "IP to Country Lite" database from DB-IP
(CC BY 4.0, https://db-ip.com), downloaded once and refreshed monthly, stored in
SQLite as 16-byte range keys (IPv4 as ::ffff:a.b.c.d), so a lookup is one index seek
and nothing big sits in memory.
"""

import csv
import gzip
import io
import ipaddress
import logging
import os
import sqlite3
import threading
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("pi_dashboard.geoip")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "geoip.db")
URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"
REFRESH_DAYS = 32
ATTRIBUTION = {"text": "IP Geolocation by DB-IP", "url": "https://db-ip.com"}

_state: Dict[str, Any] = {"running": False, "error": "", "progress": 0}
on_update: List[Any] = []   # called after a successful (re)load, e.g. to refresh country blocks
_lock = threading.Lock()


def _key(addr) -> bytes:
    if addr.version == 4:
        return b"\x00" * 10 + b"\xff\xff" + addr.packed
    return addr.packed


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


def installed() -> bool:
    # An empty file (left behind by an aborted copy or a stray sqlite3.connect) is no database
    try:
        return os.path.getsize(DB_PATH) > 0
    except OSError:
        return False


def _meta() -> Dict[str, str]:
    if not installed():
        return {}
    try:
        with _connect() as conn:
            return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return {}


def info() -> Dict[str, Any]:
    meta = _meta()
    return {"installed": bool(meta), "month": meta.get("month", ""), "rows": int(meta.get("rows", 0) or 0),
            "updated": int(float(meta.get("updated", 0) or 0)),
            "size": os.path.getsize(DB_PATH) if installed() else 0,
            "running": _state["running"], "progress": _state["progress"], "error": _state["error"],
            "attribution": ATTRIBUTION}


@lru_cache(maxsize=4096)
def lookup(ip: str) -> str:
    """Two-letter country code, "" for private/unknown addresses or without database."""
    if not installed():
        return ""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return ""
    if not addr.is_global:
        return ""
    k = _key(addr)
    try:
        with _connect() as conn:
            row = conn.execute("SELECT end, cc FROM ranges WHERE start <= ? ORDER BY start DESC LIMIT 1", (k,)).fetchone()
    except sqlite3.Error:
        return ""
    return row[1] if row and row[0] >= k and row[1] != "ZZ" else ""


def lookup_many(ips) -> Dict[str, str]:
    return {ip: lookup(ip) for ip in set(ips) if ip}


# ---------------------------------------------------------------------------
# Download / refresh
# ---------------------------------------------------------------------------
def _months() -> List[str]:
    t = time.localtime()
    y, m = t.tm_year, t.tm_mon
    prev = (y, m - 1) if m > 1 else (y - 1, 12)
    return [f"{y:04d}-{m:02d}", f"{prev[0]:04d}-{prev[1]:02d}"]


def _download() -> None:
    tmp = DB_PATH + ".tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        data, month = None, ""
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            for m in _months():
                res = client.get(URL.format(month=m))
                if res.status_code == 200:
                    data, month = res.content, m
                    break
        if not data:
            raise RuntimeError("Datenbank nicht gefunden (DB-IP).")
        _state["progress"] = 30
        conn = sqlite3.connect(tmp)
        conn.execute("CREATE TABLE ranges (start BLOB PRIMARY KEY, end BLOB NOT NULL, cc TEXT NOT NULL) WITHOUT ROWID")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        rows, batch = 0, []
        reader = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(data)), encoding="ascii", errors="replace"))
        for rec in reader:
            if len(rec) < 3:
                continue
            try:
                start, end = ipaddress.ip_address(rec[0]), ipaddress.ip_address(rec[1])
            except ValueError:
                continue
            batch.append((_key(start), _key(end), rec[2][:2].upper()))
            if len(batch) >= 20000:
                conn.executemany("INSERT OR REPLACE INTO ranges VALUES (?,?,?)", batch)
                rows += len(batch)
                batch = []
                _state["progress"] = min(95, 30 + rows // 12000)
        conn.executemany("INSERT OR REPLACE INTO ranges VALUES (?,?,?)", batch)
        rows += len(batch)
        if rows < 100000:
            raise RuntimeError("Datenbank unvollständig.")
        conn.executemany("INSERT INTO meta VALUES (?,?)", [("month", month), ("rows", str(rows)), ("updated", str(time.time()))])
        conn.commit()
        conn.close()
        os.replace(tmp, DB_PATH)
        lookup.cache_clear()
        _countries_cache.clear()
        _state.update(error="", progress=100)
        logger.info(f"GeoIP database {month} loaded ({rows} ranges)")
        for fn in on_update:
            try:
                fn()
            except Exception as e:
                logger.warning(f"GeoIP update hook failed: {e}")
    except Exception as e:
        _state["error"] = str(e)[:200]
        logger.warning(f"GeoIP download failed: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
    finally:
        _state["running"] = False


def start_download() -> Dict[str, Any]:
    with _lock:
        if not _state["running"]:
            _state.update(running=True, error="", progress=0)
            threading.Thread(target=_download, daemon=True).start()
    return info()


def remove() -> Dict[str, Any]:
    if _state["running"]:
        raise RuntimeError("Download läuft gerade.")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    lookup.cache_clear()
    _countries_cache.clear()
    return info()


def refresh_if_old() -> None:
    """Called once a day: keep an installed database current (DB-IP publishes monthly)."""
    meta = _meta()
    if meta and time.time() - float(meta.get("updated", 0) or 0) > REFRESH_DAYS * 86400 and not _state["running"]:
        start_download()


# ---------------------------------------------------------------------------
# Countries -> networks (for geo-blocking)
# ---------------------------------------------------------------------------
_countries_cache: Dict[str, Any] = {}


def countries() -> List[Dict[str, Any]]:
    """All country codes in the database with their number of ranges."""
    if "list" in _countries_cache:
        return _countries_cache["list"]
    if not installed():
        return []
    with _connect() as conn:
        rows = conn.execute("SELECT cc, COUNT(*) FROM ranges WHERE cc != 'ZZ' GROUP BY cc ORDER BY cc").fetchall()
    _countries_cache["list"] = [{"cc": cc, "ranges": n} for cc, n in rows]
    return _countries_cache["list"]


def _addr(key: bytes):
    if key[:12] == b"\x00" * 10 + b"\xff\xff":
        return ipaddress.IPv4Address(key[12:])
    return ipaddress.IPv6Address(key)


def networks(ccs: List[str]) -> Dict[str, List[str]]:
    """CIDR lists (v4, v6) covering the given countries - adjacent ranges merged."""
    ccs = [c for c in ccs if len(c) == 2 and c.isalpha()]
    if not ccs or not installed():
        return {"v4": [], "v6": []}
    with _connect() as conn:
        q = ",".join("?" * len(ccs))
        rows = conn.execute(f"SELECT start, end FROM ranges WHERE cc IN ({q}) ORDER BY start", [c.upper() for c in ccs]).fetchall()
    v4: List[str] = []
    v6: List[str] = []
    cur_s = cur_e = None
    merged = []
    for s, e in rows:
        a, b = _addr(s), _addr(e)
        if cur_s is not None and a.version == cur_e.version and int(a) == int(cur_e) + 1:
            cur_e = b
            continue
        if cur_s is not None:
            merged.append((cur_s, cur_e))
        cur_s, cur_e = a, b
    if cur_s is not None:
        merged.append((cur_s, cur_e))
    for a, b in merged:
        for net in ipaddress.summarize_address_range(a, b):
            (v4 if net.version == 4 else v6).append(str(net))
    return {"v4": v4, "v6": v6}


def country_of_networks(ip: str, ccs: List[str]) -> Optional[str]:
    cc = lookup(ip)
    return cc if cc and cc in ccs else None
