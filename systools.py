"""
System tools page: network details, hardware info (and more tools added over time).
Everything here is read on demand when the page asks, nothing runs in the background.
"""

import glob
import os
import platform
import re
import shutil
import socket
import subprocess
from typing import Any, Dict, List, Optional

import psutil

IS_LINUX = platform.system() == "Linux"


def _read(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip().strip("\x00")
    except OSError:
        return ""


def _run(cmd: List[str], timeout: float = 5) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace").stdout
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def _iface_kind(name: str) -> str:
    if name in ("lo", "lo0"):
        return "loopback"
    if name.startswith(("wlan", "wlp", "wl")):
        return "wlan"
    if name.startswith(("eth", "enp", "eno", "ens", "enx", "end", "en")):
        return "ethernet"
    if name.startswith(("wt", "tun", "tap", "wg", "tailscale", "utun", "zt")):
        return "vpn"
    if name.startswith(("docker", "br-", "veth", "cni", "flannel", "cali", "kube", "virbr", "bridge")):
        return "container"
    return "other"


def _wlan_info(name: str) -> Dict[str, Any]:
    out = _run(["iw", "dev", name, "link"])
    info: Dict[str, Any] = {}
    if out and "Not connected" not in out:
        for key, pattern in (("ssid", r"SSID: (.+)"), ("signal_dbm", r"signal: (-?\d+) dBm"),
                             ("freq_mhz", r"freq: ([\d.]+)"), ("bitrate", r"tx bitrate: ([\d.]+ \S+)")):
            m = re.search(pattern, out)
            if m:
                info[key] = m.group(1).strip()
        if "signal_dbm" in info:
            info["signal_dbm"] = int(info["signal_dbm"])
        if "freq_mhz" in info:
            info["band"] = "5 GHz" if float(info["freq_mhz"]) > 4000 else "2,4 GHz"
    elif IS_LINUX:
        # Fallback without iw: /proc/net/wireless has the signal level
        for line in _read("/proc/net/wireless").splitlines():
            if line.strip().startswith(f"{name}:"):
                parts = line.split()
                try:
                    info["signal_dbm"] = int(float(parts[3].rstrip(".")))
                except (IndexError, ValueError):
                    pass
    return info


def _gateway() -> str:
    if IS_LINUX:
        m = re.search(r"default via (\S+)", _run(["ip", "route", "show", "default"]))
    else:
        m = re.search(r"gateway:\s*(\S+)", _run(["route", "-n", "get", "default"]))
    return m.group(1) if m else ""


def _dns_servers() -> List[str]:
    servers = re.findall(r"^nameserver\s+(\S+)", _read("/etc/resolv.conf"), re.MULTILINE)
    if servers == ["127.0.0.53"]:
        # systemd-resolved stub: ask resolvectl for the real upstream servers
        real = re.findall(r"(?:DNS Servers?|Current DNS Server):\s*(.+)", _run(["resolvectl", "dns"]) + _run(["resolvectl", "status"]))
        found = sorted({s for line in real for s in line.split()})
        return found or servers
    return servers


def _listening_ports() -> Dict[str, Any]:
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return {"ports": [], "hint": "Offene Ports konnten ohne Root-Rechte nicht gelesen werden."}
    seen, ports = set(), []
    for c in conns:
        is_tcp = c.type == socket.SOCK_STREAM
        if (is_tcp and c.status != psutil.CONN_LISTEN) or not c.laddr:
            continue
        if not is_tcp and c.raddr:
            continue
        key = (is_tcp, c.laddr.ip, c.laddr.port)
        if key in seen:
            continue
        seen.add(key)
        proc = ""
        if c.pid:
            try:
                proc = psutil.Process(c.pid).name()
            except psutil.Error:
                pass
        try:
            service = socket.getservbyport(c.laddr.port, "tcp" if is_tcp else "udp")
        except OSError:
            service = ""
        ports.append({"proto": "TCP" if is_tcp else "UDP", "address": c.laddr.ip, "port": c.laddr.port,
                      "process": proc, "service": service,
                      "public": c.laddr.ip in ("0.0.0.0", "::", "*")})
    ports.sort(key=lambda p: (p["port"], p["proto"]))
    hidden = "" if all(p["process"] for p in ports) else \
        "Prozessnamen anderer Benutzer (z.B. root) sind ohne Root-Rechte nicht sichtbar."
    return {"ports": ports, "hint": hidden}


def network_details() -> Dict[str, Any]:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    counters = psutil.net_io_counters(pernic=True)
    interfaces = []
    for name in sorted(stats):
        st = stats[name]
        kind = _iface_kind(name)
        ipv4, ipv6, mac = [], [], ""
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                ipv4.append(f"{a.address}/{_prefix(a.netmask)}" if a.netmask else a.address)
            elif a.family == socket.AF_INET6:
                ipv6.append(a.address.split("%")[0])
            elif a.family == psutil.AF_LINK:
                mac = a.address
        io = counters.get(name)
        iface = {"name": name, "kind": kind, "up": st.isup, "speed_mbps": st.speed or None, "mtu": st.mtu,
                 "mac": mac, "ipv4": ipv4, "ipv6": ipv6,
                 "rx_bytes": io.bytes_recv if io else 0, "tx_bytes": io.bytes_sent if io else 0}
        if kind == "wlan" and st.isup:
            iface["wlan"] = _wlan_info(name)
        interfaces.append(iface)
    order = {"ethernet": 0, "wlan": 1, "vpn": 2, "other": 3, "container": 4, "loopback": 5}
    interfaces.sort(key=lambda i: (order[i["kind"]], not i["up"], i["name"]))
    return {"hostname": socket.gethostname(), "gateway": _gateway(), "dns": _dns_servers(),
            "interfaces": interfaces, **_listening_ports()}


def _prefix(netmask: str) -> int:
    try:
        return sum(bin(int(x)).count("1") for x in netmask.split("."))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
SOC_NAMES = {
    "bcm2712": "Broadcom BCM2712 (4× Cortex-A76)",
    "bcm2711": "Broadcom BCM2711 (4× Cortex-A72)",
    "bcm2837": "Broadcom BCM2837 (4× Cortex-A53)",
}
PCIE_GEN = {"2.5": 1, "5.0": 2, "8.0": 3, "16.0": 4}


def _cpuinfo(key: str) -> str:
    m = re.search(rf"^{key}\s*:\s*(.+)$", _read("/proc/cpuinfo"), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _pcie_gen(speed: str) -> Optional[int]:
    m = re.match(r"([\d.]+) GT/s", speed or "")
    return PCIE_GEN.get(m.group(1)) if m else None


def _nvme_devices() -> List[Dict[str, Any]]:
    devices = []
    for ctrl in sorted(glob.glob("/sys/class/nvme/nvme*")):
        dev = os.path.join(ctrl, "device")
        cur, maxs = _read(f"{dev}/current_link_speed"), _read(f"{dev}/max_link_speed")
        cur_gen, max_gen = _pcie_gen(cur), _pcie_gen(maxs)
        devices.append({
            "name": os.path.basename(ctrl),
            "model": _read(f"{ctrl}/model"),
            "firmware": _read(f"{ctrl}/firmware_rev"),
            "link_speed": cur, "link_width": _read(f"{dev}/current_link_width"),
            "pcie_gen": cur_gen, "max_gen": max_gen,
            # Pi 5 runs the PCIe slot at Gen 2 unless told otherwise
            "gen3_hint": cur_gen == 2 and (max_gen or 0) >= 3,
        })
    return devices


def _usb_devices() -> List[Dict[str, Any]]:
    devices = []
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vendor = _read(f"{d}/idVendor")
        if not vendor or os.path.basename(d).startswith("usb"):
            continue  # interfaces and root hubs
        speed = _read(f"{d}/speed")
        devices.append({
            "product": _read(f"{d}/product") or "Unbekanntes Gerät",
            "manufacturer": _read(f"{d}/manufacturer"),
            "id": f"{vendor}:{_read(f'{d}/idProduct')}",
            "speed_mbps": int(float(speed)) if speed else None,
            "port": os.path.basename(d),
            "is_hub": _read(f"{d}/bDeviceClass") == "09",
        })
    return devices


def _eeprom_status() -> Dict[str, Any]:
    out = _run(["rpi-eeprom-update"], timeout=15)
    if not out:
        version = _run(["vcgencmd", "bootloader_version"]).splitlines()
        return {"current": version[0] if version else "", "status": ""}
    status = "update" if "UPDATE AVAILABLE" in out.upper() else "ok" if "up to date" in out.lower() else ""
    cur = re.search(r"CURRENT:\s*(.+)", out)
    latest = re.search(r"LATEST:\s*(.+)", out)
    return {"current": cur.group(1).strip() if cur else "", "latest": latest.group(1).strip() if latest else "",
            "status": status}


def _block_devices() -> List[Dict[str, Any]]:
    out = _run(["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,TYPE,MODEL,TRAN,ROTA"])
    try:
        import json
        devs = json.loads(out).get("blockdevices", []) if out else []
    except ValueError:
        return []
    return [{"name": d.get("name"), "size": int(d.get("size") or 0), "model": (d.get("model") or "").strip(),
             "transport": d.get("tran") or "", "rotational": d.get("rota") in (True, "1", 1)}
            for d in devs if d.get("type") == "disk" and not str(d.get("name", "")).startswith(("zram", "loop", "ram"))]


def hardware_info() -> Dict[str, Any]:
    compatible = _read("/proc/device-tree/compatible").replace("\x00", " ")
    soc = next((SOC_NAMES[k] for k in SOC_NAMES if k in compatible), "")
    max_khz = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    firmware = _run(["vcgencmd", "version"]).splitlines()
    return {
        "model": _read("/proc/device-tree/model") or platform.node(),
        "soc": soc or platform.processor() or platform.machine(),
        "revision": _cpuinfo("Revision"),
        "serial": _cpuinfo("Serial") or _read("/proc/device-tree/serial-number"),
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_max_mhz": int(max_khz) // 1000 if max_khz.isdigit() else None,
        "ram_bytes": psutil.virtual_memory().total,
        "architecture": platform.machine(),
        "kernel": platform.release(),
        "firmware": firmware[0].strip() if firmware else "",
        "eeprom": _eeprom_status() if IS_LINUX else {},
        "nvme": _nvme_devices(),
        "disks": _block_devices(),
        "usb": _usb_devices(),
    }
