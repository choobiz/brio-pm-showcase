"""Address → (lat, lng) via Nominatim (OpenStreetMap), plus haversine distance.

Nominatim is the free OSM geocoder. It requires a descriptive User-Agent and
asks for <=1 req/sec — fine here because we geocode a project's address once and
cache it on the row (projects.latitude/longitude/geocoded_at). Best-effort:
any failure returns None and the caller stores geocoded_at so we don't retry on
every load.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "brio-pm/1.0 (amir@sister-co.example)"
TIMEOUT_S = 8


def geocode(address: str) -> tuple[float, float] | None:
    """Resolve a free-text address to (lat, lng). None on any failure."""
    if not address or not address.strip():
        return None
    q = urllib.parse.urlencode({"q": address.strip(), "format": "json", "limit": 1})
    req = urllib.request.Request(f"{NOMINATIM_URL}?{q}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two lat/lng points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))
