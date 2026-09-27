"""
System Info Collector for Raspberry Pi and Host Systems.
Gathers CPU, Memory, Disk, Network, Temperature, Processes, and Hardware diagnostics.
Automatically detects Raspberry Pi vs macOS/generic Linux and provides seamless fallbacks.
"""

import glob
import os
import platform
import re
import subprocess
import time
import socket
import psutil
from typing import Any, Dict, List

# Cache previous network counters for bandwidth rate calculation
_last_net_time = 0
_last_net_io = None


def is_raspberry_pi() -> bool:
    """Check if the current host is a genuine Raspberry Pi."""
    # Check device tree model
    try:
        if os.path.exists("/proc/device-tree/model"):
            with open("/proc/device-tree/model", "r") as f:
                model = f.read().lower()
                if "raspberry pi" in model:
                    return True
    except Exception:
        pass

    # Check os-release / issue
    try:
        if os.path.exists("/etc/rpi-issue"):
            return True
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r") as f:
                content = f.read().lower()
                if "raspbian" in content or "raspberry pi reference" in content:
                    return True
    except Exception:
        pass

    return False


def soc_compatible() -> List[str]:
    """SoC ids from the device tree, e.g. ["raspberrypi,5-model-b", "brcm,bcm2712"]."""
    try:
        with open("/proc/device-tree/compatible", "rb") as f:
            return [x.decode(errors="replace") for x in f.read().split(b"\x00") if x]
    except OSError:
        return []


def is_pi5() -> bool:
    """Pi 5 generation (BCM2712: Pi 5, Pi 500, CM5) - PMIC, fan header, PCIe slot."""
    return any("bcm2712" in c for c in soc_compatible())


_caps_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
CAPS_CACHE_SECONDS = 300


def get_capabilities() -> dict:
    """
    Which hardware features this machine has, so the interface can leave out what
    can't work here (e.g. a Pi 4 has no PMIC power readings and no fan curve).
    """
    now = time.time()
    if _caps_cache["data"] is not None and now - _caps_cache["ts"] < CAPS_CACHE_SECONDS:
        return _caps_cache["data"]
    pi5 = is_pi5()
    fan = bool(get_fan_status().get("available"))
    caps = []
    if fan:
        caps.append("fan")
    if fan and pi5:
        caps.append("fancurve")   # the fan_temp dtparams only exist for the Pi 5 fan header
    if get_power_status().get("available"):
        caps.append("pmic")
    if read_throttled_raw() is not None:
        caps.append("throttle")
    if pi5:
        caps.append("pi5")
    data = {"model": get_pi_model(), "pi": is_raspberry_pi(), "caps": caps}
    _caps_cache.update(ts=now, data=data)
    return data


def get_pi_model() -> str:
    """Get the model description of the system or Raspberry Pi."""
    if is_raspberry_pi():
        try:
            with open("/proc/device-tree/model", "r") as f:
                return f.read().strip("\x00 \n\t")
        except Exception:
            pass

    # Fallback to general platform info
    uname = platform.uname()
    if platform.system() == "Darwin":
        # macOS nice name
        try:
            cmd = ["sysctl", "-n", "machdep.cpu.brand_string"]
            cpu_brand = subprocess.check_output(cmd, text=True).strip()
            return f"macOS Host ({cpu_brand})"
        except Exception:
            return f"macOS ({uname.machine})"
    return f"{uname.system} {uname.machine} ({uname.node})"


def get_cpu_temp() -> dict:
    """
    Get CPU temperature.
    Supports Raspberry Pi vcgencmd / thermal_zone, Linux hwmon/sensors,
    and falls back to realistic simulation on macOS/Dev.
    """
    # 1. Raspberry Pi thermal zone
    thermal_paths = [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp"
    ]
    for path in thermal_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    temp_raw = float(f.read().strip())
                    celsius = round(temp_raw / 1000.0, 1)
                    return {"celsius": celsius, "source": "thermal_zone0", "is_simulated": False}
            except Exception:
                pass

    # 2. Raspberry Pi vcgencmd
    try:
        res = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0 and "temp=" in res.stdout:
            # Output format: temp=48.2'C
            val = res.stdout.split("=")[1].replace("'C", "").strip()
            return {"celsius": float(val), "source": "vcgencmd", "is_simulated": False}
    except Exception:
        pass

    # 3. psutil sensors if available on Linux
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                if entries:
                    return {"celsius": round(entries[0].current, 1), "source": f"sensors ({name})", "is_simulated": False}
    except Exception:
        pass

    # 4. Realistic fallback based on CPU load for dev/macOS preview
    cpu_percent = psutil.cpu_percent(interval=None)
    simulated_temp = round(38.0 + (cpu_percent * 0.35) + (time.time() % 3 * 0.4), 1)
    return {"celsius": simulated_temp, "source": "Dev-Simulation", "is_simulated": True}


