"""
Simple OSRM client to request alternative routes and decode polyline geometries.

- Uses the public OSRM demo by default: http://router.project-osrm.org
- Returns list of routes, each route is a list of (lat, lon) tuples (in decimal degrees).
- No external polyline dependency required (includes small decoder).
"""
from typing import List, Tuple, Optional
import json
import urllib.parse
import urllib.request


def _decode_polyline(encoded: str) -> List[Tuple[float, float]]:
    # Google / OSRM polyline decoding (precision 1e-5)
    coords: List[Tuple[float, float]] = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        result = shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        result = shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append((lat / 1e5, lng / 1e5))
    # coords are (lat, lon)
    return coords


def get_routes_osrm(
    start: Tuple[float, float],
    end: Tuple[float, float],
    osrm_url: str = "http://router.project-osrm.org",
    alternatives: bool = True,
    overview: str = "full",
    geometries: str = "polyline",
    max_retries: int = 1,
) -> Optional[List[List[Tuple[float, float]]]]:
    """
    Request routes from OSRM.

    - start/end: (lat, lon)
    - Returns: list of routes (each a list of (lat, lon)), or None on failure.
    """
    lat1, lon1 = start
    lat2, lon2 = end
    coords = f"{lon1},{lat1};{lon2},{lat2}"
    params = {
        "alternatives": "true" if alternatives else "false",
        "overview": overview,
        "geometries": geometries,
    }
    query = urllib.parse.urlencode(params)
    url = f"{osrm_url.rstrip('/')}/route/v1/driving/{coords}?{query}"

    for _ in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            if data.get("code") != "Ok":
                return None
            routes = []
            for r in data.get("routes", []):
                geom = r.get("geometry")
                if not geom:
                    continue
                if geometries == "polyline":
                    pts = _decode_polyline(geom)
                else:
                    # support geojson coordinates if present
                    coords_list = r.get("geometry", {}).get("coordinates", [])
                    pts = [(lat, lon) for lon, lat in coords_list]
                routes.append(pts)
            return routes
        except Exception:
            continue
    return None
