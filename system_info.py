"""
System Info Collector for Raspberry Pi and Host Systems.
Gathers CPU, Memory, Disk, Network, Temperature, Processes, and Hardware diagnostics.
Automatically detects Raspberry Pi vs macOS/generic Linux and provides seamless fallbacks.
"""

import glob
import os
import platform
import subprocess
import time
import socket
import psutil

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


def get_top_processes(limit: int = 8) -> list:
    """Return top running processes sorted by CPU and memory usage."""
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'username']):
        try:
            info = p.info
            if info['cpu_percent'] is None:
                info['cpu_percent'] = 0.0
            if info['memory_percent'] is None:
                info['memory_percent'] = 0.0
            info['cpu_percent'] = round(info['cpu_percent'], 1)
            info['memory_percent'] = round(info['memory_percent'], 1)
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Sort descending by CPU, then memory
    procs.sort(key=lambda x: (x['cpu_percent'], x['memory_percent']), reverse=True)
    return procs[:limit]


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
        "fan": get_fan_status(),
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
