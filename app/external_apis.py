"""
Real-time contextual enrichment: weather and traffic.

Design goals:
- Never let an external outage break a prediction request. Every function
  here catches its own exceptions and returns a `data_available: False`
  result with a neutral (zero) adjustment rather than raising.
- Keyless, free endpoints are used so the demo works without provisioning
  API keys: Open-Meteo for weather, OSRM's public demo router for a crude
  traffic-congestion proxy (comparing current route duration to a
  free-flow estimate).
"""
from __future__ import annotations

import httpx

from app.config import (
    EXTERNAL_API_TIMEOUT_SECONDS,
    MAX_TRAFFIC_ADJUSTMENT,
    MAX_WEATHER_ADJUSTMENT,
    OPEN_METEO_URL,
    OSRM_URL,
)


def fetch_weather(lat: float, lon: float) -> dict:
    """
    Fetch current weather from Open-Meteo (no API key required).

    Returns dict: {data_available, condition, precipitation_mm, visibility_note, adjustment}
    """
    try:
        resp = httpx.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "precipitation,weather_code,wind_speed_10m",
                "timezone": "Asia/Kolkata",
            },
            timeout=EXTERNAL_API_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("current", {})
        precipitation = float(data.get("precipitation", 0.0))
        weather_code = int(data.get("weather_code", 0))
        wind_speed = float(data.get("wind_speed_10m", 0.0))

        # WMO weather codes: 45/48 = fog, 51-67/80-82 = rain/drizzle/showers,
        # 71-77/85-86 = snow (irrelevant for Delhi but handled anyway).
        is_fog = weather_code in (45, 48)
        is_rain = (51 <= weather_code <= 67) or (80 <= weather_code <= 82)
        is_severe = weather_code >= 95

        adjustment = 0.0
        notes = []
        if is_severe:
            adjustment += MAX_WEATHER_ADJUSTMENT
            notes.append("thunderstorm activity reported")
        elif is_rain or precipitation > 0.5:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.7
            notes.append("rain reduces visibility and pedestrian footing")
        elif is_fog:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.6
            notes.append("fog reduces visibility")
        if wind_speed > 40:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.2
            notes.append("high winds")

        adjustment = min(adjustment, MAX_WEATHER_ADJUSTMENT)

        return {
            "data_available": True,
            "weather_code": weather_code,
            "precipitation_mm": precipitation,
            "wind_speed_kmh": wind_speed,
            "notes": notes,
            "adjustment": round(adjustment, 4),
        }
    except Exception as exc:  # network error, timeout, bad response, etc.
        return {
            "data_available": False,
            "error": str(exc),
            "notes": [],
            "adjustment": 0.0,
        }


def fetch_traffic_context(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    """
    Use OSRM's public demo routing server as a crude traffic-congestion
    proxy: request a driving route and compare distance/duration to a
    free-flow speed assumption. This is a heuristic, not real live-traffic
    data (OSRM's public demo doesn't carry live traffic), but it keeps the
    "traffic enrichment" pipeline real and demonstrates the fallback path
    cleanly when unavailable.

    A busier/slower implied route nudges risk slightly *down* at night
    (more vehicles/people around) and slightly *up* during off-peak
    isolated hours logic is actually handled by the caller; this function
    only reports the congestion estimate.
    """
    try:
        lon1, lat1 = origin[1], origin[0]
        lon2, lat2 = destination[1], destination[0]
        url = f"{OSRM_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
        resp = httpx.get(url, params={"overview": "false"}, timeout=EXTERNAL_API_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError("OSRM returned no route")
        route = data["routes"][0]
        distance_km = route["distance"] / 1000.0
        duration_min = route["duration"] / 60.0
        implied_speed_kmh = (distance_km / (duration_min / 60.0)) if duration_min > 0 else 0.0
        # Free-flow city assumption ~35km/h; much slower implies congestion.
        congestion_ratio = 1 - min(implied_speed_kmh / 35.0, 1.0) if implied_speed_kmh else 0.5
        return {
            "data_available": True,
            "distance_km": round(distance_km, 2),
            "duration_min": round(duration_min, 1),
            "implied_speed_kmh": round(implied_speed_kmh, 1),
            "congestion_ratio": round(max(0.0, congestion_ratio), 3),
        }
    except Exception as exc:
        return {"data_available": False, "error": str(exc), "congestion_ratio": None}


def traffic_adjustment(congestion_ctx: dict, time_of_day_risk: float) -> float:
    """
    Convert a congestion estimate into a signed risk adjustment.

    Heuristic: during high time-of-day risk windows (night), *more* traffic
    (people/vehicles around) is protective (small negative adjustment).
    During low-risk daytime windows, heavy congestion mostly just means
    delays, not a safety signal, so the adjustment is muted.
    """
    if not congestion_ctx.get("data_available") or congestion_ctx.get("congestion_ratio") is None:
        return 0.0
    congestion = congestion_ctx["congestion_ratio"]
    if time_of_day_risk > 0.6:
        # More congestion at night -> more people/vehicles around -> safer.
        return round(-MAX_TRAFFIC_ADJUSTMENT * congestion, 4)
    # Daytime: negligible safety signal from congestion alone.
    return round(-MAX_TRAFFIC_ADJUSTMENT * 0.15 * congestion, 4)
