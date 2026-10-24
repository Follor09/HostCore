"""
Network tools for the system page:

- LAN device scan: ping every address of the Pi's LAN/WLAN subnets (that also fills the
  kernel's neighbour table), then read the neighbour table for IP + MAC. Names come from
  reverse DNS (the router's DHCP names), manufacturers from the IEEE OUI list if installed.
  Seen devices are remembered, so offline ones stay listed; each can get its own name.
- Port check: which of the Pi's listening ports are reachable from LAN / VPN, taking
  the bind address and the ufw rules into account - plus a manual TCP test to any host.
No root needed.
"""

import concurrent.futures
import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

import settings_store
import systools
import wol

MAX_HOSTS = 1024               # never sweep more than a /22
SCAN_WORKERS = 64
_scan_lock = threading.Lock()
_scan_state: Dict[str, Any] = {"running": False, "last": None}

OUI_FILES = ["/usr/share/ieee-data/oui.txt", "/usr/share/misc/oui.txt", "/var/lib/ieee-data/oui.txt"]
# Small fallback for common homelab hardware when no OUI list is installed
OUI_BUILTIN = {
    "B827EB": "Raspberry Pi Foundation", "DCA632": "Raspberry Pi Trading", "E45F01": "Raspberry Pi Trading",
    "D83ADD": "Raspberry Pi Trading", "2CCF67": "Raspberry Pi Trading", "88A29E": "Raspberry Pi Trading",
    "3C2AF4": "Brother", "001132": "Synology", "9009D0": "Synology", "245EBE": "QNAP",
    "3810D5": "AVM (FRITZ!Box)", "C80E14": "AVM (FRITZ!Box)", "E0286D": "AVM (FRITZ!Box)", "2C3AFD": "AVM (FRITZ!Box)",
    "444E6D": "AVM (FRITZ!Box)", "989BCB": "AVM (FRITZ!Box)", "5C4979": "AVM (FRITZ!Box)", "DC396F": "AVM (FRITZ!Box)",
    "F0B014": "AVM (FRITZ!Box)", "7CFF4D": "AVM (FRITZ!Box)", "B0F208": "AVM (FRITZ!Box)",
    "001788": "Philips Hue", "ECB5FA": "Philips Hue", "18B430": "Nest", "F4F5D8": "Google", "F4F5E8": "Google",
    "44650D": "Amazon", "FCA667": "Amazon", "B0BE76": "TP-Link", "50C7BF": "TP-Link", "98DAC4": "TP-Link",
    "74DA38": "Edimax", "001A11": "Google",
    "00E04C": "Realtek", "000C29": "VMware", "080027": "VirtualBox", "525400": "QEMU/KVM",
    "24A160": "Espressif (ESP32/ESP8266)", "240AC4": "Espressif (ESP32/ESP8266)", "84CCA8": "Espressif (ESP32/ESP8266)",
    "E868E7": "Espressif (ESP32/ESP8266)", "C44F33": "Espressif (ESP32/ESP8266)", "3C71BF": "Espressif (ESP32/ESP8266)",
    "5CCF7F": "Espressif (ESP32/ESP8266)", "A020A6": "Espressif (ESP32/ESP8266)",
}
_oui_cache: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# LAN scan
# ---------------------------------------------------------------------------
def local_subnets() -> List[Dict[str, Any]]:
    """IPv4 subnets of the LAN/WLAN interfaces that are up (no VPN, containers, loopback)."""
    stats = psutil.net_if_stats()
    nets = []
    for name, addrs in psutil.net_if_addrs().items():
        if systools._iface_kind(name) not in ("ethernet", "wlan") or not (stats.get(name) and stats[name].isup):
            continue
        for a in addrs:
            if a.family != socket.AF_INET or not a.netmask or a.address.startswith("169.254."):
                continue
            net = ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False)
            if not net.is_private:
                continue
            if net.num_addresses > MAX_HOSTS:  # large network: only the /22 around our own address
                net = ipaddress.ip_network(f"{a.address}/22", strict=False)
            if str(net) not in {n["network"] for n in nets}:  # LAN + WLAN in the same network: sweep once
                nets.append({"iface": name, "network": str(net), "own_ip": a.address})
    return nets


