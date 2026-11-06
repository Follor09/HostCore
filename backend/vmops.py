"""
Day-to-day VM operations on top of vms.py (page /vms):

- Templates: tested base configurations (size, network, cloud-init without passwords) a new VM
  starts from; the service catalog (servicecatalog.py) creates VMs from them too.
- Backups: full, self-contained copies of a VM's disks in a storage pool, taken while it runs
  (virsh backup-begin), restored as a new VM. Manifests live in vms/backups, written by vmtool.py.
- Schedules: snapshots, backups, start and shutdown by weekday and time, with retention.
- Live migration between HostCore servers: the main dashboard asks the target to accept the
  source's dedicated SSH key (limited to virt-ssh-helper) and hands over the target's host keys
  through the authenticated agent connection, then the source checks everything and migrates.
"""

import json
import logging
import os
import platform
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

import settings_store
import tasks
import vms

logger = logging.getLogger("pi_dashboard.vmops")

TEMPLATES_KEY = "vm_templates"
SCHEDULES_KEY = "vm_schedules"
BACKUP_KEY = "vm_backup"
BACKUP_DIR = os.path.join(vms.SPEC_DIR, "backups")
MIGRATE_KEY = os.path.join(vms.SPEC_DIR, "migrate_key")
ACTIONS = {"snapshot": "Snapshot", "backup": "Sicherung", "start": "Starten", "shutdown": "Herunterfahren"}
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
ID_RE = re.compile(r"^[0-9a-f]{10}$")
KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]{60,100}$")
HOSTKEY_RE = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]{60,800}$")
HOST_RE = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:_-]{0,252}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAX_TEMPLATES = 100
MAX_SCHEDULES = 200
CATCH_UP = 1800      # snapshots and backups missed by a restart still run within 30 minutes
_lock = threading.Lock()


class VmOpsError(vms.VmError):
    pass


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def templates() -> List[Dict[str, Any]]:
    return sorted(settings_store.get(TEMPLATES_KEY, []) or [], key=lambda t: t["name"].lower())


def template(tid: str) -> Dict[str, Any]:
    t = next((x for x in templates() if x["id"] == tid), None)
    if not t:
        raise VmOpsError("Vorlage nicht gefunden.")
    return t


def _clean_template(f: Dict[str, Any]) -> Dict[str, Any]:
    name = str(f.get("name") or "").strip()
    if not 1 <= len(name) <= 80:
        raise VmOpsError("Name der Vorlage fehlt (höchstens 80 Zeichen).")
    source = str(f.get("source") or "cloud")
    if source not in ("cloud", "iso", "pxe"):
        raise VmOpsError("Vorlagen gibt es für Cloud-Image, ISO und Netzwerkstart.")
    try:
        vcpus, memory, disk = int(f.get("vcpus") or 2), int(f.get("memory_mb") or 2048), int(f.get("disk_gb") or 20)
    except (TypeError, ValueError):
        raise VmOpsError("vCPUs, Arbeitsspeicher und Laufwerk müssen Zahlen sein.")
    if not (1 <= vcpus <= 256 and 128 <= memory <= 4194304 and 1 <= disk <= 65536):
        raise VmOpsError("vCPUs, Arbeitsspeicher oder Laufwerk außerhalb des möglichen Bereichs.")
    login = str(f.get("user") or "").strip()
    if login and not vms.USER_RE.match(login):
        raise VmOpsError("Ungültiger Benutzername.")
    keys = vms._ssh_keys(str(f.get("ssh_keys") or ""))
    return {"name": name, "description": str(f.get("description") or "").strip()[:500], "source": source,
            "image": str(f.get("image") or "")[:128], "iso": str(f.get("iso") or "")[:512], "osinfo": str(f.get("osinfo") or "")[:40],
            "vcpus": vcpus, "memory_mb": memory, "disk_gb": disk, "pool": str(f.get("pool") or "default")[:64],
            "network": str(f.get("network") or "network:default")[:80], "firmware": "uefi" if f.get("firmware") == "uefi" else "bios",
            "autostart": bool(f.get("autostart")), "user": login, "ssh_keys": "\n".join(keys), "agent": bool(f.get("agent", True)),
            "upgrade": bool(f.get("upgrade"))}


