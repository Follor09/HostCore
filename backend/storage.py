"""
Storage overview like `lsblk`: disks with their partitions, swap (zram, partitions,
files) and other block devices (loop ...).

A partition often has many mountpoints on a homelab box: k3s and Docker bind-mount
directories of the root partition into /var/lib/kubelet/pods/... - those are grouped
under the partition's main mountpoint instead of showing up as extra "drives".

The lsblk structure is cached for 30 s (it hardly changes); usage numbers are read
live with statvfs on every call.
"""

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

CACHE_SECONDS = 30
# Mountpoints that are bind mounts of container runtimes, not places a person looks at
NOISE = ("/var/lib/kubelet", "/var/lib/rancher", "/var/lib/docker", "/var/lib/containers", "/run/",
         "/snap/", "/var/snap/", "/var/lib/lxd", "/var/lib/containerd")
SKIP_DEVICES = re.compile(r"^(ram\d+|mmcblk\d+boot\d+|mmcblk\d+rpmb|sr\d+)$")

_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def is_noise(mount: str) -> bool:
    return any(mount.startswith(p) for p in NOISE)


def _noise_kind(mounts: List[str]) -> str:
    if any(m.startswith(("/var/lib/kubelet", "/var/lib/rancher")) for m in mounts):
        return "kubernetes"
    if any(m.startswith(("/var/lib/docker", "/var/lib/containerd", "/var/lib/containers")) for m in mounts):
        return "docker"
    return "other"


def _primary_mount(mounts: List[str]) -> Optional[str]:
    if not mounts:
        return None
    if "/" in mounts:
        return "/"
    clean = sorted((m for m in mounts if not is_noise(m)), key=len)
    return clean[0] if clean else sorted(mounts, key=len)[0]


def _bool(v: Any) -> bool:
    return v in (True, 1, "1", "true")


