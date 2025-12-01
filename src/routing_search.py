from heapq import heappush, heappop
from typing import List, Optional, Tuple, Dict
import itertools

import networkx as nx

from graph import haversine_km
from energy_model import kwh_needed, charge_time_minutes


class State:
    def __init__(
        self,
        node: str,
        battery_percent: float,
        path: List[str],
        total_distance_km: float,
        total_time_min: float,
        charges: List[Dict],
    ):
        self.node = node
        self.battery_percent = battery_percent
        self.path = path
        self.total_distance_km = total_distance_km
        self.total_time_min = total_time_min
        self.charges = charges


def _edge_distance(u, v, data) -> float:
    return data.get("distance", data.get("distance_km", float("inf")))


def ucs_ev_search(
    G,
    stations_df,
    start: str,
    end: str,
    battery_percent: float,
    battery_kwh_max: float,
    consumption_kwh_per_100km: float,
    safe_threshold_percent: float = 20.0,
    charge_target_percent: float = 80.0,
    avg_speed_kmh: float = 60.0,
    enable_nearby_search: bool = True,
    max_search_distance_km: float = 100.0,
    nearby_k: int = 5,
    charge_penalty_minutes: float = 0.0,
    max_expansions: int = 100000,
) -> Tuple[Optional[List[str]], Optional[float], Optional[float], Optional[List[Dict]]]:
    """
    UCS cho EV với:
    - giới hạn candidate nearby_k cho tìm trạm lân cận
    - penalty cố định cho mỗi lần sạc (charge_penalty_minutes) được cộng vào chi phí thời gian
    """
    counter = itertools.count()
    pq = []
    start_state = State(start, battery_percent, [start], 0.0, 0.0, [])
    heappush(pq, (0.0, next(counter), start_state))
    visited = {}

    stations_index = stations_df.set_index("id")

    expansions = 0

    while pq:
        expansions += 1
        if expansions > max_expansions:
            # reached limit; abort search to avoid long stall
            return None, None, None, None

        cost, _, state = heappop(pq)

        if state.node == end:
            return state.path, state.total_distance_km, state.total_time_min, state.charges

        key = (state.node, round(state.battery_percent, 1))
        if key in visited and visited[key] <= cost:
            continue
        visited[key] = cost

        for neighbor in G.neighbors(state.node):
            edge = G[state.node][neighbor]
            dist = _edge_distance(state.node, neighbor, edge)
            if dist is None:
                continue

            need_kwh = kwh_needed(dist, consumption_kwh_per_100km)
            need_percent = (need_kwh / battery_kwh_max) * 100.0
            drive_time_min = (dist / avg_speed_kmh) * 60.0 if avg_speed_kmh > 0 else 0.0

            if need_percent > state.battery_percent:
                continue

            if state.battery_percent - need_percent < safe_threshold_percent:
                station_rows = stations_index.loc[[state.node]] if state.node in stations_index.index else None
                if station_rows is not None and not station_rows.empty:
                    power_kw = station_rows.iloc[0].get("power_kw", None)
                    if power_kw and power_kw > 0:
                        desired_after_arrival = safe_threshold_percent + need_percent
                        target_percent = min(max(desired_after_arrival, state.battery_percent), charge_target_percent)
                        if target_percent > state.battery_percent:
                            delta_percent = target_percent - state.battery_percent
                            delta_kwh = battery_kwh_max * delta_percent / 100.0
                            charge_minutes = charge_time_minutes(power_kw, delta_kwh)
                            charged_state = State(
                                state.node,
                                target_percent,
                                state.path + [f"Charge@{state.node}"],
                                state.total_distance_km,
                                state.total_time_min + charge_minutes + charge_penalty_minutes,
                                state.charges
                                + [
                                    {
                                        "station_id": state.node,
                                        "arrive_soc": round(state.battery_percent, 1),
                                        "leave_soc": round(target_percent, 1),
                                        "charge_minutes": round(charge_minutes, 1),
                                        "penalty_minutes": round(charge_penalty_minutes, 1),
                                    }
                                ],
                            )
                            heappush(pq, (charged_state.total_time_min, next(counter), charged_state))
                continue
            else:
                new_batt = state.battery_percent - need_percent
                if new_batt < 0:
                    continue
                new_state = State(
                    neighbor,
                    new_batt,
                    state.path + [neighbor],
                    state.total_distance_km + dist,
                    state.total_time_min + drive_time_min,
                    state.charges,
                )
                heappush(pq, (new_state.total_time_min, next(counter), new_state))

        # Nearby search: chọn K gần nhất (bằng haversine) trong bán kính
        if enable_nearby_search:
            # thu thập candidate với approx distance
            candidates = []
            try:
                cur_lat = float(stations_index.loc[state.node]["lat"])
                cur_lon = float(stations_index.loc[state.node]["lon"])
            except Exception:
                cur_lat = cur_lon = None

            if cur_lat is not None:
                for cand_id, row in stations_index.iterrows():
                    if cand_id == state.node:
                        continue
                    approx_dist = haversine_km(cur_lat, cur_lon, float(row["lat"]), float(row["lon"]))
                    if approx_dist <= max_search_distance_km:
                        candidates.append((cand_id, approx_dist, row))
                # sort và cắt K nhỏ nhất
                candidates.sort(key=lambda x: x[1])
                candidates = candidates[:max(0, nearby_k)]

                for cand_id, approx_dist, row in candidates:
                    # shortest path on graph to candidate
                    try:
                        sp_dist = nx.shortest_path_length(G, state.node, cand_id, weight=lambda u, v, d: _edge_distance(u, v, d))
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue

                    need_kwh_to_cand = kwh_needed(sp_dist, consumption_kwh_per_100km)
                    need_percent_to_cand = (need_kwh_to_cand / battery_kwh_max) * 100.0
                    if need_percent_to_cand > state.battery_percent:
                        continue

                    power_kw = row.get("power_kw", None)
                    if power_kw is None or power_kw <= 0:
                        continue

                    target_percent = charge_target_percent
                    if target_percent <= state.battery_percent:
                        continue

                    delta_percent = target_percent - state.battery_percent
                    delta_kwh = battery_kwh_max * delta_percent / 100.0
                    charge_minutes = charge_time_minutes(power_kw, delta_kwh)
                    drive_time_min = (sp_dist / avg_speed_kmh) * 60.0 if avg_speed_kmh > 0 else 0.0

                    new_state = State(
                        cand_id,
                        target_percent,
                        state.path + [f"Drive->{cand_id}"] + [f"Charge@{cand_id}"],
                        state.total_distance_km + sp_dist,
                        state.total_time_min + drive_time_min + charge_minutes + charge_penalty_minutes,
                        state.charges
                        + [
                            {
                                "station_id": cand_id,
                                "arrive_soc": round(state.battery_percent - need_percent_to_cand, 1),
                                "leave_soc": round(target_percent, 1),
                                "charge_minutes": round(charge_minutes, 1),
                                "penalty_minutes": round(charge_penalty_minutes, 1),
                            }
                        ],
                    )
                    heappush(pq, (new_state.total_time_min, next(counter), new_state))

    return None, None, None, None