def template_save(tid: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    clean = _clean_template(fields)
    with _lock:
        items = list(settings_store.get(TEMPLATES_KEY, []) or [])
        if tid:
            t = next((x for x in items if x["id"] == tid), None)
            if not t:
                raise VmOpsError("Vorlage nicht gefunden.")
            t.update(clean, edited=int(time.time()), edited_by=user)
        else:
            if len(items) >= MAX_TEMPLATES:
                raise VmOpsError(f"Höchstens {MAX_TEMPLATES} Vorlagen.")
            items.append({"id": secrets.token_hex(5), **clean, "user_created": user, "created": int(time.time())})
        settings_store.set(TEMPLATES_KEY, items)
    return {"templates": templates()}


def template_delete(tid: str) -> Dict[str, Any]:
    template(tid)
    with _lock:
        settings_store.set(TEMPLATES_KEY, [x for x in settings_store.get(TEMPLATES_KEY, []) or [] if x["id"] != tid])
    return {"templates": templates()}


def create_from_template(tid: str, name: str, ssh_keys: str, user: str) -> Dict[str, Any]:
    """A VM from a template without the dialog (service catalog). Needs an SSH key: no password is stored."""
    t = template(tid)
    keys = "\n".join(k for k in (t.get("ssh_keys", ""), ssh_keys.strip()) if k)
    if t["source"] == "cloud" and not keys:
        raise VmOpsError("Die Vorlage enthält keinen SSH-Schlüssel – bitte einen angeben.")
    return vms.create({**t, "name": name, "ssh_keys": keys, "hostname": "", "description": f"Vorlage: {t['name']}"}, user)


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
def backup_config() -> Dict[str, Any]:
    return {"pool": str((settings_store.get(BACKUP_KEY, {}) or {}).get("pool") or "default")}


def backup_config_save(pool: str) -> Dict[str, Any]:
    vms.check_name(pool, "Pool")
    settings_store.set(BACKUP_KEY, {"pool": pool})
    return backup_config()


def _manifests() -> List[Dict[str, Any]]:
    out = []
    try:
        names = os.listdir(BACKUP_DIR)
    except OSError:
        return []
    for name in names:
        if name.endswith(".json"):
            try:
                with open(os.path.join(BACKUP_DIR, name)) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda m: -m["ts"])


def _manifest(bid: str) -> Dict[str, Any]:
    if not ID_RE.match(bid):
        raise VmOpsError("Sicherung nicht gefunden.")
    m = next((x for x in _manifests() if x["id"] == bid), None)
    if not m:
        raise VmOpsError("Sicherung nicht gefunden.")
    return m


def backups() -> Dict[str, Any]:
    items = [{k: v for k, v in m.items() if k != "xml"} | {"size": sum(d["allocation"] for d in m["disks"])} for m in _manifests()]
    last: Dict[str, int] = {}
    for m in items:
        last[m["vm"]] = max(last.get(m["vm"], 0), m["ts"])
    return {"backups": items, "config": backup_config(), "total": sum(m["size"] for m in items), "last_per_vm": last}


def backup_start(name: str, user: str, keep: int = 0, schedule: str = "", pool: str = "") -> Dict[str, Any]:
    vms.check_name(name)
    vms._domain(name, fresh=True)
    pool = pool or backup_config()["pool"]
    vms.check_name(pool, "Pool")
    spec = {"id": secrets.token_hex(5), "vm": name, "pool": pool, "user": user, "keep": int(keep), "schedule": schedule}
    return vms._tool_task(f"VM {name} sichern", "backup", spec, user, key=f"vm:{name}", vm=name)


