"""
Storage cleanup, the parts that need root: run through pi-helper.sh cleanup (from the root-owned copy).

    cleanuptool.py sizes           JSON {item: {"size": bytes|null, ...details}} for the items this server has
    cleanuptool.py run <item>      clean up one item

Items: snap (disabled snap revisions), autoremove (packages nothing needs any more, old kernels
among them), varlog (rotated log files in /var/log), docker_logs (container logs: rotated parts
deleted, the live file emptied), crash (crash reports and core dumps), k3s_images (images no
k3s container uses). The dashboard only names an item - which files or packages that means is
decided here. Only the standard library.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

ITEMS = ("snap", "autoremove", "varlog", "docker_logs", "crash", "k3s_images")
ROTATED_RE = re.compile(r"(\.\d+|-\d{8})(\.(gz|xz|bz2|zst|lz4))?$|\.(gz|xz|bz2|zst|lz4)$")
SNAP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(:[a-z0-9]+)?$")
KERNEL_RE = re.compile(r"^(linux-image-\d|raspberrypi-kernel)")     # one per kernel version
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
DOCKER_ROOTS = ("/var/lib/docker", "/var/snap/docker/common/var-lib-docker")
CRASH_DIRS = ("/var/crash", "/var/lib/systemd/coredump")


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def run(cmd: List[str], timeout: float = 60, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace",
                          env={**os.environ, **(env or {})})


def out(cmd: List[str], timeout: float = 60) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return run(cmd, timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def fsize(path: str) -> int:
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    return st.st_size if os.path.isfile(path) and not os.path.islink(path) else 0


# --------------------------------------------------------------------------- snap
def snap_disabled() -> List[Dict[str, Any]]:
    """Old revisions snap keeps next to the active one (`snap list --all`, notes 'disabled')."""
    found = []
    for line in out(["snap", "list", "--all"]).splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 6 and "disabled" in cols[-1].split(",") and SNAP_NAME_RE.match(cols[0]) and cols[2].isdigit():
            found.append({"name": cols[0], "rev": cols[2], "size": fsize(f"/var/lib/snapd/snaps/{cols[0]}_{cols[2]}.snap")})
    return found


# --------------------------------------------------------------------------- apt autoremove
def parse_autoremove(sim: str) -> List[str]:
    """Package names from `apt-get -s --purge autoremove` (lines 'Purg pkg [ver]' / 'Remv pkg [ver]')."""
    names = []
    for line in sim.splitlines():
        m = re.match(r"^(Purg|Remv) (\S+)", line)
        if m and PKG_RE.match(m.group(2)) and m.group(2) not in names:
            names.append(m.group(2))
    return names


def autoremove_list() -> List[str]:
    return parse_autoremove(out(["apt-get", "-s", "--purge", "autoremove"], timeout=120))


def installed_size(names: List[str]) -> int:
    if not names:
        return 0
    kib = 0
    for line in out(["dpkg-query", "-W", "-f=${Installed-Size}\n", *names]).splitlines():
        if line.strip().isdigit():
            kib += int(line.strip())
    return kib * 1024


# --------------------------------------------------------------------------- /var/log
def rotated_logs(root: str = "/var/log") -> List[str]:
    """Rotated or compressed old log files (syslog.1, kern.log.2.gz, messages-20261101); never the journal."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == root:
            dirnames[:] = [d for d in dirnames if d != "journal"]
        for f in filenames:
            p = os.path.join(dirpath, f)
            if ROTATED_RE.search(f) and os.path.isfile(p) and not os.path.islink(p):
                found.append(p)
    return found


