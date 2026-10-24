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


# ---------------------------------------------------------------------------
# CPU mode (cpufreq governor)
# ---------------------------------------------------------------------------
CPUFREQ_DIR = "/sys/devices/system/cpu/cpufreq"
GOVERNOR_HIDDEN = {"userspace"}  # needs a manual frequency, not useful here


def _policies() -> List[str]:
    return sorted(glob.glob(os.path.join(CPUFREQ_DIR, "policy*")))


def cpu_mode() -> Dict[str, Any]:
    import settings_store
    policies = _policies()
    if not policies:
        return {"available": False}
    p0 = policies[0]

    def khz(name: str) -> Optional[int]:
        v = _read(os.path.join(p0, name))
        return int(v) // 1000 if v.isdigit() else None

    governors = [g for g in _read(os.path.join(p0, "scaling_available_governors")).split() if g not in GOVERNOR_HIDDEN]
    return {
        "available": True,
        "governor": _read(os.path.join(p0, "scaling_governor")),
        "governors": governors,
        "saved": settings_store.get("cpu_governor"),
        "cur_mhz": khz("scaling_cur_freq"),
        "min_mhz": khz("cpuinfo_min_freq"),
        "max_mhz": khz("cpuinfo_max_freq"),
        "policies": len(policies),
    }


def set_governor(governor: str, persist: bool = True) -> Dict[str, Any]:
    import settings_store
    status = cpu_mode()
    if not status["available"]:
        return {"ok": False, "message": "CPU-Modus wird auf diesem System nicht unterstützt."}
    if governor not in status["governors"]:
        return {"ok": False, "message": "Unbekannter CPU-Modus."}
    tee = shutil.which("tee") or "/usr/bin/tee"
    for policy in _policies():
        target = os.path.join(policy, "scaling_governor")
        try:
            # Exactly the command form allowed by the sudoers rule from install.sh
            res = subprocess.run(["sudo", "-n", tee, target], input=governor, capture_output=True, text=True, timeout=5)
        except Exception as e:
            return {"ok": False, "message": str(e)}
        if res.returncode != 0:
            err = res.stderr.strip()
            if "password" in err.lower() or "not allowed" in err.lower():
                return {"ok": False, "message": "Keine Berechtigung – install.sh auf dem Pi erneut ausführen."}
            return {"ok": False, "message": err[:200] or "Umschalten fehlgeschlagen."}
    if persist:
        settings_store.set("cpu_governor", governor)
    return {"ok": True, "message": f"CPU-Modus: {governor}"}


def restore_governor() -> None:
    """Re-apply the saved governor after a reboot (the kernel starts with its default)."""
    import settings_store
    saved = settings_store.get("cpu_governor")
    status = cpu_mode()
    if saved and status["available"] and status["governor"] != saved and saved in status["governors"]:
        set_governor(saved, persist=False)


# ---------------------------------------------------------------------------
# Speed test: drive (sequential write/read + 4K random read) and CPU
# ---------------------------------------------------------------------------
import hashlib
import random
import threading
import time

_bench_lock = threading.Lock()
BENCH_SIZE_MB = 512
CHUNK = 4 * 1024 * 1024


def _bench_dir(mount: str) -> str:
    """A writable folder on the given drive: the mount itself or (for /) the user's cache."""
    mount_dev = os.stat(mount).st_dev
    candidates = [mount] if mount != "/" else []
    candidates.append(os.path.join(os.path.expanduser("~"), ".cache"))
    for c in candidates:
        os.makedirs(c, exist_ok=True) if c.endswith(".cache") else None
        if os.path.isdir(c) and os.access(c, os.W_OK) and os.stat(c).st_dev == mount_dev:
            return c
    raise PermissionError("Kein Schreibzugriff auf diesem Laufwerk.")


def _drop_cache(fd: int) -> None:
    if hasattr(os, "posix_fadvise"):
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def _no_cache(fd: int) -> None:
    import fcntl
    if hasattr(fcntl, "F_NOCACHE"):  # macOS
        fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)