def backup_restore(bid: str, name: str, pool: str, user: str) -> Dict[str, Any]:
    m = _manifest(bid)
    vms.check_name(name)
    pool = pool or m["pool"]
    vms.check_name(pool, "Pool")
    if name in vms._names():
        raise VmOpsError(f"Eine VM {name} gibt es schon – bitte einen anderen Namen wählen.")
    return vms._tool_task(f"VM {name} aus Sicherung wiederherstellen", "restore", {"manifest": m, "name": name, "pool": pool}, user,
                          key=f"vm:{name}", vm=name)


def backup_delete(bid: str) -> Dict[str, Any]:
    m = _manifest(bid)
    for d in m["disks"]:
        vms._virsh("vol-delete", "--pool", m["pool"], d["volume"], check=False)
    os.unlink(os.path.join(BACKUP_DIR, f"{bid}.json"))
    return backups()


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
def schedules() -> Dict[str, Any]:
    items = sorted(settings_store.get(SCHEDULES_KEY, []) or [], key=lambda s: (s["vm"].lower(), s["time"]))
    return {"schedules": items, "actions": ACTIONS}


def schedule_save(sid: Optional[str], f: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    vm = vms.check_name(str(f.get("vm") or ""))
    action = str(f.get("action") or "")
    if action not in ACTIONS:
        raise VmOpsError("Unbekannte Aktion.")
    at = str(f.get("time") or "")
    if not TIME_RE.match(at):
        raise VmOpsError("Uhrzeit als HH:MM angeben.")
    try:
        days = sorted({int(d) for d in f.get("days") or []})
        keep = int(f.get("keep") or 0)
    except (TypeError, ValueError):
        raise VmOpsError("Ungültige Wochentage oder Aufbewahrung.")
    if not days or any(d < 0 or d > 6 for d in days):
        raise VmOpsError("Mindestens einen Wochentag wählen.")
    if action in ("snapshot", "backup") and not 1 <= keep <= 365:
        raise VmOpsError("Aufbewahrung: 1 bis 365 Stück.")
    clean = {"vm": vm, "action": action, "time": at, "days": days, "keep": keep, "enabled": bool(f.get("enabled", True))}
    with _lock:
        items = list(settings_store.get(SCHEDULES_KEY, []) or [])
        if sid:
            s = next((x for x in items if x["id"] == sid), None)
            if not s:
                raise VmOpsError("Zeitplan nicht gefunden.")
            s.update(clean)
        else:
            if len(items) >= MAX_SCHEDULES:
                raise VmOpsError(f"Höchstens {MAX_SCHEDULES} Zeitpläne.")
            items.append({"id": secrets.token_hex(5), **clean, "user": user, "created": int(time.time()), "last_run": 0, "last_state": "", "last_error": ""})
        settings_store.set(SCHEDULES_KEY, items)
    return schedules()


def schedule_delete(sid: str) -> Dict[str, Any]:
    with _lock:
        items = settings_store.get(SCHEDULES_KEY, []) or []
        if not any(x["id"] == sid for x in items):
            raise VmOpsError("Zeitplan nicht gefunden.")
        settings_store.set(SCHEDULES_KEY, [x for x in items if x["id"] != sid])
    return schedules()


def _record(sid: str, **fields: Any) -> None:
    with _lock:
        items = list(settings_store.get(SCHEDULES_KEY, []) or [])
        s = next((x for x in items if x["id"] == sid), None)
        if s:
            s.update(fields)
            settings_store.set(SCHEDULES_KEY, items)


def _prune_snapshots(vm: str, keep: int) -> None:
    autos = [s["name"] for s in vms.snapshots(vm) if s["name"].startswith("auto-")]
    for name in sorted(autos)[:-keep]:   # the name holds the date, so this is oldest first
        vms.snapshot_action(vm, name, "delete")


def _slot(s: Dict[str, Any], now: float) -> int:
    """The latest planned time at or before now, within the last two days (0 = none)."""
    hour, minute = map(int, s["time"].split(":"))
    for back in (0, 1):
        t = time.localtime(now - back * 86400)
        slot = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1)))
        if slot <= now and time.localtime(slot).tm_wday in s["days"]:
            return slot
    return 0