def _ping(ip: str) -> bool:
    flag = ["-W", "1000"] if platform.system() == "Darwin" else ["-W", "1"]
    try:
        return subprocess.run(["ping", "-c", "1", "-n", *flag, ip], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _neighbours() -> Dict[str, Dict[str, str]]:
    """ip -> {mac, state} from the kernel neighbour table."""
    out: Dict[str, Dict[str, str]] = {}
    if shutil.which("ip"):
        text = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=5).stdout
        for line in text.splitlines():
            m = re.match(r"^(\S+) dev (\S+)(?: lladdr (\S+))?.*\s(\S+)$", line.strip())
            if m and m.group(3) and m.group(4) not in ("FAILED", "INCOMPLETE"):
                out[m.group(1)] = {"mac": m.group(3).lower(), "iface": m.group(2), "state": m.group(4)}
    elif shutil.which("arp"):
        text = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=5).stdout
        for line in text.splitlines():
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+) on (\S+)", line, re.I)
            if m:
                mac = ":".join(p.zfill(2) for p in m.group(2).lower().split(":"))
                out[m.group(1)] = {"mac": mac, "iface": m.group(3), "state": "REACHABLE"}
    return out


def _load_oui() -> Dict[str, str]:
    global _oui_cache
    if _oui_cache is None:
        _oui_cache = dict(OUI_BUILTIN)
        for path in OUI_FILES:
            if os.path.exists(path):
                try:
                    with open(path, errors="replace") as f:
                        for line in f:
                            m = re.match(r"^([0-9A-F]{6})\s+\(base 16\)\s+(.+)$", line.strip())
                            if m:
                                _oui_cache[m.group(1)] = m.group(2).strip()
                except OSError:
                    pass
                break
    return _oui_cache


def vendor(mac: str) -> str:
    hexmac = mac.replace(":", "").upper()
    if len(hexmac) < 6:
        return ""
    if int(hexmac[1], 16) & 0x2:
        return "Private Adresse (zufällige MAC)"  # locally administered: phones/laptops hide their MAC
    return _load_oui().get(hexmac[:6], "")


def _reverse_dns(ip: str) -> str:
    try:
        name = socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return ""
    return "" if name == ip else name


def scan() -> Dict[str, Any]:
    """Sweep the LAN, merge with remembered devices, return the device list."""
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scan läuft bereits.")
    _scan_state["running"] = True
    started = time.time()
    try:
        subnets = local_subnets()
        if not subnets:
            raise RuntimeError("Kein LAN/WLAN mit IPv4-Adresse gefunden.")
        hosts = [str(h) for s in subnets for h in ipaddress.ip_network(s["network"]).hosts()]
        own = {s["own_ip"] for s in subnets}
        with concurrent.futures.ThreadPoolExecutor(SCAN_WORKERS) as pool:
            answered = {ip for ip, ok in zip(hosts, pool.map(_ping, hosts)) if ok}
        neigh = _neighbours()
        in_scope = [ipaddress.ip_network(s["network"]) for s in subnets]
        found = {ip: n for ip, n in neigh.items()
                 if any(ipaddress.ip_address(ip) in net for net in in_scope) and n["mac"] not in ("ff:ff:ff:ff:ff:ff",)}
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            names = dict(zip(found, pool.map(_reverse_dns, found)))

        try:
            watch_ip_owners(found)
        except Exception:
            pass
        now = int(time.time())
        known = settings_store.get("lan_devices", {}) or {}
        for ip, n in found.items():
            mac = n["mac"]
            dev = known.get(mac, {"first_seen": now})
            dev.update(mac=mac, ip=ip, last_seen=now, hostname=names.get(ip) or dev.get("hostname", ""),
                       vendor=vendor(mac), pinged=ip in answered)
            known[mac] = dev
        # Own interfaces are not in the neighbour table
        for s in subnets:
            mac = next((a.address.lower() for a in psutil.net_if_addrs().get(s["iface"], [])
                        if a.family == psutil.AF_LINK), "")
            if mac:
                dev = known.get(mac, {"first_seen": now})
                dev.update(mac=mac, ip=s["own_ip"], last_seen=now, hostname=socket.gethostname(),
                           vendor=vendor(mac), self=True)
                known[mac] = dev
        settings_store.set("lan_devices", known)
        _scan_state["last"] = {"ts": now, "duration": round(time.time() - started, 1), "online": len(found) + len(own),
                               "subnets": [s["network"] for s in subnets]}
        return devices()
    finally:
        _scan_state["running"] = False
        _scan_lock.release()


