# -*- coding: utf-8 -*-
"""
coord_ui.py - native Tk UI (tkinter + tkintermapview) for selecting start/end on a map,
entering planner options, and running the planner from XeDien_AI_Nhom7.
Added: text geocode for Start/End entries (search button / Enter key),
and Vietnamese translation of UI strings.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import Optional, Tuple, List

import pandas as pd

# project imports (no plan_route here to avoid circular import)
from data_load import load_ev_stations_kml
from graph import build_graph, nearest_station

# optional geocode helper (prefer project helper if available)
try:
    from XeDien_AI_Nhom7 import geocode_candidates  # type: ignore
except Exception:
    geocode_candidates = None

# optional geopy fallback
try:
    from geopy.geocoders import Nominatim  # type: ignore
    _GEOPY_AVAILABLE = True
except Exception:
    _GEOPY_AVAILABLE = False

# external widget (install via pip: tkintermapview)
try:
    from tkintermapview import TkinterMapView
except Exception as ex:
    raise RuntimeError(
        "tkintermapview required. Install with: pip install tkintermapview"
    ) from ex


class CoordPlannerUI:
    def __init__(self, stations_kml: str = "../data/evcs_map.kml"):
        self.root = tk.Tk()
        self.root.title("Bộ lập kế hoạch EV - Giao diện bản đồ")
        self.root.geometry("1100x700")

        # load stations
        self.stations = load_ev_stations_kml(stations_kml)
        try:
            first = self.stations.iloc[0]
            center = (float(first["lat"]), float(first["lon"]))
        except Exception:
            center = (10.762622, 106.660172)  # HCMC fallback

        # left: map
        self.map_widget = TkinterMapView(self.root, width=760, height=680, corner_radius=0)
        self.map_widget.set_position(center[0], center[1])
        self.map_widget.set_zoom(9)
        self.map_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)

        # right: control panel
        right = ttk.Frame(self.root, padding=(8, 8))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # click instructions (Vietnamese)
        ttk.Label(right, text="Nhấp bản đồ 1 lần -> Điểm bắt đầu. Nhấp lần 2 -> Điểm đích.").pack(anchor=tk.W, pady=(0,6))

        # selected coords display
        self.start_coord: Optional[Tuple[float, float]] = None
        self.end_coord: Optional[Tuple[float, float]] = None
        self.start_marker = None
        self.end_marker = None

        # station markers storage (so we can remove them later)
        self.station_markers = []
        # Do not show stations by default (user asked to hide them)
        self.show_stations_var = tk.BooleanVar(value=False)

        coords_frame = ttk.Frame(right)
        coords_frame.pack(fill=tk.X, pady=(0,8))
        ttk.Label(coords_frame, text="Điểm bắt đầu (lat,lon):").grid(row=0, column=0, sticky=tk.W)
        self.start_var = tk.StringVar()
        start_entry = ttk.Entry(coords_frame, textvariable=self.start_var, width=30)
        start_entry.grid(row=0, column=1, sticky=tk.W)
        # search button for start
        ttk.Button(coords_frame, text="Tìm", command=lambda: self._geocode_and_set("start")).grid(row=0, column=2, padx=(6,0))

        ttk.Label(coords_frame, text="Điểm đích (lat,lon):").grid(row=1, column=0, sticky=tk.W)
        self.end_var = tk.StringVar()
        end_entry = ttk.Entry(coords_frame, textvariable=self.end_var, width=30)
        end_entry.grid(row=1, column=1, sticky=tk.W)
        ttk.Button(coords_frame, text="Tìm", command=lambda: self._geocode_and_set("end")).grid(row=1, column=2, padx=(6,0))

        # bind Enter in entries to trigger geocode for that field
        start_entry.bind("<Return>", lambda e: self._geocode_and_set("start"))
        end_entry.bind("<Return>", lambda e: self._geocode_and_set("end"))

        # vehicle and options
        opts = ttk.LabelFrame(right, text="Thông số xe & Tùy chọn", padding=(6,6))
        opts.pack(fill=tk.X, pady=(4,8))

        self.consumption_var = tk.DoubleVar(value=16.3)
        self.battery_kwh_var = tk.DoubleVar(value=60.0)
        self.battery_pct_var = tk.DoubleVar(value=50.0)
        self.safe_threshold_var = tk.DoubleVar(value=20.0)

        ttk.Label(opts, text="Mức tiêu thụ (kWh/100km)").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.consumption_var, width=12).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(opts, text="Dung lượng pin (kWh)").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.battery_kwh_var, width=12).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(opts, text="Pin lúc bắt đầu (%)").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.battery_pct_var, width=12).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(opts, text="Ngưỡng an toàn (%)").grid(row=3, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.safe_threshold_var, width=12).grid(row=3, column=1, sticky=tk.W)

        # preferences and filters
        prefs = ttk.LabelFrame(right, text="Tùy chọn", padding=(6,6))
        prefs.pack(fill=tk.X, pady=(4,8))

        self.pref_var = tk.StringVar(value="1")
        ttk.Label(prefs, text="Ưu tiên (1=time,2=distance,3=ít sạc)").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(prefs, textvariable=self.pref_var, values=["1","2","3"], width=12).grid(row=0, column=1, sticky=tk.W)

        self.avoid_highway_var = tk.BooleanVar(value=False)
        self.avoid_toll_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prefs, text="Tránh cao tốc", variable=self.avoid_highway_var).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(prefs, text="Tránh trạm thu phí", variable=self.avoid_toll_var).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(prefs, text="Số trạm lân cận (K)").grid(row=2, column=0, sticky=tk.W)
        self.nearby_k_var = tk.IntVar(value=5)
        ttk.Entry(prefs, textvariable=self.nearby_k_var, width=6).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(prefs, text="Bán kính tìm trạm (km)").grid(row=3, column=0, sticky=tk.W)
        self.max_search_dist_var = tk.DoubleVar(value=100.0)
        ttk.Entry(prefs, textvariable=self.max_search_dist_var, width=8).grid(row=3, column=1, sticky=tk.W)

        # run button
        self.run_btn = ttk.Button(right, text="Chạy bộ lập kế hoạch", command=self.on_run)
        self.run_btn.pack(fill=tk.X, pady=(6,6))

        # action row: Reset + Show stations checkbox (stations hidden by default)
        action_row = ttk.Frame(right)
        action_row.pack(fill=tk.X, pady=(0,6))
        self.reset_btn = ttk.Button(action_row, text="Đặt lại lựa chọn", command=self.reset_selection)
        self.reset_btn.pack(side=tk.LEFT, fill=tk.X, expand=False)
        ttk.Checkbutton(action_row, text="Hiển thị trạm sạc", variable=self.show_stations_var, command=self._on_toggle_stations).pack(side=tk.LEFT, padx=(8,0))

        # result text
        ttk.Label(right, text="Kết quả / Nhật ký:").pack(anchor=tk.W)
        self.logbox = scrolledtext.ScrolledText(right, height=12, wrap=tk.WORD)
        self.logbox.pack(fill=tk.BOTH, expand=True)

        # attach map click handler (tkintermapview helper)
        self.map_widget.add_left_click_map_command(self.on_map_click)

    # ----- Geocode helpers for UI -----
    def _geocode_text(self, q: str, limit: int = 6) -> List[dict]:
        """
        Return list of candidate dicts: {"lat":..., "lon":..., "display_name":...}
        Prefer project's geocode_candidates if available, else use geopy Nominatim.
        """
        q = q.strip()
        if not q:
            return []
        try:
            if geocode_candidates:
                cands = geocode_candidates(q, timeout=6, limit=limit)
                return [{"lat": lat, "lon": lon, "display_name": name} for (lat, lon, name) in cands]
        except Exception:
            pass

        if not _GEOPY_AVAILABLE:
            return []
        try:
            geol = Nominatim(user_agent="xe_ev_planner_ui", timeout=8)
            locs = geol.geocode(q, exactly_one=False, limit=limit, language="vi")
            if not locs:
                return []
            if isinstance(locs, list):
                return [{"lat": float(loc.latitude), "lon": float(loc.longitude), "display_name": getattr(loc, "address", str(loc))} for loc in locs]
            else:
                return [{"lat": float(locs.latitude), "lon": float(locs.longitude), "display_name": getattr(locs, "address", str(locs))}]
        except Exception:
            return []

    def _choose_candidate_dialog(self, candidates: List[dict]) -> Optional[dict]:
        """
        Show simple modal dialog with listbox to pick one candidate.
        Returns selected candidate dict or None.
        """
        if not candidates:
            return None
        sel = {"value": None}

        dlg = tk.Toplevel(self.root)
        dlg.title("Chọn kết quả")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Chọn một kết quả:").pack(anchor=tk.W, padx=8, pady=(8,0))
        lb = tk.Listbox(dlg, width=80, height=min(8, len(candidates)))
        for i, c in enumerate(candidates):
            display = c.get("display_name") or f"{c.get('lat')},{c.get('lon')}"
            lb.insert(tk.END, f"{i+1}. {display}")
        lb.pack(padx=8, pady=6, fill=tk.BOTH, expand=True)

        def on_ok():
            idxs = lb.curselection()
            if not idxs:
                messagebox.showwarning("Chọn kết quả", "Vui lòng chọn một mục hoặc Hủy.")
                return
            idx = idxs[0]
            sel["value"] = candidates[idx]
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=8, pady=(0,8))
        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side=tk.LEFT, padx=(0,6))
        ttk.Button(btn_frame, text="Hủy", command=on_cancel).pack(side=tk.LEFT)

        self.root.wait_window(dlg)
        return sel["value"]

    def _geocode_and_set(self, target: str):
        """
        target: "start" or "end"
        If the entry contains coords (lat,lon) set directly.
        Otherwise attempt geocode and set the picked location (first or chosen candidate).
        """
        if target == "start":
            val = self.start_var.get().strip()
        else:
            val = self.end_var.get().strip()

        # try parse as coords
        if "," in val:
            try:
                lat, lon = [float(x.strip()) for x in val.split(",")[:2]]
                if target == "start":
                    self.start_coord = (lat, lon)
                    self.start_var.set(f"{lat:.6f},{lon:.6f}")
                    if self.start_marker:
                        try:
                            self.start_marker.delete()
                        except Exception:
                            pass
                    self.start_marker = self.map_widget.set_marker(lat, lon, text="Start")
                    self.log(f"Đặt Start ở {lat:.6f},{lon:.6f}")
                else:
                    self.end_coord = (lat, lon)
                    self.end_var.set(f"{lat:.6f},{lon:.6f}")
                    if self.end_marker:
                        try:
                            self.end_marker.delete()
                        except Exception:
                            pass
                    self.end_marker = self.map_widget.set_marker(lat, lon, text="End")
                    self.log(f"Đặt End ở {lat:.6f},{lon:.6f}")
                # center map
                self.map_widget.set_position(lat, lon)
                return
            except Exception:
                messagebox.showerror("Lỗi định dạng", "Không thể đọc tọa độ. Vui lòng nhập dạng lat,lon.")
                return

        # otherwise geocode text
        self.log("Đang tìm địa điểm...")
        cands = self._geocode_text(val, limit=6)
        if not cands:
            messagebox.showinfo("Không tìm thấy", "Không tìm thấy kết quả cho truy vấn.")
            return
        chosen = cands[0]
        if len(cands) > 1:
            # let user choose
            user_choice = self._choose_candidate_dialog(cands)
            if user_choice:
                chosen = user_choice
            else:
                # user cancelled - do nothing
                self.log("Người dùng hủy chọn kết quả.")
                return

        lat = float(chosen["lat"]); lon = float(chosen["lon"])
        disp = chosen.get("display_name", f"{lat:.6f},{lon:.6f}")
        if target == "start":
            self.start_coord = (lat, lon)
            self.start_var.set(f"{lat:.6f},{lon:.6f}")
            if self.start_marker:
                try:
                    self.start_marker.delete()
                except Exception:
                    pass
            self.start_marker = self.map_widget.set_marker(lat, lon, text="Start")
            self.log(f"Start đặt: {disp}")
        else:
            self.end_coord = (lat, lon)
            self.end_var.set(f"{lat:.6f},{lon:.6f}")
            if self.end_marker:
                try:
                    self.end_marker.delete()
                except Exception:
                    pass
            self.end_marker = self.map_widget.set_marker(lat, lon, text="End")
            self.log(f"End đặt: {disp}")

        self.map_widget.set_position(lat, lon)
        self.map_widget.set_zoom(12)

    # ----- existing UI logic (mostly unchanged, with Vietnamese messages) -----
    def _draw_station_reference(self):
        try:
            if not self.show_stations_var.get():
                return
            for m in list(self.station_markers):
                try:
                    m.delete()
                except Exception:
                    pass
            self.station_markers = []
            for _, r in self.stations.iterrows():
                lat = float(r["lat"])
                lon = float(r["lon"])
                try:
                    m = self.map_widget.set_marker(lat, lon, text="", marker_color_circle="blue")
                    self.station_markers.append(m)
                except Exception:
                    try:
                        self.map_widget.set_marker(lat, lon, text="")
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_toggle_stations(self):
        if self.show_stations_var.get():
            self._draw_station_reference()
        else:
            for m in list(self.station_markers):
                try:
                    m.delete()
                except Exception:
                    pass
            self.station_markers = []

    def reset_selection(self):
        self.start_coord = None
        self.end_coord = None
        self.start_var.set("")
        self.end_var.set("")
        try:
            if self.start_marker:
                try:
                    self.start_marker.delete()
                except Exception:
                    pass
                self.start_marker = None
            if self.end_marker:
                try:
                    self.end_marker.delete()
                except Exception:
                    pass
                self.end_marker = None
            try:
                self.map_widget.delete_all_path()
            except Exception:
                pass
            try:
                self.map_widget.delete_all_marker()
            except Exception:
                pass
            if self.show_stations_var.get():
                self._draw_station_reference()
        except Exception:
            pass
        self.log("Đã đặt lại lựa chọn.")

    def on_map_click(self, coords):
        lat, lon = coords
        if not self.start_coord:
            self.start_coord = (lat, lon)
            self.start_var.set(f"{lat:.6f},{lon:.6f}")
            if self.start_marker:
                try:
                    self.start_marker.delete()
                except Exception:
                    pass
            self.start_marker = self.map_widget.set_marker(lat, lon, text="Start")
            self.log("Start đặt ở %.6f,%.6f" % (lat, lon))
        elif not self.end_coord:
            self.end_coord = (lat, lon)
            self.end_var.set(f"{lat:.6f},{lon:.6f}")
            if self.end_marker:
                try:
                    self.end_marker.delete()
                except Exception:
                    pass
            self.end_marker = self.map_widget.set_marker(lat, lon, text="End")
            self.log("End đặt ở %.6f,%.6f" % (lat, lon))
        else:
            messagebox.showinfo("Lưu ý", "Đã có Start và End. Dùng 'Đặt lại lựa chọn' để chọn lại.")

    def log(self, text: str):
        self.logbox.insert(tk.END, text + "\n")
        self.logbox.see(tk.END)

    def on_run(self):
        if not self.start_coord or not self.end_coord:
            messagebox.showerror("Thiếu thông tin", "Vui lòng đặt cả Điểm bắt đầu và Điểm đích trên bản đồ hoặc nhập địa chỉ và nhấn Tìm.")
            return

        self.run_btn.config(state=tk.DISABLED)
        self.log("Bắt đầu chạy bộ lập kế hoạch...")

        t = threading.Thread(target=self._run_planner_thread, daemon=True)
        t.start()

    def _run_planner_thread(self):
        try:
            from XeDien_AI_Nhom7 import plan_route
        except Exception as ex:
            self._after_log("Không thể import planner từ XeDien_AI_Nhom7: " + str(ex))
            self._after_enable()
            return

        try:
            G = build_graph(self.stations, k_neighbors=8)
            s_res = nearest_station(G, self.start_coord[0], self.start_coord[1], radius_km=self.max_search_dist_var.get())
            e_res = nearest_station(G, self.end_coord[0], self.end_coord[1], radius_km=self.max_search_dist_var.get())
            if not s_res or not e_res:
                self._after_log("Không tìm thấy trạm gần Start hoặc End trong bán kính đã chỉ định.")
                self._after_enable()
                return
            s_node, s_dist = s_res
            e_node, e_dist = e_res
            self._after_log(f"Trạm gần Start nhất: {s_node} ({s_dist:.2f} km)")
            self._after_log(f"Trạm gần End nhất: {e_node} ({e_dist:.2f} km)")

            cfg = {
                "consumption": float(self.consumption_var.get()),
                "battery_kwh": float(self.battery_kwh_var.get()),
                "battery_percent": float(self.battery_pct_var.get()),
                "safe_threshold": float(self.safe_threshold_var.get()),
                "pref": str(self.pref_var.get()),
                "avoid_highway": bool(self.avoid_highway_var.get()),
                "avoid_toll": bool(self.avoid_toll_var.get()),
                "enable_nearby": True,
                "nearby_k": int(self.nearby_k_var.get()),
                "max_search_dist": float(self.max_search_dist_var.get()),
                "use_astar": True,
                "use_osrm": True,
                "osrm_url": "http://router.project-osrm.org",
                "snap_radius_km": 5.0,
            }

            start_input = (self.start_coord[0], self.start_coord[1])
            end_input = (self.end_coord[0], self.end_coord[1])

            res = plan_route(start_input, end_input, config=cfg)
            out = res.get("output", "")
            self._after_log(out)

            result = res.get("result")
            if not result:
                self._after_log("Bộ lập kế hoạch không trả về kết quả khả dụng.")
                self._after_enable()
                return

            self.root.after(0, lambda: self._draw_results_on_map(result, s_node, e_node))

        except Exception as ex:
            self._after_log("Lỗi khi chạy planner: " + str(ex))
        finally:
            self._after_enable()

    def _after_log(self, txt):
        self.root.after(0, lambda: self.log(txt))

    def _after_enable(self):
        self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL))

    def _draw_results_on_map(self, result, s_node: str, e_node: str):
        try:
            self.map_widget.delete_all_marker()
            self._draw_station_reference()
        except Exception:
            pass

        self.start_marker = self.map_widget.set_marker(self.start_coord[0], self.start_coord[1], text="Start (đã chọn)")
        self.end_marker = self.map_widget.set_marker(self.end_coord[0], self.end_coord[1], text="End (đã chọn)")

        stations_index = self.stations.set_index("id")
        try:
            srow = stations_index.loc[s_node]
            s_lat, s_lon = float(srow["lat"]), float(srow["lon"])
            self.map_widget.set_marker(s_lat, s_lon, text=f"Trạm start {s_node}")
            self.map_widget.set_path([ (self.start_coord[0], self.start_coord[1]), (s_lat, s_lon) ], color="gray", width=2)
        except Exception:
            s_lat = s_lon = None

        try:
            erow = stations_index.loc[e_node]
            e_lat, e_lon = float(erow["lat"]), float(erow["lon"])
            self.map_widget.set_marker(e_lat, e_lon, text=f"Trạm end {e_node}")
            self.map_widget.set_path([ (self.end_coord[0], self.end_coord[1]), (e_lat, e_lon) ], color="gray", width=2)
        except Exception:
            e_lat = e_lon = None

        route_nodes = result.get("route", [])
        coords = []
        if route_nodes:
            for nid in route_nodes:
                try:
                    r = stations_index.loc[nid]
                    coords.append((float(r["lat"]), float(r["lon"])))
                except Exception:
                    continue
            if coords:
                self.map_widget.set_path(coords, color="red", width=4)

        base_poly = result.get("base_polyline")
        if base_poly:
            try:
                self.map_widget.set_path([(p[0], p[1]) for p in base_poly], color="darkred", width=3)
            except Exception:
                pass

        for geom in result.get("detour_polylines", []) or []:
            try:
                if geom:
                    self.map_widget.set_path([(p[0], p[1]) for p in geom], color="blue", width=3)
            except Exception:
                pass

        if coords:
            self.map_widget.set_position(coords[0][0], coords[0][1])
            self.map_widget.set_zoom(10)

        self.log("Bản đồ đã cập nhật kết quả.")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ui = CoordPlannerUI()
    ui.run()

