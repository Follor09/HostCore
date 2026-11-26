"""
Wake-on-LAN: wake devices on the local network with a magic packet
(6 x 0xFF followed by the MAC address 16 times, sent as UDP broadcast).
Devices are stored in settings.json under "wol_devices". No root needed.

The list lives on the main dashboard only, whichever server is selected. A broadcast does
not cross routers, so each device says which server sends its packet ("via": "" = this
dashboard, else a remote id) - the main dashboard then asks that server's agent to send it.
"""

import re
import secrets
import socket
from typing import Any, Dict, List, Optional

import settings_store

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)([0-9A-Fa-f]{2})(\2[0-9A-Fa-f]{2}){4}$")


def normalize_mac(mac: str) -> str:
    mac = mac.strip()
    if not MAC_RE.match(mac):
        raise ValueError("Ungültige MAC-Adresse (Format: AA:BB:CC:DD:EE:FF).")
    hexdigits = re.sub(r"[^0-9A-Fa-f]", "", mac)
    return ":".join(hexdigits[i:i + 2] for i in range(0, 12, 2)).upper()


def list_devices() -> List[Dict[str, Any]]:
    return settings_store.get("wol_devices", []) or []


def normalize_broadcast(broadcast: str) -> str:
    broadcast = (broadcast or "").strip() or "255.255.255.255"
    try:
        socket.inet_aton(broadcast)
    except OSError:
        raise ValueError("Ungültige Broadcast-Adresse (z.B. 192.168.178.255).")
    return broadcast


def add_device(name: str, mac: str, broadcast: str = "", via: str = "") -> Dict[str, Any]:
    name = name.strip()[:40]
    if not name:
        raise ValueError("Bitte einen Namen angeben.")
    device = {"id": secrets.token_hex(4), "name": name, "mac": normalize_mac(mac),
              "broadcast": normalize_broadcast(broadcast), "via": (via or "").strip()[:32]}

    def add(devices):
        devices = devices or []
        if len(devices) >= 30:
            raise ValueError("Maximal 30 Geräte.")
        return devices + [device]
    settings_store.update("wol_devices", add, [])
    return device


def update_device(device_id: str, via: Optional[str] = None, broadcast: Optional[str] = None) -> Dict[str, Any]:
    if broadcast is not None:
        broadcast = normalize_broadcast(broadcast)
    found = {}

    def change(devices):
        devices = devices or []
        device = next((d for d in devices if d["id"] == device_id), None)
        if not device:
            raise ValueError("Gerät nicht gefunden.")
        if via is not None:
            device["via"] = via.strip()[:32]
        if broadcast is not None:
            device["broadcast"] = broadcast
        found["device"] = device
        return devices
    settings_store.update("wol_devices", change, [])
    return found["device"]


def get_device(device_id: str) -> Dict[str, Any]:
    device = next((d for d in list_devices() if d["id"] == device_id), None)
    if not device:
        raise ValueError("Gerät nicht gefunden.")
    return device


def import_devices(items: List[Dict[str, Any]], via: str) -> int:
    """Take over a remote server's own (older, per-server) list, sent from there. Same MAC = skipped."""
    devices = list_devices()
    macs = {d["mac"] for d in devices}
    added = 0
    for it in items:
        try:
            mac = normalize_mac(str(it.get("mac", "")))
            broadcast = normalize_broadcast(str(it.get("broadcast", "")))
        except ValueError:
            continue
        name = str(it.get("name", "")).strip()[:40]
        if not name or mac in macs or len(devices) >= 30:
            continue
        devices.append({"id": secrets.token_hex(4), "name": name, "mac": mac, "broadcast": broadcast, "via": via})
        macs.add(mac)
        added += 1
    if added:
        settings_store.set("wol_devices", devices)
    return added


def remove_device(device_id: str) -> None:
    settings_store.update("wol_devices", lambda devices: [d for d in devices or [] if d["id"] != device_id], [])


def send(mac: str, broadcast: str = "") -> None:
    """Send the magic packet from this server."""
    mac = normalize_mac(mac)
    broadcast = normalize_broadcast(broadcast)
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):  # the two common WoL ports
            sock.sendto(packet, (broadcast, port))
