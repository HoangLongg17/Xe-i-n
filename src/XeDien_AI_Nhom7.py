from data_load import load_ev_stations_kml
from graph import build_graph
from routing_search import ucs_ev_search
from visualization import plot_path

def main():
    # Đọc dữ liệu trạm sạc từ file KML
    stations = load_ev_stations_kml("../data/evcs_map.kml")
    print("Số trạm:", len(stations))
    print(stations.head())

    # Xây dựng đồ thị từ dữ liệu trạm
    G = build_graph(stations)

    # Chạy thuật toán UCS cho xe điện
    path, dist, time, charges = ucs_ev_search(
        G, stations,
        start="ST01", end="ST03",
        battery_percent=50, battery_kwh_max=60,
        consumption_kwh_per_100km=16.3,
        safe_threshold_percent=20
    )

    # In kết quả ra console
    print("Lộ trình:", path)
    print("Tổng quãng đường:", dist, "km")
    print("Tổng thời gian:", round(time, 1), "phút")
    print("Các lần sạc:", charges)

    # Vẽ bản đồ và lưu ra file HTML
    m = plot_path(stations, path)
    m.save("ev_route.html")
    print("Đã lưu bản đồ: ev_route.html")

if __name__ == "__main__":
    main()
