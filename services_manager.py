"""
Services & Server Health Manager for Raspberry Pi Server / Homelab.
Monitors system services, open ports, mounted storage drives, and system logs.
Replaces GPIO for headless and server Raspberry Pi setups.
"""

import os
import platform
import shutil
import socket
import subprocess
import psutil
from typing import Dict, Any, List, Optional

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
            ["systemctl", "show", "--property=Id,ActiveState,Result,LoadState", "--", *units],
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


def _installed_unit(unit_names: List[str], states: Dict[str, Dict[str, str]]) -> Optional[str]:
    """The unit to control: the running one, else the first that is installed on this system."""
    _, active = _units_status(unit_names, states)
    if active:
        return f"{active}.service"
    for name in unit_names:
        if states.get(f"{name}.service", {}).get("LoadState") == "loaded":
            return f"{name}.service"
    return None


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

        unit = _installed_unit(s["units"], states)
        results.append({"id": s["id"], "name": s["name"], "status": status, "detail": detail,
                        "unit": unit, "actions": list(SERVICE_ACTIONS) if unit else []})

    results.append({**_updater_status(states), "unit": UPDATER_SERVICE,
                    "actions": ["run"] if states.get(UPDATER_SERVICE, {}).get("LoadState") == "loaded" else []})

    # Tiles hidden on the settings page
    import settings_store
    hidden = set(settings_store.get("hidden_services", []))
    results = [r for r in results if r["id"] not in hidden]

    for r in results:
        r["running"] = r["status"] == "active"
        r["status_text"] = STATUS_TEXT[r["status"]]
    return results


# ---------------------------------------------------------------------------
# Control: start / stop / restart via narrow sudo rules written by install.sh
# ---------------------------------------------------------------------------
SERVICE_ACTIONS = ("start", "stop", "restart")


def controllable_units() -> List[str]:
    """Every unit install.sh writes sudo rules for (keep in sync via this function)."""
    return [f"{u}.service" for s in TRACKED_SERVICES for u in s["units"]]


def _resolve_unit(service_id: str) -> Optional[str]:
    """Map a tile id from the browser to a unit - the browser never names units itself."""
    if service_id == "updater":
        return UPDATER_SERVICE
    svc = next((s for s in TRACKED_SERVICES if s["id"] == service_id), None)
    if not svc:
        return None
    states = get_unit_states([f"{u}.service" for u in svc["units"]])
    return _installed_unit(svc["units"], states)


def service_action(service_id: str, action: str) -> Dict[str, Any]:
    if platform.system() != "Linux":
        return {"ok": False, "message": "Nur auf dem Raspberry Pi verfügbar."}
    unit = _resolve_unit(service_id)
    if not unit:
        return {"ok": False, "message": "Dienst ist auf diesem System nicht installiert."}
    if service_id == "updater":
        if action != "run":
            return {"ok": False, "message": "Unbekannte Aktion."}
        action = "start"
    elif action not in SERVICE_ACTIONS:
        return {"ok": False, "message": "Unbekannte Aktion."}

    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        # Exactly the command form allowed in /etc/sudoers.d/pi-dashboard-restart
        res = subprocess.run(["sudo", "-n", systemctl, action, "--no-block", unit],
                             capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {"ok": False, "message": str(e)}
    if res.returncode != 0:
        err = res.stderr.strip()
        if "password" in err.lower() or "not allowed" in err.lower():
            return {"ok": False, "message": "Keine Berechtigung – install.sh auf dem Pi erneut ausführen."}
        return {"ok": False, "message": err[:200] or f"systemctl {action} fehlgeschlagen"}
    verb = {"start": "wird gestartet", "stop": "wird gestoppt", "restart": "wird neu gestartet"}[action]
    return {"ok": True, "message": f"{unit} {verb}…", "unit": unit}


def service_journal(service_id: str, lines: int = 150) -> Dict[str, Any]:
    if platform.system() != "Linux":
        return {"ok": False, "message": "Nur auf dem Raspberry Pi verfügbar."}
    unit = _resolve_unit(service_id)
    if not unit:
        return {"ok": False, "message": "Dienst ist auf diesem System nicht installiert."}
    try:
        res = subprocess.run(["journalctl", "-u", unit, "-n", str(max(10, min(lines, 500))),
                              "--no-pager", "-o", "short-iso"],
                             capture_output=True, text=True, timeout=10, errors="replace")
    except Exception as e:
        return {"ok": False, "message": str(e)}
    log = res.stdout.strip()
    hint = ""
    if "insufficient permissions" in (res.stdout + res.stderr).lower() or not log or log.startswith("-- No entries"):
        user = os.environ.get("USER", "$USER")
        hint = f"Für alle Log-Zeilen: sudo usermod -aG systemd-journal {user} && sudo systemctl restart raspberry-dashboard"
    return {"ok": True, "unit": unit, "log": log or "(keine Einträge)", "hint": hint}


def available_services() -> List[Dict[str, str]]:
    """All tiles the services widget can show (for the settings page)."""
    return [{"id": s["id"], "name": s["name"]} for s in TRACKED_SERVICES] + \
           [{"id": "updater", "name": "Auto-Updater"}]


def get_mounted_drives() -> List[Dict[str, Any]]:
    """Detect all mounted partitions and external USB drives."""
    drives = []
    seen_mounts = set()

    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if mount in seen_mounts:
            continue
        seen_mounts.add(mount)

        # Ignore special virtual mounts and container bind mounts (k3s volumes live inside "/")
        import storage
        if any(mount.startswith(p) for p in ["/proc", "/sys", "/dev"]) or storage.is_noise(mount):
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
                label = f"Boot-Partition ({mount})"
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
