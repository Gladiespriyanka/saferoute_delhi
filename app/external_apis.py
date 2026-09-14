"""
Live contextual enrichment: weather and traffic.

Two rules hold throughout:

* **An external outage must never break a prediction.** Every function here
  catches its own exceptions and returns `data_available: False` with a
  neutral zero adjustment rather than raising. Callers pass that flag
  through to the client, so it is always visible whether live data was
  actually used.
* **Context nudges, it never decides.** Adjustments are hard-capped by
  MAX_WEATHER_ADJUSTMENT / MAX_TRAFFIC_ADJUSTMENT, so no amount of weather
  can turn a Safe route Unsafe on its own.

Both endpoints are keyless so the system works out of the box.
"""
from __future__ import annotations

import logging

import httpx

from app.config import (
    EXTERNAL_API_TIMEOUT_SECONDS,
    MAX_TRAFFIC_ADJUSTMENT,
    MAX_WEATHER_ADJUSTMENT,
    OPEN_METEO_URL,
    OSRM_URL,
)

log = logging.getLogger(__name__)

# One pooled client, reused across calls. A route fans out one weather
# lookup per segment; without pooling each one pays a fresh TCP and TLS
# handshake to the same host, which dominates the request time.
_client = httpx.Client(
    timeout=EXTERNAL_API_TIMEOUT_SECONDS,
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    headers={"User-Agent": "SafeHerWay/1.0"},
)

# Free-flow speed assumed for Delhi's road network when inferring congestion.
FREE_FLOW_SPEED_KMH = 35.0


def _unavailable(source: str, exc: Exception) -> dict:
    log.debug("%s lookup failed: %s", source, exc)
    return {"data_available": False, "error": str(exc), "notes": [], "adjustment": 0.0}


def fetch_weather(lat: float, lon: float) -> dict:
    """
    Current conditions from Open-Meteo, converted into a capped risk nudge.

    Rain, fog and storms all reduce visibility and make footing worse, and
    they thin out the foot traffic that provides passive surveillance.
    """
    try:
        response = _client.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "precipitation,weather_code,wind_speed_10m",
                "timezone": "Asia/Kolkata",
            },
        )
        response.raise_for_status()
        current = response.json().get("current", {})
        precipitation = float(current.get("precipitation", 0.0) or 0.0)
        weather_code = int(current.get("weather_code", 0) or 0)
        wind_speed = float(current.get("wind_speed_10m", 0.0) or 0.0)

        # WMO codes: 45/48 fog, 51-67 drizzle/rain, 80-82 showers, 95+ storms.
        is_fog = weather_code in (45, 48)
        is_rain = 51 <= weather_code <= 67 or 80 <= weather_code <= 82
        is_severe = weather_code >= 95

        adjustment = 0.0
        notes: list[str] = []
        if is_severe:
            adjustment += MAX_WEATHER_ADJUSTMENT
            notes.append("thunderstorm activity reported")
        elif is_rain or precipitation > 0.5:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.7
            notes.append("rain reduces visibility and footing")
        elif is_fog:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.6
            notes.append("fog reduces visibility")
        if wind_speed > 40:
            adjustment += MAX_WEATHER_ADJUSTMENT * 0.2
            notes.append("high winds")

        return {
            "data_available": True,
            "weather_code": weather_code,
            "precipitation_mm": precipitation,
            "wind_speed_kmh": wind_speed,
            "notes": notes,
            "adjustment": round(min(adjustment, MAX_WEATHER_ADJUSTMENT), 4),
        }
    except Exception as exc:  # noqa: BLE001 - network, timeout, bad payload
        return _unavailable("weather", exc)


def fetch_traffic_context(origin: tuple[float, float], destination: tuple[float, float]) -> dict:
    """
    Estimate congestion along a route using OSRM's public demo router.

    This is a **proxy, not live traffic** -- the public OSRM demo carries no
    traffic layer. It compares the router's implied average speed against a
    free-flow assumption, which captures road-type and route-shape effects
    (a slow implied speed means a route through dense, signal-heavy streets)
    but not today's actual congestion. It is labelled as an estimate
    everywhere it surfaces. Swapping in a real traffic API means changing
    only this function.
    """
    try:
        lat1, lon1 = origin
        lat2, lon2 = destination
        response = _client.get(
            f"{OSRM_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}",
            params={"overview": "false"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ValueError(f"OSRM returned no route ({payload.get('code')})")

        route = payload["routes"][0]
        distance_km = route["distance"] / 1000.0
        duration_min = route["duration"] / 60.0
        if duration_min <= 0:
            raise ValueError("OSRM returned a zero-duration route")

        implied_speed = distance_km / (duration_min / 60.0)
        congestion = 1.0 - min(implied_speed / FREE_FLOW_SPEED_KMH, 1.0)
        return {
            "data_available": True,
            "distance_km": round(distance_km, 2),
            "duration_min": round(duration_min, 1),
            "implied_speed_kmh": round(implied_speed, 1),
            "congestion_ratio": round(max(0.0, congestion), 3),
        }
    except Exception as exc:  # noqa: BLE001
        result = _unavailable("traffic", exc)
        result["congestion_ratio"] = None
        return result


def traffic_adjustment(congestion_ctx: dict, time_of_day_risk: float) -> float:
    """
    Turn a congestion estimate into a signed risk adjustment.

    At night, congestion is protective: more vehicles and people around means
    more passive surveillance, so the adjustment is negative. In daylight,
    congestion mostly just means delay and carries little safety signal, so
    the same effect is heavily muted rather than dropped entirely.
    """
    if not congestion_ctx.get("data_available") or congestion_ctx.get("congestion_ratio") is None:
        return 0.0
    congestion = congestion_ctx["congestion_ratio"]
    scale = 1.0 if time_of_day_risk > 0.6 else 0.15
    return round(-MAX_TRAFFIC_ADJUSTMENT * scale * congestion, 4)
