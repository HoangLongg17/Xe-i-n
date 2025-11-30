import pandas as pd
import xml.etree.ElementTree as ET

def load_ev_stations_kml(path: str) -> pd.DataFrame:
    tree = ET.parse(path)
    root = tree.getroot()

    # namespace của KML
    ns = {"kml": "http://www.opengis.net/kml/2.2"}

    stations = []
    counter = 1

    # Duyệt tất cả Placemark
    for placemark in root.findall(".//kml:Placemark", ns):
        name = placemark.find("kml:name", ns)
        desc = placemark.find("kml:description", ns)
        coords = placemark.find(".//kml:coordinates", ns)

        if coords is not None:
            lon, lat, *_ = coords.text.strip().split(",")
            stations.append({
                "id": f"ST{counter:02d}",
                "name": name.text if name is not None else "",
                "description": desc.text if desc is not None else "",
                "lat": float(lat),
                "lon": float(lon)
            })
            counter += 1

    return pd.DataFrame(stations)
