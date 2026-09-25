"""
Network diagnostics for the system page: DNS lookup and traceroute.

DNS uses a small built-in client (UDP, TCP fallback for long answers), so any record
type can be asked for at any server without extra packages. Traceroute runs the
installed `traceroute` or `tracepath`.
"""

import concurrent.futures
import ipaddress
import random
import re
import shutil
import socket
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

TYPES = {"A": 1, "NS": 2, "CNAME": 5, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}
TYPE_NAMES = {v: k for k, v in TYPES.items()}
RCODES = {0: "OK", 1: "Formatfehler", 2: "Serverfehler", 3: "Name existiert nicht", 4: "Nicht unterstützt", 5: "Abgelehnt"}
SERVERS = {"system": None, "cloudflare": "1.1.1.1", "google": "8.8.8.8", "quad9": "9.9.9.9"}
NAME_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9])?\.?$")


def system_resolver() -> str:
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                m = re.match(r"^\s*nameserver\s+(\S+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "1.1.1.1"


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna") if label else b""
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _read_name(msg: bytes, pos: int) -> Tuple[str, int]:
    labels, jumped, end, hops = [], False, pos, 0
    while True:
        if pos >= len(msg) or hops > 64:
            raise ValueError("Kaputte DNS-Antwort.")
        length = msg[pos]
        if length & 0xC0 == 0xC0:  # compression pointer
            if not jumped:
                end = pos + 2
            pos = ((length & 0x3F) << 8) | msg[pos + 1]
            jumped, hops = True, hops + 1
            continue
        if length == 0:
            if not jumped:
                end = pos + 1
            break
        labels.append(msg[pos + 1:pos + 1 + length].decode("utf-8", errors="replace"))
        pos += 1 + length
    return ".".join(labels) or ".", end


def _rdata(msg: bytes, rtype: int, pos: int, length: int) -> str:
    data = msg[pos:pos + length]
    if rtype == 1 and length == 4:
        return socket.inet_ntop(socket.AF_INET, data)
    if rtype == 28 and length == 16:
        return socket.inet_ntop(socket.AF_INET6, data)
    if rtype in (2, 5, 12):
        return _read_name(msg, pos)[0]
    if rtype == 15:
        pref = struct.unpack("!H", data[:2])[0]
        return f"{pref} {_read_name(msg, pos + 2)[0]}"
    if rtype == 16:
        parts, i = [], 0
        while i < len(data):
            n = data[i]
            parts.append(data[i + 1:i + 1 + n].decode("utf-8", errors="replace"))
            i += 1 + n
        return "".join(parts)
    return data.hex()


def _query(server: str, packet: bytes, timeout: float) -> bytes:
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(4096)
    if len(data) > 2 and data[2] & 0x02:  # truncated: ask again over TCP
        with socket.create_connection((server, 53), timeout=timeout) as s:
            s.sendall(struct.pack("!H", len(packet)) + packet)
            size = struct.unpack("!H", s.recv(2))[0]
            data = b""
            while len(data) < size:
                chunk = s.recv(size - len(data))
                if not chunk:
                    break
                data += chunk
    return data


def dns_lookup(name: str, rtype: str = "A", server: str = "system") -> Dict[str, Any]:
    name = name.strip()
    rtype = rtype.upper()
    if rtype not in TYPES:
        raise ValueError("Unbekannter Eintragstyp.")
    # An IP address: reverse lookup
    try:
        ip = ipaddress.ip_address(name)
        name, rtype = ip.reverse_pointer, "PTR"
    except ValueError:
        if not NAME_RE.match(name):
            raise ValueError("Ungültiger Name.")
    if server in SERVERS:
        server_ip = SERVERS[server] or system_resolver()
    else:
        try:
            server_ip = str(ipaddress.ip_address(server.strip()))
        except ValueError:
            raise ValueError("DNS-Server muss eine IP-Adresse sein.")

    qid = random.randint(0, 0xFFFF)
    packet = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _encode_name(name) + struct.pack("!HH", TYPES[rtype], 1)
    t0 = time.time()
    try:
        msg = _query(server_ip, packet, 3)
    except socket.timeout:
        raise ValueError(f"Keine Antwort von {server_ip}.")
    except OSError as e:
        raise ValueError(f"DNS-Server {server_ip} nicht erreichbar: {e.strerror or e}")
    ms = round((time.time() - t0) * 1000, 1)
    rid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", msg[:12])
    if rid != qid:
        raise ValueError("Antwort passt nicht zur Anfrage.")
    pos = 12
    for _ in range(qd):
        _, pos = _read_name(msg, pos)
        pos += 4
    answers = []
    for _ in range(an):
        rname, pos = _read_name(msg, pos)
        t, _cls, ttl, length = struct.unpack("!HHIH", msg[pos:pos + 10])
        pos += 10
        answers.append({"name": rname, "type": TYPE_NAMES.get(t, str(t)), "ttl": ttl, "value": _rdata(msg, t, pos, length)})
        pos += length
    rcode = flags & 0x0F
    return {"name": name, "type": rtype, "server": server_ip, "ms": ms, "rcode": RCODES.get(rcode, str(rcode)),
            "ok": rcode == 0, "authoritative": bool(flags & 0x0400), "answers": answers}


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9A-Fa-f:]{2,39}$")


def _ptr(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return ""


def traceroute(host: str, max_hops: int = 20) -> Dict[str, Any]:
    host = host.strip()
    if not HOST_RE.match(host):
        raise ValueError("Ungültiger Hostname oder IP-Adresse.")
    max_hops = max(1, min(int(max_hops), 30))
    try:
        target = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except OSError:
        raise ValueError(f"„{host}“ lässt sich nicht auflösen.")

    if shutil.which("traceroute") or shutil.which("traceroute", path="/usr/sbin:/usr/bin"):
        tool = shutil.which("traceroute") or shutil.which("traceroute", path="/usr/sbin:/usr/bin")
        cmd = [tool, "-n", "-q", "1", "-w", "1", "-m", str(max_hops), target]
    elif shutil.which("tracepath"):
        cmd = [shutil.which("tracepath"), "-n", "-m", str(max_hops), target]
    else:
        raise ValueError("traceroute ist nicht installiert: sudo apt install traceroute")

    t0 = time.time()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")

    hops: Dict[int, Dict[str, Any]] = {}
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+):?\s+(.*)$", line)
        if not m:
            continue
        n, rest = int(m.group(1)), m.group(2)
        if "no reply" in rest or rest.strip().startswith("*"):
            hops.setdefault(n, {"hop": n, "ip": None, "ms": None})
            continue
        ipm = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]{3,39})", rest)
        msm = re.search(r"([\d.]+)\s?ms", rest)
        if ipm and (n not in hops or hops[n]["ip"] is None):
            hops[n] = {"hop": n, "ip": ipm.group(1), "ms": float(msm.group(1)) if msm else None}
    ordered = [hops[k] for k in sorted(hops)]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        names = list(pool.map(lambda h: _ptr(h["ip"]) if h["ip"] else "", ordered))
    for h, name in zip(ordered, names):
        h["name"] = name
    reached = any(h["ip"] == target for h in ordered)
    return {"host": host, "target": target, "hops": ordered, "reached": reached,
            "tool": cmd[0].split("/")[-1], "seconds": round(time.time() - t0, 1)}
