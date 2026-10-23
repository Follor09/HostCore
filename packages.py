"""
Package manager for the System page: installed packages (dpkg), search and details
(apt-cache), a dry run before every change (apt-get -s, as the dashboard user) and
install/remove through pi-helper.sh, which runs apt as its own systemd unit
(pi-dashboard-pkg) and refuses to remove anything the system or dashboard needs.
"""

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List

import security

STATE_DIR = "/run/pi-dashboard"
NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,62}(:(arm64|armhf|all))?$")
_cache: Dict[str, Any] = {}
# Same list pi-helper.sh refuses to remove - shown as protected instead of offering it
PROTECTED = re.compile(r"^(sudo|openssh-server|openssh-sftp-server|systemd|systemd-.*|ufw|apt|dpkg|bash|coreutils|libc6|python3|python3-minimal|python3\..*|"
                       r"raspi-firmware|raspberrypi-kernel|raspberrypi-bootloader|linux-image-.*|rpi-eeprom|firmware-.*|network-manager|dhcpcd|dhcpcd5|"
                       r"wpasupplicant|netbase|iproute2|iptables|ipset|git|ca-certificates|docker-ce|docker\.io|containerd|containerd\.io|login|passwd|"
                       r"util-linux|mount|init|init-system-helpers)(:.*)?$")


class PackageError(Exception):
    pass


def supported() -> bool:
    return bool(shutil.which("dpkg-query") and shutil.which("apt-cache"))


def _run(cmd: List[str], timeout: float = 30) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, errors="replace")


def installed() -> Dict[str, Any]:
    if not supported():
        return {"supported": False, "packages": []}
    hit = _cache.get("installed")
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    fmt = "${Package}\t${Version}\t${Installed-Size}\t${db:Status-Abbrev}\t${Priority}\t${Essential}\t${Section}\t${binary:Summary}\n"
    res = _run(["dpkg-query", "-W", "-f", fmt])
    manual = set(_run(["apt-mark", "showmanual"]).stdout.split()) if shutil.which("apt-mark") else set()
    pkgs = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 8 or not parts[3].startswith("ii"):
            continue
        name, version, size, _, prio, ess, section, summary = parts[:8]
        pkgs.append({"name": name, "version": version, "size": int(size or 0) * 1024, "priority": prio,
                     "essential": ess == "yes", "section": section, "summary": summary, "manual": name in manual,
                     "base": ess == "yes" or prio in ("required", "important") or bool(PROTECTED.match(name))})
    pkgs.sort(key=lambda p: -p["size"])
    data = {"supported": True, "packages": pkgs, "count": len(pkgs), "manual": sum(1 for p in pkgs if p["manual"]),
            "total_size": sum(p["size"] for p in pkgs)}
    _cache["installed"] = (time.time(), data)
    return data


def search(query: str) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.\- ]{1,60}", q):
        raise PackageError("Suchbegriff: 2–60 Zeichen (Buchstaben, Zahlen, . + -).")
    res = _run(["apt-cache", "search", "--", q], timeout=30)
    have = {p["name"]: p for p in installed().get("packages", [])}
    hits = []
    for line in res.stdout.splitlines():
        name, _, summary = line.partition(" - ")
        name = name.strip()
        if NAME.match(name):
            hits.append({"name": name, "summary": summary.strip(), "installed": name in have,
                         "version": have[name]["version"] if name in have else ""})
    words = q.split()
    # exact name first, then names containing the term, then description matches
    hits.sort(key=lambda h: (h["name"] != q, not all(w in h["name"] for w in words), len(h["name"]), h["name"]))
    return {"query": q, "results": hits[:80], "total": len(hits)}