def astar_ev_search(
    G,
    stations_df,
    start: str,
    end: str,
    battery_percent: float,
    battery_kwh_max: float,
    consumption_kwh_per_100km: float,
    safe_threshold_percent: float = 20.0,
    charge_target_percent: float = 80.0,
    avg_speed_kmh: float = 60.0,
    enable_nearby_search: bool = True,
    max_search_distance_km: float = 100.0,
    nearby_k: int = 5,
    charge_penalty_minutes: float = 0.0,
    max_expansions: int = 100000,
) -> Tuple[Optional[List[str]], Optional[float], Optional[float], Optional[List[Dict]]]:
    counter = itertools.count()
    open_pq = []
    start_state = State(start, battery_percent, [start], 0.0, 0.0, [])
    # f = g + h, tại start g=0, h = heuristic(start,end)
    try:
        start_lat = float(G.nodes[start]["lat"])
        start_lon = float(G.nodes[start]["lon"])
        end_lat = float(G.nodes[end]["lat"])
        end_lon = float(G.nodes[end]["lon"])
        h0 = (haversine_km(start_lat, start_lon, end_lat, end_lon) / avg_speed_kmh) * 60.0
    except Exception:
        h0 = 0.0
    heappush(open_pq, (h0, 0.0, next(counter), start_state))  # (f, g, tie, state)
    visited = {}

    stations_index = stations_df.set_index("id")

    expansions = 0

    while open_pq:
        expansions += 1
        if expansions > max_expansions:
            return None, None, None, None

        f, g, _, state = heappop(open_pq)

        if state.node == end:
            return state.path, state.total_distance_km, state.total_time_min, state.charges

        key = (state.node, round(state.battery_percent, 1))
        if key in visited and visited[key] <= g:
            continue
        visited[key] = g

        # expand neighbors (same logic as UCS for transitions)
        for neighbor in G.neighbors(state.node):
            edge = G[state.node][neighbor]
            dist = _edge_distance(state.node, neighbor, edge)
            if dist is None:
                continue

            need_kwh = kwh_needed(dist, consumption_kwh_per_100km)
            need_percent = (need_kwh / battery_kwh_max) * 100.0
            drive_time_min = (dist / avg_speed_kmh) * 60.0 if avg_speed_kmh > 0 else 0.0

            if need_percent > state.battery_percent:
                continue

            if state.battery_percent - need_percent < safe_threshold_percent:
                station_rows = stations_index.loc[[state.node]] if state.node in stations_index.index else None
                if station_rows is not None and not station_rows.empty:
                    power_kw = station_rows.iloc[0].get("power_kw", None)
                    if power_kw and power_kw > 0:
                        desired_after_arrival = safe_threshold_percent + need_percent
                        target_percent = min(max(desired_after_arrival, state.battery_percent), charge_target_percent)
                        if target_percent > state.battery_percent:
                            delta_percent = target_percent - state.battery_percent
                            delta_kwh = battery_kwh_max * delta_percent / 100.0
                            charge_minutes = charge_time_minutes(power_kw, delta_kwh)
                            charged_state = State(
                                state.node,
                                target_percent,
                                state.path + [f"Charge@{state.node}"],
                                state.total_distance_km,
                                state.total_time_min + charge_minutes + charge_penalty_minutes,
                                state.charges
                                + [
                                    {
                                        "station_id": state.node,
                                        "arrive_soc": round(state.battery_percent, 1),
                                        "leave_soc": round(target_percent, 1),
                                        "charge_minutes": round(charge_minutes, 1),
                                        "penalty_minutes": round(charge_penalty_minutes, 1),
                                    }
                                ],
                            )
                            try:
                                lat = float(charged_state.node and G.nodes[charged_state.node]["lat"])
                                lon = float(charged_state.node and G.nodes[charged_state.node]["lon"])
                                h = (haversine_km(lat, lon, float(G.nodes[end]["lat"]), float(G.nodes[end]["lon"])) / avg_speed_kmh) * 60.0
                            except Exception:
                                h = 0.0
                            heappush(open_pq, (charged_state.total_time_min + h, charged_state.total_time_min, next(counter), charged_state))
                continue
            else:
                new_batt = state.battery_percent - need_percent
                if new_batt < 0:
                    continue
                new_state = State(
                    neighbor,
                    new_batt,
                    state.path + [neighbor],
                    state.total_distance_km + dist,
                    state.total_time_min + drive_time_min,
                    state.charges,
                )
                try:
                    lat = float(G.nodes[neighbor]["lat"])
                    lon = float(G.nodes[neighbor]["lon"])
                    h = (haversine_km(lat, lon, float(G.nodes[end]["lat"]), float(G.nodes[end]["lon"])) / avg_speed_kmh) * 60.0
                except Exception:
                    h = 0.0
                heappush(open_pq, (new_state.total_time_min + h, new_state.total_time_min, next(counter), new_state))

        # Nearby search with K limit (reuse same candidate selection as UCS)
        if enable_nearby_search:
            candidates = []
            try:
                cur_lat = float(stations_index.loc[state.node]["lat"])
                cur_lon = float(stations_index.loc[state.node]["lon"])
            except Exception:
                cur_lat = cur_lon = None

            if cur_lat is not None:
                for cand_id, row in stations_index.iterrows():
                    if cand_id == state.node:
                        continue
                    approx_dist = haversine_km(cur_lat, cur_lon, float(row["lat"]), float(row["lon"]))
                    if approx_dist <= max_search_distance_km:
                        candidates.append((cand_id, approx_dist, row))
                candidates.sort(key=lambda x: x[1])
                candidates = candidates[:max(0, nearby_k)]

                for cand_id, approx_dist, row in candidates:
                    try:
                        sp_dist = nx.shortest_path_length(G, state.node, cand_id, weight=lambda u, v, d: _edge_distance(u, v, d))
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue

                    need_kwh_to_cand = kwh_needed(sp_dist, consumption_kwh_per_100km)
                    need_percent_to_cand = (need_kwh_to_cand / battery_kwh_max) * 100.0
                    if need_percent_to_cand > state.battery_percent:
                        continue

                    power_kw = row.get("power_kw", None)
                    if power_kw is None or power_kw <= 0:
                        continue

                    target_percent = charge_target_percent
                    if target_percent <= state.battery_percent:
                        continue

                    delta_percent = target_percent - state.battery_percent
                    delta_kwh = battery_kwh_max * delta_percent / 100.0
                    charge_minutes = charge_time_minutes(power_kw, delta_kwh)
                    drive_time_min = (sp_dist / avg_speed_kmh) * 60.0 if avg_speed_kmh > 0 else 0.0

                    new_state = State(
                        cand_id,
                        target_percent,
                        state.path + [f"Drive->{cand_id}"] + [f"Charge@{cand_id}"],
                        state.total_distance_km + sp_dist,
                        state.total_time_min + drive_time_min + charge_minutes + charge_penalty_minutes,
                        state.charges
                        + [
                            {
                                "station_id": cand_id,
                                "arrive_soc": round(state.battery_percent - need_percent_to_cand, 1),
                                "leave_soc": round(target_percent, 1),
                                "charge_minutes": round(charge_minutes, 1),
                                "penalty_minutes": round(charge_penalty_minutes, 1),
                            }
                        ],
                    )
                    try:
                        lat = float(G.nodes[new_state.node]["lat"])
                        lon = float(G.nodes[new_state.node]["lon"])
                        h = (haversine_km(lat, lon, float(G.nodes[end]["lat"]), float(G.nodes[end]["lon"])) / avg_speed_kmh) * 60.0
                    except Exception:
                        h = 0.0
                    heappush(open_pq, (new_state.total_time_min + h, new_state.total_time_min, next(counter), new_state))

    return None, None, None, None