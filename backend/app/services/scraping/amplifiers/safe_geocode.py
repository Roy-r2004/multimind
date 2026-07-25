"""Safe coordinate parsing without external calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafeCoordinates:
    latitude: float
    longitude: float


def safe_parse_coordinates(latitude: object, longitude: object) -> SafeCoordinates | None:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return SafeCoordinates(latitude=round(lat, 6), longitude=round(lon, 6))