def get_throttled_status() -> dict:
    """
    Check Raspberry Pi throttling flags (undervoltage, freq capping, temp throttle).
    """
    if not is_raspberry_pi():
        return {
            "supported": False,
            "raw_hex": "0x0",
            "summary": "Normal (Nicht auf Pi)",
            "undervoltage": False,
            "freq_capped": False,
            "throttled": False,
            "soft_temp_limit": False
        }

    try:
        res = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0 and "throttled=" in res.stdout:
            val_hex = res.stdout.split("=")[1].strip()
            val = int(val_hex, 16)
            
            # Bits:
            # 0: under-voltage detected
            # 1: arm frequency capped
            # 2: currently throttled
            # 3: soft temperature limit active
            # 16: under-voltage has occurred
            # 17: arm frequency capping has occurred
            # 18: throttling has occurred
            # 19: soft temperature limit has occurred
            undervoltage = bool(val & 0x1)
            freq_capped = bool(val & 0x2)
            throttled = bool(val & 0x4)
            soft_temp = bool(val & 0x8)

            warnings = []
            if undervoltage:
                warnings.append("⚠️ Unterspannung (Netzteil zu schwach!)")
            if freq_capped:
                warnings.append("⚠️ Taktung begrenzt")
            if throttled:
                warnings.append("🔥 Thermisches Throttling aktiv")
            if soft_temp:
                warnings.append("🌡️ Soft Temp Limit erreicht")

            summary = "OK (Keine Drosselung)" if not warnings else ", ".join(warnings)

            return {
                "supported": True,
                "raw_hex": val_hex,
                "summary": summary,
                "undervoltage": undervoltage,
                "freq_capped": freq_capped,
                "throttled": throttled,
                "soft_temp_limit": soft_temp
            }
    except Exception:
        pass

    return {
        "supported": False,
        "raw_hex": "0x0",
        "summary": "Status unbekannt",
        "undervoltage": False,
        "freq_capped": False,
        "throttled": False,
        "soft_temp_limit": False
    }



def read_throttled_raw():
    """Raw `vcgencmd get_throttled` value as int, or None when not available."""
    if not is_raspberry_pi():
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        return int(out.split("=")[1].strip(), 16) if "throttled=" in out else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


# Whole disks only (no partitions, loop or RAM devices): nvme0n1, sda, mmcblk0, vda (disk0 on macOS)
PHYSICAL_DISK = re.compile(r"^(nvme\d+n\d+|sd[a-z]+|mmcblk\d+|vd[a-z]+|disk\d+)$")
_last_disk = {"t": 0.0, "io": None}


def disk_io_counters():
    try:
        return {n: c for n, c in (psutil.disk_io_counters(perdisk=True) or {}).items() if PHYSICAL_DISK.match(n)}
    except Exception:
        return {}


def get_disk_io_rates() -> list:
    """Read/write rate per physical disk in KB/s since the last call (live view)."""
    now, cur = time.time(), disk_io_counters()
    prev, dt = _last_disk["io"], now - _last_disk["t"]
    _last_disk.update(t=now, io=cur)
    rates = []
    for name, c in sorted(cur.items()):
        p = prev.get(name) if prev else None
        read = write = 0.0
        if p and dt > 0:
            read = max(0.0, (c.read_bytes - p.read_bytes) / dt / 1024)
            write = max(0.0, (c.write_bytes - p.write_bytes) / dt / 1024)
        rates.append({"name": name, "read_kbs": round(read, 1), "write_kbs": round(write, 1)})
    return rates

