"""
Virtual machines (page /vms), modelled on Cockpit's "Virtual machines": KVM guests through
libvirt on any Debian/Ubuntu host - and on the Pi 5 (arm64 guests).

- Overview: every VM with state, vCPUs, memory, CPU load, disk/network throughput and IP
  addresses (one `virsh domstats` call plus the cached domain XML), and what the host has left
- Control: start, shut down, reboot, force off / reset, pause / resume, autostart, delete
- Details: disks (add, detach, ISO), network interfaces, snapshots, description, vCPUs and memory
- Create: from a cloud image (Debian / Ubuntu, checksum checked) with cloud-init (user, SSH key,
  hostname, guest agent), from an ISO, an existing disk or the network (PXE) - as a task (vmtool.py)
- Virtual networks and storage pools: start / stop, autostart, a new NAT network or folder pool
- Console: serial (virsh console in a pty, xterm.js) and graphical (noVNC to the VNC port on 127.0.0.1)

Everything goes through virsh against qemu:///system, so the dashboard user needs the libvirt
group (pi-helper.sh libvirt-setup). That group is as powerful as root - like the docker group -
which is why managing VMs has its own permission and asks for the password again.
Names are checked against strict patterns and virsh always gets an argument list (no shell).
"""

import grp
import ipaddress
import json
import logging
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import psutil

import settings_store
import system_info
import tasks

logger = logging.getLogger("pi_dashboard.vms")

