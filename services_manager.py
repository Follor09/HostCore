"""
Services & Server Health Manager for Raspberry Pi Server / Homelab.
Monitors system services, open ports, mounted storage drives, and system logs.
Replaces GPIO for headless and server Raspberry Pi setups.
"""

import os
import platform
import socket
import subprocess
import psutil
from typing import Dict, Any, List

# Common homelab / Raspberry Pi services to monitor
TRACKED_SERVICES = [
    {"id": "ssh", "name": "SSH Server", "port": 22, "service": "ssh"},
    {"id": "docker", "name": "Docker Engine", "port": 2375, "service": "docker"},
    {"id": "web", "name": "Webserver (HTTP)", "port": 80, "service": "nginx"},
    {"id": "pihole", "name": "DNS / Pi-hole", "port": 53, "service": "pihole-FTL"},
    {"id": "samba", "name": "Samba Shares", "port": 445, "service": "smbd"},
    {"id": "tailscale", "name": "Tailscale VPN", "port": 41641, "service": "tailscaled"}
]


def check_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a local port is actively listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            res = s.connect_ex((host, port))
            return res == 0
    except Exception:
        return False


def check_systemd_service(service_name: str) -> bool:
    """Check if a systemd unit is active."""
    if platform.system() != "Linux":
        return False
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            capture_output=True,
            timeout=1
        )
        return res.returncode == 0
    except Exception:
        return False


def get_services_status() -> List[Dict[str, Any]]:
    """Return status list of tracked homelab services."""
    results = []
    is_linux = platform.system() == "Linux"

    for s in TRACKED_SERVICES:
        port_open = check_port_open(s["port"])
        service_active = check_systemd_service(s["service"]) if is_linux else False

        is_running = port_open or service_active

        results.append({
            "id": s["id"],
            "name": s["name"],
            "port": s["port"],
            "running": is_running,
            "status_text": "Aktiv" if is_running else "Inaktiv"
        })

    return results


def get_mounted_drives() -> List[Dict[str, Any]]:
    """Detect all mounted partitions and external USB drives."""
    drives = []
    seen_mounts = set()

    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if mount in seen_mounts:
            continue
        seen_mounts.add(mount)

        # Ignore special virtual mounts
        if any(mount.startswith(p) for p in ["/proc", "/sys", "/dev", "/run", "/var/lib/docker"]):
            continue

        try:
            usage = psutil.disk_usage(mount)
            total_gb = round(usage.total / (1024 ** 3), 1)
            used_gb = round(usage.used / (1024 ** 3), 1)
            free_gb = round(usage.free / (1024 ** 3), 1)
            percent = round(usage.percent, 1)

            # Label drive
            if mount == "/":
                label = "Root System (/)"
            elif mount.startswith("/media") or mount.startswith("/mnt") or mount.startswith("/Volumes"):
                label = f"Extern: {os.path.basename(mount) or mount}"
            elif mount.startswith("/boot"):
                label = "Boot Partition (/boot)"
            else:
                label = mount

            drives.append({
                "mount": mount,
                "device": part.device,
                "fstype": part.fstype,
                "label": label,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "percent": percent
            })
        except (PermissionError, FileNotFoundError):
            pass

    return drives


def get_recent_logs(lines: int = 25) -> str:
    """Fetch recent system journal logs on Linux, or system log preview on dev."""
    if platform.system() == "Linux":
        try:
            res = subprocess.run(
                ["journalctl", "-n", str(lines), "--no-pager", "-o", "short-iso"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

        # Fallback to dmesg
        try:
            res = subprocess.run(["dmesg", "-T"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                all_lines = res.stdout.strip().splitlines()
                return "\n".join(all_lines[-lines:])
        except Exception:
            pass

    # macOS dev fallback
    try:
        res = subprocess.run(
            ["uptime"],
            capture_output=True,
            text=True,
            timeout=1
        )
        return (
            f"[Log-Viewer]: Auf Raspberry Pi OS liest dieser Bereich automatisch 'journalctl -n {lines}'.\n"
            f"Aktueller Systemstatus: {res.stdout.strip()}\n"
            f"[System Kernel]: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"[Host]: {socket.gethostname()}"
        )
    except Exception as e:
        return f"Keine Logs verfügbar: {e}"
