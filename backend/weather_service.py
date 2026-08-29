"""
weather_service.py
Wraps Open-Meteo (free, no API key) for:
  - geocoding place names -> lat/lon
  - current conditions + short-term forecast (GFS/ICON blended, i.e. real NWP output)
  - multi-day forecast
  - historical / climate trend data (for the "climate analysis" feature)

Open-Meteo is used because it requires zero signup and re-serves real NWP
model output (GFS, ICON, etc.) -- this satisfies the "NWP model integration"
requirement of the problem statement without needing to run WRF/GFS ourselves.

If you have an OpenWeatherMap or IMD data.gov.in key, drop it into .env and
extend `get_current_weather` / `get_alerts_raw` to cross-check against it --
hooks are marked below with TODO(IMD) / TODO(OWM).
"""

import os
import httpx
from datetime import datetime, timedelta

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

OWM_API_KEY = os.getenv("OWM_API_KEY", "")  # optional, TODO(OWM)

WEATHER_CODE_MAP = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Freezing rain (light)", 67: "Freezing rain (heavy)",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


async def geocode(location: str) -> dict:
    """Resolve a place name to lat/lon + resolved display name (country/admin)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(GEOCODE_URL, params={"name": location, "count": 1, "language": "en"})
        r.raise_for_status()
        data = r.json()
    results = data.get("results")
    if not results:
        raise ValueError(f"Could not find a location matching '{location}'")
    top = results[0]
    return {
        "name": top["name"],
        "admin1": top.get("admin1", ""),
        "country": top.get("country", ""),
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "timezone": top.get("timezone", "auto"),
    }


async def get_current_weather(location: str) -> dict:
    """Current conditions + today's forecast summary for a location."""
    place = await geocode(location)
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                    "weather_code,wind_speed_10m,wind_direction_10m,surface_pressure",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,"
                  "wind_speed_10m_max,uv_index_max",
        "timezone": place["timezone"],
        "forecast_days": 1,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        data = r.json()

    cur = data["current"]
    today = data["daily"]
    return {
        "location": f'{place["name"]}, {place.get("admin1","")}, {place.get("country","")}'.strip(", "),
        "temperature_c": cur["temperature_2m"],
        "feels_like_c": cur["apparent_temperature"],
        "humidity_pct": cur["relative_humidity_2m"],
        "precipitation_mm": cur["precipitation"],
        "condition": WEATHER_CODE_MAP.get(cur["weather_code"], "Unknown"),
        "wind_speed_kmh": cur["wind_speed_10m"],
        "wind_direction_deg": cur["wind_direction_10m"],
        "pressure_hpa": cur["surface_pressure"],
        "today_max_c": today["temperature_2m_max"][0],
        "today_min_c": today["temperature_2m_min"][0],
        "today_rain_probability_pct": today["precipitation_probability_max"][0],
        "today_uv_index": today["uv_index_max"][0],
        "observed_at": cur["time"],
    }


async def get_forecast(location: str, days: int = 5) -> dict:
    """Multi-day forecast (NWP model output, blended GFS/ICON via Open-Meteo)."""
    days = max(1, min(days, 16))
    place = await geocode(location)
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                  "precipitation_probability_max,wind_speed_10m_max",
        "timezone": place["timezone"],
        "forecast_days": days,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        data = r.json()

    daily = data["daily"]
    forecast_days = []
    for i, date in enumerate(daily["time"]):
        forecast_days.append({
            "date": date,
            "condition": WEATHER_CODE_MAP.get(daily["weather_code"][i], "Unknown"),
            "max_c": daily["temperature_2m_max"][i],
            "min_c": daily["temperature_2m_min"][i],
            "rain_mm": daily["precipitation_sum"][i],
            "rain_probability_pct": daily["precipitation_probability_max"][i],
            "wind_max_kmh": daily["wind_speed_10m_max"][i],
        })
    return {
        "location": f'{place["name"]}, {place.get("admin1","")}, {place.get("country","")}'.strip(", "),
        "days": forecast_days,
    }