def _due(s: Dict[str, Any], now: float) -> bool:
    if not s.get("enabled"):
        return False
    slot = _slot(s, now)
    if not slot or slot <= max(s.get("last_run", 0), s.get("created", 0)):
        return False
    window = CATCH_UP if s["action"] in ("snapshot", "backup") else 300
    return now - slot <= window


def _run(s: Dict[str, Any], now: float) -> None:
    vm, action = s["vm"], s["action"]
    try:
        if vm not in vms._names():
            raise VmOpsError("VM gibt es nicht mehr.")
        state = vms._state(vm)
        if action == "snapshot":
            vms.snapshot_create(vm, time.strftime("auto-%Y%m%d-%H%M", time.localtime(now)), "Automatischer Snapshot")
            _prune_snapshots(vm, s["keep"])
        elif action == "backup":
            task = backup_start(vm, "Zeitplan", keep=s["keep"], schedule=s["id"])
            _record(s["id"], last_run=int(now), last_state="running", last_error="", task=task["id"])
            return
        elif action == "start" and state in ("shutoff", "crashed"):
            vms.action(vm, "start")
        elif action == "shutdown" and state == "running":
            vms.action(vm, "shutdown")
        _record(s["id"], last_run=int(now), last_state="ok", last_error="")
    except (vms.VmError, tasks.TaskError) as e:
        logger.warning(f"VM schedule {vm}/{action}: {e}")
        _record(s["id"], last_run=int(now), last_state="error", last_error=str(e)[:300])


def tick(now: Optional[float] = None) -> None:
    """Once a minute. Every schedule runs at most once per planned time, also after a restart."""
    now = now or time.time()
    items = settings_store.get(SCHEDULES_KEY, []) or []
    if not items or not vms.enabled():
        return
    for s in items:
        if _due(s, now):
            _run(s, now)


def _backup_done(task: Dict[str, Any]) -> None:
    items = settings_store.get(SCHEDULES_KEY, []) or []
    s = next((x for x in items if x.get("task") == task["id"]), None)
    if s:
        ok = task.get("state") == "ok"
        _record(s["id"], last_state="ok" if ok else "error", last_error="" if ok else f"Aufgabe: {task.get('state')}")


tasks.on_done("vm", _backup_done)


def alerts() -> List[Dict[str, Any]]:
    return [{"level": "warning", "text": f"VM-Zeitplan {ACTIONS.get(s['action'], s['action'])} für {s['vm']} fehlgeschlagen: {s.get('last_error') or 'siehe Aufgaben'}"}
            for s in settings_store.get(SCHEDULES_KEY, []) or [] if s.get("enabled") and s.get("last_state") == "error"]


# ---------------------------------------------------------------------------
# Live migration
# ---------------------------------------------------------------------------
def _ssh_port() -> int:
    try:
        with open("/etc/ssh/sshd_config") as f:
            m = re.search(r"^\s*Port\s+(\d+)", f.read(), re.M | re.I)
        return int(m.group(1)) if m else 22
    except OSError:
        return 22


def _host_keys() -> List[str]:
    out = []
    for name in ("ed25519", "ecdsa", "rsa"):
        try:
            with open(f"/etc/ssh/ssh_host_{name}_key.pub") as f:
                key = " ".join(f.read().split()[:2])
            if HOSTKEY_RE.match(key):
                out.append(key)
        except OSError:
            pass
    return out


