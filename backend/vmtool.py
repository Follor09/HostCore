"""
Long VM jobs, run as a dashboard task (see vms.py) - downloading images and creating VMs.

    python3 vmtool.py fetch  <spec.json>    image / ISO by https into a storage pool
    python3 vmtool.py create <spec.json>    new VM (cloud image + cloud-init, ISO, disk, PXE)
    python3 vmtool.py backup <spec.json>    full copy of a VM's disks into a pool, with its definition
    python3 vmtool.py restore <spec.json>   a backup as a new VM
    python3 vmtool.py migrate <spec.json>   live migration to another host

The spec file was written by vms.py after checking every value; it is removed at the end
(it may hold a password hash and SSH keys). Downloads are checked against the SHA-256/512
sum published next to the image (or the one given in the form) before anything is used,
then uploaded into the pool through libvirt (the pool folders belong to root).
Only the standard library: it must not depend on the dashboard's state.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

URI = "qemu:///system"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS = os.path.join(BASE_DIR, "vms", "downloads")


def say(text: str) -> None:
    print(text, flush=True)


def fail(text: str) -> None:
    say(f"❌ {text}")
    sys.exit(1)


def virsh(*args: str, check: bool = True, timeout: float = 600) -> str:
    res = subprocess.run(["virsh", "-c", URI, *args], capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "LC_ALL": "C"}, errors="replace")
    if check and res.returncode != 0:
        fail(f"virsh {args[0]}: {res.stderr.strip().replace('error: ', '')[:500]}")
    return res.stdout


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Download + checksum
# ---------------------------------------------------------------------------
def _get(url: str, timeout: float = 60):
    if not url.startswith("https://"):
        fail("Nur https-Adressen.")
    req = urllib.request.Request(url, headers={"User-Agent": "HostCore-Dashboard"})
    return urllib.request.urlopen(req, timeout=timeout)


def expected_sum(spec: dict) -> str:
    if spec.get("sha"):
        return spec["sha"]
    if not spec.get("sums"):
        return ""
    name = os.path.basename(spec["url"])
    with _get(spec["sums"]) as r:
        text = r.read(4_000_000).decode(errors="replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name and re.match(r"^[0-9a-f]{64,128}$", parts[0]):
            return parts[0]
    fail(f"{name} steht nicht in {spec['sums']} – Download abgebrochen.")
    return ""


def download(spec: dict) -> str:
    os.makedirs(DOWNLOADS, mode=0o700, exist_ok=True)
    want = expected_sum(spec)
    algo = "sha512" if len(want) == 128 else "sha256"
    if not want:
        say("⚠️  Keine Prüfsumme angegeben – die Datei wird ungeprüft übernommen.")
    say(f"▶ Herunterladen: {spec['url']}")
    h = hashlib.new(algo)
    fd, path = tempfile.mkstemp(dir=DOWNLOADS, suffix=".part")
    try:
        with _get(spec["url"]) as r, os.fdopen(fd, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            if total:
                free = shutil.disk_usage(DOWNLOADS).free
                if total > free - 512 * 1024 * 1024:
                    fail(f"Zu wenig Platz: {human(total)} nötig, {human(free)} frei.")
                say(f"  Größe: {human(total)}")
            done, last, started = 0, -1, time.time()
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                step = int(done * 10 / total) if total else int(done / (100 * 1024 * 1024))
                if step != last:
                    last = step
                    rate = done / max(0.1, time.time() - started)
                    say(f"  {human(done)}" + (f" von {human(total)} ({done * 100 // total} %)" if total else "") + f" · {human(rate)}/s")
        if want:
            got = h.hexdigest()
            if got != want:
                fail(f"Prüfsumme stimmt nicht ({algo}): erwartet {want[:16]}…, erhalten {got[:16]}… – Datei verworfen.")
            say(f"✔ Prüfsumme ({algo.upper()}) stimmt.")
        return path
    except Exception as e:
        if os.path.exists(path):
            os.unlink(path)
        if isinstance(e, SystemExit):
            raise
        fail(f"Download fehlgeschlagen: {e}")
    return ""


def qcow2_size(path: str) -> int:
    """Virtual size from the qcow2 header (0 = not a qcow2 file)."""
    with open(path, "rb") as f:
        head = f.read(32)
    if len(head) < 32 or head[:4] != b"QFI\xfb":
        return 0
    return struct.unpack(">Q", head[24:32])[0]


def upload(path: str, pool: str, volume: str, kind: str, replace: bool) -> None:
    existing = virsh("vol-list", "--pool", pool, check=False)
    if re.search(rf"^\s*{re.escape(volume)}\s", existing, re.M):
        if not replace:
            fail(f"{volume} gibt es im Pool {pool} schon.")
        say(f"▶ Alte Version von {volume} ersetzen")
        virsh("vol-delete", "--pool", pool, volume)
    size = os.path.getsize(path)
    if kind == "cloud":
        vsize = qcow2_size(path)
        if not vsize:
            fail("Die Datei ist kein qcow2-Image.")
        virsh("vol-create-as", pool, volume, str(vsize), "--format", "qcow2")
    else:
        virsh("vol-create-as", pool, volume, str(size), "--format", "raw")
    say(f"▶ In den Pool {pool} übertragen ({human(size)})")
    res = subprocess.run(["virsh", "-c", URI, "vol-upload", "--pool", pool, volume, path], capture_output=True, text=True)
    if res.returncode != 0:
        virsh("vol-delete", "--pool", pool, volume, check=False)
        fail(f"Übertragen fehlgeschlagen: {res.stderr.strip()[:400]}")
    virsh("pool-refresh", pool, check=False)
    say(f"✔ {volume} liegt im Pool {pool}.")


def fetch(spec: dict) -> None:
    path = download(spec)
    try:
        upload(path, spec["pool"], spec["volume"], spec["kind"], spec.get("replace", False))
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# Create a VM
# ---------------------------------------------------------------------------
_osinfo_ids = None


def osinfo_known(short_id: str) -> bool:
    global _osinfo_ids
    if _osinfo_ids is None:
        _osinfo_ids = set()
        for cmd in (["virt-install", "--osinfo", "list"], ["osinfo-query", "os", "-f", "short-id"]):
            if not shutil.which(cmd[0]):
                continue
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    _osinfo_ids.update(x.strip() for x in line.replace("|", ",").split(",") if x.strip())
                break
    return short_id in _osinfo_ids


def cloud_config(ci: dict) -> str:
    user = {"name": ci["user"], "groups": ["sudo"], "shell": "/bin/bash", "lock_passwd": not ci["hash"],
            "sudo": "ALL=(ALL) ALL" if ci["hash"] else "ALL=(ALL) NOPASSWD:ALL"}
    if ci["hash"]:
        user["passwd"] = ci["hash"]
    if ci["keys"]:
        user["ssh_authorized_keys"] = ci["keys"]
    cfg = {"hostname": ci["hostname"], "manage_etc_hosts": True, "users": [user], "ssh_pwauth": bool(ci["ssh_password"]),
           "disable_root": True, "package_update": bool(ci["upgrade"] or ci["agent"]), "package_upgrade": bool(ci["upgrade"])}
    if ci.get("timezone"):
        cfg["timezone"] = ci["timezone"]
    if ci["agent"]:
        cfg["packages"] = ["qemu-guest-agent"]
        cfg["runcmd"] = [["systemctl", "enable", "--now", "qemu-guest-agent"]]
    # JSON is valid YAML: no quoting surprises with keys or hashes
    return "#cloud-config\n" + json.dumps(cfg, indent=2) + "\n"


def create(spec: dict) -> None:
    name, pool = spec["name"], spec["pool"]
    disk = f"{name}.qcow2"
    args = ["virt-install", "--connect", URI, "--name", name, "--memory", str(spec["memory_mb"]), "--vcpus", str(spec["vcpus"]),
            "--network", spec["network"], "--graphics", "vnc,listen=127.0.0.1", "--noautoconsole",
            "--channel", "unix,target_type=virtio,name=org.qemu.guest_agent.0", "--rng", "/dev/urandom"]
    if spec["kvm"]:
        args += ["--virt-type", "kvm", "--cpu", "host-passthrough"]
    else:
        say("⚠️  Kein KVM verfügbar – die VM läuft emuliert (deutlich langsamer).")
        args += ["--virt-type", "qemu"]
    if spec["firmware"] == "uefi":
        args += ["--boot", "uefi"]
    meta = []
    if spec.get("title"):
        meta.append(f"title={spec['title'].replace(',', ' ')}")
    if spec.get("description"):
        meta.append(f"description={spec['description'].replace(',', ' ').replace(chr(10), ' ')}")
    if meta:
        args += ["--metadata", ",".join(meta)]
    osinfo = spec.get("osinfo") or ""
    if osinfo and osinfo_known(osinfo):
        args += ["--os-variant", osinfo]
    else:
        if osinfo:
            say(f"  Betriebssystem {osinfo} kennt diese libosinfo-Version nicht – allgemeines Linux-Profil.")
        args += ["--os-variant", "linux2022" if osinfo_known("linux2022") else "detect=on,require=off"]
    created_vol = ""
    workdir = tempfile.mkdtemp(dir=os.path.dirname(DOWNLOADS))
    try:
        if spec["source"] == "cloud":
            vols = virsh("vol-list", "--pool", pool)
            if not re.search(rf"^\s*{re.escape(spec['base'])}\s", vols, re.M):
                if not spec.get("url"):
                    fail(f"{spec['base']} fehlt im Pool {pool}.")
                say(f"▶ Image {spec['base']} ist noch nicht da – wird zuerst heruntergeladen")
                fetch({"url": spec["url"], "sums": spec.get("sums"), "kind": "cloud", "pool": pool, "volume": spec["base"]})
            else:
                say(f"▶ Image {spec['base']} aus dem Pool {pool}")
            say(f"▶ Laufwerk {disk} anlegen ({spec['disk_gb']} GB)")
            virsh("vol-clone", "--pool", pool, spec["base"], disk)
            created_vol = disk
            info = virsh("vol-info", "--pool", pool, disk, "--bytes")
            cap = int((re.search(r"Capacity:\s+(\d+)", info) or [0, "0"])[1])
            want = spec["disk_gb"] * 1024 ** 3
            if want > cap:
                virsh("vol-resize", "--pool", pool, disk, str(want))
            ud = os.path.join(workdir, "user-data")
            md = os.path.join(workdir, "meta-data")
            with open(ud, "w") as f:
                f.write(cloud_config(spec["cloudinit"]))
            with open(md, "w") as f:
                f.write(f"instance-id: {name}-{int(time.time())}\nlocal-hostname: {spec['cloudinit']['hostname']}\n")
            args += ["--disk", f"vol={pool}/{disk},bus=virtio", "--import", "--cloud-init", f"user-data={ud},meta-data={md}"]
        elif spec["source"] == "iso":
            say(f"▶ Laufwerk {disk} anlegen ({spec['disk_gb']} GB)")
            virsh("vol-create-as", pool, disk, f"{spec['disk_gb']}G", "--format", "qcow2")
            created_vol = disk
            args += ["--disk", f"vol={pool}/{disk},bus=virtio", "--cdrom", spec["iso"], "--wait", "0"]
        elif spec["source"] == "disk":
            args += ["--disk", f"vol={pool}/{spec['volume']}", "--import"]
        elif spec["source"] == "pxe":
            say(f"▶ Laufwerk {disk} anlegen ({spec['disk_gb']} GB)")
            virsh("vol-create-as", pool, disk, f"{spec['disk_gb']}G", "--format", "qcow2")
            created_vol = disk
            args += ["--disk", f"vol={pool}/{disk},bus=virtio", "--pxe", "--wait", "0"]
        if spec.get("autostart"):
            args.append("--autostart")
        say("▶ " + " ".join(a if not a.startswith("user-data=") else "user-data=…,meta-data=…" for a in args))
        res = subprocess.run(args, stdout=sys.stdout, stderr=subprocess.STDOUT, timeout=1800)
        if res.returncode != 0:
            if created_vol:
                virsh("vol-delete", "--pool", pool, created_vol, check=False)
            fail("virt-install ist fehlgeschlagen.")
        say("")
        say(f"✅ VM {name} ist angelegt.")
        if spec["source"] == "cloud":
            ci = spec["cloudinit"]
            say(f"   Anmeldung: {ci['user']}" + (" mit SSH-Schlüssel" if ci["keys"] else "") + (" / Passwort" if ci["hash"] else ""))
            say("   Die IP-Adresse erscheint in der Übersicht, sobald der Gast sie per DHCP bezogen hat (cloud-init braucht beim ersten Start 1–2 Minuten).")
        elif spec["source"] in ("iso", "pxe"):
            say("   Die Installation läuft – weiter in der grafischen Konsole der VM.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Backups: a full copy of every disk into a storage pool, plus a manifest with the domain XML.
# A running VM goes through `virsh backup-begin` (consistent, no downtime); a VM that is off
# through vol-create-from. Both flatten backing files, so a backup stands on its own.
# ---------------------------------------------------------------------------
BACKUP_DIR = os.path.join(BASE_DIR, "vms", "backups")


class Cancelled(Exception):
    pass


def _on_term(signum, frame):
    raise Cancelled()


def pool_path(pool: str) -> str:
    path = ET.fromstring(virsh("pool-dumpxml", pool)).findtext("target/path") or ""
    if not path:
        fail(f"Pool {pool} hat keinen Ordner – nur Ordner-Pools sind als Sicherungsziel möglich.")
    return path


def real_disks(xml: str) -> list:
    """Disks of a domain (no CD-ROMs): [{target, type, path, pool, volume, format}]"""
    out = []
    for d in ET.fromstring(xml).findall("devices/disk"):
        if d.get("device", "disk") not in ("disk", "lun"):
            continue
        src, tgt, drv = d.find("source"), d.find("target"), d.find("driver")
        if src is None or tgt is None:
            continue
        pool, vol = src.get("pool") or "", src.get("volume") or ""
        path = src.get("file") or src.get("dev") or ""
        if pool and vol:
            path = virsh("vol-path", "--pool", pool, vol).strip()
        out.append({"target": tgt.get("dev"), "type": d.get("type", "file"), "path": path, "pool": pool, "volume": vol,
                    "format": drv.get("type", "raw") if drv is not None else "raw"})
    return out


def vol_capacity(pool: str, vol: str) -> dict:
    info = virsh("vol-info", "--pool", pool, vol, "--bytes")
    field = lambda k: int((re.search(rf"{k}:\s+(\d+)", info) or [0, "0"])[1])
    return {"capacity": field("Capacity"), "allocation": field("Allocation")}


def copy_volume(src_pool: str, src_vol: str, pool: str, vol: str, capacity: int) -> None:
    """vol-create-from copies across pools and flattens a backing file (qemu-img convert)."""
    xml = (f"<volume><name>{escape(vol)}</name><capacity unit='bytes'>{capacity}</capacity>"
           "<target><format type='qcow2'/></target></volume>")
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(xml)
    try:
        virsh("vol-create-from", pool, f.name, src_vol, "--inputpool", src_pool, timeout=12 * 3600)
    finally:
        os.unlink(f.name)


def job_info(vm: str, completed: bool = False) -> dict:
    out = virsh("domjobinfo", vm, *(["--completed", "--keep-completed"] if completed else []), check=False)
    info = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        info[k.strip()] = v.strip()
    return info


def run_live_backup(vm: str, plan: list, skip: list) -> None:
    disks = "".join(f"<disk name='{escape(d['target'])}' backup='yes' type='file'><target file='{escape(d['file'])}'/>"
                    "<driver type='qcow2'/></disk>" for d in plan)
    disks += "".join(f"<disk name='{escape(t)}' backup='no'/>" for t in skip)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(f"<domainbackup><disks>{disks}</disks></domainbackup>")
    try:
        virsh("backup-begin", vm, f.name)
    finally:
        os.unlink(f.name)
    shown = -10
    while True:
        time.sleep(2)
        info = job_info(vm)
        if info.get("Job type", "None") in ("None", ""):
            break
        done = float((info.get("Data processed") or "0").split()[0] or 0)
        total = float((info.get("Data total") or "0").split()[0] or 0)
        percent = int(done * 100 / total) if total else 0
        if percent >= shown + 10:
            say(f"   {percent} %")
            shown = percent
    state = job_info(vm, completed=True).get("Job type", "")
    if state in ("Failed", "Cancelled"):
        fail(f"Sicherung abgebrochen ({state}).")


def write_manifest(manifest: dict) -> None:
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    tmp = os.path.join(BACKUP_DIR, f".{manifest['id']}.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, os.path.join(BACKUP_DIR, f"{manifest['id']}.json"))


def prune(vm: str, schedule: str, keep: int) -> None:
    """Keep the newest `keep` backups of one schedule; manual backups are never removed here."""
    items = []
    for name in os.listdir(BACKUP_DIR):
        if name.endswith(".json"):
            try:
                with open(os.path.join(BACKUP_DIR, name)) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if m.get("vm") == vm and m.get("schedule") == schedule:
                items.append(m)
    items.sort(key=lambda m: -m["ts"])
    for m in items[keep:]:
        for d in m["disks"]:
            virsh("vol-delete", "--pool", m["pool"], d["volume"], check=False)
        os.unlink(os.path.join(BACKUP_DIR, f"{m['id']}.json"))
        say(f"   Alte Sicherung vom {time.strftime('%d.%m.%Y %H:%M', time.localtime(m['ts']))} entfernt.")


def backup(spec: dict) -> None:
    vm, pool, bid = spec["vm"], spec["pool"], spec["id"]
    signal.signal(signal.SIGTERM, _on_term)
    state = virsh("domstate", vm).strip()
    live = state in ("running", "paused")
    inactive_xml = virsh("dumpxml", "--inactive", vm)
    active_xml = virsh("dumpxml", vm)
    disks = real_disks(active_xml)
    if not disks:
        fail("Die VM hat kein Laufwerk.")
    if any(d["type"] not in ("file", "volume", "block") for d in disks):
        fail("Nur Datei-, Volume- und Block-Laufwerke lassen sich sichern.")
    root = pool_path(pool)
    virsh("pool-refresh", pool, check=False)
    plan = []
    for d in disks:
        vol = f"hc-backup-{vm}-{bid}-{d['target']}.qcow2"
        plan.append({**d, "backup_volume": vol, "file": os.path.join(root, vol)})
    made = []
    try:
        say(f"▶ Sicherung von {vm} ({'im laufenden Betrieb' if live else 'ausgeschaltet'}) nach Pool {pool}")
        if live:
            skip = [t.get("dev") for t in ET.fromstring(active_xml).findall("devices/disk/target")
                    if t.get("dev") not in {d["target"] for d in disks}]
            made = [d["backup_volume"] for d in plan]
            run_live_backup(vm, plan, skip)
        else:
            for d in plan:
                say(f"▶ Laufwerk {d['target']} kopieren")
                src_vol = d["volume"]
                src_pool = d["pool"]
                if not src_pool:
                    src_pool = virsh("vol-pool", d["path"], check=False).strip()
                    src_vol = virsh("vol-name", d["path"], check=False).strip()
                if not src_pool or not src_vol:
                    fail(f"{d['path']} liegt in keinem Speicher-Pool und lässt sich bei ausgeschalteter VM nicht sichern.")
                d["capacity"] = vol_capacity(src_pool, src_vol)["capacity"]
                made.append(d["backup_volume"])
                copy_volume(src_pool, src_vol, pool, d["backup_volume"], d["capacity"])
        virsh("pool-refresh", pool, check=False)
        rows = []
        for d in plan:
            size = vol_capacity(pool, d["backup_volume"])
            if not size["allocation"]:
                fail(f"Sicherung von {d['target']} ist leer.")
            rows.append({"target": d["target"], "volume": d["backup_volume"], "source": d["path"], **size})
        write_manifest({"id": bid, "vm": vm, "ts": int(time.time()), "pool": pool, "live": live, "state": state, "user": spec.get("user", ""),
                        "schedule": spec.get("schedule", ""), "disks": rows, "xml": inactive_xml})
    except (Cancelled, SystemExit, Exception) as e:
        if isinstance(e, Cancelled):
            say("Abbruch – die Sicherung wird verworfen.")
        if live:
            virsh("domjobabort", vm, check=False)
            time.sleep(1)
        virsh("pool-refresh", pool, check=False)
        for vol in made:
            virsh("vol-delete", "--pool", pool, vol, check=False)
        if isinstance(e, SystemExit):
            raise
        fail(str(e) if not isinstance(e, Cancelled) else "Abgebrochen.")
    if spec.get("keep") and spec.get("schedule"):
        prune(vm, spec["schedule"], int(spec["keep"]))
    say("")
    say(f"✅ Sicherung abgeschlossen: {human(sum(r['allocation'] for r in rows))} in Pool {pool}.")


def restored_xml(xml: str, name: str, disks: dict) -> str:
    """The saved definition as a new VM: new name, UUID and MACs, disks pointing at the copies."""
    root = ET.fromstring(xml)
    root.find("name").text = name
    uuid = root.find("uuid")
    if uuid is not None:
        root.remove(uuid)
    for mac in root.findall("devices/interface"):
        el = mac.find("mac")
        if el is not None:
            mac.remove(el)
    os_el = root.find("os")
    nvram = os_el.find("nvram") if os_el is not None else None
    if nvram is not None:
        os_el.remove(nvram)   # libvirt creates a fresh one from the template
    devices = root.find("devices")
    for d in list(devices.findall("disk")):
        tgt = d.find("target")
        dev = tgt.get("dev") if tgt is not None else ""
        if d.get("device", "disk") in ("disk", "lun"):
            if dev not in disks:
                devices.remove(d)   # added after the backup was defined
                continue
            d.set("type", "file")
            src = d.find("source")
            if src is None:
                src = ET.SubElement(d, "source")
            src.attrib.clear()
            src.set("file", disks[dev])
            drv = d.find("driver")
            if drv is not None:
                drv.set("type", "qcow2")
            bs = d.find("backingStore")
            if bs is not None:
                d.remove(bs)
        else:
            src = d.find("source")
            if src is not None:
                d.remove(src)     # the ISO may no longer exist
    return ET.tostring(root, encoding="unicode")


def restore(spec: dict) -> None:
    m, name, pool = spec["manifest"], spec["name"], spec["pool"]
    signal.signal(signal.SIGTERM, _on_term)
    root = pool_path(pool)
    paths, made = {}, []
    try:
        for d in m["disks"]:
            vol = f"{name}-{d['target']}.qcow2"
            say(f"▶ Laufwerk {d['target']} wiederherstellen ({human(d['allocation'])}) als {vol}")
            made.append(vol)
            copy_volume(m["pool"], d["volume"], pool, vol, d["capacity"])
            paths[d["target"]] = os.path.join(root, vol)
        virsh("pool-refresh", pool, check=False)
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(restored_xml(m["xml"], name, paths))
        try:
            virsh("define", f.name)
        finally:
            os.unlink(f.name)
    except (Cancelled, SystemExit, Exception) as e:
        virsh("pool-refresh", pool, check=False)
        for vol in made:
            virsh("vol-delete", "--pool", pool, vol, check=False)
        if isinstance(e, SystemExit):
            raise
        fail(str(e) if not isinstance(e, Cancelled) else "Abgebrochen.")
    say("")
    say(f"✅ {name} ist wiederhergestellt (ausgeschaltet, neue MAC-Adressen).")


def migrate(spec: dict) -> None:
    """Live migration with virsh; the source keeps running until the target has taken over."""
    vm, uri = spec["vm"], spec["uri"]
    autostart = (re.search(r"^Autostart:\s+(\S+)", virsh("dominfo", vm), re.M) or [0, ""])[1] == "enable"
    sources = [d["path"] for d in real_disks(virsh("dumpxml", vm))]
    args = ["virsh", "-c", URI, "migrate", "--live", "--persistent", "--undefinesource", "--verbose", "--auto-converge", "--abort-on-error"]
    if not spec.get("shared"):
        args.append("--copy-storage-all")
    args += ["--domain", vm, "--desturi", uri, "--migrateuri", spec["migrate_uri"]]
    say(f"▶ {vm} wird nach {spec['target_name']} verschoben" + (" (Laufwerke werden mitkopiert)" if not spec.get("shared") else ""))
    proc = subprocess.Popen(args, stdout=sys.stdout, stderr=subprocess.STDOUT, start_new_session=True)

    def stop(signum, frame):
        virsh("domjobabort", vm, check=False, timeout=30)   # the VM keeps running on the source

    signal.signal(signal.SIGTERM, stop)
    if proc.wait() != 0:
        fail("Migration fehlgeschlagen – die VM läuft weiter auf diesem Server.")
    res = subprocess.run(["virsh", "-c", uri, "domstate", vm], capture_output=True, text=True, timeout=60)
    if "running" not in res.stdout:
        fail(f"Das Ziel meldet die VM nicht als laufend: {(res.stdout + res.stderr).strip()[:200]}")
    if autostart:
        subprocess.run(["virsh", "-c", uri, "autostart", vm], capture_output=True, timeout=60)
    say("")
    say(f"✅ {vm} läuft jetzt auf {spec['target_name']}.")
    if not spec.get("shared"):
        say("   Die alten Laufwerke liegen noch hier – nach einer Kontrolle unter Speicher-Pools löschen:")
        for path in sources:
            say(f"   {path}")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in COMMANDS:
        fail("Aufruf: vmtool.py fetch|create|backup|restore|migrate <spec.json>")
    path = sys.argv[2]
    try:
        with open(path) as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        fail(f"Auftrag nicht lesbar: {e}")
    try:
        os.unlink(path)   # read once; holds a password hash and SSH keys
    except OSError:
        pass
    if not shutil.which("virsh"):
        fail("virsh fehlt (Paket libvirt-clients).")
    if sys.argv[1] == "fetch":
        fetch(spec)
    elif sys.argv[1] == "create":
        if not shutil.which("virt-install"):
            fail("virt-install fehlt (Paket virtinst).")
        create(spec)
    else:
        COMMANDS[sys.argv[1]](spec)


COMMANDS = {"fetch": fetch, "create": create, "backup": backup, "restore": restore, "migrate": migrate}

if __name__ == "__main__":
    main()