def get_network_rates() -> dict:
    """Calculate upload and download transfer rate in KB/s and MB/s."""
    global _last_net_time, _last_net_io

    now = time.time()
    current_io = psutil.net_io_counters()

    down_speed_kb = 0.0
    up_speed_kb = 0.0

    if _last_net_io is not None and _last_net_time > 0:
        time_delta = now - _last_net_time
        if time_delta > 0:
            bytes_recv_delta = current_io.bytes_recv - _last_net_io.bytes_recv
            bytes_sent_delta = current_io.bytes_sent - _last_net_io.bytes_sent

            # Avoid negative glitches if counters reset
            if bytes_recv_delta >= 0:
                down_speed_kb = round(bytes_recv_delta / time_delta / 1024.0, 1)
            if bytes_sent_delta >= 0:
                up_speed_kb = round(bytes_sent_delta / time_delta / 1024.0, 1)

    _last_net_time = now
    _last_net_io = current_io

    return {
        "download_kbps": down_speed_kb,
        "upload_kbps": up_speed_kb,
        "download_mbps": round(down_speed_kb / 1024.0, 2),
        "upload_mbps": round(up_speed_kb / 1024.0, 2),
        "total_recv_mb": round(current_io.bytes_recv / (1024 * 1024), 1),
        "total_sent_mb": round(current_io.bytes_sent / (1024 * 1024), 1)
    }


def get_ip_addresses() -> dict:
    """Get active local IP and network interfaces."""
    interfaces = {}
    primary_ip = "127.0.0.1"

    try:
        # Determine primary IP by creating dummy socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    try:
        net_addrs = psutil.net_if_addrs()
        for iface_name, addrs in net_addrs.items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    interfaces[iface_name] = addr.address
    except Exception:
        pass

    return {
        "primary_ip": primary_ip,
        "interfaces": interfaces
    }


def get_uptime() -> dict:
    """Calculate system uptime in human-readable format and seconds."""
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)

    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        formatted = f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        formatted = f"{hours}h {minutes}m {seconds}s"
    else:
        formatted = f"{minutes}m {seconds}s"

    return {
        "seconds": uptime_seconds,
        "formatted": formatted,
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot_time))
    }


# ---------------------------------------------------------------------------
# Pi 5 power supply (PMIC)
# ---------------------------------------------------------------------------
_power_cache = {"ts": 0.0, "data": None}
POWER_CACHE_SECONDS = 3


def parse_pmic_adc(output: str) -> dict:
    """Pair each rail's current (…_A) and voltage (…_V) from `vcgencmd pmic_read_adc`."""
    currents, volts = {}, {}
    for m in re.finditer(r"^\s*(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)[AV]\s*$", output, re.MULTILINE):
        rail, kind, value = m.group(1), m.group(2), float(m.group(3))
        (currents if kind == "A" else volts)[rail] = value
    rails = [{"name": r, "volt": round(volts[r], 3), "amp": round(currents[r], 3),
              "watt": round(volts[r] * currents[r], 3)} for r in currents if r in volts]
    rails.sort(key=lambda r: r["watt"], reverse=True)
    return {"rails": rails, "board_watt": round(sum(r["watt"] for r in rails), 2),
            "input_volt": round(volts["EXT5V"], 2) if "EXT5V" in volts else None}


def _psu_max_current_ma():
    """Current limit the Pi 5 negotiated with its USB-C PSU (device tree, big-endian u32 in mA)."""
    try:
        with open("/proc/device-tree/chosen/power/max_current", "rb") as f:
            raw = f.read()
        return int.from_bytes(raw[:4], "big") if len(raw) >= 4 else None
    except OSError:
        return None


def get_power_status() -> dict:
    now = time.time()
    if _power_cache["data"] is not None and now - _power_cache["ts"] < POWER_CACHE_SECONDS:
        return _power_cache["data"]

    data = {"available": False}
    try:
        res = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and "EXT5V" in res.stdout:
            data = {"available": True, **parse_pmic_adc(res.stdout)}
    except Exception:
        pass

    if data["available"]:
        flags = 0
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=1).stdout
            flags = int(out.split("=")[1].strip(), 16)
        except Exception:
            pass
        max_ma = _psu_max_current_ma()
        data.update(
            undervoltage_now=bool(flags & 0x1),
            undervoltage_since_boot=bool(flags & 0x10000),
            psu_max_ma=max_ma,
            # < 5 A PSUs make the Pi 5 limit USB ports to 600 mA total
            psu_ok=max_ma is None or max_ma >= 5000,
            top_rails=data["rails"][:4],
        )
        data.pop("rails", None)

    _power_cache.update(ts=now, data=data)
    return data


_OWN_USER = None