def _addresses() -> List[str]:
    out = []
    for iface, addrs in psutil.net_if_addrs().items():
        if iface == "lo" or iface.startswith(("virbr", "vnet", "docker", "br-", "veth")):
            continue
        out += [a.address for a in addrs if a.family == socket.AF_INET]
    return out


def source_key() -> Dict[str, Any]:
    """The source's own key pair for migrations (created on first use, never leaves this server)."""
    vms.require_ready()
    if not os.path.exists(MIGRATE_KEY):
        os.makedirs(vms.SPEC_DIR, mode=0o700, exist_ok=True)
        res = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "hostcore-migration", "-f", MIGRATE_KEY],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise VmOpsError(f"SSH-Schlüssel ließ sich nicht erzeugen: {res.stderr.strip()[:200]}")
    with open(MIGRATE_KEY + ".pub") as f:
        return {"key": " ".join(f.read().split()[:2]), "hostname": platform.node()}


def prepare_target(key: str, source: str) -> Dict[str, Any]:
    """On the target: accept the source's key - only for virt-ssh-helper - and describe this host."""
    vms.require_ready()
    if not KEY_RE.match(key):
        raise VmOpsError("Ungültiger Schlüssel der Quelle.")
    helper = shutil.which("virt-ssh-helper") or "/usr/bin/virt-ssh-helper"
    if not os.path.exists(helper):
        raise VmOpsError("virt-ssh-helper fehlt auf dem Ziel – libvirt 6.8 oder neuer nötig.")
    home = os.path.expanduser("~")
    folder = os.path.join(home, ".ssh")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, "authorized_keys")
    try:
        with open(path) as f:
            present = f.read()
    except OSError:
        present = ""
    if key not in present:
        label = re.sub(r"[^A-Za-z0-9._-]", "-", source)[:60]
        with open(path, "a") as f:
            if present and not present.endswith("\n"):
                f.write("\n")
            f.write(f'restrict,command="{helper} qemu:///system" {key} hostcore-migration-{label}\n')
        os.chmod(path, 0o600)
    pools = vms.pools()["pools"]
    nets = vms.networks()["networks"]
    vm = psutil.virtual_memory()
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f:
            cpu = (re.search(r"^model name\s*:\s*(.+)$", f.read(), re.M) or [0, ""])[1]
    except OSError:
        pass
    return {"hostname": platform.node(), "arch": vms.host_arch(), "cpu": cpu, "memory_free_mb": int(vm.available / 1048576),
            "ssh_user": pwd.getpwuid(os.getuid()).pw_name, "ssh_port": _ssh_port(), "host_keys": _host_keys(), "addresses": _addresses(),
            "pools": [{"name": p["name"], "path": p["path"], "available": p["available"], "active": p["active"]} for p in pools],
            "networks": [n["name"] for n in nets if n["active"]], "bridges": [n for n in vms.host_interfaces().get("bridges", [])],
            "isos": [v["path"] for v in vms.iso_volumes()], "vms": vms._names()}


def _trust(info: Dict[str, Any], address: str) -> str:
    """Pin the target's host keys (they came over the authenticated agent connection), return the URI."""
    port, user = int(info["ssh_port"]), str(info["ssh_user"])
    if not HOST_RE.match(address) or not USER_RE.match(user):
        raise VmOpsError("Ungültige Zieladresse.")
    keys = [k for k in info.get("host_keys", []) if HOSTKEY_RE.match(k)]
    if not keys:
        raise VmOpsError("Das Ziel meldet keinen SSH-Hostschlüssel.")
    folder = os.path.expanduser("~/.ssh")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, "known_hosts")
    host = address if port == 22 else f"[{address}]:{port}"
    try:
        with open(path) as f:
            lines = [ln for ln in f.read().splitlines() if not ln.startswith(host + " ")]
    except OSError:
        lines = []
    lines += [f"{host} {k}" for k in keys]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    name = f"[{address}]" if ":" in address else address
    return f"qemu+ssh://{user}@{name}:{port}/system?keyfile={MIGRATE_KEY}&no_tty=1"