def disk_benchmark(mount: str) -> Dict[str, Any]:
    import files
    real = files.resolve(mount)
    folder = _bench_dir(real)
    size = BENCH_SIZE_MB * 1024 * 1024
    if shutil.disk_usage(folder).free < size * 2 + 1024 ** 3:
        raise ValueError("Zu wenig freier Speicher für den Test (mind. 2 GB frei nötig).")
    path = os.path.join(folder, f".picore-speedtest-{os.getpid()}")
    block = os.urandom(CHUNK)  # random data: compressing SSD controllers can't cheat
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        _no_cache(fd)
        start = time.perf_counter()
        for _ in range(size // CHUNK):
            os.write(fd, block)
        os.fsync(fd)
        write_s = time.perf_counter() - start
        _drop_cache(fd)
        os.close(fd)

        fd = os.open(path, os.O_RDONLY)
        _no_cache(fd)
        _drop_cache(fd)
        start = time.perf_counter()
        while os.read(fd, CHUNK):
            pass
        read_s = time.perf_counter() - start

        # 4K random reads for 3 seconds
        _drop_cache(fd)
        rnd = random.Random(1)
        ops, start = 0, time.perf_counter()
        while time.perf_counter() - start < 3:
            os.pread(fd, 4096, rnd.randrange(0, size // 4096) * 4096)
            ops += 1
        iops_s = time.perf_counter() - start
        os.close(fd)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    mb = size / 1024 / 1024
    return {"write_mbs": round(mb / write_s, 1), "read_mbs": round(mb / read_s, 1),
            "iops_4k": int(ops / iops_s), "size_mb": BENCH_SIZE_MB, "folder": folder}


def cpu_benchmark(seconds: float = 3) -> Dict[str, Any]:
    data = os.urandom(1024 * 1024)

    def worker(result: list, idx: int) -> None:
        n, end = 0, time.perf_counter() + seconds
        while time.perf_counter() < end:
            hashlib.sha256(data).digest()  # releases the GIL -> threads really run in parallel
            n += 1
        result[idx] = n

    single = [0]
    worker(single, 0)
    cores = psutil.cpu_count(logical=True) or 1
    multi = [0] * cores
    threads = [threading.Thread(target=worker, args=(multi, i)) for i in range(cores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    single_mbs = single[0] / seconds
    multi_mbs = sum(multi) / seconds
    return {"single_mbs": round(single_mbs, 1), "multi_mbs": round(multi_mbs, 1),
            "cores": cores, "scaling": round(multi_mbs / single_mbs, 2) if single_mbs else None}


def run_benchmark(kind: str, mount: str = "/") -> Dict[str, Any]:
    import settings_store
    import system_info
    if not _bench_lock.acquire(blocking=False):
        raise ValueError("Es läuft bereits ein Test.")
    try:
        result = disk_benchmark(mount) if kind == "disk" else cpu_benchmark() if kind == "cpu" else None
        if result is None:
            raise ValueError("Unbekannter Test.")
    finally:
        _bench_lock.release()
    temp = system_info.get_cpu_temp()
    entry = {"ts": int(time.time()), "kind": kind, "target": mount if kind == "disk" else "CPU",
             "governor": (cpu_mode() or {}).get("governor"), "temp_c": None if temp.get("is_simulated") else temp["celsius"],
             **result}
    history = settings_store.get("benchmarks", []) or []
    settings_store.set("benchmarks", ([entry] + history)[:20])
    return entry


def benchmark_history() -> Dict[str, Any]:
    import settings_store
    import services_manager
    drives = [{"mount": d["mount"], "label": d["label"]} for d in services_manager.get_mounted_drives()]
    return {"results": settings_store.get("benchmarks", []) or [], "drives": drives, "running": _bench_lock.locked()}


# ---------------------------------------------------------------------------
# Log search (systemd journal)
# ---------------------------------------------------------------------------
import json as _json

LOG_SINCE = {"15m": "15 min ago", "1h": "1 hour ago", "24h": "24 hours ago", "7d": "7 days ago"}
LOG_PRIORITY = {"all": None, "warning": "warning", "err": "err"}
LOG_LIMIT = 300


def journal_units() -> Dict[str, Any]:
    if not shutil.which("journalctl"):
        return {"supported": False, "units": []}
    out = _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"])
    units = sorted({line.split()[0] for line in out.splitlines() if line.split()
                    and line.split()[0].endswith(".service")})
    return {"supported": True, "units": units}


def _journal_hint(stderr: str, count: int) -> str:
    if "insufficient permissions" in stderr.lower() or (count == 0 and "not seeing messages from other users" in stderr.lower()):
        user = os.environ.get("USER", "$USER")
        return f"Nur eigene Logs sichtbar. Für alle: sudo usermod -aG systemd-journal {user} && sudo systemctl restart raspberry-dashboard"
    return ""


def _message(entry: dict) -> str:
    msg = entry.get("MESSAGE", "")
    if isinstance(msg, list):  # non-UTF-8 messages come as a byte array
        msg = bytes(msg).decode("utf-8", errors="replace")
    return str(msg)


def journal_search(unit: str = "", priority: str = "all", since: str = "1h", query: str = "") -> Dict[str, Any]:
    if not shutil.which("journalctl"):
        return {"supported": False, "entries": []}
    cmd = ["journalctl", "--no-pager", "-o", "json", "-r", "-n", str(LOG_LIMIT)]
    if unit == "kernel":
        cmd.append("-k")
    elif unit:
        if unit not in journal_units()["units"]:
            raise ValueError("Unbekannter Dienst.")
        cmd += ["-u", unit]
    if LOG_PRIORITY.get(priority):
        cmd += ["-p", LOG_PRIORITY[priority]]
    cmd += ["--since", LOG_SINCE.get(since, LOG_SINCE["1h"])]
    query = query.strip()[:200]
    if query:
        # journalctl's own grep searches the whole range, not just the newest lines
        cmd += ["-g", re.escape(query), "--case-sensitive=false"]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, errors="replace")
    except subprocess.TimeoutExpired:
        raise ValueError("Suche dauerte zu lange – Zeitraum verkleinern.")
    if query and "pattern matching" in res.stderr.lower():
        # journalctl built without PCRE2: fetch more lines and filter here
        cmd = [c for c in cmd if c not in ("-g", re.escape(query), "--case-sensitive=false")]
        cmd[cmd.index("-n") + 1] = "5000"
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, errors="replace")

    entries = []
    for line in res.stdout.splitlines():
        try:
            e = _json.loads(line)
        except ValueError:
            continue
        msg = _message(e)
        if query and query.lower() not in msg.lower() and "-g" not in cmd:
            continue
        entries.append({
            "ts": int(e.get("__REALTIME_TIMESTAMP", "0")) // 1_000_000,
            "unit": e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or ("kernel" if e.get("_TRANSPORT") == "kernel" else ""),
            "priority": int(e.get("PRIORITY", 6)),
            "pid": e.get("_PID", ""),
            "message": msg[:2000],
        })
        if len(entries) >= LOG_LIMIT:
            break
    return {"supported": True, "entries": entries, "limit": LOG_LIMIT,
            "hint": _journal_hint(res.stderr, len(entries))}


# ---------------------------------------------------------------------------
# Storage cleanup
# ---------------------------------------------------------------------------
JOURNAL_KEEP = "14d"
USER_CACHE = os.path.join(os.path.expanduser("~"), ".cache")
USER_TRASH = os.path.join(os.path.expanduser("~"), ".local", "share", "Trash")

_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?)i?B?", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def _parse_size(text: str) -> int:
    m = _SIZE_RE.search(text or "")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()]) if m else 0