def _own_user() -> str:
    global _OWN_USER
    if _OWN_USER is None:
        try:
            _OWN_USER = psutil.Process().username()
        except Exception:
            _OWN_USER = ""
    return _OWN_USER


def _protected_pids() -> set:
    """Never offer to kill init, the dashboard itself or its parent (systemd / shell)."""
    return {1, os.getpid(), os.getppid()}


def _process_list() -> list:
    procs = []
    own, protected = _own_user(), _protected_pids()
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'username', 'create_time']):
        try:
            info = p.info
            info['cpu_percent'] = round(info['cpu_percent'] or 0.0, 1)
            info['memory_percent'] = round(info['memory_percent'] or 0.0, 1)
            # The dashboard runs as a normal user: it can only signal its own processes
            info['killable'] = info['username'] == own and info['pid'] not in protected
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    # Sort descending by CPU, then memory
    procs.sort(key=lambda x: (x['cpu_percent'], x['memory_percent']), reverse=True)
    return procs


def get_top_processes(limit: int = 8) -> list:
    """Return top running processes sorted by CPU and memory usage."""
    return _process_list()[:limit]


def search_processes(query: str, limit: int = 50) -> list:
    q = query.strip().lower()
    if not q:
        return []
    return [p for p in _process_list()
            if q in (p['name'] or '').lower() or str(p['pid']) == q][:limit]


def signal_process(pid: int, create_time: float, force: bool = False) -> dict:
    """SIGTERM (or SIGKILL with force) one of our own processes.
    create_time guards against the PID having been reused by another process meanwhile."""
    if pid in _protected_pids():
        return {"ok": False, "message": "Dieser Prozess ist geschützt."}
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - create_time) > 0.01:
            return {"ok": False, "message": "Prozess existiert nicht mehr (PID wurde neu vergeben)."}
        if proc.username() != _own_user():
            return {"ok": False, "message": f"Gehört Benutzer '{proc.username()}' – dafür das Web-Terminal mit sudo nutzen."}
        name = proc.name()
        proc.kill() if force else proc.terminate()
    except psutil.NoSuchProcess:
        return {"ok": True, "message": "Prozess ist bereits beendet.", "gone": True}
    except psutil.AccessDenied:
        return {"ok": False, "message": "Keine Berechtigung."}
    try:
        proc.wait(timeout=3)
        return {"ok": True, "message": f"{name} ({pid}) beendet.", "gone": True}
    except psutil.TimeoutExpired:
        return {"ok": True, "gone": False,
                "message": f"{name} ({pid}) reagiert nicht auf SIGTERM." if not force else f"{name} ({pid}) läuft noch."}


