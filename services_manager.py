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

# Homelab services to monitor. A service counts as running if any of its
# systemd units is active or its TCP port is listening.
TRACKED_SERVICES = [
    {"id": "ssh", "name": "SSH Server", "port": 22, "units": ["ssh", "sshd"]},
    {"id": "docker", "name": "Docker Engine", "port": None, "units": ["docker"]},
    {"id": "web", "name": "Webserver (HTTP)", "port": 80, "units": ["nginx", "apache2", "caddy"]},
    {"id": "netbird", "name": "NetBird VPN", "port": None, "units": ["netbird"], "interface": "wt0"},
    {"id": "kubernetes", "name": "Kubernetes", "port": 6443,
     "units": ["k3s", "k3s-agent", "rke2-server", "rke2-agent", "kubelet", "snap.microk8s.daemon-kubelite"]},
]

UPDATER_TIMER = "pi-dashboard-update.timer"
UPDATER_SERVICE = "pi-dashboard-update.service"

STATUS_TEXT = {"active": "Aktiv", "inactive": "Inaktiv", "error": "Fehler"}


def check_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a local port is actively listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            res = s.connect_ex((host, port))
            return res == 0
    except Exception:
        return False


def get_unit_states(units: List[str]) -> Dict[str, Dict[str, str]]:
    """Fetch ActiveState/Result of many systemd units with a single systemctl call."""
    if platform.system() != "Linux" or not units:
        return {}
    try:
        res = subprocess.run(
            ["systemctl", "show", "--property=Id,ActiveState,Result", "--", *units],
            capture_output=True, text=True, timeout=2
        )
    except Exception:
        return {}

    states, current = {}, {}
    for line in res.stdout.splitlines() + [""]:
        if not line.strip():
            if current.get("Id"):
                states[current["Id"]] = current
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    return states


def _units_status(unit_names: List[str], states: Dict[str, Dict[str, str]]) -> tuple[str, str]:
    """Combine several candidate units into one status; returns (status, active unit name)."""
    failed = False
    for name in unit_names:
        st = states.get(f"{name}.service", {})
        if st.get("ActiveState") in ("active", "reloading", "activating"):
            return "active", name
        if st.get("ActiveState") == "failed":
            failed = True
    return ("error" if failed else "inactive"), ""


def _interface_ip(name: str) -> str:
    for addr in psutil.net_if_addrs().get(name, []):
        if addr.family == socket.AF_INET:
            return addr.address
    return ""


def _updater_status(states: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Auto-updater: inactive if the timer is off, error if the last pull/restart failed."""
    import updater  # local import: updater is optional for this module

    timer = states.get(UPDATER_TIMER, {})
    run = states.get(UPDATER_SERVICE, {})
    commit = updater.update_state.get("current_commit", "")

    if timer.get("ActiveState") != "active":
        status, detail = "inactive", "Timer aus"
    elif run.get("ActiveState") == "failed" or run.get("Result", "success") != "success":
        status, detail = "error", "Letztes Update fehlgeschlagen"
    else:
        status, detail = "active", f"Commit {commit}" if commit and " " not in commit else "GitHub · jede Minute"

    return {"id": "updater", "name": "Auto-Updater", "status": status, "detail": detail}


def get_services_status() -> List[Dict[str, Any]]:
    """Return status list of tracked homelab services."""
    all_units = [f"{u}.service" for s in TRACKED_SERVICES for u in s["units"]]
    states = get_unit_states(all_units + [UPDATER_TIMER, UPDATER_SERVICE])

    results = []
    for s in TRACKED_SERVICES:
        status, active_unit = _units_status(s["units"], states)
        if status != "active" and s["port"] and check_port_open(s["port"]):
            status = "active"

        ip = _interface_ip(s["interface"]) if s.get("interface") else ""
        if status != "active" and ip:
            status = "active"  # interface up counts even without the systemd unit

        if ip:
            detail = f"{s['interface']} · {ip}"
        elif s["id"] == "kubernetes" and active_unit:
            detail = f"{active_unit.replace('snap.microk8s.daemon-kubelite', 'microk8s')} · :{s['port']}"
        elif s["port"]:
            detail = f"Port :{s['port']}"
        else:
            detail = f"{active_unit or s['units'][0]}.service"

        results.append({"id": s["id"], "name": s["name"], "status": status, "detail": detail})

    results.append(_updater_status(states))

    for r in results:
        r["running"] = r["status"] == "active"
        r["status_text"] = STATUS_TEXT[r["status"]]
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
