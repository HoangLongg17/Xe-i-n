from heapq import heappush, heappop
from graph import haversine_km
from energy_model import kwh_needed, charge_time_minutes

class State:
    def __init__(self, node, battery_percent, path, total_distance, total_time, charges):
        self.node = node
        self.battery_percent = battery_percent
        self.path = path
        self.total_distance = total_distance
        self.total_time = total_time
        self.charges = charges

def ucs_ev_search(G, stations_df, start, end,
                  battery_percent, battery_kwh_max,
                  consumption_kwh_per_100km,
                  safe_threshold_percent=20):
    """UCS tìm đường cho xe điện với pin và trạm sạc"""
    pq = []
    start_state = State(start, battery_percent, [start], 0, 0, [])
    heappush(pq, (0, start_state))
    visited = {}

    while pq:
        cost, state = heappop(pq)

        if state.node == end:
            return state.path, state.total_distance, state.total_time, state.charges

        key = (state.node, round(state.battery_percent, 1))
        if key in visited and visited[key] <= cost:
            continue
        visited[key] = cost

        for neighbor in G.neighbors(state.node):
            dist = G[state.node][neighbor]["distance"]
            need_kwh = kwh_needed(dist, consumption_kwh_per_100km)
            need_percent = (need_kwh / battery_kwh_max) * 100

            if state.battery_percent - need_percent < safe_threshold_percent:
                station = stations_df.loc[stations_df["id"] == state.node]
                if station.empty:
                    continue
                power_kw = station.iloc[0]["power_kw"]

                delta_percent = 80 - state.battery_percent
                delta_kwh = battery_kwh_max * delta_percent / 100.0
                charge_minutes = charge_time_minutes(power_kw, delta_kwh)

                new_state = State(
                    state.node,
                    80,
                    state.path + [f"Sạc tại {state.node}"],
                    state.total_distance,
                    state.total_time + charge_minutes,
                    state.charges + [(state.node, state.battery_percent, 80)]
                )
                heappush(pq, (new_state.total_time, new_state))
            else:
                new_state = State(
                    neighbor,
                    state.battery_percent - need_percent,
                    state.path + [neighbor],
                    state.total_distance + dist,
                    state.total_time + dist/60*60,  # giả sử tốc độ TB 60km/h
                    state.charges
                )
                heappush(pq, (new_state.total_time, new_state))

    return None, None, None, None
