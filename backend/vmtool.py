"""
Long VM jobs, run as a dashboard task (see vms.py) - downloading images and creating VMs.

    python3 vmtool.py fetch  <spec.json>    image / ISO by https into a storage pool
    python3 vmtool.py create <spec.json>    new VM (cloud image + cloud-init, ISO, disk, PXE)

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
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

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


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("fetch", "create"):
        fail("Aufruf: vmtool.py fetch|create <spec.json>")
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
    else:
        if not shutil.which("virt-install"):
            fail("virt-install fehlt (Paket virtinst).")
        create(spec)


if __name__ == "__main__":
    main()
