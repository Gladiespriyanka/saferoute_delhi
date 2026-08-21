"""
Domain-specific feature engineering for SafeRoute Delhi.

This module is deliberately dependency-light (numpy/pandas only) so it can
be unit tested in isolation from the model, the API, and any external
service.
"""
from __future__ import annotations

import math

MODEL_FEATURE_COLUMNS = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "lighting_score",
    "crowd_density",
    "cctv_coverage",
    "streetlight_density",
    "footpath_quality",
    "infra_score",
    "infra_risk",
    "isolation_index",
    "crime_risk_index",
    "time_of_day_risk",
    "lighting_risk",
    "dist_metro_km",
    "dist_bus_km",
    "dist_hospital_km",
    "dist_police_km",
]


def cyclic_time_features(hour: int, day_of_week: int) -> dict:
    """
    Encode hour-of-day and day-of-week cyclically so the model understands
    that 23:00 and 00:00 are adjacent (a plain integer encoding would treat
    them as maximally distant), and likewise Sunday(6)/Monday(0).
    """
    hour_angle = 2 * math.pi * (hour % 24) / 24
    dow_angle = 2 * math.pi * (day_of_week % 7) / 7
    return {
        "hour_sin": math.sin(hour_angle),
        "hour_cos": math.cos(hour_angle),
        "dow_sin": math.sin(dow_angle),
        "dow_cos": math.cos(dow_angle),
    }


def time_of_day_risk(hour: int) -> float:
    """
    Piecewise risk curve over the 24h clock, peaking in the late-night /
    early-morning "isolation" window and troughing at midday.

    Values are in [0, 1]. This is intentionally a smooth, explainable
    curve (not learned) so it can be quoted directly in explanations
    ("this route is scored during a historically higher-risk night window").
    """
    # Anchor points (hour -> risk), interpolated linearly between them.
    anchors = [
        (0, 0.85),
        (2, 0.95),
        (5, 0.75),
        (7, 0.35),
        (9, 0.20),
        (12, 0.15),
        (15, 0.20),
        (18, 0.40),
        (20, 0.60),
        (22, 0.80),
        (24, 0.85),
    ]
    h = hour % 24
    for (h0, r0), (h1, r1) in zip(anchors, anchors[1:]):
        if h0 <= h <= h1:
            if h1 == h0:
                return r0
            frac = (h - h0) / (h1 - h0)
            return r0 + frac * (r1 - r0)
    return 0.5  # unreachable given anchors span 0-24, kept as a safe fallback


def infra_score_from_components(
    streetlight_density: float, cctv_coverage: float, footpath_quality: float
) -> float:
    """Weighted infrastructure quality score in [0, 1]; higher = better infra."""
    return float(
        0.35 * streetlight_density + 0.35 * cctv_coverage + 0.30 * footpath_quality
    )


def isolation_index(crowd_density: float, poi_distances_km: dict) -> float:
    """
    Estimate how "isolated" a point is: low crowd density combined with long
    distances to the nearest metro/bus/hospital/police point pushes this
    toward 1 (highly isolated); busy, well-served locations trend to 0.

    poi_distances_km: dict with keys among {metro, bus_stop, hospital, police}
    """
    if poi_distances_km:
        # Normalize each distance with a soft cap of 3km (beyond that,
        # marginal isolation stops increasing much - diminishing returns).
        norm_dists = [min(d, 3.0) / 3.0 for d in poi_distances_km.values()]
        avg_dist_factor = sum(norm_dists) / len(norm_dists)
    else:
        avg_dist_factor = 0.5

    crowd_factor = 1 - crowd_density
    # Isolation is high when BOTH crowd is sparse AND POIs are far -
    # a multiplicative-leaning blend captures that interaction better than
    # a pure average (a busy area right next to no POIs is still not very
    # isolated because of foot traffic, and vice versa).
    return float(0.5 * crowd_factor + 0.5 * avg_dist_factor + 0.2 * crowd_factor * avg_dist_factor) / 1.2


def build_feature_row(
    *,
    hour: int,
    day_of_week: int,
    lighting_score: float,
    crowd_density: float,
    cctv_coverage: float,
    streetlight_density: float,
    footpath_quality: float,
    crime_risk_index: float,
    poi_distances_km: dict,
) -> dict:
    """Assemble the full feature dict for one segment, ready for the model."""
    time_feats = cyclic_time_features(hour, day_of_week)
    infra_score = infra_score_from_components(streetlight_density, cctv_coverage, footpath_quality)
    infra_risk = 1 - infra_score
    iso_idx = isolation_index(crowd_density, poi_distances_km)
    tod_risk = time_of_day_risk(hour)
    lighting_risk = 1 - lighting_score

    row = {
        **time_feats,
        "lighting_score": lighting_score,
        "crowd_density": crowd_density,
        "cctv_coverage": cctv_coverage,
        "streetlight_density": streetlight_density,
        "footpath_quality": footpath_quality,
        "infra_score": infra_score,
        "infra_risk": infra_risk,
        "isolation_index": iso_idx,
        "crime_risk_index": crime_risk_index,
        "time_of_day_risk": tod_risk,
        "lighting_risk": lighting_risk,
        "dist_metro_km": poi_distances_km.get("metro", 5.0),
        "dist_bus_km": poi_distances_km.get("bus_stop", 5.0),
        "dist_hospital_km": poi_distances_km.get("hospital", 5.0),
        "dist_police_km": poi_distances_km.get("police", 5.0),
    }
    return row
