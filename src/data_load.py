from typing import List, Optional, Tuple
import pandas as pd
import xml.etree.ElementTree as ET
import requests


def _load_kml_file(path: str) -> pd.DataFrame:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"kml": "http://www.opengis.net/kml/2.2"}

    rows = []
    for placemark in root.findall(".//kml:Placemark", ns):
        name = placemark.find("kml:name", ns)
        desc = placemark.find("kml:description", ns)
        coords = placemark.find(".//kml:coordinates", ns)
        if coords is not None and coords.text:
            lon, lat, *_ = coords.text.strip().split(",")
            rows.append(
                {
                    "id": None,
                    "name": name.text if name is not None else "",
                    "description": desc.text if desc is not None else "",
                    "lat": float(lat),
                    "lon": float(lon),
                }
            )
    df = pd.DataFrame(rows, columns=["id", "name", "description", "lat", "lon"])
    return df


def load_ev_stations_kml(path: str) -> pd.DataFrame:
    """
    Original loader used by the app (keeps legacy STxx ids).
    If you need to load a single KML only, use this.
    """
    df = _load_kml_file(path)
    if df.empty:
        return df
    # assign stable IDs ST01...
    df = df.reset_index(drop=True)
    df["id"] = [f"ST{(i+1):02d}" for i in range(len(df))]
    return df


def load_multiple_kml(paths: List[str]) -> pd.DataFrame:
    """
    Load and merge multiple KML files (e.g. base stations + Google My Maps export).
    Returns DataFrame with columns ["id","name","description","lat","lon"] and unique ST ids.
    """
    dfs = []
    for p in paths:
        try:
            d = _load_kml_file(p)
            if not d.empty:
                dfs.append(d)
        except Exception:
            # ignore problematic files but continue
            continue
    if not dfs:
        return pd.DataFrame(columns=["id", "name", "description", "lat", "lon"])

    merged = pd.concat(dfs, ignore_index=True)
    # drop exact duplicate coordinates (very small tolerance)
    merged = merged.drop_duplicates(subset=["lat", "lon"])
    merged = merged.reset_index(drop=True)
    merged["id"] = [f"ST{(i+1):05d}" for i in range(len(merged))]  # larger id space for merged sets
    return merged


def search_poi_overpass(
    query: str,
    center: Optional[Tuple[float, float]] = None,
    radius_m: int = 5000,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    limit: int = 10,
    timeout: int = 180,
) -> pd.DataFrame:
    """
    Search OSM/Overpass for POIs whose 'name' matches `query` (case-insensitive).
    - center: (lat, lon) to search around with radius_m
    - bbox: (south, west, north, east) to search within (overrides center if provided)
    - limit: max results to return
    Returns DataFrame columns: ['id','name','description','lat','lon'] (may be empty)
    """
    if not query or (center is None and bbox is None):
        # require either bbox or center to limit query scope (avoid huge queries)
        raise ValueError("Provide query and either center or bbox")

    # build Overpass QL
    q_esc = query.replace('"', '\\"')
    name_filter = f'(node["name"~"(?i){q_esc}"]; way["name"~"(?i){q_esc}"]; rel["name"~"(?i){q_esc}"]; )'
    if bbox:
        bbox_s = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
        q = f"""
        [out:json][timeout:{timeout}];
        (
          {name_filter}({bbox_s});
        );
        out center {limit};
        """
    else:
        lat, lon = float(center[0]), float(center[1])
        q = f"""
        [out:json][timeout:{timeout}];
        (
          {name_filter}(around:{int(radius_m)},{lat},{lon});
        );
        out center {limit};
        """

    url = "https://overpass-api.de/api/interpreter"
    try:
        resp = requests.post(url, data={"data": q}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return pd.DataFrame(columns=["id", "name", "description", "lat", "lon"])

    rows = []
    seen = set()
    for el in data.get("elements", []):
        el_type = el.get("type")
        osm_id = el.get("id")
        tags = el.get("tags", {}) or {}
        name = tags.get("name", "")
        # get coordinates: nodes have lat/lon; ways/relations return 'center' with lat/lon
        if el_type == "node":
            lat = el.get("lat")
            lon = el.get("lon")
        else:
            center_el = el.get("center") or el.get("bounds")
            if center_el:
                lat = center_el.get("lat") or center_el.get("minlat")
                lon = center_el.get("lon") or center_el.get("minlon")
            else:
                # skip if no center
                continue
        if lat is None or lon is None:
            continue
        desc = "; ".join([f"{k}={v}" for k, v in tags.items() if k != "name"]) if tags else ""
        key = (round(float(lat), 6), round(float(lon), 6), name)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "id": f"OSM_{el_type}_{osm_id}",
                "name": name or "POI",
                "description": desc,
                "lat": float(lat),
                "lon": float(lon),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["id", "name", "description", "lat", "lon"])

    df = pd.DataFrame(rows, columns=["id", "name", "description", "lat", "lon"])
    # trim to limit
    if len(df) > limit:
        df = df.iloc[:limit].reset_index(drop=True)
    return df