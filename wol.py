"""
Wake-on-LAN: wake devices on the local network with a magic packet
(6 x 0xFF followed by the MAC address 16 times, sent as UDP broadcast).
Devices are stored in settings.json under "wol_devices". No root needed.
"""

import re
import secrets
import socket
from typing import Any, Dict, List

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


def add_device(name: str, mac: str, broadcast: str = "") -> Dict[str, Any]:
    name = name.strip()[:40]
    if not name:
        raise ValueError("Bitte einen Namen angeben.")
    broadcast = broadcast.strip() or "255.255.255.255"
    try:
        socket.inet_aton(broadcast)
    except OSError:
        raise ValueError("Ungültige Broadcast-Adresse (z.B. 192.168.178.255).")
    device = {"id": secrets.token_hex(4), "name": name, "mac": normalize_mac(mac), "broadcast": broadcast}
    devices = list_devices()
    if len(devices) >= 30:
        raise ValueError("Maximal 30 Geräte.")
    settings_store.set("wol_devices", devices + [device])
    return device


def remove_device(device_id: str) -> None:
    settings_store.set("wol_devices", [d for d in list_devices() if d["id"] != device_id])


def wake(device_id: str) -> Dict[str, Any]:
    device = next((d for d in list_devices() if d["id"] == device_id), None)
    if not device:
        raise ValueError("Gerät nicht gefunden.")
    packet = b"\xff" * 6 + bytes.fromhex(device["mac"].replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):  # the two common WoL ports
            sock.sendto(packet, (device["broadcast"], port))
    return device
