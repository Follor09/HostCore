"""
Fan curve of the Raspberry Pi 5 active cooler.

The four fan steps are device-tree parameters in config.txt (fan_temp0..3 + _speed);
they take effect after a reboot. Reading needs no root (config.txt is world-readable),
writing goes through pi-helper.sh from root's repo copy, which keeps a backup.
"""

import os
import re
from typing import Any, Dict, List, Optional

import security
import system_info

CONFIG_PATHS = ["/boot/firmware/config.txt", "/boot/config.txt"]
DEFAULT = {"temps": [50.0, 60.0, 67.5, 75.0], "speeds": [75, 125, 175, 250]}  # Pi 5 firmware defaults
PRESETS = {
    "standard": {"label": "Standard", "desc": "Werkseinstellung des Pi 5.", **DEFAULT},
    "quiet": {"label": "Leise", "desc": "Springt später an und dreht sanfter – wärmer, aber leiser.",
              "temps": [60.0, 67.5, 72.5, 78.0], "speeds": [50, 100, 160, 250]},
    "cool": {"label": "Kühl", "desc": "Früh und kräftig – kühler, dafür hörbarer.",
             "temps": [40.0, 50.0, 60.0, 70.0], "speeds": [100, 150, 200, 250]},
}


def config_path() -> Optional[str]:
    return next((p for p in CONFIG_PATHS if os.path.isfile(p)), None)


def configured_curve() -> Optional[Dict[str, List]]:
    """Fan parameters from config.txt, or None when all four steps use the defaults."""
    path = config_path()
    if not path:
        return None
    values: Dict[str, int] = {}
    try:
        with open(path, errors="replace") as f:
            for line in f:
                m = re.match(r"^\s*dtparam=(fan_temp[0-3](?:_speed)?)=(\d+)", line)
                if m:
                    values[m.group(1)] = int(m.group(2))
    except OSError:
        return None
    if not values:
        return None
    temps = [values.get(f"fan_temp{i}", int(DEFAULT["temps"][i] * 1000)) / 1000 for i in range(4)]
    speeds = [values.get(f"fan_temp{i}_speed", DEFAULT["speeds"][i]) for i in range(4)]
    return {"temps": temps, "speeds": speeds}


def status() -> Dict[str, Any]:
    fan = system_info.get_fan_status()
    temp = system_info.get_cpu_temp()
    configured = configured_curve()
    curve = configured or DEFAULT
    active = fan.get("thresholds") or []
    # Thresholds in sysfs are what runs now; differ from config.txt -> a reboot is pending
    pending = bool(active) and [round(t, 1) for t in active[:4]] != [round(t, 1) for t in curve["temps"]]
    preset = next((k for k, p in PRESETS.items() if p["temps"] == curve["temps"] and p["speeds"] == curve["speeds"]), None)
    return {
        "available": bool(fan.get("available")) and system_info.is_pi5() and config_path() is not None,
        "config_path": config_path(),
        "curve": curve, "custom": configured is not None, "preset": preset,
        "active_thresholds": active, "reboot_pending": pending,
        "presets": [{"id": k, **p} for k, p in PRESETS.items()],
        "temp": None if temp.get("is_simulated") else temp.get("celsius"),
        "level": fan.get("level"), "rpm": fan.get("rpm"),
    }


def set_curve(temps: List[float], speed_percent: List[float]) -> Dict[str, Any]:
    if not system_info.is_pi5():
        raise ValueError("Die Lüfterkurve gibt es nur auf dem Raspberry Pi 5.")
    if len(temps) != 4 or len(speed_percent) != 4:
        raise ValueError("Es braucht genau vier Stufen.")
    try:
        mdeg = [int(round(float(t) * 2) / 2 * 1000) for t in temps]  # 0.5 °C steps
        pwm = [int(round(float(s) * 255 / 100)) for s in speed_percent]
    except (TypeError, ValueError):
        raise ValueError("Ungültige Zahl.")
    if not all(30000 <= t <= 90000 for t in mdeg) or any(b <= a for a, b in zip(mdeg, mdeg[1:])):
        raise ValueError("Temperaturen müssen zwischen 30 und 90 °C liegen und von Stufe zu Stufe steigen.")
    if not all(0 <= s <= 255 for s in pwm) or any(b < a for a, b in zip(pwm, pwm[1:])):
        raise ValueError("Drehzahlen müssen 0–100 % sein und dürfen von Stufe zu Stufe nicht sinken.")
    security._helper("fan-curve", "set", *map(str, mdeg), *map(str, pwm))
    return status()


def reset() -> Dict[str, Any]:
    security._helper("fan-curve", "reset")
    return status()
