# Full file (updated) - plan_route now falls back to Overpass POI search when geocoding fails
import argparse
from typing import Optional, Tuple, List, Dict, Any
import sys
import io
import time

import pandas as pd

from data_load import load_ev_stations_kml, load_multiple_kml
# optional Overpass/OSM helper (may be absent)
try:
    from data_load import search_poi_overpass
    _OVERPASS_AVAILABLE = True
except Exception:
    _OVERPASS_AVAILABLE = False

from graph import build_graph, haversine_km, add_virtual_node, nearest_station
from routing_search import astar_ev_search, ucs_ev_search
from visualization import plot_path
import networkx as nx
from simulator import evaluate_routes, simulate_along_polyline

# Optional geocoding (Nominatim). Import is optional; code will handle missing package.
try:
    from geopy.geocoders import Nominatim
    _GEOPY_AVAILABLE = True
except Exception:
    _GEOPY_AVAILABLE = False

# Optional OSRM client (internal module)
try:
    from osrm_client import get_routes_osrm
    _OSRM_AVAILABLE = True
except Exception:
    _OSRM_AVAILABLE = False


# geocoding helpers — replace previous geocode_place definition with these

def geocode_candidates(place: str, timeout: int = 5, limit: int = 8) -> List[Tuple[float, float, str]]:
    """
    Robust Nominatim candidate search with retries and multiple query variants.
    Returns list of (lat, lon, display_name).
    """
    if not _GEOPY_AVAILABLE:
        return []
    try:
        geolocator = Nominatim(user_agent="xe_ev_planner", timeout=timeout)
    except Exception:
        return []

    variants = [
        place,
        f"{place}, Vietnam",
        f"{place}, VN",
        f"{place}, Long An, Vietnam",
        f"{place}, Ho Chi Minh City, Vietnam",
        f"{place}, Hồ Chí Minh, Vietnam",
    ]

    results: List[Tuple[float, float, str]] = []
    for q in variants:
        for attempt in range(2):  # small retry
            try:
                locs = geolocator.geocode(q, exactly_one=False, limit=limit, language="vi")
                if not locs:
                    break
                if isinstance(locs, list):
                    for loc in locs:
                        results.append((float(loc.latitude), float(loc.longitude), getattr(loc, "address", str(loc))))
                else:
                    results.append((float(locs.latitude), float(locs.longitude), getattr(locs, "address", str(locs))))
                break
            except Exception:
                time.sleep(0.4)
                continue
        if results:
            break

    # final fallback: single result without country restriction
    if not results:
        try:
            locs = geolocator.geocode(place, exactly_one=False, limit=limit, language="vi")
            if locs:
                if isinstance(locs, list):
                    for loc in locs:
                        results.append((float(loc.latitude), float(loc.longitude), getattr(loc, "address", str(loc))))
                else:
                    results.append((float(locs.latitude), float(locs.longitude), getattr(locs, "address", str(locs))))
        except Exception:
            pass

    # dedupe by rounded coords
    seen = set()
    deduped: List[Tuple[float, float, str]] = []
    for lat, lon, name in results:
        key = (round(lat, 6), round(lon, 6))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((lat, lon, name))
    return deduped


def geocode_place(place: str, timeout: int = 5) -> Optional[Tuple[float, float]]:
    """
    Backwards-compatible wrapper: return first candidate or None.
    """
    cands = geocode_candidates(place, timeout=timeout, limit=3)
    if not cands:
        return None
    lat, lon, _ = cands[0]
    return (lat, lon)


def _choose_candidate_interactive(candidates: List[Tuple[float, float, str]], role: str) -> Optional[Tuple[float, float]]:
    """
    Present numbered candidates to user and return chosen (lat,lon) or None.
    role: 'start' or 'end' used in prompt.
    Non-interactive environments should not call this helper.
    """
    if not candidates:
        return None
    print(f"Found {len(candidates)} place matches for {role}:")
    for i, (lat, lon, name) in enumerate(candidates):
        print(f"{i}: {name} -> {lat:.6f},{lon:.6f}")
    sel = input(f"Select {role} by number (Enter to cancel / pick 0): ").strip()
    if sel == "":
        return None
    if sel.isdigit():
        idx = int(sel)
        if 0 <= idx < len(candidates):
            return (candidates[idx][0], candidates[idx][1])
    print("Invalid selection, cancelling.")
    return None


