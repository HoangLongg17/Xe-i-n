from flask import Flask, request, jsonify, render_template_string
import os
import sys
from typing import Optional
try:
    from XeDien_AI_Nhom7 import plan_route
except Exception:
    # allow running server even if planner import fails (useful for editing)
    plan_route = None

# geocode (server-side) using geopy
try:
    from geopy.geocoders import Nominatim
    _GEOPY_AVAILABLE = True
except Exception:
    _GEOPY_AVAILABLE = False

APP_DIR = os.path.dirname(__file__) or "."
app = Flask(__name__)

INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Coord Selector + Planner</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
  <style>
    body, html {{ height: 100%; margin: 0; }}
    #map {{ width: 100%; height: 70vh; }}
    .panel {{ position: fixed; right: 10px; bottom: 10px; background:#fff; padding:10px; border-radius:6px; box-shadow:0 1px 6px rgba(0,0,0,.2); z-index:1000; max-width:360px; }}
    .panel textarea {{ width: 100%; }}
    .row {{ margin-bottom:8px; }}
  </style>
</head>
<body>
  <div id="map"></div>

  <div class="panel">
    <div class="row"><b>Search / Locate</b></div>
    <div class="row">
      <input id="search-input" placeholder="Enter address (e.g. 7/22 Đường C1)" style="width:100%"/>
      <button id="search-btn">Search</button>
      <button id="locate-btn">Locate me</button>
    </div>

    <div class="row">
      <textarea id="coords" rows="3" readonly placeholder="Start\\nEnd"></textarea>
    </div>

    <div class="row">
      <label><input type="checkbox" id="use_osrm" checked> Use OSRM</label>
    </div>

    <div class="row">
      <button id="submitBtn">Submit to planner</button>
    </div>

    <div id="result" style="white-space:pre-wrap; max-height:200px; overflow:auto;"></div>
  </div>

<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<script>
var map = L.map('map').setView([10.776889, 106.700806], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {{ maxZoom: 19 }}).addTo(map);

let start=null, end=null, startMarker=null, endMarker=null;

function setStart(lat, lng) {{
  start = {{lat: lat, lng: lng}};
  if (startMarker) map.removeLayer(startMarker);
  startMarker = L.marker([lat, lng], {{icon: L.icon({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png'}})}}).addTo(map).bindPopup("Start: "+lat.toFixed(6)+","+lng.toFixed(6)).openPopup();
  updateCoordsBox();
}}
function setEnd(lat, lng) {{
  end = {{lat: lat, lng: lng}};
  if (endMarker) map.removeLayer(endMarker);
  endMarker = L.marker([lat, lng], {{icon: L.icon({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png'}})}}).addTo(map).bindPopup("End: "+lat.toFixed(6)+","+lng.toFixed(6)).openPopup();
  updateCoordsBox();
}}
function updateCoordsBox() {{
  let lines = [];
  if (start) lines.push(start.lat.toFixed(6)+','+start.lng.toFixed(6));
  if (end) lines.push(end.lat.toFixed(6)+','+end.lng.toFixed(6));
  document.getElementById('coords').value = lines.join('\\n');
}}

map.on('click', function(e) {{
  if (!start) {{
    setStart(e.latlng.lat, e.latlng.lng);
  }} else if (!end) {{
    setEnd(e.latlng.lat, e.latlng.lng);
  }} else {{
    alert('Start and End already set. Refresh to pick again.');
  }}
}});

// Search via server geocode
document.getElementById('search-btn').addEventListener('click', function() {{
  const q = document.getElementById('search-input').value.trim();
  if (!q) return alert('Enter text to search');
  fetch('/geocode', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ q: q }})
  }}).then(r=>r.json()).then(j=>{{
    if (j.error) return alert('Geocode failed: '+j.error);
    if (!j.lat || !j.lon) return alert('No result');
    // if start empty -> set start, else set end
    if (!start) setStart(j.lat, j.lon);
    else if (!end) setEnd(j.lat, j.lon);
    else {{
      // replace end if both set
      setEnd(j.lat, j.lon);
    }}
    map.setView([j.lat, j.lon], 15);
  }}).catch(err=>alert('Search error: '+err));
}});

// Locate me via browser geolocation
document.getElementById('locate-btn').addEventListener('click', function() {{
  if (!navigator.geolocation) return alert('Geolocation not supported.');
  navigator.geolocation.getCurrentPosition(function(pos) {{
    const lat = pos.coords.latitude, lon = pos.coords.longitude;
    if (!start) setStart(lat, lon);
    else if (!end) setEnd(lat, lon);
    else setEnd(lat, lon);
    map.setView([lat, lon], 15);
  }}, function(err) {{
    alert('Geolocation error: ' + (err.message || err.code));
  }});
}});

// Submit to planner
document.getElementById('submitBtn').addEventListener('click', function() {{
  const txt = document.getElementById('coords').value.trim();
  if (!txt) return alert('Pick start and end first.');
  const lines = txt.split('\\n').map(s=>s.trim());
  const startc = lines[0];
  const endc = lines[1] || lines[0];
  const use_osrm = document.getElementById('use_osrm').checked;
  document.getElementById('result').textContent = "Running planner...\\n";
  fetch('/plan', {{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{start:startc, end:endc, use_osrm: use_osrm}})
  }}).then(r=>r.json()).then(j=>{{
    if (j.error) {{
      document.getElementById('result').textContent = j.error;
      return;
    }}
    document.getElementById('result').innerHTML = '<pre>' + (j.output || '') + '</pre>';
    if (j.map_html) {{
      const w = window.open();
      w.document.write(j.map_html);
      w.document.close();
    }}
  }}).catch(err=>{{ document.getElementById('result').textContent = 'Request failed: '+err; }});
}});
</script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)

@app.route("/geocode", methods=["POST"])
def geocode():
    if not _GEOPY_AVAILABLE:
        return jsonify({"error": "geopy not installed on server"}), 500
    data = request.get_json() or {}
    q = data.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query"}), 400
    try:
        geo = Nominatim(user_agent="xe_ev_planner", timeout=10).geocode(q)
        if not geo:
            return jsonify({"error": "no result"}), 404
        return jsonify({"lat": geo.latitude, "lon": geo.longitude, "display_name": geo.address})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

@app.route("/plan", methods=["POST"])
def plan():
    if plan_route is None:
        return jsonify({"error": "planner not available (import error)"}), 500
    data = request.get_json() or {}
    start = data.get("start")
    end = data.get("end")
    use_osrm = bool(data.get("use_osrm", True))
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    cfg = {{
        "use_osrm": use_osrm,
        "osrm_url": "http://router.project-osrm.org",
        "use_geocode": True,
        "max_search_dist": 100.0,
        "nearby_k": 5,
    }}

    try:
        res = plan_route(start, end, cfg)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

    return jsonify({"output": res.get("output"), "map_html": res.get("map_html")})

if __name__ == "__main__":
    print("Start coord selector server on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)