def _lsblk() -> Optional[List[Dict[str, Any]]]:
    if not shutil.which("lsblk"):
        return None
    try:
        out = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MODEL,TRAN,RM,RO,MOUNTPOINTS"],
                             capture_output=True, text=True, timeout=5).stdout
        return json.loads(out).get("blockdevices", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _swaps() -> Dict[str, Dict[str, int]]:
    """/proc/swaps: path -> {size, used} in bytes."""
    out: Dict[str, Dict[str, int]] = {}
    try:
        with open("/proc/swaps") as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    out[parts[0]] = {"size": int(parts[2]) * 1024, "used": int(parts[3]) * 1024, "kind": parts[1]}
    except (OSError, StopIteration, ValueError):
        pass
    return out


def _mountpoints(dev: Dict[str, Any]) -> List[str]:
    mps = dev.get("mountpoints")
    if mps is None:  # older lsblk: single MOUNTPOINT column
        mps = [dev.get("mountpoint")]
    return [m for m in mps if m]


def _disk_kind(dev: Dict[str, Any]) -> str:
    name, tran = dev.get("name", ""), (dev.get("tran") or "").lower()
    if name.startswith("nvme") or tran == "nvme":
        return "nvme"
    if name.startswith("mmcblk"):
        return "sd"
    if tran == "usb" or _bool(dev.get("rm")):
        return "usb"
    if name.startswith("zram"):
        return "zram"
    if name.startswith("loop"):
        return "loop"
    return tran or "disk"


def _partition(dev: Dict[str, Any], swaps: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    mounts = _mountpoints(dev)
    swap = "[SWAP]" in mounts or dev.get("fstype") == "swap"
    real = [m for m in mounts if m != "[SWAP]"]
    primary = _primary_mount(real)
    extra = [m for m in real if m != primary]
    part = {
        "name": dev.get("name"), "path": dev.get("path") or f"/dev/{dev.get('name')}",
        "size": int(dev.get("size") or 0), "fstype": dev.get("fstype") or "", "label": dev.get("label") or "",
        "mount": primary, "extra_mounts": len(extra), "extra_kind": _noise_kind(extra) if extra else None,
        "extra_examples": extra[:3], "readonly": _bool(dev.get("ro")), "swap": swap,
    }
    if swap:
        s = swaps.get(part["path"], {})
        part.update(used=s.get("used"), total=s.get("size") or part["size"])
    return part


def _usage(part: Dict[str, Any]) -> None:
    if not part.get("mount"):
        return
    try:
        u = psutil.disk_usage(part["mount"])
        part.update(total=u.total, used=u.used, free=u.free, percent=round(u.percent, 1))
    except OSError:
        pass


def _kernel_mounts() -> Dict[str, List[str]]:
    """device path -> mountpoints according to the kernel (complements lsblk's list)."""
    out: Dict[str, List[str]] = {}
    try:
        for p in psutil.disk_partitions(all=True):
            if p.device.startswith("/dev/"):
                out.setdefault(os.path.realpath(p.device), []).append(p.mountpoint)
    except OSError:
        pass
    return out


def _structure() -> Dict[str, Any]:
    devices = _lsblk()
    if devices is None:
        return _fallback()
    swaps = _swaps()
    kernel = _kernel_mounts()

    def merge(dev: Dict[str, Any]) -> None:
        path = dev.get("path") or f"/dev/{dev.get('name')}"
        extra = [m for m in kernel.get(os.path.realpath(path), []) if m not in _mountpoints(dev)]
        if extra:
            dev["mountpoints"] = _mountpoints(dev) + extra
        for c in dev.get("children") or []:
            merge(c)

    for d in devices:
        merge(d)
    disks, swap_list, other = [], [], []
    for dev in devices:
        name = dev.get("name", "")
        if SKIP_DEVICES.match(name):
            continue
        kind = _disk_kind(dev)
        children = dev.get("children") or []
        if kind == "zram" or (not children and ("[SWAP]" in _mountpoints(dev))):
            p = _partition(dev, swaps)
            swap_list.append({**p, "kind": "zram" if kind == "zram" else "partition"})
            continue
        if kind == "loop" or dev.get("type") == "loop":
            mounts = [m for m in _mountpoints(dev) if not is_noise(m)]
            other.append({"name": name, "size": int(dev.get("size") or 0), "type": "loop", "mount": mounts[0] if mounts else None})
            continue
        parts = [_partition(c, swaps) for c in children if not SKIP_DEVICES.match(c.get("name", ""))]
        if not children and (dev.get("fstype") or _mountpoints(dev)):
            parts = [_partition(dev, swaps)]  # file system directly on the disk (e.g. USB stick)
        swap_list += [{**p, "kind": "partition"} for p in parts if p["swap"]]
        disks.append({
            "name": name, "path": dev.get("path") or f"/dev/{name}", "size": int(dev.get("size") or 0),
            "model": (dev.get("model") or "").strip(), "kind": kind, "removable": _bool(dev.get("rm")),
            "partitions": [p for p in parts if not p["swap"]],
        })
    # Swap files (not block devices)
    for path, s in swaps.items():
        if not path.startswith("/dev/"):
            swap_list.append({"name": os.path.basename(path), "path": path, "size": s["size"], "total": s["size"],
                              "used": s["used"], "kind": "file", "swap": True})
    order = {"nvme": 0, "sd": 1, "disk": 2, "sata": 2, "usb": 3}
    disks.sort(key=lambda d: (order.get(d["kind"], 4), d["name"]))
    return {"source": "lsblk", "disks": disks, "swap": swap_list, "other": other}


def _fallback() -> Dict[str, Any]:
    """No lsblk (e.g. macOS for development): mounted file systems grouped by device."""
    by_dev: Dict[str, List[str]] = {}
    fstypes: Dict[str, str] = {}
    for p in psutil.disk_partitions(all=False):
        if p.mountpoint.startswith(("/proc", "/sys", "/dev")):
            continue
        by_dev.setdefault(p.device, []).append(p.mountpoint)
        fstypes[p.device] = p.fstype
    parts = []
    for dev, mounts in sorted(by_dev.items()):
        primary = _primary_mount(mounts)
        extra = [m for m in mounts if m != primary]
        parts.append({"name": os.path.basename(dev), "path": dev, "size": 0, "fstype": fstypes[dev], "label": "",
                      "mount": primary, "extra_mounts": len(extra), "extra_kind": _noise_kind(extra) if extra else None,
                      "extra_examples": extra[:3], "readonly": False, "swap": False})
    return {"source": "psutil", "disks": [{"name": "", "path": "", "size": 0, "model": "", "kind": "disk",
                                           "removable": False, "partitions": parts}], "swap": [], "other": []}


def overview() -> Dict[str, Any]:
    now = time.time()
    if not _cache["data"] or now - _cache["ts"] > CACHE_SECONDS:
        _cache.update(ts=now, data=_structure())
    data = json.loads(json.dumps(_cache["data"]))  # copy: usage is filled per call
    swaps = _swaps()
    for disk in data["disks"]:
        for part in disk["partitions"]:
            _usage(part)
    for s in data["swap"]:
        live = swaps.get(s["path"])
        if live:
            s.update(used=live["used"], total=live["size"])
        if s.get("total"):
            s["percent"] = round((s.get("used") or 0) / s["total"] * 100, 1)
    return data