async def get_climate_trend(location: str, years: int = 10) -> dict:
    """Historical daily data over N years -> yearly averages, for trend analysis."""
    years = max(1, min(years, 40))
    place = await geocode(location)
    end = datetime.utcnow().date() - timedelta(days=5)  # archive lags a few days
    start = end.replace(year=end.year - years)
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": place["timezone"],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(ARCHIVE_URL, params=params)
        r.raise_for_status()
        data = r.json()

    daily = data["daily"]
    yearly = {}
    for i, date in enumerate(daily["time"]):
        y = date[:4]
        yearly.setdefault(y, {"tmax": [], "tmin": [], "rain": []})
        if daily["temperature_2m_max"][i] is not None:
            yearly[y]["tmax"].append(daily["temperature_2m_max"][i])
        if daily["temperature_2m_min"][i] is not None:
            yearly[y]["tmin"].append(daily["temperature_2m_min"][i])
        if daily["precipitation_sum"][i] is not None:
            yearly[y]["rain"].append(daily["precipitation_sum"][i])

    trend = []
    for y in sorted(yearly.keys()):
        v = yearly[y]
        trend.append({
            "year": y,
            "avg_max_c": round(sum(v["tmax"]) / len(v["tmax"]), 1) if v["tmax"] else None,
            "avg_min_c": round(sum(v["tmin"]) / len(v["tmin"]), 1) if v["tmin"] else None,
            "total_rain_mm": round(sum(v["rain"]), 1) if v["rain"] else None,
        })
    return {
        "location": f'{place["name"]}, {place.get("admin1","")}, {place.get("country","")}'.strip(", "),
        "years_covered": years,
        "yearly_trend": trend,
    }


# ---- Alert thresholds -----------------------------------------------------
# TODO(IMD): swap/augment these with IMD's official warning levels
# (data.gov.in weather warning APIs) when a key/dataset is available.
ALERT_THRESHOLDS = {
    "heavy_rain_mm": 64.5,       # IMD "heavy rain" 24h threshold
    "very_heavy_rain_mm": 115.6,  # IMD "very heavy rain" 24h threshold
    "heatwave_c": 40.0,
    "high_wind_kmh": 50.0,
    "high_rain_probability_pct": 75,
}


async def get_alerts_raw(location: str) -> list:
    """Rule-based extreme-weather check against forecast data (next 3 days)."""
    fc = await get_forecast(location, days=3)
    alerts = []
    for day in fc["days"]:
        if day["rain_mm"] >= ALERT_THRESHOLDS["very_heavy_rain_mm"]:
            alerts.append({"date": day["date"], "severity": "severe", "type": "rainfall",
                            "message": f'Very heavy rainfall expected ({day["rain_mm"]} mm)'})
        elif day["rain_mm"] >= ALERT_THRESHOLDS["heavy_rain_mm"]:
            alerts.append({"date": day["date"], "severity": "moderate", "type": "rainfall",
                            "message": f'Heavy rainfall expected ({day["rain_mm"]} mm)'})
        elif day["rain_probability_pct"] >= ALERT_THRESHOLDS["high_rain_probability_pct"]:
            alerts.append({"date": day["date"], "severity": "mild", "type": "rainfall",
                            "message": f'High chance of rain ({day["rain_probability_pct"]}%)'})

        if day["max_c"] >= ALERT_THRESHOLDS["heatwave_c"]:
            alerts.append({"date": day["date"], "severity": "moderate", "type": "heat",
                            "message": f'Heatwave-level temperature expected ({day["max_c"]}°C)'})

        if day["wind_max_kmh"] >= ALERT_THRESHOLDS["high_wind_kmh"]:
            alerts.append({"date": day["date"], "severity": "moderate", "type": "wind",
                            "message": f'High winds expected ({day["wind_max_kmh"]} km/h)'})

    return {"location": fc["location"], "alerts": alerts}