def devices() -> Dict[str, Any]:
    known = settings_store.get("lan_devices", {}) or {}
    last = _scan_state["last"]
    wol_macs = {d["mac"].lower() for d in wol.list_devices()}
    # Devices watched by a ping check (healthchecks, type "ping" with this MAC)
    monitors: Dict[str, Dict[str, Any]] = {}
    try:
        import healthchecks
        if any(c.get("mac") for c in healthchecks.checks()):
            for c in healthchecks.status(24)["checks"]:
                if c.get("mac"):
                    monitors[c["mac"]] = {"id": c["id"], "ok": bool(c["last"] and c["last"]["ok"]) if c.get("last") else None,
                                          "uptime": c.get("uptime"), "ms": c["last"]["ms"] if c.get("last") else None}
    except Exception:
        pass
    items = []
    for mac, d in known.items():
        online = bool(last) and d.get("last_seen", 0) >= last["ts"]
        items.append({**d, "online": online, "in_wol": mac in wol_macs, "monitor": monitors.get(mac),
                      "new": bool(last) and d.get("first_seen", 0) >= last["ts"] and len(known) > 1})
    items.sort(key=lambda d: (not d["online"], tuple(int(x) for x in d.get("ip", "0.0.0.0").split("."))))
    return {"devices": items, "last_scan": last, "running": _scan_state["running"], "subnets": [s["network"] for s in local_subnets()],
            "ip_events": ip_events(30)[:50]}


def rename(mac: str, alias: str) -> Dict[str, Any]:
    mac = wol.normalize_mac(mac).lower()
    alias = alias.strip()[:40]
    known = settings_store.get("lan_devices", {}) or {}
    if mac not in known:
        raise ValueError("Gerät nicht gefunden.")
    known[mac]["alias"] = alias
    settings_store.set("lan_devices", known)
    return devices()


def forget(mac: str) -> Dict[str, Any]:
    mac = wol.normalize_mac(mac).lower()
    known = settings_store.get("lan_devices", {}) or {}
    known.pop(mac, None)
    settings_store.set("lan_devices", known)
    return devices()


# ---------------------------------------------------------------------------
# Port reachability
# ---------------------------------------------------------------------------
def _source_scope(source: str, lans: List[ipaddress.IPv4Network]) -> str:
    src = source.replace(" (v6)", "").strip()
    if src.startswith("Anywhere") or not src:
        return "all"
    try:
        net = ipaddress.ip_network(src, strict=False)
    except ValueError:
        return "other"
    if net.version == 4 and net.subnet_of(ipaddress.ip_network("100.64.0.0/10")):
        return "vpn"  # NetBird / Tailscale range
    # Only the Pi's own LAN counts, not e.g. the k3s pod network 10.42.0.0/16
    return "lan" if any(net.version == lan.version and net.overlaps(lan) for lan in lans) else "other"


def _rule_covers(rule: Dict[str, Any], port: int, proto: str, lans: Optional[List] = None) -> Optional[str]:
    """Scope ('all' / 'lan' / 'vpn') an ufw allow/limit rule opens this port for, else None."""
    if rule.get("action") not in ("allow", "limit") or rule.get("direction", "in") != "in":
        return None
    to = rule.get("to", "").replace(" (v6)", "")
    scope = _source_scope(rule.get("from", ""), lans or [])
    if "on wt0" in to:
        return "vpn"             # everything arriving over the VPN interface
    if to.startswith("Anywhere"):
        return scope             # all ports, from that source
    m = re.match(r"^(\d+)(?::(\d+))?(?:/(tcp|udp))?", to)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if not lo <= port <= hi or (m.group(3) and m.group(3) != proto):
        return None
    return scope


def port_reachability() -> Dict[str, Any]:
    import security
    listening = systools._listening_ports()
    fw = security.firewall_status()
    fw_active = bool(fw.get("installed") and fw.get("active"))
    rules = fw.get("rules", []) if fw_active else []
    lans = [ipaddress.ip_network(s["network"]) for s in local_subnets()]
    docker_ports = set()
    if shutil.which("docker"):
        try:
            out = subprocess.run(["docker", "ps", "--format", "{{.Ports}}"], capture_output=True, text=True, timeout=5).stdout
            docker_ports = {int(p) for p in re.findall(r"(?:0\.0\.0\.0|\[::\]|:::?):(\d+)->", out)}
        except (OSError, subprocess.SubprocessError):
            pass

    rows = []
    for p in listening["ports"]:
        proto = p["proto"].lower()
        local_only = p["address"].startswith("127.") or p["address"] == "::1"
        if local_only:
            lan = vpn = "local"
        elif not fw_active or p["port"] in docker_ports:
            lan = vpn = "open"
        else:
            covers = {_rule_covers(r, p["port"], proto, lans) for r in rules}
            lan = "open" if covers & {"all", "lan"} else "blocked"
            vpn = "open" if covers & {"all", "vpn"} else "blocked"
        rows.append({**p, "lan": lan, "vpn": vpn, "docker": p["port"] in docker_ports})
    return {"ports": rows, "firewall": "active" if fw_active else ("inactive" if fw.get("installed") else "missing"),
            "hint": listening.get("hint", "")}


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9A-Fa-f:]{2,39}$")


