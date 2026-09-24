"""
Homelab extras for the dashboard:
- System updates: pending apt packages (security separately), reboot-required flag
- NVMe health: temperature (hwmon) and SMART data (smartctl via a narrow sudo rule)
- Workloads: Docker containers and Kubernetes pods
Everything slow is cached so the Pi isn't hammered by open browser tabs.
"""

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

IS_LINUX = platform.system() == "Linux"


def _run(cmd: List[str], timeout: float = 5, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


class _Cache:
    """Tiny TTL cache so each expensive probe runs at most once per interval."""

    def __init__(self):
        self._data: Dict[str, tuple] = {}

    def get(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        hit = self._data.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._data[key] = (time.time(), value)
        return value

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)


_cache = _Cache()


# ---------------------------------------------------------------------------
# System updates (apt)
# ---------------------------------------------------------------------------
def _apt_updates() -> Dict[str, Any]:
    if not IS_LINUX or not shutil.which("apt"):
        return {"supported": False}
    try:
        out = _run(["apt", "list", "--upgradable"], timeout=30).stdout
    except Exception as e:
        return {"supported": True, "error": str(e)}

    packages = []
    for line in out.splitlines():
        # name/suite version arch [upgradable from: old]
        m = re.match(r"^(\S+?)/(\S+)\s+(\S+)\s+\S+\s+\[upgradable from: (\S+)\]", line)
        if m:
            packages.append({"name": m.group(1), "new": m.group(3), "old": m.group(4),
                             "security": "security" in m.group(2)})
    packages.sort(key=lambda p: (not p["security"], p["name"]))

    lists = glob.glob("/var/lib/apt/lists/*_InRelease")
    return {
        "supported": True,
        "count": len(packages),
        "security_count": sum(p["security"] for p in packages),
        "packages": packages[:500],
        "lists_updated": int(max(os.path.getmtime(f) for f in lists)) if lists else None,
        "reboot_required": os.path.exists("/var/run/reboot-required"),
    }


def get_updates() -> Dict[str, Any]:
    _check_apt_run_finished()
    return _cache.get("apt", 30 * 60, _apt_updates)


# apt update / upgrade, run as root by pi-dashboard-apt@<mode>.service (see apt-run.sh).
# Running it as a systemd unit means a dashboard restart can't kill apt halfway.
APT_STATE_DIR = os.environ.get("PI_DASHBOARD_STATE_DIR", "/run/pi-dashboard")
APT_MODES = ("update", "upgrade")
_apt_seen_finish = 0


def _apt_unit_running() -> bool:
    units = [f"pi-dashboard-apt@{m}.service" for m in APT_MODES]
    try:
        out = _run(["systemctl", "is-active", *units], timeout=3).stdout.split()
    except Exception:
        return False
    return any(state in ("active", "activating", "reloading") for state in out)


def _read_apt_status() -> Dict[str, Any]:
    try:
        with open(os.path.join(APT_STATE_DIR, "apt.status")) as f:
            return json.load(f)
    except Exception:
        return {}


def _check_apt_run_finished() -> None:
    """After a finished apt run, drop the cached package list so counts are fresh."""
    global _apt_seen_finish
    finished = _read_apt_status().get("finished") or 0
    if finished > _apt_seen_finish:
        _apt_seen_finish = finished
        _cache.invalidate("apt")


def get_apt_run() -> Dict[str, Any]:
    if not IS_LINUX:
        return {"supported": False, "running": False}
    status = _read_apt_status()
    running = _apt_unit_running()
    log = ""
    try:
        with open(os.path.join(APT_STATE_DIR, "apt.log"), errors="replace") as f:
            log = "".join(deque(f, maxlen=300))
    except Exception:
        pass
    _check_apt_run_finished()
    # Between "start" and the script's first write the old status is still on disk
    state = "running" if running else status.get("state", "idle")
    return {"supported": True, "running": running, "state": state, "mode": status.get("mode"),
            "exit_code": status.get("exit_code"), "started": status.get("started"),
            "finished": status.get("finished"), "log": log if running or status else ""}


def start_apt_run(mode: str) -> Dict[str, Any]:
    if mode not in APT_MODES:
        return {"ok": False, "message": "Unbekannte Aktion."}
    if not IS_LINUX:
        return {"ok": False, "message": "Nur auf dem Raspberry Pi verfügbar."}
    if _apt_unit_running():
        return {"ok": False, "message": "apt läuft bereits."}

    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        # Must match the sudoers rule written by install.sh exactly
        res = _run(["sudo", "-n", systemctl, "start", "--no-block", f"pi-dashboard-apt@{mode}.service"], timeout=10)
    except Exception as e:
        return {"ok": False, "message": str(e)}
    if res.returncode != 0:
        err = res.stderr.strip()
        if "password" in err.lower() or "not allowed" in err.lower():
            return {"ok": False, "message": "Keine Berechtigung – install.sh auf dem Pi erneut ausführen."}
        return {"ok": False, "message": err[:200] or "Start fehlgeschlagen."}
    return {"ok": True, "message": "Paketlisten werden aktualisiert…" if mode == "update" else "Update & Upgrade gestartet…"}


# ---------------------------------------------------------------------------
# NVMe health
# ---------------------------------------------------------------------------
NVME_DEVICE = "/dev/nvme0"


def _nvme_temp() -> Optional[float]:
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(f"{hw}/name") as f:
                if f.read().strip() != "nvme":
                    continue
            with open(f"{hw}/temp1_input") as f:
                return round(int(f.read().strip()) / 1000, 1)
        except Exception:
            continue
    return None


def _nvme_smart() -> Dict[str, Any]:
    smartctl = shutil.which("smartctl") or "/usr/sbin/smartctl"
    if not os.path.exists(smartctl):
        return {"smart": False, "hint": "smartmontools nicht installiert (install.sh erneut ausführen)"}
    try:
        # sudo -n: never prompt; allowed by the sudoers rule install.sh writes
        res = _run(["sudo", "-n", smartctl, "-j", "-a", NVME_DEVICE], timeout=10)
        data = json.loads(res.stdout or "{}")
    except Exception:
        return {"smart": False, "hint": "SMART nicht lesbar (install.sh erneut ausführen)"}

    log = data.get("nvme_smart_health_information_log")
    if not log:
        return {"smart": False, "hint": "SMART nicht lesbar (install.sh erneut ausführen)"}

    return {
        "smart": True,
        "model": data.get("model_name", ""),
        "passed": data.get("smart_status", {}).get("passed"),
        "percentage_used": log.get("percentage_used"),
        "available_spare": log.get("available_spare"),
        "tb_written": round(log.get("data_units_written", 0) * 512000 / 1e12, 2),
        "power_on_hours": log.get("power_on_hours"),
        "media_errors": log.get("media_errors", 0),
        "critical_warning": log.get("critical_warning", 0),
        "smart_temp": log.get("temperature"),
    }


def get_nvme_health() -> Dict[str, Any]:
    if not IS_LINUX or not os.path.exists(NVME_DEVICE):
        return {"present": False}
    smart = _cache.get("smart", 10 * 60, _nvme_smart)
    temp = _nvme_temp()
    return {"present": True, **smart, "temp": temp if temp is not None else smart.get("smart_temp")}


# ---------------------------------------------------------------------------
# Workloads: Docker + Kubernetes
# ---------------------------------------------------------------------------
def _docker() -> Dict[str, Any]:
    if not shutil.which("docker"):
        return {"available": False}
    try:
        res = _run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=8)
    except Exception as e:
        return {"available": True, "error": str(e)}
    if res.returncode != 0:
        err = res.stderr.strip()
        if "permission denied" in err.lower():
            return {"available": True, "error": "Keine Berechtigung für Docker.",
                    "hint": f"sudo usermod -aG docker {os.environ.get('USER', '$USER')} && sudo systemctl restart raspberry-dashboard"}
        return {"available": True, "error": err[:200] or "docker ps fehlgeschlagen"}

    containers = []
    for line in res.stdout.splitlines():
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        state = c.get("State", "")
        health = "unhealthy" if "(unhealthy)" in c.get("Status", "") else ""
        containers.append({
            "name": c.get("Names", ""),
            "image": c.get("Image", ""),
            "state": state,
            "status": c.get("Status", ""),
            "ports": c.get("Ports", ""),
            "level": "error" if health or (state == "exited" and not c.get("Status", "").startswith("Exited (0)"))
                     or state in ("dead", "restarting") else "active" if state == "running" else "inactive",
        })
    containers.sort(key=lambda c: ({"error": 0, "active": 1, "inactive": 2}[c["level"]], c["name"]))
    return {"available": True, "containers": containers}


