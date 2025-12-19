import typing
from typing import List, Tuple, Optional

import folium


def plot_path(
    stations_df,
    path_ids: typing.List[str],
    charging_stops: typing.Optional[typing.List[typing.Dict]] = None,
    detour_polylines: Optional[List[List[Tuple[float, float]]]] = None,
    base_polyline: Optional[List[Tuple[float, float]]] = None,
    zoom_start: int = 6,
    tiles: str = "OpenStreetMap",
    output_file: typing.Optional[str] = None,
) -> folium.Map:
    """
    Vẽ lộ trình trên bản đồ cùng các trạm sạc (nếu có).

    - base_polyline: optional road-aligned polyline (list of (lat,lon)) to draw as primary route.
    - detour_polylines: optional list of polylines (each a list of (lat,lon)) to draw detours.
    """
    if not path_ids:
        raise ValueError("path_ids không được rỗng")

    # Build quick index for lookups
    stations_index = stations_df.set_index("id")

    # Determine map center
    try:
        if base_polyline and len(base_polyline) > 0:
            start_row_coords = base_polyline[0]
            start_row = {"lat": start_row_coords[0], "lon": start_row_coords[1]}
        else:
            start_row = stations_index.loc[path_ids[0]]
    except Exception:
        # fallback to first row of dataframe
        start_row = stations_df.iloc[0]
    m = folium.Map(location=[float(start_row["lat"]), float(start_row["lon"])], zoom_start=zoom_start, tiles=tiles)

    # Feature groups for legend/layers
    fg_all = folium.FeatureGroup(name="Tất cả trạm", show=False)
    fg_path = folium.FeatureGroup(name="Lộ trình", show=True)
    fg_stops = folium.FeatureGroup(name="Trạm sạc đề xuất", show=True)
    fg_detours = folium.FeatureGroup(name="Detours (to chargers)", show=True)

    # Add all stations as small blue circles
    for node_id, row in stations_index.iterrows():
        lat = float(row["lat"])
        lon = float(row["lon"])
        popup_lines = [f"<b>{row.get('name', '')}</b>", f"ID: {node_id}"]
        if "power_kw" in row and row.get("power_kw") is not None:
            popup_lines.append(f"Power: {row.get('power_kw')}")
        popup = folium.Popup("<br/>".join(popup_lines), max_width=300)
        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            color="blue",
            fill=True,
            fill_opacity=0.8,
            popup=popup,
        ).add_to(fg_all)

    # Build list of coordinates for node-based polyline (skip missing ids gracefully)
    coords = []
    for pid in path_ids:
        try:
            r = stations_index.loc[pid]
            coords.append([float(r["lat"]), float(r["lon"])])
        except Exception:
            # ignore missing node ids
            continue

    # Draw primary route: prefer base_polyline (road geometry) when available
    if base_polyline:
        # base_polyline is list of (lat, lon)
        folium.PolyLine(base_polyline, color="red", weight=5, opacity=0.9).add_to(fg_path)
        # mark start and end using base_polyline endpoints
        folium.Marker(
            location=[base_polyline[0][0], base_polyline[0][1]],
            popup="Start",
            icon=folium.Icon(color="green", icon="play", prefix="fa"),
        ).add_to(fg_path)
        folium.Marker(
            location=[base_polyline[-1][0], base_polyline[-1][1]],
            popup="Destination",
            icon=folium.Icon(color="darkred", icon="flag", prefix="fa"),
        ).add_to(fg_path)
        # also show node-sequence as a thinner dashed overlay for reference
        if coords:
            folium.PolyLine(coords, color="orange", weight=3, opacity=0.8, dash_array="6,6").add_to(fg_path)
    else:
        if coords:
            folium.PolyLine(coords, color="red", weight=4, opacity=0.9).add_to(fg_path)
            # mark start and end
            folium.Marker(
                location=coords[0],
                popup="Start",
                icon=folium.Icon(color="green", icon="play", prefix="fa"),
            ).add_to(fg_path)
            folium.Marker(
                location=coords[-1],
                popup="Destination",
                icon=folium.Icon(color="darkred", icon="flag", prefix="fa"),
            ).add_to(fg_path)

    # Add charging stops if provided
    if charging_stops:
        for stop in charging_stops:
            sid = stop.get("station_id")
            try:
                srow = stations_index.loc[sid]
            except Exception:
                continue
            lat = float(srow["lat"])
            lon = float(srow["lon"])
            arrive = stop.get("arrive_soc")
            leave = stop.get("leave_soc")
            mins = stop.get("charge_minutes")
            popup_lines = [
                f"<b>{srow.get('name', '')}</b>",
                f"ID: {sid}",
                f"Arrive SOC: {arrive}%",
                f"Leave SOC: {leave}%",
                f"Charge time: {mins} min" if mins is not None else "",
            ]
            popup = folium.Popup("<br/>".join([p for p in popup_lines if p]), max_width=300)
            folium.Marker(
                location=[lat, lon],
                popup=popup,
                icon=folium.Icon(color="green", icon="bolt", prefix="fa"),
            ).add_to(fg_stops)

    # Draw detour polylines (if any)
    if detour_polylines:
        for geom in detour_polylines:
            if not geom:
                continue
            folium.PolyLine(geom, color="blue", weight=3, opacity=0.9, dash_array="8, 6").add_to(fg_detours)

    # Attach feature groups and layer control
    fg_all.add_to(m)
    fg_path.add_to(m)
    fg_stops.add_to(m)
    fg_detours.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # Optionally save to file
    if output_file:
        m.save(output_file)

    return m


