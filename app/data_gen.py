"""
Synthetic data generation for SafeRoute Delhi.

No real Delhi crime/infrastructure dataset is wired in for this build, so
we generate a *plausible* synthetic world: a grid of "areas" over Delhi's
bounding box, each with a crime index and POI density, plus segment-level
lighting/crowd/infra attributes sampled with realistic correlations (e.g.
poorly lit areas tend to also have lower CCTV coverage).

Everything here is deterministic given RANDOM_SEED so re-running produces
the same synthetic world -- important for reproducible demos/tests.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

from app.config import (
    COMPOSITE_WEIGHTS,
    DELHI_LAT_RANGE,
    DELHI_LON_RANGE,
    MODERATE_UPPER_BOUND,
    POI_CATEGORIES,
    RANDOM_SEED,
    SAFE_UPPER_BOUND,
)

GRID_CELLS_PER_SIDE = 12  # 144 synthetic "areas" covering the city


def area_code_for_point(lat: float, lon: float) -> str:
    """Deterministically map a lat/lon to a synthetic area grid cell.

    This stands in for a real geocoding / administrative-ward lookup. Using
    a fixed grid keeps the mapping stable and fast (no external call).
    """
    lat_lo, lat_hi = DELHI_LAT_RANGE
    lon_lo, lon_hi = DELHI_LON_RANGE
    lat_clamped = min(max(lat, lat_lo), lat_hi)
    lon_clamped = min(max(lon, lon_lo), lon_hi)
    row = int((lat_clamped - lat_lo) / (lat_hi - lat_lo) * (GRID_CELLS_PER_SIDE - 1e-9))
    col = int((lon_clamped - lon_lo) / (lon_hi - lon_lo) * (GRID_CELLS_PER_SIDE - 1e-9))
    return f"AREA-{row:02d}-{col:02d}"


def _stable_hash_unit(*parts: str) -> float:
    """Deterministic pseudo-random float in [0, 1) derived from string parts."""
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def build_crime_table() -> pd.DataFrame:
    """One row per synthetic area with a crime risk index and monthly incident count."""
    rows = []
    for row in range(GRID_CELLS_PER_SIDE):
        for col in range(GRID_CELLS_PER_SIDE):
            area_code = f"AREA-{row:02d}-{col:02d}"
            base = _stable_hash_unit(area_code, "crime")
            # A handful of "hotspot" cells get pushed higher to mimic real
            # city crime-density skew (concentrated, not uniform).
            hotspot_boost = 0.35 if _stable_hash_unit(area_code, "hotspot") > 0.85 else 0.0
            crime_risk_index = min(1.0, base * 0.7 + hotspot_boost)
            reported_incidents_month = int(crime_risk_index * 40 + _stable_hash_unit(area_code, "inc") * 10)
            rows.append(
                {
                    "area_code": area_code,
                    "crime_risk_index": round(crime_risk_index, 4),
                    "reported_incidents_month": reported_incidents_month,
                }
            )
    return pd.DataFrame(rows)


def build_poi_table() -> pd.DataFrame:
    """Synthetic points of interest: metro stations, bus stops, hospitals, police posts."""
    rng = np.random.default_rng(RANDOM_SEED)
    lat_lo, lat_hi = DELHI_LAT_RANGE
    lon_lo, lon_hi = DELHI_LON_RANGE
    counts = {"metro": 60, "bus_stop": 400, "hospital": 90, "police": 120}
    rows = []
    poi_id = 0
    for category in POI_CATEGORIES:
        n = counts.get(category, 50)
        lats = rng.uniform(lat_lo, lat_hi, n)
        lons = rng.uniform(lon_lo, lon_hi, n)
        for lat, lon in zip(lats, lons):
            rows.append({"poi_id": poi_id, "category": category, "lat": lat, "lon": lon})
            poi_id += 1
    return pd.DataFrame(rows)


def haversine_km(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Vectorized haversine distance in km between one point and arrays of points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def nearest_poi_distances(lat: float, lon: float, poi_table: pd.DataFrame) -> dict[str, float]:
    """Return nearest distance (km) to each POI category for a point."""
    result = {}
    for category in POI_CATEGORIES:
        subset = poi_table[poi_table["category"] == category]
        if subset.empty:
            result[category] = 5.0  # sane fallback
            continue
        dists = haversine_km(lat, lon, subset["lat"].to_numpy(), subset["lon"].to_numpy())
        result[category] = float(dists.min())
    return result


def generate_segment_attributes(lat: float, lon: float, area_code: str, seed_suffix: str = "") -> dict:
    """Generate correlated synthetic infra/lighting/crowd attributes for a point."""
    base_quality = _stable_hash_unit(area_code, "quality", seed_suffix)
    # Correlate lighting, streetlight density, and CCTV: neighborhoods that
    # invest in one infra dimension tend to invest in others.
    noise = lambda tag: _stable_hash_unit(area_code, tag, str(lat), str(lon), seed_suffix)
    lighting_score = float(np.clip(base_quality * 0.7 + noise("light") * 0.3, 0, 1))
    streetlight_density = float(np.clip(base_quality * 0.6 + noise("streetlight") * 0.4, 0, 1))
    cctv_coverage = float(np.clip(base_quality * 0.5 + noise("cctv") * 0.5, 0, 1))
    footpath_quality = float(np.clip(base_quality * 0.6 + noise("footpath") * 0.4, 0, 1))
    crowd_density = float(np.clip(noise("crowd") * 0.8 + 0.1, 0, 1))
    return {
        "lighting_score": lighting_score,
        "streetlight_density": streetlight_density,
        "cctv_coverage": cctv_coverage,
        "footpath_quality": footpath_quality,
        "crowd_density": crowd_density,
    }


def generate_training_dataset(n_samples: int = 6000) -> pd.DataFrame:
    """
    Generate a synthetic labeled dataset of route segments for model training.

    The label is derived from a transparent, weighted composite-risk formula
    (see app.config.COMPOSITE_WEIGHTS) plus noise, so the ML model learns a
    *smoothed, generalizable* approximation of that ground truth rather than
    memorizing the formula -- similar to how a real project would bootstrap
    a model from a rule-based baseline before real audit labels accumulate.
    """
    from app.features import cyclic_time_features, isolation_index, time_of_day_risk

    rng = np.random.default_rng(RANDOM_SEED)
    lat_lo, lat_hi = DELHI_LAT_RANGE
    lon_lo, lon_hi = DELHI_LON_RANGE
    crime_table = build_crime_table().set_index("area_code")
    poi_table = build_poi_table()

    lats = rng.uniform(lat_lo, lat_hi, n_samples)
    lons = rng.uniform(lon_lo, lon_hi, n_samples)
    hours = rng.integers(0, 24, n_samples)
    dows = rng.integers(0, 7, n_samples)

    rows = []
    for i in range(n_samples):
        lat, lon, hour, dow = lats[i], lons[i], int(hours[i]), int(dows[i])
        area_code = area_code_for_point(lat, lon)
        attrs = generate_segment_attributes(lat, lon, area_code, seed_suffix=str(i))
        poi_dist = nearest_poi_distances(lat, lon, poi_table)
        crime_risk_index = float(crime_table.loc[area_code, "crime_risk_index"])

        infra_score = 0.35 * attrs["streetlight_density"] + 0.35 * attrs["cctv_coverage"] + 0.30 * attrs["footpath_quality"]
        infra_risk = 1 - infra_score
        iso_idx = isolation_index(attrs["crowd_density"], poi_dist)
        tod_risk = time_of_day_risk(hour)
        lighting_risk = 1 - attrs["lighting_score"]

        composite = (
            COMPOSITE_WEIGHTS["infra_risk"] * infra_risk
            + COMPOSITE_WEIGHTS["isolation_index"] * iso_idx
            + COMPOSITE_WEIGHTS["crime_risk_index"] * crime_risk_index
            + COMPOSITE_WEIGHTS["time_of_day_risk"] * tod_risk
            + COMPOSITE_WEIGHTS["lighting_risk"] * lighting_risk
        )
        composite = float(np.clip(composite + rng.normal(0, 0.05), 0, 1))

        if composite <= SAFE_UPPER_BOUND:
            label = "Safe"
        elif composite <= MODERATE_UPPER_BOUND:
            label = "Moderate"
        else:
            label = "Unsafe"

        time_feats = cyclic_time_features(hour, dow)

        rows.append(
            {
                "lat": lat,
                "lon": lon,
                "area_code": area_code,
                "hour": hour,
                "day_of_week": dow,
                **time_feats,
                "lighting_score": attrs["lighting_score"],
                "crowd_density": attrs["crowd_density"],
                "cctv_coverage": attrs["cctv_coverage"],
                "streetlight_density": attrs["streetlight_density"],
                "footpath_quality": attrs["footpath_quality"],
                "infra_score": infra_score,
                "infra_risk": infra_risk,
                "isolation_index": iso_idx,
                "crime_risk_index": crime_risk_index,
                "time_of_day_risk": tod_risk,
                "lighting_risk": lighting_risk,
                "dist_metro_km": poi_dist["metro"],
                "dist_bus_km": poi_dist["bus_stop"],
                "dist_hospital_km": poi_dist["hospital"],
                "dist_police_km": poi_dist["police"],
                "composite_risk_score": composite,
                "label": label,
            }
        )

    return pd.DataFrame(rows)