def _kube_cmd() -> Optional[List[str]]:
    if shutil.which("kubectl"):
        return ["kubectl"]
    if shutil.which("k3s"):
        return ["k3s", "kubectl"]
    return None


def _kube_env() -> Optional[dict]:
    env = dict(os.environ)
    if env.get("KUBECONFIG"):
        return env
    home_cfg = os.path.expanduser("~/.kube/config")
    for cfg in (home_cfg, "/etc/rancher/k3s/k3s.yaml", "/etc/rancher/rke2/rke2.yaml"):
        if os.path.exists(cfg) and os.access(cfg, os.R_OK):
            env["KUBECONFIG"] = cfg
            return env
    return None


def _age(ts: str) -> str:
    try:
        start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ""
    s = int((datetime.now(timezone.utc) - start).total_seconds())
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit}"
    return f"{s}s"


def _kubernetes() -> Dict[str, Any]:
    cmd = _kube_cmd()
    if not cmd:
        return {"available": False}
    env = _kube_env()
    if env is None:
        return {"available": True, "error": "Keine lesbare kubeconfig gefunden.",
                "hint": "mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config"}
    try:
        res = _run(cmd + ["get", "pods", "-A", "-o", "json", "--request-timeout=5s"], timeout=10, env=env)
    except Exception as e:
        return {"available": True, "error": str(e)}
    if res.returncode != 0:
        return {"available": True, "error": res.stderr.strip()[:200] or "kubectl fehlgeschlagen"}

    pods = []
    for item in json.loads(res.stdout).get("items", []):
        meta, status, spec = item.get("metadata", {}), item.get("status", {}), item.get("spec", {})
        cstatuses = status.get("containerStatuses", []) or []
        ready = sum(1 for c in cstatuses if c.get("ready"))
        restarts = sum(c.get("restartCount", 0) for c in cstatuses)
        phase = status.get("phase", "Unknown")
        # A waiting reason like CrashLoopBackOff says more than the phase
        reason = next((c["state"]["waiting"].get("reason") for c in cstatuses
                       if c.get("state", {}).get("waiting")), None)
        display = reason or phase
        if phase == "Succeeded":
            level = "inactive"
        elif reason or phase in ("Failed", "Unknown") or (phase == "Running" and ready < len(cstatuses)):
            level = "error" if reason not in ("ContainerCreating", "PodInitializing") else "inactive"
        else:
            level = "active" if phase == "Running" else "inactive"
        pods.append({
            "namespace": meta.get("namespace", ""),
            "name": meta.get("name", ""),
            "status": display,
            "ready": f"{ready}/{len(cstatuses) or len(spec.get('containers', []))}",
            "restarts": restarts,
            "age": _age(meta.get("creationTimestamp", "")),
            "node": spec.get("nodeName", ""),
            "level": level,
        })
    pods.sort(key=lambda p: ({"error": 0, "active": 1, "inactive": 2}[p["level"]], p["namespace"], p["name"]))
    return {"available": True, "pods": pods}


def get_workloads() -> Dict[str, Any]:
    return {
        "docker": _cache.get("docker", 15, _docker),
        "kubernetes": _cache.get("kube", 15, _kubernetes),
    }


def get_all() -> Dict[str, Any]:
    return {
        "updates": get_updates(),
        "nvme": get_nvme_health(),
        "workloads": get_workloads(),
    }