# --------------------------------------------------------------------------- docker logs
def docker_roots() -> List[str]:
    roots = [r for r in DOCKER_ROOTS if os.path.isdir(os.path.join(r, "containers"))]
    info = out(["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=15).strip()
    if info.startswith("/") and ".." not in info and os.path.isdir(os.path.join(info, "containers")) and info not in roots:
        roots.append(info)
    return roots


def docker_logs() -> Dict[str, List[str]]:
    """{'live': [<id>-json.log], 'rotated': [<id>-json.log.N]} of all containers."""
    live, rotated = [], []
    for root in docker_roots():
        for path in glob.glob(os.path.join(root, "containers", "*", "*-json.log*")):
            cid = os.path.basename(os.path.dirname(path))
            name = os.path.basename(path)
            if not CONTAINER_ID_RE.match(cid) or os.path.islink(path) or not os.path.isfile(path):
                continue
            if name == f"{cid}-json.log":
                live.append(path)
            elif re.fullmatch(re.escape(cid) + r"-json\.log\.\d+(\.gz)?", name):
                rotated.append(path)
    return {"live": live, "rotated": rotated}


# --------------------------------------------------------------------------- crash dumps
def crash_files() -> List[str]:
    found = []
    for d in CRASH_DIRS:
        try:
            entries = os.scandir(d)
        except OSError:
            continue
        for e in entries:
            if e.is_file(follow_symlinks=False):
                found.append(e.path)
    return found


# --------------------------------------------------------------------------- k3s images
def crictl() -> List[str]:
    if shutil.which("crictl"):
        return ["crictl"]
    if shutil.which("k3s"):
        return ["k3s", "crictl"]
    return []


def unused_images(images: Dict[str, Any], containers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Images (crictl images -o json) that no container (crictl ps -a -o json) uses; pinned ones stay."""
    used = set()
    for c in containers.get("containers") or []:
        used.add(c.get("imageRef") or "")
        used.add((c.get("image") or {}).get("image") or "")
    found = []
    for img in images.get("images") or []:
        if img.get("pinned") or img.get("id") in used or any(t in used for t in img.get("repoTags") or []):
            continue
        try:
            size = int(img.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        tags = img.get("repoTags") or []
        found.append({"name": tags[0] if tags else (img.get("id") or "")[7:19], "size": size})
    return found


def k3s_unused() -> Optional[List[Dict[str, Any]]]:
    base = crictl()
    if not base:
        return None
    try:
        images = json.loads(out([*base, "images", "-o", "json"]) or "{}")
        containers = json.loads(out([*base, "ps", "-a", "-o", "json"]) or "{}")
    except ValueError:
        return None
    if not images:
        return None
    return unused_images(images, containers)


# --------------------------------------------------------------------------- commands
def sizes() -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    if shutil.which("snap"):
        snaps = snap_disabled()
        res["snap"] = {"size": sum(s["size"] for s in snaps), "names": [f"{s['name']} ({s['rev']})" for s in snaps]}
    if shutil.which("apt-get"):
        names = autoremove_list()
        res["autoremove"] = {"size": installed_size(names), "names": names,
                             "kernels": len([n for n in names if KERNEL_RE.match(n)])}
    if os.path.isdir("/var/log"):
        logs = rotated_logs()
        res["varlog"] = {"size": sum(fsize(p) for p in logs), "count": len(logs)}
    if docker_roots():
        dlogs = docker_logs()
        res["docker_logs"] = {"size": sum(fsize(p) for p in dlogs["live"] + dlogs["rotated"]), "count": len(dlogs["live"])}
    files = crash_files()
    if files or any(os.path.isdir(d) for d in CRASH_DIRS):
        res["crash"] = {"size": sum(fsize(p) for p in files), "count": len(files)}
    imgs = k3s_unused()
    if imgs is not None:
        res["k3s_images"] = {"size": sum(i["size"] for i in imgs), "names": [i["name"] for i in imgs]}
    return res


def remove_files(paths: List[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def cleanup(item: str) -> None:
    if item == "snap":
        failed = []
        for s in snap_disabled():
            r = run(["snap", "remove", s["name"], f"--revision={s['rev']}"], timeout=300)
            if r.returncode != 0:
                failed.append(s["name"])
        if failed:
            die("snap konnte nicht entfernen: " + ", ".join(failed))
    elif item == "autoremove":
        r = run(["apt-get", "-y", "--purge", "-o", "Dpkg::Options::=--force-confold", "autoremove"], timeout=1200,
                env={"DEBIAN_FRONTEND": "noninteractive"})
        if r.returncode != 0:
            err = r.stderr.strip()
            if "lock" in err.lower():
                die("apt ist gerade beschäftigt (Updates laufen?) – später erneut versuchen.")
            die(err.splitlines()[-1][:200] if err else "apt-get autoremove fehlgeschlagen.")
    elif item == "varlog":
        remove_files(rotated_logs())
    elif item == "docker_logs":
        logs = docker_logs()
        remove_files(logs["rotated"])
        for p in logs["live"]:
            try:
                os.truncate(p, 0)    # Docker keeps writing to the open file; emptying is safe, deleting is not
            except OSError:
                pass
    elif item == "crash":
        remove_files(crash_files())
    elif item == "k3s_images":
        base = crictl()
        if not base:
            die("crictl / k3s nicht gefunden.")
        r = run([*base, "rmi", "--prune"], timeout=600)
        if r.returncode != 0:
            die((r.stderr.strip().splitlines() or ["crictl rmi --prune fehlgeschlagen."])[-1][:200])


def main(argv: List[str]) -> None:
    if os.geteuid() != 0:
        die("Muss als root laufen (pi-helper.sh cleanup).")
    if argv == ["sizes"]:
        print(json.dumps(sizes()))
    elif len(argv) == 2 and argv[0] == "run" and argv[1] in ITEMS:
        cleanup(argv[1])
        print("ok")
    else:
        die("Unbekannter Befehl.")


if __name__ == "__main__":
    main(sys.argv[1:])
