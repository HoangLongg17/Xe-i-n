from flask import Flask, request, jsonify, render_template_string
import os
import sys
from typing import Optional
try:
    # import planner and geocode helper if available
    from XeDien_AI_Nhom7 import plan_route, geocode_candidates
except Exception:
    # allow running server even if planner import fails (useful for editing)
    try:
        from XeDien_AI_Nhom7 import plan_route
    except Exception:
        plan_route = None
    geocode_candidates = None

# geocode (server-side) using geopy (fallback)
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
  <title>Chọn tọa độ & Lập kế hoạch</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
  <style>
    body, html {{ height: 100%; margin: 0; }}
    #map {{ width: 100%; height: 70vh; }}
    .panel {{ position: fixed; right: 10px; bottom: 10px; background:#fff; padding:10px; border-radius:6px; box-shadow:0 1px 6px rgba(0,0,0,.2); z-index:1000; max-width:380px; }}
    .panel textarea {{ width: 100%; }}
    .row {{ margin-bottom:8px; }}
    .search-results {{ max-height:150px; overflow:auto; border:1px solid #ddd; padding:6px; margin-top:6px; }}
    .search-result-item {{ padding:4px 6px; cursor:pointer; border-bottom:1px solid #f0f0f0; }}
    .search-result-item:hover {{ background:#f7f7f7; }}
    #result {{ white-space:pre-wrap; max-height:200px; overflow:auto; margin-top:8px; }}
    .small-note {{ font-size:0.9em; color:#444; margin-bottom:6px; }}
  </style>
</head>
<body>
  <div id="map"></div>

  <div class="panel">
    <div class="row"><b>Tìm kiếm / Định vị</b></div>
    <div class="row">
      <input id="search-input" placeholder="Nhập địa chỉ (ví dụ: 7/22 Đường C1)" style="width:100%"/>
      <div style="margin-top:6px;">
        <button id="search-btn">Tìm</button>
        <button id="locate-btn">Xác định vị trí của tôi</button>
      </div>
    </div>

    <div id="search-results-container" class="row" style="display:none;">
      <div class="small-note">Nhấn vào kết quả để đặt Điểm Bắt đầu / Đích</div>
      <div id="search-results" class="search-results"></div>
    </div>

    <div class="row">
      <textarea id="coords" rows="3" readonly placeholder="Start\\nEnd"></textarea>
    </div>

    <div class="row">
      <label><input type="checkbox" id="use_osrm" checked> Dùng OSRM (đường thực tế)</label>
    </div>

    <div class="row">
      <button id="submitBtn">Gửi tới bộ lập kế hoạch</button>
    </div>

    <div id="result"></div>
  </div>

<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<script>
var map = L.map('map').setView([10.776889, 106.700806], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {{ maxZoom: 19 }}).addTo(map);

let start=null, end=null, startMarker=null, endMarker=null;

function setStart(lat, lng, label) {{
  start = {{lat: lat, lng: lng}};
  if (startMarker) map.removeLayer(startMarker);
  startMarker = L.marker([lat, lng], {{icon: L.icon({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png'}})}}).addTo(map).bindPopup((label || "Start") + ": "+lat.toFixed(6)+","+lng.toFixed(6)).openPopup();
  updateCoordsBox();
  showMessage("Đã đặt điểm bắt đầu.");
}}
function setEnd(lat, lng, label) {{
  end = {{lat: lat, lng: lng}};
  if (endMarker) map.removeLayer(endMarker);
  endMarker = L.marker([lat, lng], {{icon: L.icon({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png'}})}}).addTo(map).bindPopup((label || "End") + ": "+lat.toFixed(6)+","+lng.toFixed(6)).openPopup();
  updateCoordsBox();
  showMessage("Đã đặt điểm đích.");
}}
function updateCoordsBox() {{
  let lines = [];
  if (start) lines.push(start.lat.toFixed(6)+','+start.lng.toFixed(6));
  if (end) lines.push(end.lat.toFixed(6)+','+end.lng.toFixed(6));
  document.getElementById('coords').value = lines.join('\\n');
}}

// display messages to user in result panel
function showMessage(msg) {{
  const r = document.getElementById('result');
  r.textContent = msg;
}}

map.on('click', function(e) {{
  if (!start) {{
    setStart(e.latlng.lat, e.latlng.lng);
  }} else if (!end) {{
    setEnd(e.latlng.lat, e.latlng.lng);
  }} else {{
    showMessage('Đã có Start và End. Tải lại trang để chọn lại.');
  }}
}});

// helper to render candidate list
function renderCandidates(cands) {{
  const container = document.getElementById('search-results');
  container.innerHTML = '';
  if (!cands || !cands.length) {{
    document.getElementById('search-results-container').style.display = 'none';
    return;
  }}
  document.getElementById('search-results-container').style.display = 'block';
  cands.forEach((c, idx) => {{
    const div = document.createElement('div');
    div.className = 'search-result-item';
    div.innerText = (idx+1) + ': ' + (c.display_name || (c.lat+','+c.lon));
    div.addEventListener('click', () => {{
      // on click, ask whether set as start or end (Vietnamese)
      const which = prompt('Đặt làm (s)tart hay (e)nd? (mặc định s)', 's');
      if (which === null) return;
      const key = which.trim().toLowerCase();
      if (key === 'e') {{
        setEnd(c.lat, c.lon, c.display_name);
      }} else {{
        setStart(c.lat, c.lon, c.display_name);
      }}
      map.setView([c.lat, c.lon], 15);
      // hide results after selection
      document.getElementById('search-results-container').style.display = 'none';
    }});
    container.appendChild(div);
  }});
}}

// Search via server geocode
document.getElementById('search-btn').addEventListener('click', function() {{
  const q = document.getElementById('search-input').value.trim();
  if (!q) {{
    showMessage('Nhập địa chỉ để tìm.');
    return;
  }}
  showMessage('Đang tìm...');

  fetch('/geocode', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ q: q }})
  }}).then(r=>r.json()).then(j=>{
    if (j.error) {{
      showMessage('Tìm thất bại: ' + j.error);
      console.error('geocode error', j.error);
      return;
    }}
    // server returns candidates array when multiple results available
    if (Array.isArray(j.candidates) && j.candidates.length > 0) {{
      renderCandidates(j.candidates);
      // if only one candidate, auto-select (set start/end)
      if (j.candidates.length === 1) {{
        const c = j.candidates[0];
        if (!start) setStart(c.lat, c.lon, c.display_name);
        else if (!end) setEnd(c.lat, c.lon, c.display_name);
        else setEnd(c.lat, c.lon, c.display_name);
        map.setView([c.lat, c.lon], 15);
        document.getElementById('search-results-container').style.display = 'none';
      }} else {{
        showMessage('Chọn một kết quả để đặt Start/End.');
      }}
      return;
    }}
    // fallback - older single-result response
    if (!j.lat || !j.lon) {{
      showMessage('Không tìm thấy kết quả.');
      return;
    }}
    if (!start) setStart(j.lat, j.lon, j.display_name);
    else if (!end) setEnd(j.lat, j.lon, j.display_name);
    else setEnd(j.lat, j.lon, j.display_name);
    map.setView([j.lat, j.lon], 15);
    showMessage('Đã đặt vị trí từ kết quả tìm được.');
  }).catch(err=>{
    console.error('Search error', err);
    showMessage('Lỗi khi gọi server: ' + (err.message || err));
  });
}});

// Enter key triggers search
document.getElementById('search-input').addEventListener('keyup', function(e) {{
  if (e.key === 'Enter') document.getElementById('search-btn').click();
}});

// Locate me via browser geolocation
document.getElementById('locate-btn').addEventListener('click', function() {{
  if (!navigator.geolocation) {{
    showMessage('Trình duyệt không hỗ trợ định vị.');
    return;
  }}
  navigator.geolocation.getCurrentPosition(function(pos) {{
    const lat = pos.coords.latitude, lon = pos.coords.longitude;
    if (!start) setStart(lat, lon);
    else if (!end) setEnd(lat, lon);
    else setEnd(lat, lon);
    map.setView([lat, lon], 15);
  }}, function(err) {{
    showMessage('Lỗi định vị: ' + (err.message || err.code));
  }});
}});

// Submit to planner
document.getElementById('submitBtn').addEventListener('click', function() {{
  const txt = document.getElementById('coords').value.trim();
  if (!txt) {{
    showMessage('Chọn Start và End trước khi gửi.');
    return;
  }}
  const lines = txt.split('\\n').map(s=>s.trim());
  const startc = lines[0];
  const endc = lines[1] || lines[0];
  const use_osrm = document.getElementById('use_osrm').checked;
  showMessage('Đang chạy bộ lập kế hoạch...');

  fetch('/plan', {{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{start:startc, end:endc, use_osrm: use_osrm}})
  }}).then(r=>r.json()).then(j=>{
    if (j.error) {{
      showMessage('Lỗi planner: ' + j.error);
      return;
    }}
    showMessage(j.output || 'Hoàn tất. Bản đồ sẽ mở trong cửa sổ mới.');
    if (j.map_html) {{
      const w = window.open();
      w.document.write(j.map_html);
      w.document.close();
    }}
  }).catch(err=>{
    console.error('Planner request failed', err);
    showMessage('Gửi tới planner thất bại: ' + (err.message || err));
  });
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
    data = request.get_json() or {}
    q = data.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query"}), 400

    # Prefer the project's robust geocode helper if available
    try:
        if geocode_candidates:
            cands = geocode_candidates(q, timeout=10, limit=6)
            if not cands:
                return jsonify({"error": "no result"}), 404
            candidates = [{"lat": lat, "lon": lon, "display_name": name} for (lat, lon, name) in cands]
            return jsonify({"candidates": candidates})
    except Exception:
        # fallback to geopy below
        pass

    # fallback to geopy directly
    if not _GEOPY_AVAILABLE:
        return jsonify({"error": "geopy not installed on server"}), 500
    try:
        geol = Nominatim(user_agent="xe_ev_planner", timeout=10)
        locs = geol.geocode(q, exactly_one=False, limit=6, language="vi")
        if not locs:
            return jsonify({"error": "no result"}), 404
        if isinstance(locs, list):
            candidates = [{"lat": float(loc.latitude), "lon": float(loc.longitude), "display_name": getattr(loc, "address", str(loc))} for loc in locs]
            return jsonify({"candidates": candidates})
        else:
            return jsonify({"lat": float(locs.latitude), "lon": float(locs.longitude), "display_name": getattr(locs, "address", str(locs))})
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

    cfg = {
        "use_osrm": use_osrm,
        "osrm_url": "http://router.project-osrm.org",
        "use_geocode": True,
        "max_search_dist": 100.0,
        "nearby_k": 5,
    }

    try:
        res = plan_route(start, end, cfg)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

    return jsonify({"output": res.get("output"), "map_html": res.get("map_html")})

if __name__ == "__main__":
    print("Start coord selector server on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)