def create_coord_selector_map(
    stations_df,
    output_file: str = "select_coords.html",
    center: Optional[tuple] = None,
    zoom_start: int = 6,
    tiles: str = "OpenStreetMap",
) -> None:
    """
    Create an interactive HTML map that lets the user pick Start and End by clicking.
    - Open the generated HTML in a browser.
    - Click once to set Start, click a second time to set End.
    - The two coordinates (lat,lon) will be shown in the text box and can be copied.
    - Then run the CLI with --start "lat,lon" --end "lat,lon".
    This avoids adding a web server dependency; it's a simple manual workflow.
    """
    # determine center
    try:
        if center is None:
            r = stations_df.iloc[0]
            center = (float(r["lat"]), float(r["lon"]))
    except Exception:
        center = (0.0, 0.0)

    m = folium.Map(location=[center[0], center[1]], zoom_start=zoom_start, tiles=tiles)

    # show stations lightly to help the user orient
    stations_index = stations_df.set_index("id")
    fg = folium.FeatureGroup(name="Stations (reference)", show=False)
    for node_id, row in stations_index.iterrows():
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except Exception:
            continue
        folium.CircleMarker(location=[lat, lon], radius=3, color="blue", fill=True, fill_opacity=0.6).add_to(fg)
    fg.add_to(m)

    # Instruction box (HTML)
    instruction_html = """
    <div style="position: fixed; top: 10px; left: 10px; z-index:1000;
                background: rgba(255,255,255,0.9); padding:10px; border-radius:6px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-size:13px;">
      <b>Select Start and End</b><br/>
      1) Click once on the map to set <b>Start</b>.<br/>
      2) Click a second time on the map to set <b>End</b>.<br/>
      3) Coordinates will appear in the box below — click Copy and paste into CLI:<br/>
      <code>--start "lat,lon" --end "lat,lon"</code>
    </div>
    """
    m.get_root().html.add_child(folium.Element(instruction_html))

    # JavaScript for click handling + UI
    map_name = m.get_name()
    js = f"""
    <script>
    var start = null;
    var end = null;
    var startMarker = null;
    var endMarker = null;

    function _onMapClick(e) {{
      if (!start) {{
        start = e.latlng;
        startMarker = L.marker(e.latlng, {{riseOnHover: true}}).addTo({map_name}).bindPopup("Start: " + start.lat.toFixed(6) + "," + start.lng.toFixed(6)).openPopup();
        document.getElementById('coord-box').value = start.lat.toFixed(6) + "," + start.lng.toFixed(6);
      }} else if (!end) {{
        end = e.latlng;
        endMarker = L.marker(e.latlng, {{icon: L.icon({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png'}})}}).addTo({map_name}).bindPopup("End: " + end.lat.toFixed(6) + "," + end.lng.toFixed(6)).openPopup();
        document.getElementById('coord-box').value = start.lat.toFixed(6) + "," + start.lng.toFixed(6) + "\\n" + end.lat.toFixed(6) + "," + end.lng.toFixed(6);
      }} else {{
        alert('Start and End already set. Refresh the page to pick again.');
      }}
    }}

    {map_name}.on('click', _onMapClick);

    // control box for showing coords and copy button
    var controlDiv = L.DomUtil.create('div', 'coord-control');
    controlDiv.style.position = 'fixed';
    controlDiv.style.bottom = '10px';
    controlDiv.style.left = '10px';
    controlDiv.style.zIndex = '1000';
    controlDiv.style.background = 'white';
    controlDiv.style.padding = '8px';
    controlDiv.style.borderRadius = '6px';
    controlDiv.style.boxShadow = '0 1px 4px rgba(0,0,0,0.3)';
    controlDiv.innerHTML = '<textarea id="coord-box" rows="3" cols="34" readonly placeholder="Click start then end on the map"></textarea><br/><button id="copy-btn">Copy coords</button>';
    document.body.appendChild(controlDiv);

    document.getElementById('copy-btn').addEventListener('click', function() {{
      var val = document.getElementById('coord-box').value;
      navigator.clipboard.writeText(val).then(function() {{
        alert('Coordinates copied to clipboard. Paste into CLI as --start and --end.');
      }}, function(err) {{
        alert('Copy failed, select and copy manually.');
      }});
    }});
    </script>
    """
    m.get_root().html.add_child(folium.Element(js))

    # Save file
    m.save(output_file)