def show(name: str) -> Dict[str, Any]:
    if not NAME.match(name or ""):
        raise PackageError("Ungültiger Paketname.")
    res = _run(["apt-cache", "show", "--no-all-versions", name])
    if res.returncode != 0 or not res.stdout.strip():
        raise PackageError(f"Paket {name} nicht gefunden.")
    fields: Dict[str, str] = {}
    key = None
    for line in res.stdout.splitlines():
        if line.startswith(" ") and key:
            fields[key] += "\n" + line[1:]
        elif ":" in line:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    have = {p["name"]: p for p in installed().get("packages", [])}
    desc = fields.get("Description", "")
    return {"name": name, "version": fields.get("Version", ""), "installed": name in have,
            "installed_version": have[name]["version"] if name in have else "",
            "size": int(fields.get("Installed-Size", "0") or 0) * 1024, "download": int(fields.get("Size", "0") or 0),
            "depends": fields.get("Depends", ""), "homepage": fields.get("Homepage", ""), "section": fields.get("Section", ""),
            "summary": desc.split("\n", 1)[0], "description": desc.split("\n", 1)[1].replace("\n.\n", "\n\n") if "\n" in desc else "",
            "base": name in have and have[name]["base"]}


def simulate(action: str, names: List[str]) -> Dict[str, Any]:
    """What apt would do - runs without root (apt-get -s). "upgrade" = only newer versions of
    installed packages (apt-get install --only-upgrade), e.g. from the fleet's update list."""
    if action not in ("install", "remove", "upgrade"):
        raise PackageError("Unbekannte Aktion.")
    names = [n for n in names if n]
    limit = 200 if action == "upgrade" else 20
    if not 1 <= len(names) <= limit or not all(NAME.match(n) for n in names):
        raise PackageError(f"1–{limit} gültige Paketnamen angeben.")
    cmd = ["install", "--only-upgrade"] if action == "upgrade" else [action]
    res = _run(["apt-get", "-s", *cmd, "--", *names], timeout=60)
    out = res.stdout + res.stderr
    lists: Dict[str, List[str]] = {"install": [], "remove": [], "upgrade": []}
    for line in out.splitlines():
        m = re.match(r"^(Inst|Remv) (\S+)(?: \[([^\]]*)\])?(?: \(([^ )]*))?", line)
        if m:
            if m.group(1) == "Remv":
                lists["remove"].append(m.group(2))
            elif m.group(3):
                lists["upgrade"].append(m.group(2))
            else:
                lists["install"].append(m.group(2))
    space = re.search(r"After this operation, ([\d.,]+ [kMG]?B) (of additional disk space will be used|disk space will be freed)", out)
    error = ""
    if res.returncode != 0:
        error = next((l[3:].strip() for l in out.splitlines() if l.startswith("E: ")), "") or "apt meldet einen Fehler."
    base = {p["name"] for p in installed().get("packages", []) if p["base"]}
    return {"action": action, "packages": names, **lists, "error": error,
            "space": space.group(1) if space else "", "frees": bool(space and "freed" in space.group(2)),
            "blocked": sorted(set(lists["remove"]) & base)}


def start(action: str, names: List[str]) -> Dict[str, Any]:
    sim = simulate(action, names)
    if sim["error"]:
        raise PackageError(sim["error"])
    if sim["blocked"]:
        raise PackageError(f"Würde Basis-Pakete entfernen: {', '.join(sim['blocked'][:5])}")
    if not (sim["install"] or sim["remove"] or sim["upgrade"]):
        raise PackageError("Nichts zu tun – schon installiert bzw. nicht installiert.")
    security._helper("apt-pkg", action, *names, timeout=30)
    _cache.pop("installed", None)
    time.sleep(0.5)
    return status()


def status() -> Dict[str, Any]:
    st: Dict[str, Any] = {}
    try:
        with open(os.path.join(STATE_DIR, "pkg.status")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        pass
    log = ""
    try:
        with open(os.path.join(STATE_DIR, "pkg.log"), errors="replace") as f:
            log = f.read()[-60000:]
    except OSError:
        pass
    running = False
    try:
        running = subprocess.run(["systemctl", "is-active", "--quiet", "pi-dashboard-pkg.service"], timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    if st.get("state") in ("done", "failed"):
        _cache.pop("installed", None)
    return {**st, "running": running or st.get("state") == "running", "log": log}