def parse_ports(text: str) -> List[int]:
    ports: List[int] = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", part)
        if not m:
            raise ValueError(f"Ungültiger Port: {part}")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not (1 <= lo <= hi <= 65535):
            raise ValueError(f"Ungültiger Port: {part}")
        ports.extend(range(lo, hi + 1))
        if len(ports) > 100:
            raise ValueError("Höchstens 100 Ports auf einmal.")
    if not ports:
        raise ValueError("Mindestens einen Port angeben.")
    return sorted(set(ports))


def _probe(host: str, port: int) -> Dict[str, Any]:
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=2) as s:
            ms = round((time.time() - t0) * 1000, 1)
            banner = ""
            s.settimeout(0.8)
            try:
                banner = s.recv(120).decode("utf-8", errors="replace").strip().splitlines()[0][:100]
            except (OSError, IndexError):
                pass
            return {"port": port, "state": "open", "ms": ms, "banner": banner, "service": _service(port)}
    except socket.timeout:
        return {"port": port, "state": "filtered", "service": _service(port)}
    except ConnectionRefusedError:
        return {"port": port, "state": "closed", "service": _service(port)}
    except OSError as e:
        return {"port": port, "state": "error", "error": str(e)[:80], "service": _service(port)}


def _service(port: int) -> str:
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


def test_ports(host: str, ports_text: str) -> Dict[str, Any]:
    host = host.strip()
    if not HOST_RE.match(host):
        raise ValueError("Ungültiger Hostname oder IP-Adresse.")
    ports = parse_ports(ports_text)
    try:
        ip = socket.getaddrinfo(host, None)[0][4][0]
    except OSError:
        raise ValueError(f"„{host}“ lässt sich nicht auflösen.")
    with concurrent.futures.ThreadPoolExecutor(min(32, len(ports))) as pool:
        results = list(pool.map(lambda p: _probe(ip, p), ports))
    return {"host": host, "ip": ip, "results": results}


# ---------------------------------------------------------------------------
# IP conflicts & MAC changes: an address that suddenly belongs to another device. Checked on
# every scan and every 5 minutes from the kernel's neighbour table (no sweep needed).
# ---------------------------------------------------------------------------
IP_EVENTS_KEY = "lan_ip_events"
IP_WATCH_KEY = "lan_ip_owner"      # ip -> {mac, ts}: who answered for the address last
FLAP_WINDOW = 24 * 3600


def _dev_name(known: Dict[str, Any], mac: str) -> str:
    d = known.get(mac) or {}
    return d.get("alias") or d.get("hostname") or d.get("vendor") or mac


def watch_ip_owners(found: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Compare who answers for each LAN address with the last time; returns new events."""
    if found is None:
        try:
            subnets = local_subnets()
        except Exception:
            return []
        nets = [ipaddress.ip_network(s["network"]) for s in subnets]
        found = {ip: n for ip, n in _neighbours().items()
                 if any(ipaddress.ip_address(ip) in net for net in nets) and n["mac"] != "ff:ff:ff:ff:ff:ff"}
    now = int(time.time())
    owners = settings_store.get(IP_WATCH_KEY, {}) or {}
    events = settings_store.get(IP_EVENTS_KEY, []) or []
    known = settings_store.get("lan_devices", {}) or {}
    new = []
    for ip, n in found.items():
        mac = n["mac"]
        prev = owners.get(ip)
        if prev and prev["mac"] != mac and now - prev["ts"] < 7 * 86400:
            # The same two devices taking turns on one address within a day: a conflict, not a replacement
            flapping = any(e["ip"] == ip and now - e["ts"] < FLAP_WINDOW and {e["old_mac"], e["new_mac"]} == {mac, prev["mac"]}
                           for e in events)
            ev = {"ts": now, "ip": ip, "old_mac": prev["mac"], "new_mac": mac, "kind": "conflict" if flapping else "change",
                  "old_name": _dev_name(known, prev["mac"]), "new_name": _dev_name(known, mac)}
            events.append(ev)
            new.append(ev)
        owners[ip] = {"mac": mac, "ts": now}
    if new:
        settings_store.set(IP_EVENTS_KEY, events[-300:])
    settings_store.set(IP_WATCH_KEY, {ip: o for ip, o in owners.items() if now - o["ts"] < 30 * 86400})
    return new


def ip_events(days: int = 30) -> List[Dict[str, Any]]:
    since = time.time() - days * 86400
    return [e for e in reversed(settings_store.get(IP_EVENTS_KEY, []) or []) if e["ts"] >= since]
