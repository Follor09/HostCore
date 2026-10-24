"""
Every sensor the Pi exposes, in one list:

- hwmon (/sys/class/hwmon): CPU/SoC, RP1 I/O chip, NVMe, fan speed, ...
- vcgencmd: PMIC temperature and the PMIC's voltage/current readings per power rail
- thermal zones as a fallback on other boards

A few key values are stored once a minute (monitor.py) for a 24h / 7-day history.
"""

import glob
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import system_info

# hwmon chip name -> readable name (German, translated in the UI)
CHIP_LABEL = {
    "cpu_thermal": "CPU (SoC)", "rp1_adc": "RP1-Chip", "nvme": "NVMe", "pwmfan": "Lüfter",
    "cooling_fan": "Lüfter", "rpi_volt": "Spannungswächter", "coretemp": "CPU", "k10temp": "CPU",
    "acpitz": "Mainboard", "drivetemp": "Festplatte", "iwlwifi_1": "WLAN", "brcmfmac": "WLAN",
}
# Rail names of the Pi 5 PMIC (vcgencmd pmic_read_adc)
RAIL_LABEL = {
    "EXT5V": "Eingang 5 V (Netzteil)", "VDD_CORE": "CPU-Kern", "3V3_SYS": "3,3 V System", "1V8_SYS": "1,8 V System",
    "DDR_VDD2": "RAM VDD2", "DDR_VDDQ": "RAM VDDQ", "1V1_SYS": "1,1 V System", "0V8_SW": "0,8 V (geschaltet)",
    "VDD_CORE_A": "CPU-Kern", "3V7_WL_SW": "3,7 V WLAN", "HDMI": "HDMI", "3V3_DAC": "3,3 V Audio-DAC",
    "3V3_ADC": "3,3 V ADC", "0V8_AON": "0,8 V (immer an)",
}
# Stored for the history chart (sensor id -> kept)
HISTORY_IDS = ("temp:cpu_thermal:1", "temp:pmic", "temp:rp1_adc:1", "temp:nvme:1", "volt:EXT5V", "volt:VDD_CORE")

_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _num(path: str) -> Optional[float]:
    v = _read(path)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _hwmon() -> List[Dict[str, Any]]:
    out = []
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        chip = _read(f"{hw}/name") or os.path.basename(hw)
        base = CHIP_LABEL.get(chip, chip)
        for f in sorted(glob.glob(f"{hw}/temp*_input")):
            n = re.search(r"temp(\d+)_input", f).group(1)
            v = _num(f)
            if v is None or v <= -40000:
                continue
            label = _read(f"{hw}/temp{n}_label")
            name = base if n == "1" and not label else f"{base} · {label or 'Sensor ' + n}"
            crit = _num(f"{hw}/temp{n}_crit")
            out.append({"id": f"temp:{chip}:{n}", "group": "temp", "label": name, "value": round(v / 1000, 1),
                        "unit": "°C", "source": chip, "crit": round(crit / 1000) if crit and crit > 0 else None})
        if chip == "rp1_adc":
            continue  # its in1..in4 are unconnected ADC pins, not supply voltages
        for f in sorted(glob.glob(f"{hw}/in*_input")):
            n = re.search(r"in(\d+)_input", f).group(1)
            v = _num(f)
            if v is None:
                continue
            label = _read(f"{hw}/in{n}_label") or f"Spannung {n}"
            out.append({"id": f"volt:{chip}:{n}", "group": "volt", "label": f"{base} · {label}", "value": round(v / 1000, 3),
                        "unit": "V", "source": chip})
        for f in sorted(glob.glob(f"{hw}/fan*_input")):
            n = re.search(r"fan(\d+)_input", f).group(1)
            v = _num(f)
            if v is not None:
                out.append({"id": f"fan:{chip}:{n}", "group": "fan", "label": base, "value": int(v), "unit": "U/min", "source": chip})
        for f in sorted(glob.glob(f"{hw}/power*_input")):
            n = re.search(r"power(\d+)_input", f).group(1)
            v = _num(f)
            if v is not None:
                out.append({"id": f"power:{chip}:{n}", "group": "power", "label": base, "value": round(v / 1e6, 2), "unit": "W", "source": chip})
    # rpi_volt only has an alarm flag: under-voltage right now?
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(f"{hw}/name") == "rpi_volt":
            alarm = _read(f"{hw}/in0_lcrit_alarm")
            if alarm is not None:
                out.append({"id": "alarm:undervoltage", "group": "status", "label": "Unterspannung", "value": alarm == "1",
                            "unit": "", "source": "rpi_volt"})
    return out


def _vcgencmd(*args: str) -> str:
    try:
        return subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _pmic() -> List[Dict[str, Any]]:
    if not system_info.is_raspberry_pi():
        return []
    out = []
    m = re.search(r"temp=([\d.]+)", _vcgencmd("measure_temp", "pmic"))
    if m:
        out.append({"id": "temp:pmic", "group": "temp", "label": "PMIC (Stromversorgung)", "value": float(m.group(1)),
                    "unit": "°C", "source": "vcgencmd"})
    adc = _vcgencmd("pmic_read_adc")
    volts, amps = {}, {}
    for m in re.finditer(r"^\s*(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)[AV]\s*$", adc, re.MULTILINE):
        (amps if m.group(2) == "A" else volts)[m.group(1)] = float(m.group(3))
    for rail, v in volts.items():
        out.append({"id": f"volt:{rail}", "group": "volt", "label": RAIL_LABEL.get(rail, rail), "value": round(v, 3),
                    "unit": "V", "source": "pmic", "rail": rail,
                    "amp": round(amps[rail], 3) if rail in amps else None,
                    "watt": round(v * amps[rail], 2) if rail in amps else None})
    return out


def _thermal_zones(existing: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Other boards: thermal zones that hwmon didn't already cover."""
    if any(s["group"] == "temp" for s in existing):
        return []
    out = []
    for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        v = _num(f"{z}/temp")
        if v is not None:
            kind = _read(f"{z}/type") or os.path.basename(z)
            out.append({"id": f"temp:zone:{os.path.basename(z)}", "group": "temp", "label": kind, "value": round(v / 1000, 1),
                        "unit": "°C", "source": "thermal"})
    return out


def read_all(max_age: float = 3) -> Dict[str, Any]:
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < max_age:
        return _cache["data"]
    sensors = _hwmon()
    sensors += _pmic()
    sensors += _thermal_zones(sensors)
    data = {"sensors": sensors, "ts": int(now), "supported": bool(sensors)}
    _cache.update(ts=now, data=data)
    return data


def history_values() -> Dict[str, float]:
    """The key sensors stored once a minute."""
    return {s["id"]: s["value"] for s in read_all(max_age=30)["sensors"]
            if s["id"] in HISTORY_IDS and isinstance(s["value"], (int, float))}
