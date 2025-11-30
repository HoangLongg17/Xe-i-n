import folium

def plot_path(stations_df, path_ids, zoom_start=6):
    """Vẽ lộ trình trên bản đồ"""
    start = stations_df.loc[stations_df["id"] == path_ids[0]].iloc[0]
    m = folium.Map(location=[start["lat"], start["lon"]], zoom_start=zoom_start)

    for _, row in stations_df.iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=5,
            popup=f'{row["name"]} ({row["id"]})',
            color="blue",
            fill=True
        ).add_to(m)

    coords = []
    for pid in path_ids:
        r = stations_df.loc[stations_df["id"] == pid].iloc[0]
        coords.append([r["lat"], r["lon"]])
    folium.PolyLine(coords, color="red", weight=5).add_to(m)
    return m