def _choose_overpass_interactive(pois_df: "pd.DataFrame", role: str) -> Optional[Tuple[float, float]]:
    """
    Present Overpass results to user and return selected (lat, lon) or None.
    """
    if pois_df is None or pois_df.empty:
        return None
    print(f"Found {len(pois_df)} OSM candidates for {role}:")
    for i, row in pois_df.reset_index(drop=True).iterrows():
        name = row.get("name", "")
        desc = row.get("description", "")
        lat = row.get("lat")
        lon = row.get("lon")
        print(f"{i}: {name} ({lat:.6f},{lon:.6f})  {desc}")
    sel = input(f"Select {role} by number (Enter to cancel / pick 0): ").strip()
    if sel == "":
        return None
    if sel.isdigit():
        idx = int(sel)
        if 0 <= idx < len(pois_df):
            r = pois_df.reset_index(drop=True).iloc[idx]
            return (float(r["lat"]), float(r["lon"]))
    print("Invalid selection.")
    return None


def choose_station_by_input(user_str: str, stations) -> Optional[Tuple[float, float] or str]:
    s = user_str.strip()
    if s in list(stations["id"]):
        return s

    matches = stations[stations["name"].str.contains(s, case=False, na=False)]
    if len(matches) == 1:
        return matches.iloc[0]["id"]
    if len(matches) > 1:
        print(f"Tìm thấy {len(matches)} địa điểm phù hợp:")
        for i, row in matches.reset_index(drop=True).iterrows():
            print(f"{i}: {row['name']} (ID={row['id']})")
        sel = input("Chọn số tương ứng (Enter để hủy): ").strip()
        if sel.isdigit():
            idx = int(sel)
            if 0 <= idx < len(matches):
                return matches.reset_index(drop=True).iloc[idx]["id"]
        return None

    if "," in s:
        try:
            lat, lon = [float(x.strip()) for x in s.split(",")[:2]]
            return (lat, lon)
        except Exception:
            return None
    return None


def apply_filters(G: nx.Graph, avoid_highway: bool, avoid_toll: bool) -> nx.Graph:
    G2 = G.copy()
    if avoid_highway:
        for u, v, d in list(G2.edges(data=True)):
            if d.get("is_highway", False):
                G2.remove_edge(u, v)
    if avoid_toll:
        for u, v, d in list(G2.edges(data=True)):
            if d.get("toll", False):
                G2.remove_edge(u, v)
    return G2


def _append_virtual_station_row(stations_df: pd.DataFrame, node_id: str, name: str, lat: float, lon: float) -> pd.DataFrame:
    row = pd.DataFrame([{"id": node_id, "name": name, "lat": float(lat), "lon": float(lon)}])
    return pd.concat([stations_df, row], ignore_index=True)


def _snap_polyline_to_stations(G: nx.Graph, stations_df: pd.DataFrame, poly: List[Tuple[float, float]], snap_radius_km: float = 5.0) -> List[str]:
    snapped: List[str] = []
    for lat, lon in poly:
        res = nearest_station(G, lat, lon, radius_km=snap_radius_km)
        if res:
            nid, d = res
            if not snapped or snapped[-1] != nid:
                snapped.append(nid)
    return snapped