def check(name: str, info: Dict[str, Any], address: str, shared: bool) -> Dict[str, Any]:
    """Everything that can be known before a migration starts: blockers and warnings."""
    vms.check_name(name)
    d = vms._domain(name, fresh=True)
    blockers, warnings = [], []
    if vms._state(name) != "running":
        blockers.append("Nur laufende VMs lassen sich live verschieben. Für ausgeschaltete VMs: Sicherung und Wiederherstellung.")
    if info["arch"] != vms.host_arch():
        blockers.append(f"Andere Architektur auf dem Ziel ({info['arch']}).")
    if name in info["vms"]:
        blockers.append(f"Auf dem Ziel gibt es schon eine VM {name}.")
    if d["memory_mb"] > info["memory_free_mb"]:
        blockers.append(f"Zu wenig freier Arbeitsspeicher auf dem Ziel ({info['memory_free_mb']} MB frei, {d['memory_mb']} MB nötig).")
    if d["cpu_mode"] == "host-passthrough":
        local = ""
        try:
            with open("/proc/cpuinfo") as f:
                local = (re.search(r"^model name\s*:\s*(.+)$", f.read(), re.M) or [0, ""])[1]
        except OSError:
            pass
        if local != info.get("cpu"):
            warnings.append(f"Die VM nutzt die Host-CPU; das Ziel hat ein anderes Modell ({info.get('cpu') or 'unbekannt'}). Die Migration kann deshalb scheitern.")
    for i in d["interfaces"]:
        if i["type"] == "network" and i["source"] not in info["networks"]:
            blockers.append(f"Netzwerk {i['source']} ist auf dem Ziel nicht aktiv.")
        elif i["type"] == "bridge" and info["bridges"] and i["source"] not in info["bridges"]:
            blockers.append(f"Bridge {i['source']} fehlt auf dem Ziel.")
    disks = [x for x in d["disks"] if x["device"] != "cdrom"]
    total = 0
    for x in disks:
        path = x["source"]
        if x["pool"]:
            path = vms._virsh("vol-path", "--pool", x["pool"], x["volume"]).strip()
        pool = next((p for p in info["pools"] if p["active"] and p["path"] and path.startswith(p["path"].rstrip("/") + "/")), None)
        if not shared and not pool:
            blockers.append(f"Laufwerk {x['target']}: auf dem Ziel fehlt ein Speicher-Pool mit dem Ordner {os.path.dirname(path)}.")
        if not shared and pool:
            res = vms._virsh("vol-info", path, "--bytes", check=False)
            cap = int((re.search(r"Capacity:\s+(\d+)", res) or [0, "0"])[1])
            total += cap
            if pool.get("available") and cap > pool["available"]:
                blockers.append(f"Laufwerk {x['target']}: zu wenig Platz in Pool {pool['name']} auf dem Ziel.")
    for x in d["disks"]:
        if x["device"] == "cdrom" and x["source"] and x["source"] not in info["isos"]:
            blockers.append(f"ISO in {x['target']} gibt es auf dem Ziel nicht – vorher auswerfen.")
    if vms.snapshots(name):
        warnings.append("Snapshots werden nicht mitgenommen; libvirt kann die Migration deshalb ablehnen. Vorher löschen.")
    warnings.append("Die Daten gehen unverschlüsselt über die Ports 49152–49215 zum Ziel: nur im eigenen Netz oder VPN verwenden und diese Ports dort für die Quelle freigeben.")
    uri = ""
    if not blockers:
        uri = _trust(info, address)
        try:
            res = subprocess.run(["virsh", "-c", uri, "version"], capture_output=True, text=True, timeout=30, errors="replace",
                                 env={**os.environ, "LC_ALL": "C"})
            if res.returncode != 0:
                blockers.append(f"Verbindung per SSH zum Ziel fehlgeschlagen: {vms._clean_err(res.stderr)[:300]}")
        except subprocess.TimeoutExpired:
            blockers.append("Das Ziel antwortet nicht per SSH (Zeitüberschreitung).")
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "uri": uri, "copy_bytes": total, "memory_mb": d["memory_mb"]}