def _read_sysfs(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


FAN_LEVEL_NAMES = {0: "Aus", 1: "Niedrig", 2: "Mittel", 3: "Hoch", 4: "Maximal"}


def get_fan_status() -> dict:
    """
    Fan level from the thermal cooling device (Pi 5 active cooler: pwm-fan, states 0-4),
    plus RPM/PWM from hwmon and the temperatures at which the fan steps up.
    """
    devices = sorted(glob.glob("/sys/class/thermal/cooling_device*"))
    fan_dev = next((d for d in devices if "fan" in _read_sysfs(f"{d}/type").lower()), None)
    if fan_dev is None and os.path.exists("/sys/class/thermal/cooling_device0/cur_state"):
        fan_dev = "/sys/class/thermal/cooling_device0"
    if fan_dev is None:
        return {"available": False}

    try:
        level = int(_read_sysfs(f"{fan_dev}/cur_state"))
        max_level = int(_read_sysfs(f"{fan_dev}/max_state"))
    except ValueError:
        return {"available": False}

    rpm, pwm_percent = None, None
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        if os.path.exists(f"{hw}/fan1_input"):
            val = _read_sysfs(f"{hw}/fan1_input")
            rpm = int(val) if val.isdigit() else None
            pwm = _read_sysfs(f"{hw}/pwm1")
            pwm_percent = round(int(pwm) / 255 * 100) if pwm.isdigit() else None
            break

    # "active" trip points of the CPU thermal zone are where the fan steps up
    thresholds = []
    for trip in sorted(glob.glob("/sys/class/thermal/thermal_zone0/trip_point_*_type")):
        if _read_sysfs(trip) == "active":
            temp = _read_sysfs(trip.replace("_type", "_temp"))
            if temp.lstrip("-").isdigit():
                thresholds.append(round(int(temp) / 1000, 1))

    return {
        "available": True,
        "device": _read_sysfs(f"{fan_dev}/type") or os.path.basename(fan_dev),
        "level": level,
        "max_level": max_level,
        "level_name": FAN_LEVEL_NAMES.get(level, f"Stufe {level}") if max_level == 4 else ("Aus" if level == 0 else f"Stufe {level}"),
        "rpm": rpm,
        "pwm_percent": pwm_percent,
        "thresholds": sorted(thresholds),
    }


_root_drive_cache = None


def get_root_drive() -> dict:
    """Identify the drive the root filesystem lives on (NVMe, SD card, USB SSD/HDD)."""
    global _root_drive_cache
    if _root_drive_cache is not None:
        return _root_drive_cache

    device = ""
    try:
        # findmnt resolves /dev/root to the real partition on Raspberry Pi OS
        device = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"],
                                capture_output=True, text=True, timeout=2).stdout.strip()
        device = device.split("[")[0]  # btrfs subvolumes: /dev/nvme0n1p2[/@]
    except Exception:
        pass
    if not device:
        device = next((p.device for p in psutil.disk_partitions() if p.mountpoint == "/"), "")

    name = os.path.basename(device)
    # Partition -> whole disk: nvme0n1p2 -> nvme0n1, mmcblk0p2 -> mmcblk0, sda2 -> sda
    if name.startswith(("nvme", "mmcblk")):
        disk = name.split("p")[0] if name.startswith("mmcblk") else name.rsplit("p", 1)[0]
    else:
        disk = name.rstrip("0123456789")

    def read_sys(path: str) -> str:
        try:
            with open(f"/sys/block/{disk}/{path}") as f:
                return f.read().strip()
        except Exception:
            return ""

    if name.startswith("nvme"):
        kind = "NVMe SSD"
    elif name.startswith("mmcblk"):
        kind = "eMMC" if read_sys("device/type") == "MMC" else "SD-Karte"
    elif name.startswith("sd"):
        kind = "Festplatte (HDD)" if read_sys("queue/rotational") == "1" else "USB-SSD"
    else:
        kind = "Systemlaufwerk"

    _root_drive_cache = {
        "type": kind,
        "model": read_sys("device/model") or read_sys("device/name"),
        "device": device,
    }
    return _root_drive_cache


def get_system_snapshot() -> dict:
    """Aggregate full system metrics into a single dictionary."""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_percent(percpu=True, interval=None)
    cpu_freq = psutil.cpu_freq()
    freq_current = round(cpu_freq.current, 0) if cpu_freq else 0

    # Memory
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disk
    disk = psutil.disk_usage('/')

    # System Info
    pi_detected = is_raspberry_pi()
    model = get_pi_model()
    temp_info = get_cpu_temp()
    throttled = get_throttled_status()
    net = get_network_rates()
    ips = get_ip_addresses()
    uptime = get_uptime()

    return {
        "timestamp": time.time(),
        "is_pi": pi_detected,
        "host_name": socket.gethostname(),
        "model": model,
        "os": f"{platform.system()} {platform.release()}",
        "architecture": platform.machine(),
        "uptime": uptime,
        "network_ip": ips,
        "cpu": {
            "percent": round(cpu_percent, 1),
            "cores": [round(c, 1) for c in cpu_cores],
            "count": psutil.cpu_count(logical=True),
            "frequency_mhz": freq_current
        },
        "temperature": temp_info,
        "throttled": throttled,
        "disk_io": get_disk_io_rates(),
        "fan": get_fan_status(),
        "power": get_power_status(),
        "memory": {
            "total_mb": round(mem.total / (1024 * 1024), 0),
            "used_mb": round(mem.used / (1024 * 1024), 0),
            "available_mb": round(mem.available / (1024 * 1024), 0),
            "percent": round(mem.percent, 1)
        },
        "swap": {
            "total_mb": round(swap.total / (1024 * 1024), 0),
            "used_mb": round(swap.used / (1024 * 1024), 0),
            "percent": round(swap.percent, 1)
        },
        "disk": {
            "total_gb": round(disk.total / (1024 ** 3), 1),
            "used_gb": round(disk.used / (1024 ** 3), 1),
            "free_gb": round(disk.free / (1024 ** 3), 1),
            "percent": round(disk.percent, 1),
            **get_root_drive()
        },
        "network": net
    }