def plan_route(
    start_input: str,
    end_input: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Programmatic planner entry point.

    start_input/end_input: strings, either "lat,lon" or station id or partial name (same as CLI).
    config: optional dict to override parameters:
      consumption (kWh/100km), battery_kwh, battery_percent, safe_threshold,
      pref, avoid_highway, avoid_toll, enable_nearby, nearby_k,
      max_search_dist, use_astar, use_osrm, osrm_url, snap_radius_km, max_expansions
    Returns dict:
      { "output": str (text), "map_html": str (full HTML) or None, "result": dict (best simulated) }
    """
    cfg = {
        "consumption": 16.3,
        "battery_kwh": 60.0,
        "battery_percent": 50.0,
        "safe_threshold": 20.0,
        "pref": "1",
        "avoid_highway": False,
        "avoid_toll": False,
        "enable_nearby": True,
        "nearby_k": 5,
        "max_search_dist": 100.0,
        "use_astar": True,
        "use_osrm": True,
        "osrm_url": "http://router.project-osrm.org",
        "snap_radius_km": 5.0,
        "max_expansions": 50000,
        # default to attempt geocode for free-form place names
        "use_geocode": True,
    }
    if config:
        cfg.update(config)

    # load stations (base)
    stations = load_ev_stations_kml("../data/evcs_map.kml")

    # allow extra_kml via config
    if cfg.get("extra_kml"):
        extra = load_multiple_kml(cfg["extra_kml"])
        if not extra.empty:
            combined = pd.concat([stations, extra], ignore_index=True)
            combined = combined.drop_duplicates(subset=["lat", "lon"]).reset_index(drop=True)
            combined["id"] = [f"ST{(i+1):05d}" for i in range(len(combined))]
            stations = combined

    # Build graph
    G = build_graph(stations, k_neighbors=8)
    G_filtered = apply_filters(G, cfg["avoid_highway"], cfg["avoid_toll"])

    # Resolve start/end against station list (ID or name) or coords
    # interactive_flow may already provide (lat, lon) tuples.
    if isinstance(start_input, tuple):
        start_sel = start_input
    else:
        start_sel = choose_station_by_input(str(start_input), stations)

    if isinstance(end_input, tuple):
        end_sel = end_input
    else:
        end_sel = choose_station_by_input(str(end_input), stations)

    # If unresolved, try geocode (Nominatim) and if that fails try Overpass POI search (first result)
    if start_sel is None:
        if _GEOPY_AVAILABLE and cfg.get("use_geocode"):
            cands = geocode_candidates(start_input)
            if cands:
                if sys.stdin.isatty():
                    sel = _choose_candidate_interactive(cands, "start")
                    if sel:
                        start_sel = sel
                else:
                    # server / non-interactive -> pick best (first)
                    start_sel = (cands[0][0], cands[0][1])
        # after attempting geocode...
        if start_sel is None and _OVERPASS_AVAILABLE:
            try:
                pois = search_poi_overpass(start_input, center=(14.0583, 108.2772), radius_m=300000, limit=8)
                if not pois.empty:
                    if sys.stdin.isatty():
                        sel = _choose_overpass_interactive(pois, "start")
                        if sel:
                            start_sel = sel
                    else:
                        r = pois.iloc[0]
                        start_sel = (float(r["lat"]), float(r["lon"]))
                        print(f"Overpass selected start -> {r.get('name','<no-name>')} at {start_sel}")
            except Exception:
                pass

    if end_sel is None:
            if _GEOPY_AVAILABLE and cfg.get("use_geocode"):
                cands = geocode_candidates(end_input)
                if cands:
                    if sys.stdin.isatty():
                        sel = _choose_candidate_interactive(cands, "end")
                        if sel:
                            end_sel = sel
                    else:
                        end_sel = (cands[0][0], cands[0][1])
            if end_sel is None and _OVERPASS_AVAILABLE:
                try:
                    pois = search_poi_overpass(end_input, center=(14.0583, 108.2772), radius_m=300000, limit=5)
                    if not pois.empty:
                        r = pois.iloc[0]
                        end_sel = (float(r["lat"]), float(r["lon"]))
                        print(f"Overpass matched end -> {r.get('name','<no-name>')} at {end_sel}")
                except Exception:
                    pass

    if start_sel is None or end_sel is None:
        return {"output": "Cannot resolve start or end.", "map_html": None, "result": None}

    # Prepare stations_mod and virtual nodes
    stations_mod = stations.copy()

    def _unique_id(base: str):
        idx = 0
        nid = base
        while nid in G.nodes:
            idx += 1
            nid = f"{base}_{idx}"
        return nid

    # start
    start_coord = None
    end_coord = None
    if isinstance(start_sel, tuple):
        lat, lon = start_sel
        start_coord = (lat, lon)
        start_node = _unique_id("START")
        add_virtual_node(G, start_node, lat, lon, k_neighbors=8, max_dist_km=cfg["max_search_dist"])
        stations_mod = _append_virtual_station_row(stations_mod, start_node, "Start (user)", lat, lon)
    else:
        start_node = start_sel
        try:
            r = stations_mod.loc[stations_mod["id"] == start_node].iloc[0]
            start_coord = (float(r["lat"]), float(r["lon"]))
        except Exception:
            start_coord = None

    # end
    if isinstance(end_sel, tuple):
        lat, lon = end_sel
        end_coord = (lat, lon)
        end_node = _unique_id("END")
        add_virtual_node(G, end_node, lat, lon, k_neighbors=8, max_dist_km=cfg["max_search_dist"])
        stations_mod = _append_virtual_station_row(stations_mod, end_node, "End (user)", lat, lon)
    else:
        end_node = end_sel
        try:
            r = stations_mod.loc[stations_mod["id"] == end_node].iloc[0]
            end_coord = (float(r["lat"]), float(r["lon"]))
        except Exception:
            end_coord = None

    # Search / simulate
    candidate_sim_results: List[Dict] = []

    # OSRM baseroutes (real-road) + simulate along polyline.
    # also pass osrm_url into simulator so detours use OSRM too.
    if cfg["use_osrm"] and _OSRM_AVAILABLE and start_coord and end_coord:
        routes = get_routes_osrm(start_coord, end_coord, osrm_url=cfg["osrm_url"], alternatives=True)
        if routes:
            for poly in routes:
                sim = simulate_along_polyline(
                    G_filtered,
                    stations_mod,
                    poly,
                    battery_percent_start=cfg["battery_percent"],
                    battery_kwh_max=cfg["battery_kwh"],
                    consumption_kwh_per_100km=cfg["consumption"],
                    safe_threshold_percent=cfg["safe_threshold"],
                    charge_target_percent=(90.0 if cfg["pref"] == "3" else 80.0),
                    avg_speed_kmh=(60.0 if cfg["pref"] != "2" else 1.0),
                    nearby_k=cfg["nearby_k"],
                    nearby_radius_km=cfg["max_search_dist"],
                    virtual_k_neighbors=8,
                    virtual_max_dist_km=cfg["max_search_dist"],
                    osrm_url=cfg.get("osrm_url"),
                )
                if sim and sim.get("feasible"):
                    snapped_nodes = _snap_polyline_to_stations(G_filtered, stations_mod, poly, snap_radius_km=cfg["snap_radius_km"])
                    if start_node not in snapped_nodes:
                        snapped_nodes = [start_node] + snapped_nodes
                    if end_node not in snapped_nodes:
                        snapped_nodes = snapped_nodes + [end_node]
                    dedup: List[str] = []
                    for n in snapped_nodes:
                        if not dedup or dedup[-1] != n:
                            dedup.append(n)
                    sim["route"] = dedup
                    sim["detour_polylines"] = sim.get("detour_polylines", [])
                    candidate_sim_results.append(sim)

    if not candidate_sim_results:
        # graph-based search + evaluate (fallback)
        search_fn = astar_ev_search if cfg["use_astar"] else ucs_ev_search
        path, dist, time_min, charges = search_fn(
            G_filtered,
            stations_mod,
            start=start_node,
            end=end_node,
            battery_percent=cfg["battery_percent"],
            battery_kwh_max=cfg["battery_kwh"],
            consumption_kwh_per_100km=cfg["consumption"],
            safe_threshold_percent=cfg["safe_threshold"],
            charge_target_percent=(90.0 if cfg["pref"] == "3" else 80.0),
            avg_speed_kmh=(60.0 if cfg["pref"] != "2" else 1.0),
            enable_nearby_search=cfg["enable_nearby"],
            max_search_distance_km=cfg["max_search_dist"],
            nearby_k=cfg["nearby_k"],
            charge_penalty_minutes=0.0,
            max_expansions=cfg["max_expansions"],
        )
        if path is None:
            return {"output": "No feasible path found.", "map_html": None, "result": None}
        vehicle_params = {
            "battery_percent_start": cfg["battery_percent"],
            "battery_kwh_max": cfg["battery_kwh"],
            "consumption_kwh_per_100km": cfg["consumption"],
        }
        sim_options = {
            "avg_speed_kmh": (60.0 if cfg["pref"] != "2" else 1.0),
            "safe_threshold_percent": cfg["safe_threshold"],
            "charge_target_percent": (90.0 if cfg["pref"] == "3" else 80.0),
            "nearby_k": cfg["nearby_k"],
            "nearby_radius_km": cfg["max_search_dist"],
        }
        eval_results = evaluate_routes(G_filtered, stations_mod, [path], vehicle_params, sim_options, sort_by="time")
        best_eval = next((r for r in eval_results if r.get("feasible")), None)
        if not best_eval:
            return {"output": "No feasible simulated route after evaluation.", "map_html": None, "result": None}
        best_eval["route"] = path
        candidate_sim_results = [best_eval]

    # Pick best by time
    candidate_sim_results.sort(key=lambda r: (float("inf") if not r.get("feasible") else r["total_time_min"]))
    best = candidate_sim_results[0]

    # Build text output
    buf = io.StringIO()
    buf.write("KẾT QUẢ MÔ PHỎNG TỐT NHẤT:\n")
    buf.write(f"Route nodes: {best.get('route')}\n")
    buf.write(f"Tổng quãng đường: {best.get('total_distance_km')} km\n")
    buf.write(f"Tổng thời gian (phút): {best.get('total_time_min')} (drive {best.get('total_driving_time_min')} + charge {best.get('total_charging_time_min')})\n")
    buf.write(f"Các lần sạc: {best.get('charges')}\n")

    # Create map HTML
    m = plot_path(stations_mod, best["route"], charging_stops=best.get("charges"), detour_polylines=best.get("detour_polylines", []), output_file=None)
    map_html = m.get_root().render()

    return {"output": buf.getvalue(), "map_html": map_html, "result": best}


def prompt_float(prompt: str, default: Optional[float] = None) -> float:
    raw = input(f"{prompt} [{'Enter' if default is not None else 'required'}]: ").strip()
    if raw == "" and default is not None:
        return default
    return float(raw)


def interactive_flow(stations):
    print("\nNhập vị trí bắt đầu A. Có thể nhập ID (VD: ST01), một phần tên, hoặc 'lat,lon'.")
    a_in = input("A (start): ").strip()
    start_sel = choose_station_by_input(a_in, stations)
    if start_sel is None:
        if _GEOPY_AVAILABLE:
            try_geocode = input("Không tìm thấy trạm trùng tên. Thử geocode tên địa danh này thành tọa độ? (Y/n): ").strip().lower() != "n"
            if try_geocode:
                geo = geocode_place(a_in)
                if geo:
                    print(f"Geocode thành công: {geo[0]:.6f},{geo[1]:.6f}")
                    start_sel = geo
                else:
                    print("Geocode thất bại.")
        if start_sel is None:
            print("Không xác định được điểm A. Hủy.")
            return None

    print("\nNhập vị trí đích B. Có thể nhập ID (VD: ST03), một phần tên, hoặc 'lat,lon'.")
    b_in = input("B (end): ").strip()
    end_sel = choose_station_by_input(b_in, stations)
    if end_sel is None:
        if _GEOPY_AVAILABLE:
            try_geocode = input("Không tìm thấy trạm trùng tên. Thử geocode tên địa danh này thành tọa độ? (Y/n): ").strip().lower() != "n"
            if try_geocode:
                geo = geocode_place(b_in)
                if geo:
                    print(f"Geocode thành công: {geo[0]:.6f},{geo[1]:.6f}")
                    end_sel = geo
                else:
                    print("Geocode thất bại.")
        if end_sel is None:
            print("Không xác định được điểm B. Hủy.")
            return None

    print("\nNhập thông số xe (Enter để dùng giá trị mặc định):")
    consumption = prompt_float("Mức tiêu thụ (kWh/100km)", 16.3)
    battery_kwh_max = prompt_float("Dung lượng pin tối đa (kWh)", 60.0)
    battery_percent = prompt_float("Mức pin hiện tại (%)", 50.0)
    safe_threshold = prompt_float("Ngưỡng pin an toàn (%)", 20.0)

    print("\nTùy chọn ưu tiên:")
    print("1: Tổng thời gian ít nhất")
    print("2: Tổng quãng đường ngắn nhất")
    print("3: Ít lần sạc nhất (ưu tiên tránh sạc nếu khả thi)")
    pref = input("Chọn 1/2/3 (mặc định 1): ").strip() or "1"

    print("\nBộ lọc đường:")
    avoid_highway = input("Tránh cao tốc? (y/N): ").strip().lower() == "y"
    avoid_toll = input("Tránh trạm thu phí? (y/N): ").strip().lower() == "y"

    enable_nearby = input("Bật tìm trạm lân cận khi pin yếu? (Y/n): ").strip().lower() != "n"
    nearby_k = int(input("Số trạm lân cận tối đa (nearby_k) [default 5]: ").strip() or "5")
    max_search_dist = float(input("Bán kính tìm trạm lân cận (km) [default 100]: ").strip() or "100")

    return {
        "start": start_sel,
        "end": end_sel,
        "consumption": consumption,
        "battery_kwh_max": battery_kwh_max,
        "battery_percent": battery_percent,
        "safe_threshold": safe_threshold,
        "pref": pref,
        "avoid_highway": avoid_highway,
        "avoid_toll": avoid_toll,
        "enable_nearby": enable_nearby,
        "nearby_k": nearby_k,
        "max_search_dist": max_search_dist,
    }


def main():
    parser = argparse.ArgumentParser(description="Xe EV route planner (CLI)")
    # keep existing args
    parser.add_argument("--start", help="Start station id or name or lat,lon")
    parser.add_argument("--end", help="End station id or name or lat,lon")
    parser.add_argument("--consumption", type=float, default=16.3)
    parser.add_argument("--battery-kwh", type=float, default=60.0)
    parser.add_argument("--battery-percent", type=float, default=50.0)
    parser.add_argument("--safe-threshold", type=float, default=20.0)
    parser.add_argument("--pref", choices=["1", "2", "3"], default="1", help="1=time,2=distance,3=fewest charges")
    parser.add_argument("--avoid-highway", action="store_true")
    parser.add_argument("--avoid-toll", action="store_true")
    parser.add_argument("--no-nearby", action="store_true", help="Disable nearby search")
    parser.add_argument("--nearby-k", type=int, default=5)
    parser.add_argument("--max-search-dist", type=float, default=100.0)
    parser.add_argument("--use-astar", action="store_true", help="Use A* (default if provided)")
    parser.add_argument("--charge-penalty", type=float, default=0.0, help="Penalty minutes to add per charging action")
    parser.add_argument("--use-geocode", action="store_true", help="If enabled, try geocoding free-form place names to coords (requires geopy)")
    parser.add_argument("--use-osrm", action="store_true", help="Use OSRM to fetch alternative baseroutes (requires osrm_client.py)")
    parser.add_argument("--osrm-url", default="http://router.project-osrm.org", help="OSRM service URL")
    parser.add_argument("--snap-radius-km", type=float, default=5.0, help="Radius to snap polyline points to stations (km)")
    parser.add_argument("--max-expansions", type=int, default=50000, help="Max search node expansions to avoid long runs")
    parser.add_argument("--extra-kml", nargs="+", help="Additional KML file(s) (e.g. Google My Maps export) to merge into station list")
    args = parser.parse_args()

    cfg = {
        "consumption": args.consumption,
        "battery_kwh": args.battery_kwh,
        "battery_percent": args.battery_percent,
        "safe_threshold": args.safe_threshold,
        "pref": args.pref,
        "avoid_highway": args.avoid_highway,
        "avoid_toll": args.avoid_toll,
        "enable_nearby": not args.no_nearby,
        "nearby_k": args.nearby_k,
        "max_search_dist": args.max_search_dist,
        "use_astar": args.use_astar,
        "use_osrm": args.use_osrm,
        "osrm_url": args.osrm_url,
        "snap_radius_km": args.snap_radius_km,
        "max_expansions": args.max_expansions,
        "extra_kml": args.extra_kml,
        "use_geocode": args.use_geocode,
    }

    # Resolve start/end: use CLI if provided, otherwise interactive if terminal attached
    if args.start and args.end:
        start_input = args.start
        end_input = args.end
    else:
        # if running interactively, prompt user
        if sys.stdin.isatty():
            print("\n--- Chuyển sang chế độ tương tác ---", flush=True)
            user_inputs = interactive_flow(load_ev_stations_kml("../data/evcs_map.kml"))
            if user_inputs is None:
                return
            # map interactive keys to cfg
            cfg.update({
                "consumption": user_inputs["consumption"],
                "battery_kwh": user_inputs["battery_kwh_max"],
                "battery_percent": user_inputs["battery_percent"],
                "safe_threshold": user_inputs["safe_threshold"],
                "pref": user_inputs["pref"],
                "avoid_highway": user_inputs["avoid_highway"],
                "avoid_toll": user_inputs["avoid_toll"],
                "enable_nearby": user_inputs["enable_nearby"],
                "nearby_k": user_inputs["nearby_k"],
                "max_search_dist": user_inputs["max_search_dist"],
            })
            start_input = user_inputs["start"]
            end_input = user_inputs["end"]
        else:
            print("Start/end required (no interactive terminal).")
            return

    res = plan_route(start_input, end_input, cfg)
    print(res["output"])
    # save map to file for CLI usage
    if res.get("map_html"):
        with open("ev_route_simulated.html", "w", encoding="utf-8") as f:
            f.write(res["map_html"])
        print("Bản đồ mô phỏng đã lưu: ev_route_simulated.html")


if __name__ == "__main__":
    main()