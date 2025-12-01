import math
from typing import Dict, List, Optional, Tuple

import networkx as nx


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính khoảng cách giữa 2 tọa độ theo km (haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def build_graph(
    stations_df,
    max_edge_km: float = 200.0,
    k_neighbors: Optional[int] = None,
    avg_speed_kmh: float = 60.0,
) -> nx.Graph:
    """
    Xây dựng đồ thị trạm sạc.

    - stations_df: DataFrame có cột ["id", "name", "lat", "lon"].
    - Thực thi để luôn thêm thuộc tính cạnh "distance" (km) để chuẩn hóa.
    """
    G = nx.Graph()

    # Add nodes
    for _, row in stations_df.iterrows():
        G.add_node(
            row["id"],
            name=row.get("name"),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
        )

    ids: List = list(stations_df["id"])
    coords: List[Tuple[float, float]] = [
        (float(r["lat"]), float(r["lon"])) for _, r in stations_df.iterrows()
    ]

    n = len(ids)
    if k_neighbors is not None:
        # For each node, compute distances to all others and add k nearest edges
        for i in range(n):
            dists: List[Tuple[int, float]] = []
            lat1, lon1 = coords[i]
            for j in range(n):
                if i == j:
                    continue
                lat2, lon2 = coords[j]
                dist = haversine_km(lat1, lon1, lat2, lon2)
                dists.append((j, dist))
            dists.sort(key=lambda x: x[1])
            for j, dist in dists[:k_neighbors]:
                if G.has_edge(ids[i], ids[j]) or G.has_edge(ids[j], ids[i]):
                    continue
                # Standardize attribute name "distance" (km)
                G.add_edge(
                    ids[i],
                    ids[j],
                    distance=dist,
                    distance_km=dist,
                    travel_time_h=dist / avg_speed_kmh if avg_speed_kmh > 0 else None,
                    is_highway=False,
                    toll=False,
                )
    else:
        # Add edges only when distance <= max_edge_km (avoid complete graph)
        for i in range(n):
            lat1, lon1 = coords[i]
            for j in range(i + 1, n):
                lat2, lon2 = coords[j]
                dist = haversine_km(lat1, lon1, lat2, lon2)
                if dist <= max_edge_km:
                    G.add_edge(
                        ids[i],
                        ids[j],
                        distance=dist,
                        distance_km=dist,
                        travel_time_h=dist / avg_speed_kmh if avg_speed_kmh > 0 else None,
                        is_highway=False,
                        toll=False,
                    )
    return G


def add_virtual_node(
    G: nx.Graph,
    node_id: str,
    lat: float,
    lon: float,
    k_neighbors: int = 5,
    max_dist_km: float = 200.0,
    avg_speed_kmh: float = 60.0,
) -> None:
    """
    Thêm một nút ảo (virtual node) vào đồ thị G tại tọa độ (lat, lon).
    Kết nối nút ảo đến K nút gần nhất trong G (trong bán kính max_dist_km).
    - node_id: chuỗi id duy nhất cho nút ảo (ví dụ "START" hoặc "END").
    - Nếu node_id đã tồn tại -> sẽ raise ValueError.
    """
    if node_id in G.nodes:
        raise ValueError(f"Node id '{node_id}' already exists in graph")

    G.add_node(node_id, name=node_id, lat=float(lat), lon=float(lon))

    # Tính khoảng cách tới tất cả node khác
    dists: List[Tuple[str, float]] = []
    for n, data in G.nodes(data=True):
        if n == node_id:
            continue
        nlat = float(data.get("lat"))
        nlon = float(data.get("lon"))
        d = haversine_km(lat, lon, nlat, nlon)
        if d <= max_dist_km:
            dists.append((n, d))

    # Lấy k nhỏ nhất
    dists.sort(key=lambda x: x[1])
    for n, dist in dists[:k_neighbors]:
        G.add_edge(
            node_id,
            n,
            distance=dist,
            distance_km=dist,
            travel_time_h=dist / avg_speed_kmh if avg_speed_kmh > 0 else None,
            is_highway=False,
            toll=False,
        )


def nearest_station(
    G: nx.Graph, lat: float, lon: float, radius_km: float = 50.0
) -> Optional[Tuple[str, float]]:
    """
    Tìm trạm gần nhất trong radius_km.
    Trả về tuple (node_id, distance_km) hoặc None nếu không có trạm nào trong bán kính.
    """
    best_id = None
    best_dist = float("inf")
    for node, data in G.nodes(data=True):
        nlat = float(data.get("lat"))
        nlon = float(data.get("lon"))
        d = haversine_km(lat, lon, nlat, nlon)
        if d < best_dist:
            best_dist = d
            best_id = node
    if best_id is not None and best_dist <= radius_km:
        return best_id, best_dist
    return None


def add_map_routes(G: nx.Graph, routes: List[Dict]) -> None:
    """
    Thêm các cung đường (route segments) từ nguồn bản đồ vào đồ thị.
    Mỗi route element nên có: {"from": id_from, "to": id_to", "distance_km": float, "is_highway": bool, "toll": bool}
    Nếu node không tồn tại trong G, route sẽ bị bỏ qua.
    """
    for r in routes:
        a = r.get("from")
        b = r.get("to")
        if a not in G.nodes or b not in G.nodes:
            continue
        dist = float(r.get("distance_km", 0.0))
        # use standardized "distance" attribute
        G.add_edge(
            a,
            b,
            distance=dist,
            distance_km=dist,
            travel_time_h=(dist / r.get("avg_speed_kmh", 60.0))
            if r.get("avg_speed_kmh", None)
            else None,
            is_highway=bool(r.get("is_highway", False)),
            toll=bool(r.get("toll", False)),
        )