URI = "qemu:///system"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vmtool.py")
SPEC_DIR = os.path.join(BASE_DIR, "vms")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
VOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
TARGET_RE = re.compile(r"^(vd|sd|hd|xvd)[a-z]{1,2}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HEX_RE = re.compile(r"^([0-9a-f]{64}|[0-9a-f]{128})$")
BASE_PREFIX = "picore-base-"
TICKET_TTL = 60

STATES = {1: "running", 2: "running", 3: "paused", 4: "shutdown", 5: "shutoff", 6: "crashed", 7: "pmsuspended"}
STATE_LABELS = {"running": "Läuft", "paused": "Angehalten", "shutdown": "Fährt herunter", "shutoff": "Aus",
                "crashed": "Abgestürzt", "pmsuspended": "Ruhezustand"}
STATE_LEVEL = {"running": "good", "paused": "warning", "shutdown": "warning", "shutoff": "", "crashed": "critical",
               "pmsuspended": "warning"}

# Power actions: (virsh command, message, allowed in these states)
ACTIONS = {
    "start": ("start", "{} wird gestartet.", ("shutoff", "crashed")),
    "shutdown": ("shutdown", "{} fährt herunter.", ("running",)),
    "reboot": ("reboot", "{} startet neu.", ("running",)),
    "forceoff": ("destroy", "{} wurde ausgeschaltet.", ("running", "paused", "shutdown", "crashed", "pmsuspended")),
    "reset": ("reset", "{} wurde zurückgesetzt.", ("running",)),
    "suspend": ("suspend", "{} ist angehalten.", ("running",)),
    "resume": ("resume", "{} läuft weiter.", ("paused",)),
}

# Cloud images (official builds, checked against the SHA sums published next to them)
CATALOG = [
    {"id": "debian-13", "name": "Debian 13 (Trixie)", "osinfo": "debian13",
     "url": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-{arch}.qcow2",
     "sums": "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS", "user": "debian"},
    {"id": "debian-12", "name": "Debian 12 (Bookworm)", "osinfo": "debian12",
     "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-{arch}.qcow2",
     "sums": "https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS", "user": "debian"},
    {"id": "ubuntu-26.04", "name": "Ubuntu 26.04 LTS", "osinfo": "ubuntu26.04",
     "url": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-{arch}.img",
     "sums": "https://cloud-images.ubuntu.com/resolute/current/SHA256SUMS", "user": "ubuntu"},
    {"id": "ubuntu-24.04", "name": "Ubuntu 24.04 LTS", "osinfo": "ubuntu24.04",
     "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-{arch}.img",
     "sums": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS", "user": "ubuntu"},
    {"id": "ubuntu-22.04", "name": "Ubuntu 22.04 LTS", "osinfo": "ubuntu22.04",
     "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-{arch}.img",
     "sums": "https://cloud-images.ubuntu.com/jammy/current/SHA256SUMS", "user": "ubuntu"},
]

PACKAGES = {
    "amd64": ["qemu-system-x86", "qemu-utils", "libvirt-daemon-system", "libvirt-clients", "virtinst", "ovmf", "xorriso"],
    "arm64": ["qemu-system-arm", "qemu-utils", "qemu-efi-aarch64", "libvirt-daemon-system", "libvirt-clients", "virtinst", "xorriso"],
}

_lock = threading.Lock()
_xml_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_ip_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_prev: Dict[str, Tuple[float, Dict[str, float]]] = {}
_status_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_tickets: Dict[str, Tuple[str, float, str]] = {}   # ticket -> (user, expires, vm)
XML_TTL = 15
IP_TTL = 30


class VmError(Exception):
    pass


class VmDisabled(VmError):
    pass


# ---------------------------------------------------------------------------
# Feature switch: never on a Raspberry Pi, elsewhere on unless switched off in the settings
# ---------------------------------------------------------------------------
FEATURE_KEY = "vms_enabled"


def available() -> bool:
    return sys.platform.startswith("linux") and host_arch() in PACKAGES and not system_info.is_raspberry_pi()


def enabled() -> bool:
    return available() and bool(settings_store.get(FEATURE_KEY, True))


def feature() -> Dict[str, Any]:
    return {"available": available(), "enabled": enabled(), "pi": system_info.is_raspberry_pi()}


def set_enabled(on: bool) -> Dict[str, Any]:
    if not available():
        raise VmError("Virtuelle Maschinen gibt es auf diesem Gerät nicht (Raspberry Pi oder nicht unterstützte Plattform).")
    settings_store.set(FEATURE_KEY, bool(on))
    system_info.reset_capabilities()
    _invalidate()
    return {**feature(), "message": "Virtuelle Maschinen sind eingeschaltet." if on else
            "Virtuelle Maschinen sind ausgeschaltet – vorhandene VMs laufen weiter, werden hier aber nicht mehr verwaltet."}


def require_enabled() -> None:
    if not enabled():
        raise VmDisabled("Virtuelle Maschinen sind auf diesem Server ausgeschaltet (Einstellungen → Virtuelle Maschinen)."
                         if available() else "Virtuelle Maschinen gibt es auf einem Raspberry Pi nicht.")


# ---------------------------------------------------------------------------
# virsh
# ---------------------------------------------------------------------------
def _clean_err(text: str) -> str:
    lines = [re.sub(r"^error:\s*", "", l.strip()) for l in (text or "").splitlines() if l.strip()]
    lines = [l for l in lines if not l.startswith("Failed to ") or len(lines) == 1] or lines
    return " ".join(lines)[:400]


def _virsh(*args: str, timeout: float = 30, check: bool = True) -> str:
    if not shutil.which("virsh"):
        raise VmError("libvirt ist nicht installiert.")
    try:
        res = subprocess.run(["virsh", "-c", URI, *args], capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "LC_ALL": "C", "LANG": "C"}, errors="replace")
    except subprocess.TimeoutExpired:
        raise VmError(f"virsh {args[0]}: Zeitüberschreitung.")
    if check and res.returncode != 0:
        err = _clean_err(res.stderr)
        if "Permission denied" in err or "authentication unavailable" in err:
            err = "Kein Zugriff auf libvirt – der Dashboard-Benutzer braucht die Gruppe libvirt (Virtualisierung einrichten)."
        raise VmError(err or f"virsh {args[0]} fehlgeschlagen.")
    return res.stdout


def _virsh_xml(args: List[str], xml: str, timeout: float = 30) -> str:
    """virsh commands that take an XML file (define, attach-device ...)."""
    os.makedirs(SPEC_DIR, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=SPEC_DIR, suffix=".xml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(xml)
        return _virsh(*[path if a == "{xml}" else a for a in args], timeout=timeout)
    finally:
        os.unlink(path)


def check_name(name: str, what: str = "VM") -> str:
    if not NAME_RE.match(name or ""):
        raise VmError(f"Ungültiger Name für {what}: Buchstaben, Ziffern, - _ . (max. 63 Zeichen).")
    return name


def host_arch() -> str:
    m = platform.machine()
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(m, m)


def _invalidate(name: str = "") -> None:
    if name:
        _xml_cache.pop(name, None)
        _ip_cache.pop(name, None)
    else:
        _xml_cache.clear()
    _status_cache["ts"] = 0.0


# ---------------------------------------------------------------------------
# Setup state
# ---------------------------------------------------------------------------
def _missing_packages() -> List[str]:
    want = PACKAGES.get(host_arch(), [])
    if not want or not shutil.which("dpkg-query"):
        return []
    res = subprocess.run(["dpkg-query", "-W", "-f", "${Package} ${db:Status-Abbrev}\n", *want], capture_output=True, text=True, timeout=20)
    have = {l.split()[0] for l in res.stdout.splitlines() if len(l.split()) > 1 and l.split()[1].startswith("ii")}
    return [p for p in want if p not in have]


def _group_state() -> Dict[str, bool]:
    user = pwd.getpwuid(os.getuid()).pw_name
    try:
        g = grp.getgrnam("libvirt")
    except KeyError:
        return {"exists": False, "member": False, "active": False}
    member = user in g.gr_mem or pwd.getpwuid(os.getuid()).pw_gid == g.gr_gid
    return {"exists": True, "member": member, "active": g.gr_gid in os.getgroups() or os.getuid() == 0}


def _virt_flag() -> bool:
    """CPU supports hardware virtualisation (x86: vmx/svm; arm64 has it when the kernel offers /dev/kvm)."""
    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
        return bool(re.search(r"^flags\s*:.*\b(vmx|svm)\b", text, re.M))
    except OSError:
        return False


def _detect_virt() -> str:
    if not shutil.which("systemd-detect-virt"):
        return ""
    try:
        out = subprocess.run(["systemd-detect-virt", "--vm"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return "" if out in ("", "none") else out


def status(fresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not fresh and _status_cache["data"] and now - _status_cache["ts"] < 10:
        return _status_cache["data"]
    arch = host_arch()
    data: Dict[str, Any] = {
        "arch": arch, "machine": platform.machine(), "kvm": os.path.exists("/dev/kvm"),
        "kvm_access": os.access("/dev/kvm", os.R_OK | os.W_OK), "virt_flag": _virt_flag(), "host_virt": _detect_virt(),
        "virsh": bool(shutil.which("virsh")), "virt_install": bool(shutil.which("virt-install")),
        "virt_clone": bool(shutil.which("virt-clone")), "group": _group_state(), "missing": [], "connected": False,
        "error": "", "version": "", "supported": arch in PACKAGES and sys.platform.startswith("linux"),
    }
    if data["supported"]:
        data["missing"] = _missing_packages()
    if data["virsh"]:
        try:
            out = _virsh("version", timeout=10)
            m = re.search(r"Running hypervisor:\s*(.+)", out)
            lib = re.search(r"Using library:\s*libvirt\s*(\S+)", out)
            data["version"] = " · ".join(x for x in ((m.group(1).strip() if m else ""), (f"libvirt {lib.group(1)}" if lib else "")) if x)
            data["connected"] = True
        except VmError as e:
            data["error"] = str(e)
    data["ready"] = data["connected"] and data["virt_install"]
    _status_cache.update(ts=now, data=data)
    return data


def require_ready() -> None:
    st = status()
    if not st["connected"]:
        raise VmError(st["error"] or "libvirt ist nicht eingerichtet.")


# ---------------------------------------------------------------------------
# Domain XML
# ---------------------------------------------------------------------------
def _mib(el: Optional[ET.Element]) -> int:
    if el is None or not (el.text or "").strip():
        return 0
    val = int(el.text.strip())
    unit = (el.get("unit") or "KiB").lower()
    factor = {"b": 1 / 1048576, "bytes": 1 / 1048576, "k": 1 / 1024, "kib": 1 / 1024, "kb": 1000 / 1048576,
              "m": 1, "mib": 1, "mb": 1e6 / 1048576, "g": 1024, "gib": 1024, "gb": 1e9 / 1048576}.get(unit, 1 / 1024)
    return int(round(val * factor))


def _parse_domain(xml: str) -> Dict[str, Any]:
    root = ET.fromstring(xml)
    os_el = root.find("os")
    otype = os_el.find("type") if os_el is not None else None
    vcpu = root.find("vcpu")
    disks, ifaces = [], []
    for d in root.findall("devices/disk"):
        src = d.find("source")
        tgt = d.find("target")
        drv = d.find("driver")
        source = ""
        pool = vol = ""
        if src is not None:
            source = src.get("file") or src.get("dev") or src.get("name") or ""
            pool, vol = src.get("pool") or "", src.get("volume") or ""
            if pool and vol:
                source = f"{pool}/{vol}"
        disks.append({"device": d.get("device", "disk"), "type": d.get("type", ""), "source": source, "pool": pool, "volume": vol,
                      "target": tgt.get("dev", "") if tgt is not None else "", "bus": tgt.get("bus", "") if tgt is not None else "",
                      "format": drv.get("type", "") if drv is not None else "", "readonly": d.find("readonly") is not None})
    for i in root.findall("devices/interface"):
        src = i.find("source")
        mac = i.find("mac")
        model = i.find("model")
        link = i.find("link")
        tgt = i.find("target")
        source = ""
        if src is not None:
            source = src.get("network") or src.get("bridge") or src.get("dev") or ""
        ifaces.append({"type": i.get("type", ""), "source": source, "mode": src.get("mode", "") if src is not None else "",
                       "mac": mac.get("address", "") if mac is not None else "", "model": model.get("type", "") if model is not None else "",
                       "link": link.get("state", "up") if link is not None else "up", "dev": tgt.get("dev", "") if tgt is not None else ""})
    graphics = [{"type": g.get("type", ""), "port": int(g.get("port", "-1") or -1), "listen": g.get("listen", "")}
                for g in root.findall("devices/graphics")]
    serial = any(True for _ in root.findall("devices/serial")) or any(c.get("type") == "pty" for c in root.findall("devices/console"))
    agent = None
    for ch in root.findall("devices/channel"):
        t = ch.find("target")
        if t is not None and t.get("name") == "org.qemu.guest_agent.0":
            agent = t.get("state") or "configured"
    osinfo = ""
    for el in root.iter():
        if el.tag.endswith("}os") and el.get("id"):
            osinfo = el.get("id")
    loader = os_el.find("loader") if os_el is not None else None
    firmware = (os_el.get("firmware") if os_el is not None else "") or ("efi" if loader is not None else "bios")
    cpu = root.find("cpu")
    return {
        "name": root.findtext("name", ""), "uuid": root.findtext("uuid", ""), "title": root.findtext("title", "") or "",
        "description": root.findtext("description", "") or "",
        "memory_mb": _mib(root.find("currentMemory")) or _mib(root.find("memory")), "max_memory_mb": _mib(root.find("memory")),
        "vcpus": int(vcpu.get("current") or vcpu.text or 1) if vcpu is not None else 1,
        "max_vcpus": int(vcpu.text or 1) if vcpu is not None else 1,
        "arch": otype.get("arch", "") if otype is not None else "", "machine": otype.get("machine", "") if otype is not None else "",
        "firmware": firmware, "cpu_mode": cpu.get("mode", "") if cpu is not None else "", "virt_type": root.get("type", ""),
        "disks": disks, "interfaces": ifaces, "graphics": graphics, "serial": serial, "agent": agent,
        "osinfo": osinfo, "os": _os_label(osinfo),
        "boot": [b.get("dev") for b in (os_el.findall("boot") if os_el is not None else [])],
    }


def _os_label(osinfo: str) -> str:
    """http://debian.org/debian/12 -> Debian 12, http://ubuntu.com/ubuntu/24.04 -> Ubuntu 24.04"""
    m = re.match(r"^https?://[^/]+/([^/]+)/([^/]+)$", osinfo or "")
    if not m:
        return ""
    distro = {"debian": "Debian", "ubuntu": "Ubuntu", "fedora": "Fedora", "rhel": "RHEL", "centos": "CentOS", "win": "Windows",
              "opensuse": "openSUSE", "alpinelinux": "Alpine", "archlinux": "Arch Linux", "rocky": "Rocky Linux",
              "almalinux": "AlmaLinux", "freebsd": "FreeBSD", "nixos": "NixOS"}.get(m.group(1), m.group(1))
    return f"{distro} {m.group(2)}"


def _domain(name: str, fresh: bool = False) -> Dict[str, Any]:
    hit = _xml_cache.get(name)
    if hit and not fresh and time.time() - hit[0] < XML_TTL:
        return hit[1]
    info = _parse_domain(_virsh("dumpxml", name))
    _xml_cache[name] = (time.time(), info)
    return info


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _domstats() -> Dict[str, Dict[str, str]]:
    out = _virsh("domstats", "--raw", "--state", "--cpu-total", "--balloon", "--vcpu", "--interface", "--block", timeout=30)
    doms: Dict[str, Dict[str, str]] = {}
    cur: Optional[Dict[str, str]] = None
    for line in out.splitlines():
        m = re.match(r"^Domain:\s*'?(.*?)'?\s*$", line)
        if m:
            cur = doms.setdefault(m.group(1), {})
            continue
        if cur is not None and "=" in line:
            k, v = line.strip().split("=", 1)
            cur[k] = v
    return doms


def _num(st: Dict[str, str], key: str) -> float:
    try:
        return float(st.get(key, 0) or 0)
    except ValueError:
        return 0.0


def _sum(st: Dict[str, str], prefix: str, suffix: str) -> float:
    n = int(_num(st, f"{prefix}.count"))
    return sum(_num(st, f"{prefix}.{i}.{suffix}") for i in range(n))


def _rates(name: str, st: Dict[str, str], vcpus: int) -> Dict[str, Optional[float]]:
    now = time.monotonic()
    cur = {"cpu": _num(st, "cpu.time"), "rx": _sum(st, "net", "rx.bytes"), "tx": _sum(st, "net", "tx.bytes"),
           "rd": _sum(st, "block", "rd.bytes"), "wr": _sum(st, "block", "wr.bytes")}
    prev = _prev.get(name)
    _prev[name] = (now, cur)
    out: Dict[str, Optional[float]] = {"cpu": None, "rx": None, "tx": None, "rd": None, "wr": None}
    if not prev or now - prev[0] < 0.5:
        return out
    dt = now - prev[0]
    for k in ("rx", "tx", "rd", "wr"):
        d = cur[k] - prev[1][k]
        out[k] = round(d / dt) if d >= 0 else None
    d = cur["cpu"] - prev[1]["cpu"]
    if d >= 0 and vcpus:
        out["cpu"] = round(min(100.0, d / 1e9 / dt / vcpus * 100), 1)
    return out


def _addresses(name: str, info: Dict[str, Any]) -> List[Dict[str, str]]:
    """IP addresses: DHCP leases of libvirt networks, then the guest agent, then the host's ARP table."""
    hit = _ip_cache.get(name)
    if hit and time.time() - hit[0] < IP_TTL:
        return hit[1]
    sources = ["lease"]
    if info.get("agent") == "connected":
        sources.append("agent")
    sources.append("arp")
    found: List[Dict[str, str]] = []
    for src in sources:
        out = _virsh("domifaddr", name, "--source", src, timeout=8, check=False)
        for line in out.splitlines():
            m = re.match(r"^\s*(\S+)\s+([0-9a-f:]{17}|-)\s+(ipv4|ipv6)\s+(\S+)", line)
            if not m:
                continue
            addr = m.group(4).split("/")[0]
            if m.group(1) == "lo" or addr.startswith(("127.", "fe80:", "::1")):
                continue
            if not any(a["address"] == addr for a in found):
                found.append({"address": addr, "family": m.group(3), "mac": m.group(2), "source": src})
        if found:
            break
    _ip_cache[name] = (time.time(), found)
    return found


def _summary(name: str, info: Dict[str, Any], st: Dict[str, str], autostart: set) -> Dict[str, Any]:
    state = STATES.get(int(_num(st, "state.state")), "shutoff") if st else "shutoff"
    running = state in ("running", "paused")
    vcpus = int(_num(st, "vcpu.current")) or info["vcpus"]
    rates = _rates(name, st, vcpus) if state == "running" else {}
    mem_used = None
    if running:
        avail, unused = _num(st, "balloon.available"), _num(st, "balloon.unused")
        if avail and unused:
            mem_used = round((avail - unused) / 1024)
        elif _num(st, "balloon.rss"):
            mem_used = round(_num(st, "balloon.rss") / 1024)
    disk_cap = sum(_num(st, f"block.{i}.capacity") for i in range(int(_num(st, "block.count"))))
    has_vnc = any(g["type"] == "vnc" for g in info["graphics"])
    return {
        "name": name, "title": info["title"], "description": info["description"][:300], "state": state,
        "state_label": STATE_LABELS.get(state, state), "level": STATE_LEVEL.get(state, ""), "autostart": name in autostart,
        "vcpus": vcpus, "memory_mb": info["memory_mb"], "memory_used_mb": mem_used, "os": info["os"], "arch": info["arch"],
        "cpu": rates.get("cpu"), "net_rx": rates.get("rx"), "net_tx": rates.get("tx"), "disk_rd": rates.get("rd"), "disk_wr": rates.get("wr"),
        "disk_bytes": int(disk_cap) or None, "disks": sum(1 for d in info["disks"] if d["device"] == "disk"),
        "ips": _addresses(name, info) if state == "running" else [], "vnc": has_vnc, "serial": info["serial"],
        "agent": info["agent"], "firmware": info["firmware"],
    }


def overview() -> Dict[str, Any]:
    st = status()
    data: Dict[str, Any] = {"status": st, "vms": [], "host": _host(), "jobs": _running_jobs()}
    if not st["connected"]:
        return data
    with _lock:
        stats = _domstats()
        names = [n for n in _virsh("list", "--all", "--name").splitlines() if n.strip()]
        autostart = set(n for n in _virsh("list", "--all", "--autostart", "--name").splitlines() if n.strip())
        for name in names:
            try:
                info = _domain(name)
            except VmError:
                continue
            data["vms"].append(_summary(name, info, stats.get(name, {}), autostart))
        for gone in set(_prev) - set(names):
            _prev.pop(gone, None)
    data["vms"].sort(key=lambda v: (v["state"] != "running", v["name"].lower()))
    run = [v for v in data["vms"] if v["state"] in ("running", "paused")]
    data["host"].update(vms=len(data["vms"]), running=len(run), vcpus_used=sum(v["vcpus"] for v in run),
                        memory_used_mb=sum(v["memory_mb"] for v in run),
                        vcpus_defined=sum(v["vcpus"] for v in data["vms"]), memory_defined_mb=sum(v["memory_mb"] for v in data["vms"]))
    return data


def _host() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    return {"cpus": os.cpu_count() or 1, "memory_mb": int(vm.total / 1048576), "memory_available_mb": int(vm.available / 1048576)}


def _running_jobs() -> List[Dict[str, Any]]:
    try:
        return [t for t in tasks.overview(limit=40, kind="vm")["tasks"] if t["state"] == "running"]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# One VM
# ---------------------------------------------------------------------------
def _state(name: str) -> str:
    out = _virsh("domstate", name).strip().splitlines()
    word = (out[0] if out else "").strip()
    return {"running": "running", "paused": "paused", "shut off": "shutoff", "in shutdown": "shutdown", "crashed": "crashed",
            "pmsuspended": "pmsuspended", "idle": "running", "blocked": "running"}.get(word, word)


def detail(name: str) -> Dict[str, Any]:
    check_name(name)
    require_ready()
    info = dict(_domain(name, fresh=True))
    state = _state(name)
    info.update(state=state, state_label=STATE_LABELS.get(state, state), level=STATE_LEVEL.get(state, ""))
    info["autostart"] = bool(re.search(r"^Autostart:\s+enable", _virsh("dominfo", name), re.M))
    info["ips"] = _addresses(name, info) if state == "running" else []
    info["disks"] = [dict(d) for d in info["disks"]]   # the cached XML info stays untouched
    for d in info["disks"]:
        d.update(capacity=None, allocation=None)
        if d["device"] == "disk" and d["target"]:
            out = _virsh("domblkinfo", name, d["target"], check=False, timeout=10)
            cap = re.search(r"Capacity:\s+(\d+)", out)
            alloc = re.search(r"Allocation:\s+(\d+)", out)
            d.update(capacity=int(cap.group(1)) if cap else None, allocation=int(alloc.group(1)) if alloc else None)
    info["snapshots"] = snapshots(name)
    info["jobs"] = [t for t in _running_jobs() if (t.get("data") or {}).get("vm") == name]
    info["console"] = {"serial": info["serial"] and state == "running",
                       "vnc": any(g["type"] == "vnc" and g["port"] > 0 for g in info["graphics"]) and state == "running"}
    return info


def action(name: str, act: str) -> Dict[str, Any]:
    check_name(name)
    if act not in ACTIONS:
        raise VmError("Unbekannte Aktion.")
    cmd, msg, allowed = ACTIONS[act]
    state = _state(name)
    if state not in allowed:
        raise VmError(f"Nicht möglich, die VM ist gerade „{STATE_LABELS.get(state, state)}“.")
    _virsh(cmd, name, timeout=60)
    _invalidate(name)
    logger.info(f"VM {name}: {act}")
    return {"ok": True, "message": msg.format(name)}


def set_autostart(name: str, enabled: bool) -> Dict[str, Any]:
    check_name(name)
    _virsh("autostart", *([] if enabled else ["--disable"]), name)
    _invalidate(name)
    return {"ok": True, "message": f"Autostart für {name} {'an' if enabled else 'aus'}."}


def delete(name: str, storage: bool) -> Dict[str, Any]:
    check_name(name)
    info = _domain(name, fresh=True)
    if _state(name) != "shutoff":
        _virsh("destroy", name, check=False, timeout=60)
    args = ["undefine", name, "--snapshots-metadata", "--managed-save"]
    if info["firmware"] == "efi":
        args.append("--nvram")
    # Only real disks, never an installation ISO other VMs may still use
    targets = [d["target"] for d in info["disks"] if d["device"] == "disk" and d["target"]]
    if storage and targets:
        args += ["--storage", ",".join(targets)]
    _virsh(*args, timeout=120)
    _invalidate(name)
    _prev.pop(name, None)
    logger.info(f"VM {name} deleted (storage: {storage})")
    return {"ok": True, "message": f"{name} wurde gelöscht{' samt Laufwerken' if storage and targets else ''}."}


def settings(name: str, vcpus: Optional[int], memory_mb: Optional[int], title: Optional[str], description: Optional[str]) -> Dict[str, Any]:
    check_name(name)
    info = _domain(name, fresh=True)
    state = _state(name)
    host = _host()
    changed = []
    if (vcpus and vcpus != info["vcpus"]) or (memory_mb and memory_mb != info["memory_mb"]):
        if state != "shutoff":
            raise VmError("vCPUs und Arbeitsspeicher lassen sich nur bei ausgeschalteter VM ändern.")
    if vcpus and vcpus != info["vcpus"]:
        if not 1 <= vcpus <= host["cpus"]:
            raise VmError(f"vCPUs: 1 bis {host['cpus']} (so viele hat der Host).")
        order = (["--maximum"], []) if vcpus > info["max_vcpus"] else ([], ["--maximum"])
        for extra in order:
            _virsh("setvcpus", name, str(vcpus), "--config", *extra)
        changed.append(f"{vcpus} vCPUs")
    if memory_mb and memory_mb != info["memory_mb"]:
        if not 128 <= memory_mb <= host["memory_mb"]:
            raise VmError(f"Arbeitsspeicher: 128 MB bis {host['memory_mb']} MB.")
        size = f"{memory_mb}M"
        steps = (["setmaxmem", "setmem"] if memory_mb > info["max_memory_mb"] else ["setmem", "setmaxmem"])
        for cmd in steps:
            _virsh(cmd, name, size, "--config")
        changed.append(f"{memory_mb} MB RAM")
    live = ["--live"] if state in ("running", "paused") else []
    if title is not None and title.strip() != info["title"]:
        _virsh("desc", name, "--config", *live, "--title", "--new-desc", title.strip()[:120])
        changed.append("Titel")
    if description is not None and description.strip() != info["description"]:
        _virsh("desc", name, "--config", *live, "--new-desc", description.strip()[:4000])
        changed.append("Beschreibung")
    _invalidate(name)
    return {"ok": True, "message": f"{name}: {', '.join(changed)} gespeichert." if changed else "Keine Änderung."}


def rename(name: str, new: str) -> Dict[str, Any]:
    check_name(name)
    check_name(new)
    if _state(name) != "shutoff":
        raise VmError("Umbenennen geht nur bei ausgeschalteter VM.")
    _virsh("domrename", name, new)
    _invalidate()
    return {"ok": True, "message": f"{name} heißt jetzt {new}."}


def clone(name: str, new: str, user: str) -> Dict[str, Any]:
    check_name(name)
    check_name(new)
    if not shutil.which("virt-clone"):
        raise VmError("virt-clone fehlt (Paket virtinst).")
    if _state(name) != "shutoff":
        raise VmError("Klonen geht nur bei ausgeschalteter VM.")
    if new in _names():
        raise VmError(f"Es gibt schon eine VM {new}.")
    steps = [{"argv": ["virt-clone", "--connect", URI, "--original", name, "--name", new, "--auto-clone"],
              "show": f"virt-clone {name} → {new}"}]
    return tasks.start("vm", f"VM {name} klonen als {new}", steps, user=user, key=f"vm:{new}", timeout=6 * 3600, data={"vm": new})


def _names() -> List[str]:
    return [n for n in _virsh("list", "--all", "--name").splitlines() if n.strip()]


# ---------------------------------------------------------------------------
# Disks, ISO, network interfaces
# ---------------------------------------------------------------------------
def _live(name: str) -> List[str]:
    return ["--config", "--live"] if _state(name) in ("running", "paused") else ["--config"]


def _next_target(info: Dict[str, Any], prefix: str) -> str:
    used = {d["target"] for d in info["disks"]}
    for c in "abcdefghijklmnopqrstuvwxyz":
        if f"{prefix}{c}" not in used:
            return f"{prefix}{c}"
    raise VmError("Keine freie Laufwerksbezeichnung mehr.")


def add_disk(name: str, pool: str, size_gb: int, bus: str) -> Dict[str, Any]:
    check_name(name)
    check_name(pool, "Speicher-Pool")
    if bus not in ("virtio", "scsi", "sata"):
        raise VmError("Ungültiger Bus.")
    if not 1 <= int(size_gb) <= 16384:
        raise VmError("Größe: 1 bis 16384 GB.")
    info = _domain(name, fresh=True)
    prefix = "vd" if bus == "virtio" else "sd"
    target = _next_target(info, prefix)
    vols = {v["name"] for v in volumes(pool)}
    vol = next(f"{name}-{target}{'' if i == 0 else f'-{i}'}.qcow2" for i in range(100)
               if f"{name}-{target}{'' if i == 0 else f'-{i}'}.qcow2" not in vols)
    _virsh("vol-create-as", pool, vol, f"{int(size_gb)}G", "--format", "qcow2", timeout=120)
    path = _virsh("vol-path", "--pool", pool, vol).strip()
    try:
        _virsh("attach-disk", name, path, target, "--driver", "qemu", "--subdriver", "qcow2", "--targetbus", bus, *_live(name))
    except VmError:
        _virsh("vol-delete", "--pool", pool, vol, check=False)
        raise
    _invalidate(name)
    return {"ok": True, "message": f"Laufwerk {target} ({size_gb} GB) hinzugefügt."}


def detach_disk(name: str, target: str, delete_volume: bool) -> Dict[str, Any]:
    check_name(name)
    if not TARGET_RE.match(target or ""):
        raise VmError("Ungültiges Laufwerk.")
    info = _domain(name, fresh=True)
    disk = next((d for d in info["disks"] if d["target"] == target), None)
    if not disk:
        raise VmError("Laufwerk nicht gefunden.")
    _virsh("detach-disk", name, target, *_live(name))
    msg = f"Laufwerk {target} entfernt"
    if delete_volume and disk["device"] == "disk" and disk["source"]:
        if disk["pool"] and disk["volume"]:
            _virsh("vol-delete", "--pool", disk["pool"], disk["volume"], check=False)
        else:
            _virsh("vol-delete", disk["source"], check=False)
        msg += " und gelöscht"
    _invalidate(name)
    return {"ok": True, "message": f"{msg}."}


def change_media(name: str, path: str) -> Dict[str, Any]:
    """Insert an ISO (a volume of a pool) into the CD drive, or eject it (path "")."""
    check_name(name)
    info = _domain(name, fresh=True)
    cdrom = next((d for d in info["disks"] if d["device"] == "cdrom"), None)
    if path:
        if path not in {v["path"] for v in iso_volumes()}:
            raise VmError("Nur ISO-Dateien aus einem Speicher-Pool.")
        if cdrom:
            _virsh("change-media", name, cdrom["target"], path, "--insert" if not cdrom["source"] else "--update", *_live(name))
        else:
            if _state(name) != "shutoff":
                raise VmError("Die VM hat kein CD-Laufwerk – zum Hinzufügen erst ausschalten.")
            _virsh("attach-disk", name, path, _next_target(info, "sd"), "--type", "cdrom", "--mode", "readonly",
                   "--targetbus", "sata" if info["arch"] in ("x86_64", "i686") else "scsi", "--config")
        msg = f"{os.path.basename(path)} eingelegt."
    else:
        if not cdrom or not cdrom["source"]:
            raise VmError("Kein Medium eingelegt.")
        _virsh("change-media", name, cdrom["target"], "--eject", *_live(name))
        msg = "Medium ausgeworfen."
    _invalidate(name)
    return {"ok": True, "message": msg}


def _iface_xml(kind: str, source: str, model: str, mac: str = "") -> str:
    if model not in ("virtio", "e1000e", "rtl8139"):
        raise VmError("Ungültiges Netzwerkkarten-Modell.")
    if kind == "network":
        check_name(source, "Netzwerk")
        src = f"<source network='{source}'/>"
    elif kind == "bridge":
        if not IFACE_RE.match(source or ""):
            raise VmError("Ungültige Bridge.")
        src = f"<source bridge='{source}'/>"
    elif kind == "direct":
        if not IFACE_RE.match(source or ""):
            raise VmError("Ungültige Schnittstelle.")
        src = f"<source dev='{source}' mode='bridge'/>"
    else:
        raise VmError("Ungültiger Netzwerktyp.")
    mac_el = f"<mac address='{mac}'/>" if mac else ""
    return f"<interface type='{kind}'>{mac_el}{src}<model type='{model}'/></interface>"


def add_interface(name: str, kind: str, source: str, model: str) -> Dict[str, Any]:
    check_name(name)
    _virsh_xml(["attach-device", name, "{xml}", *_live(name)], _iface_xml(kind, source, model))
    _invalidate(name)
    return {"ok": True, "message": "Netzwerkkarte hinzugefügt."}


def interface_action(name: str, mac: str, act: str) -> Dict[str, Any]:
    check_name(name)
    mac = (mac or "").lower()
    if not MAC_RE.match(mac):
        raise VmError("Ungültige MAC-Adresse.")
    info = _domain(name, fresh=True)
    iface = next((i for i in info["interfaces"] if i["mac"].lower() == mac), None)
    if not iface:
        raise VmError("Netzwerkkarte nicht gefunden.")
    if act == "delete":
        _virsh("detach-interface", name, iface["type"], "--mac", mac, *_live(name))
        msg = "Netzwerkkarte entfernt."
    elif act in ("up", "down"):
        if _state(name) in ("running", "paused"):
            _virsh("domif-setlink", name, mac, act)
        _virsh("domif-setlink", name, mac, act, "--config")
        msg = "Kabel eingesteckt." if act == "up" else "Kabel gezogen (Verbindung getrennt)."
    else:
        raise VmError("Unbekannte Aktion.")
    _invalidate(name)
    return {"ok": True, "message": msg}


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------
def snapshots(name: str) -> List[Dict[str, Any]]:
    out = _virsh("snapshot-list", name, check=False)
    current = _virsh("snapshot-current", name, "--name", check=False).strip()
    items = []
    for line in out.splitlines()[2:]:
        m = re.match(r"^\s*(.+?)\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(\S+)\s*$", line)
        if not m:
            continue
        snap = m.group(1)
        desc = ""
        try:
            x = ET.fromstring(_virsh("snapshot-dumpxml", name, snap, timeout=10))
            desc = x.findtext("description", "") or ""
        except (VmError, ET.ParseError):
            pass
        items.append({"name": snap, "created": m.group(2), "state": m.group(3), "current": snap == current, "description": desc})
    return items


def snapshot_create(name: str, snap: str, description: str) -> Dict[str, Any]:
    check_name(name)
    check_name(snap, "Snapshot")
    args = ["snapshot-create-as", name, snap, "--atomic"]
    if description.strip():
        args += ["--description", description.strip()[:1000]]
    try:
        _virsh(*args, timeout=900)
    except VmError as e:
        if "pflash" in str(e) or "nvram" in str(e).lower():
            raise VmError(f"{e} – mit UEFI-Firmware kann diese libvirt-Version keine internen Snapshots; "
                          "die VM ausschalten und das Laufwerk sichern oder libvirt aktualisieren.")
        raise
    return {"ok": True, "message": f"Snapshot {snap} erstellt."}


def snapshot_action(name: str, snap: str, act: str) -> Dict[str, Any]:
    check_name(name)
    check_name(snap, "Snapshot")
    if act == "revert":
        _virsh("snapshot-revert", name, snap, timeout=900)
        _invalidate(name)
        return {"ok": True, "message": f"{name} auf {snap} zurückgesetzt."}
    if act == "delete":
        _virsh("snapshot-delete", name, snap, timeout=900)
        return {"ok": True, "message": f"Snapshot {snap} gelöscht."}
    raise VmError("Unbekannte Aktion.")


# ---------------------------------------------------------------------------
# Virtual networks
# ---------------------------------------------------------------------------
def networks() -> Dict[str, Any]:
    require_ready()
    items = []
    for name in [n for n in _virsh("net-list", "--all", "--name").splitlines() if n.strip()]:
        info = _virsh("net-info", name, check=False)
        flag = lambda k: bool(re.search(rf"^{k}:\s+yes", info, re.M))
        item = {"name": name, "active": flag("Active"), "autostart": flag("Autostart"), "persistent": flag("Persistent"),
                "bridge": (re.search(r"^Bridge:\s+(\S+)", info, re.M) or [None, ""])[1], "mode": "isolated", "ip": "", "dhcp": ""}
        try:
            x = ET.fromstring(_virsh("net-dumpxml", name))
            fwd = x.find("forward")
            if fwd is not None:
                item["mode"] = fwd.get("mode", "nat")
            ip = x.find("ip")
            if ip is not None and ip.get("address"):
                pre = ip.get("prefix") or (str(ipaddress.ip_network(f"0.0.0.0/{ip.get('netmask')}").prefixlen) if ip.get("netmask") else "")
                item["ip"] = f"{ip.get('address')}/{pre}" if pre else ip.get("address")
                rng = ip.find("dhcp/range")
                if rng is not None:
                    item["dhcp"] = f"{rng.get('start')} – {rng.get('end')}"
        except (VmError, ET.ParseError, ValueError):
            pass
        items.append(item)
    return {"networks": items, "host_interfaces": host_interfaces()}


def host_interfaces() -> Dict[str, List[str]]:
    """Bridges for "bridge" interfaces, physical NICs for direct attachment (macvtap)."""
    bridges, nics = [], []
    for dev in sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []:
        if dev == "lo" or dev.startswith(("virbr", "vnet", "macvtap", "veth", "docker", "br-", "cni", "flannel", "tun", "wt", "tailscale")):
            continue
        if os.path.isdir(f"/sys/class/net/{dev}/bridge"):
            bridges.append(dev)
        elif os.path.exists(f"/sys/class/net/{dev}/device"):
            nics.append(dev)
    return {"bridges": bridges, "nics": nics}


def network_action(name: str, act: str) -> Dict[str, Any]:
    check_name(name, "Netzwerk")
    cmds = {"start": [["net-start", name]], "stop": [["net-destroy", name]], "autostart": [["net-autostart", name]],
            "noautostart": [["net-autostart", "--disable", name]], "delete": [["net-destroy", name], ["net-undefine", name]]}
    if act not in cmds:
        raise VmError("Unbekannte Aktion.")
    for i, c in enumerate(cmds[act]):
        _virsh(*c, check=not (act == "delete" and i == 0))
    return {"ok": True, "message": {"start": f"Netzwerk {name} gestartet.", "stop": f"Netzwerk {name} gestoppt.",
                                    "autostart": f"{name} startet mit dem Host.", "noautostart": f"{name} startet nicht mehr automatisch.",
                                    "delete": f"Netzwerk {name} gelöscht."}[act]}


def _host_networks() -> List[Any]:
    nets = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family.name == "AF_INET" and a.netmask:
                try:
                    nets.append(ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False))
                except ValueError:
                    pass
    return nets


def network_create(name: str, cidr: str, mode: str, dhcp: bool) -> Dict[str, Any]:
    check_name(name, "Netzwerk")
    if mode not in ("nat", "isolated", "route"):
        raise VmError("Ungültiger Modus.")
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        raise VmError("Ungültiges Netz (z. B. 192.168.150.0/24).")
    if net.version != 4 or not net.is_private or not 16 <= net.prefixlen <= 29:
        raise VmError("Nur private IPv4-Netze von /16 bis /29.")
    clash = next((n for n in _host_networks() if n.overlaps(net)), None)
    if clash:
        raise VmError(f"Überschneidet sich mit {clash} auf dem Host.")
    for other in networks()["networks"]:
        if other["ip"]:
            try:
                if ipaddress.ip_network(other["ip"], strict=False).overlaps(net):
                    raise VmError(f"Überschneidet sich mit dem Netzwerk {other['name']}.")
            except ValueError:
                pass
    hosts = list(net.hosts())
    gw = hosts[0]
    fwd = "" if mode == "isolated" else f"<forward mode='{mode}'/>"
    dh = f"<dhcp><range start='{hosts[1]}' end='{hosts[-1]}'/></dhcp>" if dhcp and len(hosts) > 2 else ""
    xml = (f"<network><name>{name}</name>{fwd}<bridge stp='on' delay='0'/>"
           f"<ip address='{gw}' netmask='{net.netmask}'>{dh}</ip></network>")
    _virsh_xml(["net-define", "{xml}"], xml)
    _virsh("net-start", name)
    _virsh("net-autostart", name)
    return {"ok": True, "message": f"Netzwerk {name} ({net}) angelegt und gestartet."}


# ---------------------------------------------------------------------------
# Storage pools and volumes
# ---------------------------------------------------------------------------
FORBIDDEN_POOL_DIRS = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/proc", "/sys", "/dev", "/run", "/root", "/tmp")


def pools() -> Dict[str, Any]:
    require_ready()
    items = []
    used = _used_paths()
    for name in [n for n in _virsh("pool-list", "--all", "--name").splitlines() if n.strip()]:
        info = _virsh("pool-info", name, "--bytes", check=False)
        val = lambda k: (re.search(rf"^{k}:\s+(.+)$", info, re.M) or [None, ""])[1].strip()
        item = {"name": name, "active": val("State") == "running", "autostart": val("Autostart") == "yes",
                "capacity": int(val("Capacity") or 0) or None, "allocation": int(val("Allocation") or 0) or None,
                "available": int(val("Available") or 0) or None, "type": "", "path": "", "volumes": []}
        try:
            x = ET.fromstring(_virsh("pool-dumpxml", name))
            item["type"] = x.get("type", "")
            item["path"] = x.findtext("target/path", "") or ""
        except (VmError, ET.ParseError):
            pass
        if item["active"]:
            item["volumes"] = volumes(name, used)
        items.append(item)
    return {"pools": items}


def _used_paths() -> Dict[str, List[str]]:
    used: Dict[str, List[str]] = {}
    for name in _names():
        try:
            for d in _domain(name)["disks"]:
                if d["source"]:
                    used.setdefault(d["source"], []).append(name)
        except VmError:
            continue
    return used


def volumes(pool: str, used: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, Any]]:
    check_name(pool, "Speicher-Pool")
    out = _virsh("vol-list", "--pool", pool, "--details", check=False)
    used = used if used is not None else {}
    items = []
    for line in out.splitlines()[2:202]:
        m = re.match(r"^\s*(\S+)\s+(/\S+)\s+(\S+)\s+", line)
        if not m:
            continue
        name, path = m.group(1), m.group(2)
        info = _virsh("vol-info", "--pool", pool, name, "--bytes", check=False, timeout=10)
        cap = re.search(r"Capacity:\s+(\d+)", info)
        alloc = re.search(r"Allocation:\s+(\d+)", info)
        items.append({"name": name, "path": path, "type": m.group(3), "capacity": int(cap.group(1)) if cap else None,
                      "allocation": int(alloc.group(1)) if alloc else None, "used_by": used.get(path, []) + used.get(f"{pool}/{name}", []),
                      "iso": name.lower().endswith(".iso"), "base": name.startswith(BASE_PREFIX)})
    return items


def iso_volumes() -> List[Dict[str, Any]]:
    out = []
    for p in [n for n in _virsh("pool-list", "--name").splitlines() if n.strip()]:
        out += [{**v, "pool": p} for v in volumes(p) if v["iso"]]
    return out


def pool_action(name: str, act: str) -> Dict[str, Any]:
    check_name(name, "Speicher-Pool")
    cmds = {"start": ["pool-start", name], "stop": ["pool-destroy", name], "autostart": ["pool-autostart", name],
            "noautostart": ["pool-autostart", "--disable", name], "refresh": ["pool-refresh", name], "delete": ["pool-undefine", name]}
    if act not in cmds:
        raise VmError("Unbekannte Aktion.")
    if act == "delete":
        _virsh("pool-destroy", name, check=False)
    _virsh(*cmds[act], timeout=120)
    return {"ok": True, "message": {"start": f"Pool {name} gestartet.", "stop": f"Pool {name} gestoppt.",
                                    "autostart": f"{name} startet mit dem Host.", "noautostart": f"{name} startet nicht mehr automatisch.",
                                    "refresh": f"{name} neu eingelesen.", "delete": f"Pool {name} entfernt (die Dateien bleiben)."}[act]}


def pool_create(name: str, path: str) -> Dict[str, Any]:
    check_name(name, "Speicher-Pool")
    path = os.path.normpath((path or "").strip())
    if not path.startswith("/") or path == "/" or any(path == d or path.startswith(d + "/") for d in FORBIDDEN_POOL_DIRS) \
            or "/." in path:
        raise VmError("Bitte einen eigenen Ordner angeben (z. B. /srv/vms) – keine Systemordner.")
    _virsh("pool-define-as", name, "dir", "--target", path)
    try:
        _virsh("pool-build", name, timeout=60)
        _virsh("pool-start", name)
        _virsh("pool-autostart", name)
    except VmError:
        _virsh("pool-undefine", name, check=False)
        raise
    return {"ok": True, "message": f"Speicher-Pool {name} ({path}) angelegt."}


def volume_delete(pool: str, vol: str) -> Dict[str, Any]:
    check_name(pool, "Speicher-Pool")
    if not VOL_RE.match(vol or ""):
        raise VmError("Ungültiges Volume.")
    used = _used_paths()
    v = next((x for x in volumes(pool, used) if x["name"] == vol), None)
    if not v:
        raise VmError("Volume nicht gefunden.")
    if v["used_by"]:
        raise VmError(f"Wird noch von {', '.join(v['used_by'])} benutzt.")
    _virsh("vol-delete", "--pool", pool, vol, timeout=120)
    return {"ok": True, "message": f"{vol} gelöscht."}


# ---------------------------------------------------------------------------
# Images (cloud images and ISOs) - downloaded by vmtool.py as a task
# ---------------------------------------------------------------------------
def catalog() -> Dict[str, Any]:
    arch = host_arch()
    have: Dict[str, Dict[str, Any]] = {}
    if status()["connected"]:
        for p in [n for n in _virsh("pool-list", "--name").splitlines() if n.strip()]:
            for v in volumes(p):
                if v["base"]:
                    have[v["name"]] = {**v, "pool": p}
    items = []
    for c in CATALOG:
        vol = f"{BASE_PREFIX}{c['id']}-{arch}.qcow2"
        items.append({"id": c["id"], "name": c["name"], "user": c["user"], "url": c["url"].format(arch=arch),
                      "volume": vol, "downloaded": have.get(vol)})
    return {"arch": arch, "images": items, "isos": iso_volumes() if status()["connected"] else []}


def _https_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?/[^\s\"'<>]*$", url) or len(url) > 1000:
        raise VmError("Nur https-Adressen.")
    return url


def _write_spec(spec: Dict[str, Any]) -> str:
    os.makedirs(SPEC_DIR, mode=0o700, exist_ok=True)
    os.chmod(SPEC_DIR, 0o700)
    path = os.path.join(SPEC_DIR, f"spec-{secrets.token_hex(8)}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(spec, f)
    return path


def _tool_task(title: str, cmd: str, spec: Dict[str, Any], user: str, key: str, vm: str = "") -> Dict[str, Any]:
    path = _write_spec(spec)
    steps = [{"argv": [sys.executable, TOOL, cmd, path], "show": title}]
    try:
        return tasks.start("vm", title, steps, user=user, key=key, timeout=4 * 3600, data={"vm": vm, "spec": path})
    except tasks.TaskError:
        os.unlink(path)
        raise


def fetch_image(image_id: str, url: str, sha: str, kind: str, pool: str, volume: str, user: str) -> Dict[str, Any]:
    """Download a cloud image from the catalog, or any ISO / qcow2 image by https URL, into a pool."""
    require_ready()
    check_name(pool, "Speicher-Pool")
    arch = host_arch()
    if image_id:
        c = next((x for x in CATALOG if x["id"] == image_id), None)
        if not c:
            raise VmError("Unbekanntes Image.")
        spec = {"url": c["url"].format(arch=arch), "sums": c["sums"], "kind": "cloud", "pool": pool,
                "volume": f"{BASE_PREFIX}{c['id']}-{arch}.qcow2", "replace": True}
        title = f"Image {c['name']} herunterladen"
    else:
        if kind not in ("iso", "cloud"):
            raise VmError("Ungültige Art.")
        sha = (sha or "").strip().lower()
        if sha and not HEX_RE.match(sha):
            raise VmError("Prüfsumme: SHA-256 (64) oder SHA-512 (128 Hex-Zeichen).")
        url = _https_url(url)
        volume = (volume or os.path.basename(url.split("?")[0])).strip()
        if kind == "iso" and not volume.lower().endswith(".iso"):
            volume += ".iso"
        if kind == "cloud":
            volume = BASE_PREFIX + re.sub(r"\.(qcow2|img)$", "", volume) + ".qcow2"
        if not VOL_RE.match(volume):
            raise VmError("Ungültiger Dateiname.")
        spec = {"url": url, "sha": sha, "kind": kind, "pool": pool, "volume": volume, "replace": False}
        title = f"{'ISO' if kind == 'iso' else 'Image'} {volume} herunterladen"
    return _tool_task(title, "fetch", spec, user, key=f"vm-image:{spec['volume']}")


def _hash_password(password: str) -> str:
    if not shutil.which("openssl"):
        raise VmError("openssl fehlt – Passwort lässt sich nicht verschlüsseln.")
    res = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True, text=True, timeout=10)
    if res.returncode != 0 or not res.stdout.startswith("$6$"):
        raise VmError("Passwort ließ sich nicht verschlüsseln.")
    return res.stdout.strip()


def _ssh_keys(text: str) -> List[str]:
    keys = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.match(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com) [A-Za-z0-9+/=]{20,}( [^\n]{0,200})?$", line):
            raise VmError(f"Kein gültiger öffentlicher SSH-Schlüssel: {line[:40]}…")
        keys.append(line)
    if len(keys) > 20:
        raise VmError("Höchstens 20 SSH-Schlüssel.")
    return keys


def _network_arg(net: str) -> str:
    """network:<name> | bridge:<br> | direct:<nic> | none -> virt-install --network value"""
    kind, _, src = (net or "").partition(":")
    if kind == "none":
        return "none"
    if kind == "network":
        check_name(src, "Netzwerk")
        return f"network={src},model=virtio"
    if kind == "bridge" and IFACE_RE.match(src):
        return f"bridge={src},model=virtio"
    if kind == "direct" and IFACE_RE.match(src):
        return f"type=direct,source={src},source.mode=bridge,model=virtio"
    raise VmError("Ungültiges Netzwerk.")


def create(req: Dict[str, Any], user: str) -> Dict[str, Any]:
    """Validate the form and start vmtool.py create as a task (the spec file holds no plain password)."""
    st = status(fresh=True)
    if not st["ready"]:
        raise VmError(st["error"] or "Virtualisierung ist nicht eingerichtet (libvirt und virt-install).")
    name = check_name(str(req.get("name", "")).strip())
    if name in _names():
        raise VmError(f"Es gibt schon eine VM {name}.")
    host = _host()
    vcpus, memory = int(req.get("vcpus") or 0), int(req.get("memory_mb") or 0)
    if not 1 <= vcpus <= host["cpus"]:
        raise VmError(f"vCPUs: 1 bis {host['cpus']}.")
    if not 256 <= memory <= host["memory_mb"]:
        raise VmError(f"Arbeitsspeicher: 256 MB bis {host['memory_mb']} MB.")
    pool = check_name(str(req.get("pool") or "default"), "Speicher-Pool")
    source = req.get("source")
    disk_gb = int(req.get("disk_gb") or 0)
    if source != "disk" and not 1 <= disk_gb <= 16384:
        raise VmError("Laufwerk: 1 bis 16384 GB.")
    vols = {v["name"]: v for v in volumes(pool)}
    if f"{name}.qcow2" in vols and source != "disk":
        raise VmError(f"Im Pool {pool} gibt es schon {name}.qcow2.")
    firmware = req.get("firmware") or "bios"
    if firmware not in ("bios", "uefi"):
        raise VmError("Ungültige Firmware.")
    if host_arch() == "arm64":
        firmware = "uefi"
    spec: Dict[str, Any] = {
        "name": name, "vcpus": vcpus, "memory_mb": memory, "disk_gb": disk_gb, "pool": pool, "source": source,
        "network": _network_arg(str(req.get("network") or "network:default")), "firmware": firmware,
        "autostart": bool(req.get("autostart")), "kvm": st["kvm"] and st["kvm_access"],
        "arch": host_arch(), "title": str(req.get("title") or "")[:120], "description": str(req.get("description") or "")[:4000],
    }
    if source == "cloud":
        image = str(req.get("image") or "")
        c = next((x for x in CATALOG if x["id"] == image), None)
        if c:
            spec.update(url=c["url"].format(arch=host_arch()), sums=c["sums"], osinfo=c["osinfo"],
                        base=f"{BASE_PREFIX}{c['id']}-{host_arch()}.qcow2")
        elif image.startswith(BASE_PREFIX) and image in vols:
            spec.update(base=image, osinfo=str(req.get("osinfo") or ""))
        else:
            raise VmError("Image auswählen.")
        login = str(req.get("user") or "").strip()
        if not USER_RE.match(login) or login in ("root", "daemon", "bin", "sys", "nobody"):
            raise VmError("Ungültiger Benutzername (klein, a–z, 0–9, - _).")
        password = str(req.get("password") or "")
        keys = _ssh_keys(str(req.get("ssh_keys") or ""))
        if not password and not keys:
            raise VmError("Passwort oder SSH-Schlüssel angeben – sonst kommt niemand in die VM.")
        if password and len(password) < 8:
            raise VmError("Passwort: mindestens 8 Zeichen.")
        hostname = str(req.get("hostname") or name).strip().lower()
        if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", hostname):
            raise VmError("Ungültiger Hostname.")
        spec["cloudinit"] = {"user": login, "hash": _hash_password(password) if password else "", "keys": keys, "hostname": hostname,
                             "ssh_password": bool(req.get("ssh_password")) and bool(password),
                             "upgrade": bool(req.get("upgrade")), "agent": bool(req.get("agent", True)), "timezone": _timezone()}
    elif source == "iso":
        path = str(req.get("iso") or "")
        if path not in {v["path"] for v in iso_volumes()}:
            raise VmError("ISO aus einem Speicher-Pool auswählen (vorher herunterladen).")
        spec.update(iso=path, osinfo=str(req.get("osinfo") or ""))
    elif source == "disk":
        vol = str(req.get("volume") or "")
        if vol not in vols or vols[vol]["iso"]:
            raise VmError("Vorhandenes Laufwerk aus dem Pool auswählen.")
        if vols[vol]["used_by"]:
            raise VmError(f"{vol} wird schon von {', '.join(vols[vol]['used_by'])} benutzt.")
        spec.update(volume=vol, osinfo=str(req.get("osinfo") or ""))
    elif source == "pxe":
        if spec["network"] == "none":
            raise VmError("Netzwerkstart braucht ein Netzwerk.")
    else:
        raise VmError("Installationsquelle auswählen.")
    if spec.get("osinfo") and not re.match(r"^[a-z0-9][a-z0-9.+-]{0,40}$", spec["osinfo"]):
        raise VmError("Ungültige Betriebssystem-Kennung.")
    labels = {"cloud": "aus Cloud-Image", "iso": "von ISO", "disk": "aus vorhandenem Laufwerk", "pxe": "per Netzwerkstart"}
    logger.info(f"VM {name} create ({source}) by '{user}'")
    return _tool_task(f"VM {name} erstellen ({labels[source]})", "create", spec, user, key=f"vm:{name}", vm=name)


def _timezone() -> str:
    try:
        link = os.readlink("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", link)
        if m and re.match(r"^[A-Za-z0-9_/+-]{1,60}$", m.group(1)):
            return m.group(1)
    except OSError:
        pass
    return ""


def _task_done(task: Dict[str, Any]) -> None:
    spec = (task.get("data") or {}).get("spec")
    if spec and os.path.dirname(os.path.abspath(spec)) == SPEC_DIR and os.path.exists(spec):
        os.unlink(spec)   # also when the task was cancelled before vmtool.py could remove it
    _invalidate()


tasks.on_done("vm", _task_done)


# ---------------------------------------------------------------------------
# Setup (root parts via pi-helper.sh)
# ---------------------------------------------------------------------------
def setup(step: str) -> Dict[str, Any]:
    import packages
    import security
    st = status(fresh=True)
    if not st["supported"]:
        raise VmError(f"Auf {st['machine']} nicht unterstützt (nur x86_64 und arm64 mit Debian/Ubuntu).")
    if step == "packages":
        if not st["missing"]:
            raise VmError("Alle Pakete sind schon installiert.")
        try:
            return {"packages": packages.start("install", st["missing"]), "message": "Pakete werden installiert."}
        except packages.PackageError as e:
            raise VmError(str(e))
        except (PermissionError, RuntimeError) as e:
            raise VmError(str(e))
    if step == "access":
        try:
            out = security._helper("libvirt-setup", timeout=120)
        except (PermissionError, RuntimeError) as e:
            raise VmError(str(e))
        _invalidate()
        return {"message": "Zugriff eingerichtet. Das Dashboard startet in wenigen Sekunden neu.", "output": out[-4000:]}
    raise VmError("Unbekannter Schritt.")


# ---------------------------------------------------------------------------
# Console (serial: virsh console in a pty; graphical: the VNC port on 127.0.0.1)
# ---------------------------------------------------------------------------
def console_info(name: str) -> Dict[str, Any]:
    check_name(name)
    if _state(name) != "running":
        raise VmError("Die VM läuft nicht.")
    info = _domain(name, fresh=True)
    vnc = next((g for g in info["graphics"] if g["type"] == "vnc" and g["port"] > 0), None)
    return {"serial": info["serial"], "vnc": bool(vnc)}


def issue_ticket(user: str, name: str) -> str:
    now = time.time()
    for t, e in list(_tickets.items()):
        if e[1] < now:
            _tickets.pop(t, None)
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (user, now + TICKET_TTL, name)
    return ticket


def redeem_ticket(ticket: str, user: str) -> Optional[str]:
    entry = _tickets.pop(ticket or "", None)
    if not entry or entry[0] != user or entry[1] < time.time():
        return None
    return entry[2]


def serial_argv(name: str) -> List[str]:
    check_name(name)
    return ["virsh", "-c", URI, "console", "--force", name]


def vnc_port(name: str) -> int:
    check_name(name)
    info = _domain(name, fresh=True)
    vnc = next((g for g in info["graphics"] if g["type"] == "vnc" and g["port"] > 0), None)
    if not vnc:
        raise VmError("Die VM hat keine grafische Konsole (VNC).")
    if vnc["listen"] not in ("", "127.0.0.1", "localhost", "0.0.0.0", "::", "::1"):
        raise VmError("VNC lauscht nicht lokal.")
    return vnc["port"]
