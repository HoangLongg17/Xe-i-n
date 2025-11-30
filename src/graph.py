import math
import networkx as nx

def haversine_km(lat1, lon1, lat2, lon2):
    """Tính khoảng cách giữa 2 tọa độ theo km"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def build_graph(stations_df):
    """Xây dựng đồ thị từ dữ liệu trạm sạc"""
    G = nx.Graph()
    for _, row in stations_df.iterrows():
        G.add_node(row["id"], name=row["name"], lat=row["lat"], lon=row["lon"])
    ids = list(stations_df["id"])
    for i in range(len(ids)):
        for j in range(i+1, len(ids)):
            a = stations_df.iloc[i]
            b = stations_df.iloc[j]
            dist = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            G.add_edge(a["id"], b["id"], distance=dist)
    return G
