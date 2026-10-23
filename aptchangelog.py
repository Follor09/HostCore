"""
What a pending apt update changes (Dashboard -> System-Updates -> Änderungen): the changelog
entries between the installed and the new version (`apt-get changelog`), the CVEs they name,
the highest urgency, which services the package ships (Debian restarts them during the
upgrade) and whether a reboot is needed afterwards (kernel, firmware, libc, systemd).

Fetched on request per package and kept in memory for the version; Raspberry Pi packages
whose source publishes no changelogs say so.
"""

import os
import re
import shutil
import subprocess
import threading
from typing import Any, Dict, List

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,99}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+~:-]{1,100}$")
HEADER_RE = re.compile(r"^(\S+) \(([^)]+)\) ([^;]+);.*?urgency=(\w+)", re.I)
TRAILER_RE = re.compile(r"^ -- (.+?)  (.+)$")
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}")
UNIT_RE = re.compile(r"^/(?:usr/)?lib/systemd/system/([^/]+\.(?:service|socket|timer))$")
REBOOT_RE = re.compile(r"^(linux-image|linux-headers|raspberrypi-kernel|raspi-firmware|raspberrypi-bootloader|rpi-eeprom|firmware-|libc6$|systemd$|dbus)")
URGENCY = ["low", "medium", "high", "emergency", "critical"]
MAX_ENTRIES = 25
MAX_TEXT = 3000
_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class ChangelogError(Exception):
    pass


def _newer(a: str, b: str) -> bool:
    """dpkg version order: a > b"""
    try:
        return subprocess.run(["dpkg", "--compare-versions", a, "gt", b], timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return a != b


def parse(text: str, installed: str, limit: int = MAX_ENTRIES) -> List[Dict[str, Any]]:
    """Changelog entries newer than the installed version (changelogs list the newest first)."""
    entries: List[Dict[str, Any]] = []
    cur = None
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if m:
            if installed and not _newer(m.group(2), installed):
                cur = None
                break
            cur = {"version": m.group(2), "dist": m.group(3).strip(), "urgency": m.group(4).lower(), "lines": [], "by": "", "date": ""}
            entries.append(cur)
            if len(entries) > limit:
                entries.pop()
                break
            continue
        if cur is None:
            continue
        t = TRAILER_RE.match(line)
        if t:
            cur["by"], cur["date"] = t.group(1).strip(), t.group(2).strip()
            cur = None
            continue
        if line.strip():
            cur["lines"].append(line.rstrip())
    for e in entries:
        text_ = "\n".join(e.pop("lines"))
        e["text"] = text_[:MAX_TEXT] + (" …" if len(text_) > MAX_TEXT else "")
        e["cves"] = sorted(set(CVE_RE.findall(text_)))
    return entries


def _units(name: str) -> List[str]:
    try:
        res = subprocess.run(["dpkg", "-L", name], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    units = []
    for line in res.stdout.splitlines():
        m = UNIT_RE.match(line.strip())
        if m and "@" not in m.group(1):
            units.append(m.group(1))
    return sorted(set(units))


def changelog(name: str, old: str, new: str) -> Dict[str, Any]:
    name, old, new = str(name or "").strip(), str(old or "").strip(), str(new or "").strip()
    if not NAME_RE.match(name) or (old and not VERSION_RE.match(old)) or (new and not VERSION_RE.match(new)):
        raise ChangelogError("Ungültiger Paketname oder Version.")
    if not shutil.which("apt-get"):
        raise ChangelogError("Nur auf Debian / Raspberry Pi OS verfügbar.")
    key = f"{name}|{old}|{new}"
    with _lock:
        if key in _cache:
            return _cache[key]
    out: Dict[str, Any] = {"name": name, "old": old, "new": new, "entries": [], "error": "",
                           "units": _units(name), "reboot": bool(REBOOT_RE.match(name))}
    try:
        res = subprocess.run(["apt-get", "changelog", "-qq", name], capture_output=True, text=True, timeout=45,
                             env={**os.environ, "PAGER": "cat", "LC_ALL": "C.UTF-8"})
        if res.returncode != 0 or not res.stdout.strip():
            err = (res.stderr or "").strip().splitlines()
            out["error"] = "Für dieses Paket bietet die Paketquelle keinen Changelog an." if not err or "changelog" in err[-1].lower() \
                else err[-1][:200]
        else:
            out["entries"] = parse(res.stdout, old)
    except subprocess.TimeoutExpired:
        out["error"] = "Changelog-Server antwortet nicht."
    except OSError as e:
        out["error"] = str(e)[:200]
    out["cves"] = sorted({c for e in out["entries"] for c in e["cves"]})
    urg = [e["urgency"] for e in out["entries"] if e["urgency"] in URGENCY]
    out["urgency"] = max(urg, key=URGENCY.index) if urg else ""
    if not out["error"]:
        with _lock:
            if len(_cache) > 500:
                _cache.clear()
            _cache[key] = out
    return out
