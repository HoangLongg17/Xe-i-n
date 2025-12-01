from typing import Optional, Tuple
import pandas as pd
import folium

from data_load import load_ev_stations_kml


def create_coord_selector_map(
    stations_kml: str = "../data/evcs_map.kml",
    output_file: str = "select_coords.html",
    zoom_start: int = 12,
    tiles: str = "OpenStreetMap",
) -> None:
    """
    Create a simple HTML map where user clicks once for Start and once for End.
    Result: `output_file` with a small UI to copy coordinates.

    Usage:
      python coord_selector.py
    Open the generated select_coords.html in a browser, click Start then End,
    press "Copy coords" and paste into CLI:
      python XeDien_AI_Nhom7.py --start "LAT1,LON1" --end "LAT2,LON2"
    """
    stations = load_ev_stations_kml(stations_kml)
    # fallback center
    try:
        r = stations.iloc[0]
        center = (float(r["lat"]), float(r["lon"]))
    except Exception:
        center = (0.0, 0.0)

    m = folium.Map(location=[center[0], center[1]], zoom_start=zoom_start, tiles=tiles)

    # optional: show stations as faint markers to orient user
    try:
        stations_index = stations.set_index("id")
        fg = folium.FeatureGroup(name="Stations (reference)", show=False)
        for node_id, row in stations_index.iterrows():
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except Exception:
                continue
            folium.CircleMarker(location=[lat, lon], radius=3, color="blue", fill=True, fill_opacity=0.5).add_to(fg)
        fg.add_to(m)
    except Exception:
        pass

    # instruction box
    instruction_html = """
    <div style="position: fixed; top: 10px; left: 10px; z-index:1000;
                background: rgba(255,255,255,0.95); padding:10px; border-radius:6px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-size:13px;">
      <b>Pick Start and End</b><br/>
      1) Click once on the map → sets <b>Start</b>.<br/>
      2) Click again → sets <b>End</b>.<br/>
      3) Click "Copy coords" and paste into CLI:<br/>
      <code>--start "lat,lon" --end "lat,lon"</code>
    </div>
    """
    m.get_root().html.add_child(folium.Element(instruction_html))

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
        startMarker = L.marker(e.latlng).addTo({map_name}).bindPopup("Start: " + start.lat.toFixed(6) + "," + start.lng.toFixed(6)).openPopup();
        document.getElementById('coord-box').value = start.lat.toFixed(6) + "," + start.lng.toFixed(6);
      }} else if (!end) {{
        end = e.latlng;
        endMarker = L.marker(e.latlng).addTo({map_name}).bindPopup("End: " + end.lat.toFixed(6) + "," + end.lng.toFixed(6)).openPopup();
        document.getElementById('coord-box').value = start.lat.toFixed(6) + "," + start.lng.toFixed(6) + "\\n" + end.lat.toFixed(6) + "," + end.lng.toFixed(6);
      }} else {{
        alert('Start and End already set. Refresh page to pick again.');
      }}
    }}

    {map_name}.on('click', _onMapClick);

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
      if (!val) {{
        alert('No coordinates to copy. Click on the map first.');
        return;
      }}
      navigator.clipboard.writeText(val).then(function() {{
        alert('Coordinates copied to clipboard. Paste into CLI as --start and --end.');
      }}, function(err) {{
        alert('Copy failed, select and copy manually: ' + val);
      }});
    }});
    </script>
    """
    m.get_root().html.add_child(folium.Element(js))

    m.save(output_file)
    print(f"Coordinate selector saved: {output_file}")


if __name__ == "__main__":
    create_coord_selector_map()
