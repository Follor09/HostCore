"""
Weather widget: location (settings.json) and the cached Open-Meteo answer.
"""

import settings_store


# Weather location, persisted in settings.json (untracked, so it survives git updates)
DEFAULT_WEATHER_LOCATION = {"city": "Berlin", "lat": 52.52, "lon": 13.41}


def load_weather_location() -> dict:
    loc = settings_store.get("weather_location")
    if isinstance(loc, dict) and {"city", "lat", "lon"} <= loc.keys():
        return loc
    return dict(DEFAULT_WEATHER_LOCATION)


def save_weather_location(loc: dict) -> None:
    settings_store.set("weather_location", loc)


# Cached weather response to prevent rate limiting (changed in place, shared by app.py and routes)
cache = {
    "temp": 20.0,
    "humidity": 50,
    "wind_speed": 10.0,
    "description": "Klar",
    "weather_code": 0,
    "city": "Berlin (Default)",
    "timestamp": 0
}


def interpret_wmo_code(code: int) -> tuple[str, str]:
    """Translate WMO weather code to German description and icon."""
    mapping = {
        0: ("Klarer Himmel", "sun"),
        1: ("Überwiegend klar", "sun-dim"),
        2: ("Teilweise bewölkt", "cloud-sun"),
        3: ("Bedeckt", "cloud"),
        45: ("Neblig", "cloud-fog"),
        48: ("Raureifnebel", "cloud-fog"),
        51: ("Leichter Nieselregen", "cloud-drizzle"),
        53: ("Nieselregen", "cloud-drizzle"),
        55: ("Starker Nieselregen", "cloud-drizzle"),
        61: ("Leichter Regen", "cloud-rain"),
        63: ("Mäßiger Regen", "cloud-rain"),
        65: ("Starker Regen", "cloud-rain-heavy"),
        71: ("Leichter Schneefall", "cloud-snow"),
        73: ("Schneefall", "cloud-snow"),
        75: ("Starker Schneefall", "cloud-snow"),
        80: ("Regenschauer", "cloud-showers"),
        81: ("Starke Schauer", "cloud-showers"),
        82: ("Heftige Schauer", "cloud-showers-heavy"),
        95: ("Gewitter", "cloud-lightning"),
        96: ("Gewitter mit Hagel", "cloud-lightning-hail"),
    }
    return mapping.get(code, ("Heiter", "cloud-sun"))