def _tree_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path, onerror=lambda e: None):
        for f in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _docker_reclaimable() -> Optional[int]:
    out = _run(["docker", "system", "df", "--format", "{{json .}}"], timeout=20)
    if not out:
        return None
    total = 0
    for line in out.splitlines():
        try:
            row = _json.loads(line)
        except ValueError:
            continue
        if row.get("Type") in ("Images", "Build Cache"):
            total += _parse_size(row.get("Reclaimable", "").split("(")[0])
    return total


def cleanup_status() -> Dict[str, Any]:
    items = []
    if shutil.which("journalctl"):
        m = re.search(r"take up ([\d.]+\s*[KMGT]?)", _run(["journalctl", "--disk-usage"]))
        items.append({"id": "journal", "label": "System-Logs (Journal)",
                      "desc": f"Löscht Einträge älter als {JOURNAL_KEEP.replace('d', ' Tage')}.",
                      "size": _parse_size(m.group(1)) if m else None, "size_is_total": True})
    if os.path.isdir("/var/cache/apt/archives"):
        debs = glob.glob("/var/cache/apt/archives/*.deb")
        items.append({"id": "apt", "label": "Paket-Cache (apt)", "desc": "Heruntergeladene Installationsdateien. Werden bei Bedarf neu geladen.",
                      "size": sum(os.path.getsize(f) for f in debs if os.path.isfile(f))})
    if shutil.which("docker"):
        items.append({"id": "docker", "label": "Docker", "desc": "Ungenutzte Images und Build-Cache. Laufende Container bleiben unberührt.",
                      "size": _docker_reclaimable()})
    if os.path.isdir(USER_CACHE):
        items.append({"id": "user_cache", "label": "Benutzer-Cache (~/.cache)", "desc": "Zwischenspeicher von Programmen (pip, Thumbnails …). Wird automatisch neu aufgebaut.",
                      "size": _tree_size(USER_CACHE)})
    if os.path.isdir(USER_TRASH):
        items.append({"id": "trash", "label": "Papierkorb", "desc": "Gelöschte Dateien im Papierkorb.", "size": _tree_size(USER_TRASH)})
    return {"items": items, "free_bytes": shutil.disk_usage("/").free}


def _empty_dir(path: str) -> None:
    for entry in os.scandir(path):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                os.remove(entry.path)
        except OSError:
            pass


def _sudo(cmd: List[str]) -> None:
    res = subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        err = res.stderr.strip()
        if "password" in err.lower() or "not allowed" in err.lower():
            raise PermissionError("Keine Berechtigung – install.sh auf dem Pi erneut ausführen.")
        raise RuntimeError(err[:200] or "Fehlgeschlagen.")


def cleanup_run(item: str) -> Dict[str, Any]:
    before = shutil.disk_usage("/").free
    if item == "journal":
        _sudo([shutil.which("journalctl") or "/usr/bin/journalctl", f"--vacuum-time={JOURNAL_KEEP}"])
    elif item == "apt":
        _sudo([shutil.which("apt-get") or "/usr/bin/apt-get", "clean"])
    elif item == "docker":
        for cmd in (["docker", "image", "prune", "-af"], ["docker", "builder", "prune", "-af"]):
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip()[:200] or "docker prune fehlgeschlagen.")
    elif item == "user_cache":
        _empty_dir(USER_CACHE)
    elif item == "trash":
        _empty_dir(USER_TRASH)
    else:
        raise ValueError("Unbekannter Eintrag.")
    return {"item": item, "freed": max(0, shutil.disk_usage("/").free - before)}
