def kwh_needed(distance_km, consumption_kwh_per_100km):
    """Tính số kWh cần cho quãng đường"""
    return distance_km * consumption_kwh_per_100km / 100.0

def km_from_percent(percent, battery_kwh_max, consumption_kwh_per_100km):
    """Tính số km đi được từ % pin"""
    kwh = battery_kwh_max * percent / 100.0
    return (kwh / consumption_kwh_per_100km) * 100.0

def charge_time_minutes(power_kw, delta_kwh):
    """Tính thời gian sạc (phút)"""
    hours = delta_kwh / power_kw
    return hours * 60.0