def migrate(name: str, info: Dict[str, Any], address: str, shared: bool, target_name: str, user: str) -> Dict[str, Any]:
    res = check(name, info, address, shared)
    if not res["ok"]:
        raise VmOpsError(res["blockers"][0])
    host = f"[{address}]" if ":" in address else address
    spec = {"vm": name, "uri": res["uri"], "migrate_uri": f"tcp://{host}", "shared": shared, "target_name": target_name}
    return vms._tool_task(f"VM {name} nach {target_name} verschieben", "migrate", spec, user, key=f"vm:{name}", vm=name)


# Orchestration on the main dashboard: source and target may each be this server or an agent
async def _on(remote: Optional[Dict[str, Any]], method: str, path: str, body: Any, user: str, local) -> Dict[str, Any]:
    import asyncio
    import fleet
    if remote:
        try:
            return await fleet._call(remote["id"], method, path, body, user)
        except (ValueError, OSError) as e:
            raise VmOpsError(f"{remote['name']}: {e}")
        except Exception as e:
            raise VmOpsError(f"{remote['name']}: nicht erreichbar ({type(e).__name__}).")
    return await asyncio.to_thread(local)


async def _prepare(source: Optional[Dict[str, Any]], target: Optional[Dict[str, Any]], user: str) -> Dict[str, Any]:
    key = await _on(source, "POST", "/api/vms/migration/key", {}, user, source_key)
    label = key["hostname"]
    return await _on(target, "POST", "/api/vms/migration/target", {"key": key["key"], "source": label}, user,
                     lambda: prepare_target(key["key"], label))


async def targets(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    import remotes
    out = [{"id": r["id"], "name": r["name"]} for r in remotes._all() if not source or r["id"] != source["id"]]
    if source:
        out.insert(0, {"id": "", "name": platform.node() or "Hauptserver"})
    return {"targets": out}


def _remote(target_id: str) -> Optional[Dict[str, Any]]:
    import remotes
    if not target_id:
        return None
    r = remotes.get(target_id)
    if not r:
        raise VmOpsError("Unbekannter Zielserver.")
    return r


def _default_address(target: Optional[Dict[str, Any]], info: Dict[str, Any]) -> str:
    if target:
        m = re.match(r"^https?://\[?([^\]/:]+)", target["url"])
        if m:
            return m.group(1)
    return (info.get("addresses") or [""])[0]


async def run_check(source: Optional[Dict[str, Any]], name: str, target_id: str, address: str, shared: bool, user: str) -> Dict[str, Any]:
    target = _remote(target_id)
    info = await _prepare(source, target, user)
    address = address.strip() or _default_address(target, info)
    if not address:
        raise VmOpsError("Adresse des Ziels angeben.")
    body = {"info": info, "address": address, "shared": shared}
    res = await _on(source, "POST", f"/api/vms/vm/{name}/migrate-check", body, user, lambda: check(name, info, address, shared))
    return {**res, "uri": "", "address": address, "target": info["hostname"], "addresses": info["addresses"]}


async def run_migrate(source: Optional[Dict[str, Any]], name: str, target_id: str, address: str, shared: bool, user: str) -> Dict[str, Any]:
    target = _remote(target_id)
    info = await _prepare(source, target, user)
    address = address.strip() or _default_address(target, info)
    target_name = target["name"] if target else info["hostname"]
    body = {"info": info, "address": address, "shared": shared, "target_name": target_name}
    return await _on(source, "POST", f"/api/vms/vm/{name}/migrate", body, user,
                     lambda: migrate(name, info, address, shared, target_name, user))
