# -*- coding: utf-8 -*-
"""
coord_ui.py - native Tk UI (tkinter + tkintermapview) for selecting start/end on a map,
entering planner options, and running the planner from XeDien_AI_Nhom7.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import Optional, Tuple

import pandas as pd

# project imports (no plan_route here to avoid circular import)
from data_load import load_ev_stations_kml
from graph import build_graph, nearest_station

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
        self.root.title("EV Route Planner - Map UI")
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

        # click instructions
        ttk.Label(right, text="Click map once -> Start. Click second time -> End.").pack(anchor=tk.W, pady=(0,6))

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
        ttk.Label(coords_frame, text="Start (lat,lon):").grid(row=0, column=0, sticky=tk.W)
        self.start_var = tk.StringVar()
        ttk.Entry(coords_frame, textvariable=self.start_var, width=30).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(coords_frame, text="End (lat,lon):").grid(row=1, column=0, sticky=tk.W)
        self.end_var = tk.StringVar()
        ttk.Entry(coords_frame, textvariable=self.end_var, width=30).grid(row=1, column=1, sticky=tk.W)

        # vehicle and options
        opts = ttk.LabelFrame(right, text="Vehicle & options", padding=(6,6))
        opts.pack(fill=tk.X, pady=(4,8))

        self.consumption_var = tk.DoubleVar(value=16.3)
        self.battery_kwh_var = tk.DoubleVar(value=60.0)
        self.battery_pct_var = tk.DoubleVar(value=50.0)
        self.safe_threshold_var = tk.DoubleVar(value=20.0)

        ttk.Label(opts, text="Consumption (kWh/100km)").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.consumption_var, width=12).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(opts, text="Battery (kWh)").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.battery_kwh_var, width=12).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(opts, text="Start SOC (%)").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.battery_pct_var, width=12).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(opts, text="Safe threshold (%)").grid(row=3, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.safe_threshold_var, width=12).grid(row=3, column=1, sticky=tk.W)

        # preferences and filters
        prefs = ttk.LabelFrame(right, text="Preferences", padding=(6,6))
        prefs.pack(fill=tk.X, pady=(4,8))

        self.pref_var = tk.StringVar(value="1")
        ttk.Label(prefs, text="Pref (1=time,2=distance,3=fewest)").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(prefs, textvariable=self.pref_var, values=["1","2","3"], width=6).grid(row=0, column=1, sticky=tk.W)

        self.avoid_highway_var = tk.BooleanVar(value=False)
        self.avoid_toll_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(prefs, text="Avoid highway", variable=self.avoid_highway_var).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(prefs, text="Avoid toll", variable=self.avoid_toll_var).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(prefs, text="Nearby K").grid(row=2, column=0, sticky=tk.W)
        self.nearby_k_var = tk.IntVar(value=5)
        ttk.Entry(prefs, textvariable=self.nearby_k_var, width=6).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(prefs, text="Max nearby dist (km)").grid(row=3, column=0, sticky=tk.W)
        self.max_search_dist_var = tk.DoubleVar(value=100.0)
        ttk.Entry(prefs, textvariable=self.max_search_dist_var, width=8).grid(row=3, column=1, sticky=tk.W)

        # run button
        self.run_btn = ttk.Button(right, text="Run planner", command=self.on_run)
        self.run_btn.pack(fill=tk.X, pady=(6,6))

        # action row: Reset + Show stations checkbox (stations hidden by default)
        action_row = ttk.Frame(right)
        action_row.pack(fill=tk.X, pady=(0,6))
        self.reset_btn = ttk.Button(action_row, text="Reset selection", command=self.reset_selection)
        self.reset_btn.pack(side=tk.LEFT, fill=tk.X, expand=False)
        ttk.Checkbutton(action_row, text="Show stations", variable=self.show_stations_var, command=self._on_toggle_stations).pack(side=tk.LEFT, padx=(8,0))

        # result text
        ttk.Label(right, text="Result / Log:").pack(anchor=tk.W)
        self.logbox = scrolledtext.ScrolledText(right, height=12, wrap=tk.WORD)
        self.logbox.pack(fill=tk.BOTH, expand=True)

        # attach map click handler (tkintermapview helper)
        self.map_widget.add_left_click_map_command(self.on_map_click)

        # NOTE: do not pre-draw station points by default (user requested to hide them).
        # User can enable via the "Show stations" checkbox which calls _on_toggle_stations().

    def _draw_station_reference(self):
        # show small markers for stations and keep handles so they can be removed later
        # this function will only add markers if show_stations_var is True
        try:
            if not self.show_stations_var.get():
                return
            # clear any existing station markers first
            for m in list(self.station_markers):
                try:
                    m.delete()
                except Exception:
                    pass
            self.station_markers = []
            for _, r in self.stations.iterrows():
                lat = float(r["lat"])
                lon = float(r["lon"])
                # keep marker handles so we can delete them later
                try:
                    m = self.map_widget.set_marker(lat, lon, text="", marker_color_circle="blue")
                    self.station_markers.append(m)
                except Exception:
                    # fallback: create marker without storing
                    try:
                        self.map_widget.set_marker(lat, lon, text="")
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_toggle_stations(self):
        # Called when user toggles the "Show stations" checkbox
        if self.show_stations_var.get():
            self._draw_station_reference()
        else:
            # remove station markers
            for m in list(self.station_markers):
                try:
                    m.delete()
                except Exception:
                    pass
            self.station_markers = []

    def reset_selection(self):
        # Clear picked start/end coords, markers, and any drawn paths
        self.start_coord = None
        self.end_coord = None
        self.start_var.set("")
        self.end_var.set("")
        # delete start/end markers if present
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
            # remove all paths and markers that are not station references
            # safest approach: remove all markers & paths then re-draw station refs if enabled
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
        self.log("Selection reset.")

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
            self.log("Start set at %.6f,%.6f" % (lat, lon))
        elif not self.end_coord:
            self.end_coord = (lat, lon)
            self.end_var.set(f"{lat:.6f},{lon:.6f}")
            if self.end_marker:
                try:
                    self.end_marker.delete()
                except Exception:
                    pass
            self.end_marker = self.map_widget.set_marker(lat, lon, text="End")
            self.log("End set at %.6f,%.6f" % (lat, lon))
        else:
            messagebox.showinfo("Pick coords", "Start and End already set. Use Reset selection to pick again.")

    def log(self, text: str):
        self.logbox.insert(tk.END, text + "\n")
        self.logbox.see(tk.END)

    def on_run(self):
        if not self.start_coord or not self.end_coord:
            messagebox.showerror("Missing points", "Please select both Start and End on the map.")
            return

        # disable button while running
        self.run_btn.config(state=tk.DISABLED)
        self.log("Starting planner...")

        t = threading.Thread(target=self._run_planner_thread, daemon=True)
        t.start()

    def _run_planner_thread(self):
        # Lazy import planner to avoid circular import when XeDien_AI_Nhom7 imports coord_ui
        try:
            from XeDien_AI_Nhom7 import plan_route
        except Exception as ex:
            self._after_log("Cannot import planner from XeDien_AI_Nhom7: " + str(ex))
            self._after_enable()
            return

        try:
            # build graph & find nearest stations (kept for logging / info)
            G = build_graph(self.stations, k_neighbors=8)
            s_res = nearest_station(G, self.start_coord[0], self.start_coord[1], radius_km=self.max_search_dist_var.get())
            e_res = nearest_station(G, self.end_coord[0], self.end_coord[1], radius_km=self.max_search_dist_var.get())
            if not s_res or not e_res:
                self._after_log("No nearby station found for Start or End within max_search_dist.")
                self._after_enable()
                return
            s_node, s_dist = s_res
            e_node, e_dist = e_res
            self._after_log(f"Nearest start station: {s_node} ({s_dist:.2f} km)")
            self._after_log(f"Nearest end station: {e_node} ({e_dist:.2f} km)")

            # gather config -- enable OSRM baseroutes so planner will prefer real roads (match HTML)
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
                # IMPORTANT: request OSRM routes (plan_route uses OSRM baseroutes when available)
                "use_astar": True,
                "use_osrm": True,
                "osrm_url": "http://router.project-osrm.org",
                "snap_radius_km": 5.0,
            }

            # call plan_route using the picked lat/lon tuples (not station ids)
            # passing tuples makes plan_route create virtual nodes AND allows OSRM baseroutes
            start_input = (self.start_coord[0], self.start_coord[1])
            end_input = (self.end_coord[0], self.end_coord[1])

            res = plan_route(start_input, end_input, config=cfg)        
            out = res.get("output", "")
            self._after_log(out)

            result = res.get("result")
            if not result:
                self._after_log("Planner returned no result.")
                self._after_enable()
                return

            # draw results on map (must run on main thread)
            self.root.after(0, lambda: self._draw_results_on_map(result, s_node, e_node))

        except Exception as ex:
            self._after_log("Error during planning: " + str(ex))
        finally:
            self._after_enable()

    def _after_log(self, txt):
        self.root.after(0, lambda: self.log(txt))

    def _after_enable(self):
        self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL))

    def _draw_results_on_map(self, result, s_node: str, e_node: str):
        # clear previous dynamic markers/paths (leave station reference)
        try:
            self.map_widget.delete_all_marker()
            self._draw_station_reference()
        except Exception:
            pass

        # markers: chosen points
        self.start_marker = self.map_widget.set_marker(self.start_coord[0], self.start_coord[1], text="Start (picked)")
        self.end_marker = self.map_widget.set_marker(self.end_coord[0], self.end_coord[1], text="End (picked)")

        # nearest station coords
        stations_index = self.stations.set_index("id")
        try:
            srow = stations_index.loc[s_node]
            s_lat, s_lon = float(srow["lat"]), float(srow["lon"])
            self.map_widget.set_marker(s_lat, s_lon, text=f"Start station {s_node}")
            # line from picked point to station
            self.map_widget.set_path([ (self.start_coord[0], self.start_coord[1]), (s_lat, s_lon) ], color="gray", width=2)
        except Exception:
            s_lat = s_lon = None

        try:
            erow = stations_index.loc[e_node]
            e_lat, e_lon = float(erow["lat"]), float(erow["lon"])
            self.map_widget.set_marker(e_lat, e_lon, text=f"End station {e_node}")
            self.map_widget.set_path([ (self.end_coord[0], self.end_coord[1]), (e_lat, e_lon) ], color="gray", width=2)
        except Exception:
            e_lat = e_lon = None

        # draw station-sequence route
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

        # draw base_polyline (road geometry) if present
        base_poly = result.get("base_polyline")
        if base_poly:
            try:
                # base_poly is list of (lat, lon)
                self.map_widget.set_path([(p[0], p[1]) for p in base_poly], color="darkred", width=3)
            except Exception:
                pass

        # draw detour polylines if any
        for geom in result.get("detour_polylines", []) or []:
            try:
                if geom:
                    self.map_widget.set_path([(p[0], p[1]) for p in geom], color="blue", width=3)
            except Exception:
                pass

        # center map around route start
        if coords:
            self.map_widget.set_position(coords[0][0], coords[0][1])
            self.map_widget.set_zoom(10)

        self.log("Map updated with result.")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ui = CoordPlannerUI()
    ui.run()

