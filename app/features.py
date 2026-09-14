"""
Feature engineering for SafeHerWay.

Deliberately dependency-light (stdlib + the config constants) so it can be
unit tested in isolation from the model, the API and any external service.

`crime_risk_index` is optional: when `data/delhi_district_crime.csv` has not
been populated it is absent from both the feature row and the model's
feature contract (see app/crime_data.py). `model_feature_columns()` is the
single source of truth for which columns a model was trained on, and the
list is persisted alongside the model artifact.
"""
from __future__ import annotations

import math

from app.config import COMPOSITE_WEIGHTS, POI_DISTANCE_SOFT_CAP_KM

# Columns every model sees, in a fixed order. `crime_risk_index` is appended
# by model_feature_columns() only when crime data is configured.
#
# Note what is deliberately *absent*: `infra_risk` and `lighting_risk`.
# Both are exactly `1 - <another column>`, so feeding them to the model
# alongside their complements adds no information, splits each feature's
# importance across two names, and makes SHAP attributions harder to read.
# They are still computed for the composite-risk formula and the rule-based
# explanations, which read more naturally in "risk" terms.
BASE_FEATURE_COLUMNS = (
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
    "isolation_index",
    "time_of_day_risk",
    "dist_metro_km",
    "dist_bus_km",
    "dist_hospital_km",
    "dist_police_km",
)

CRIME_FEATURE = "crime_risk_index"


def model_feature_columns(with_crime: bool) -> list[str]:
    """The ordered feature contract for a model trained with/without crime data."""
    columns = list(BASE_FEATURE_COLUMNS)
    if with_crime:
        columns.append(CRIME_FEATURE)
    return columns


def composite_weights(with_crime: bool) -> dict[str, float]:
    """
    Weights of the composite-risk formula used to bootstrap training labels.

    When crime data is not configured its weight is redistributed across the
    measured features in proportion to their existing weights, so the
    composite stays on the same [0, 1] scale instead of quietly topping out
    at 0.75.
    """
    if with_crime:
        return dict(COMPOSITE_WEIGHTS)
    remaining = {k: v for k, v in COMPOSITE_WEIGHTS.items() if k != CRIME_FEATURE}
    total = sum(remaining.values())
    return {k: v / total for k, v in remaining.items()}


def cyclic_time_features(hour: int, day_of_week: int) -> dict[str, float]:
    """
    Encode hour-of-day and day-of-week on a circle so the model understands
    that 23:00 and 00:00 are adjacent -- a plain integer encoding makes them
    maximally distant -- and likewise Sunday(6) and Monday(0).
    """
    hour_angle = 2 * math.pi * (hour % 24) / 24
    dow_angle = 2 * math.pi * (day_of_week % 7) / 7
    return {
        "hour_sin": math.sin(hour_angle),
        "hour_cos": math.cos(hour_angle),
        "dow_sin": math.sin(dow_angle),
        "dow_cos": math.cos(dow_angle),
    }


# Anchor points (hour -> risk) interpolated linearly between them, peaking
# in the late-night isolation window and troughing at midday. Intentionally
# a smooth, hand-specified curve rather than a learned one, so it can be
# quoted directly in an explanation.
_TIME_RISK_ANCHORS = (
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
)


def time_of_day_risk(hour: float) -> float:
    """Piecewise-linear risk over the 24h clock, in [0, 1]."""
    h = hour % 24
    for (h0, r0), (h1, r1) in zip(_TIME_RISK_ANCHORS, _TIME_RISK_ANCHORS[1:], strict=False):
        if h0 <= h <= h1:
            return r0 + (h - h0) / (h1 - h0) * (r1 - r0)
    # Unreachable: the anchors span [0, 24] and h is reduced mod 24.
    raise AssertionError(f"time_of_day_risk anchors do not cover hour {hour}")


# Number of mapped streetlights around a point at which their measured
# density is trusted at full weight. OpenStreetMap has only a few hundred
# `highway=street_lamp` nodes for the whole of Delhi, so most places have
# none at all.
LAMP_EVIDENCE_SATURATION = 5.0
MAX_LAMP_WEIGHT = 0.35


def blend_lighting(lit_share: float, lamp_density: float, lamp_count: float) -> float:
    """
    Combine the two lighting signals OpenStreetMap offers, weighted by how
    much evidence there actually is for each.

    `lit_share` -- the fraction of nearby road length tagged `lit=yes` --
    says *whether* streets here are lit. `lamp_density` says how thoroughly,
    but only where lamps have been mapped at all, and across Delhi they
    mostly have not.

    Blending them at a fixed ratio therefore punishes under-mapped areas:
    a well-lit street with no mapped lamp nodes would be dragged toward
    darkness by a zero it has no business being scored on. The lamp term's
    weight instead scales with the number of lamps actually observed, so a
    place with none rests entirely on its `lit` tags.
    """
    lamp_weight = MAX_LAMP_WEIGHT * min(1.0, max(0.0, lamp_count) / LAMP_EVIDENCE_SATURATION)
    blended = lit_share * (1.0 - lamp_weight) + lamp_density * lamp_weight
    return float(min(1.0, max(0.0, blended)))


INFRA_COMPONENT_WEIGHTS = {
    "streetlight_density": 0.35,
    "cctv_coverage": 0.35,
    "footpath_quality": 0.30,
}

# Infrastructure quality assumed where nothing at all was observed. Overridden
# by the data-derived value in artifacts/osm_scaling.json when present.
DEFAULT_INFRA_PRIOR = 0.5


def infra_score_from_components(
    streetlight_density: float,
    cctv_coverage: float,
    footpath_quality: float,
    observed: dict[str, bool] | None = None,
    prior: float = DEFAULT_INFRA_PRIOR,
) -> float:
    """
    Weighted infrastructure quality in [0, 1]; higher = better infrastructure.

    Components that were never observed are **excluded and the remaining
    weights renormalised**, rather than being averaged in as zeros.

    This is not a nicety. OpenStreetMap maps a streetlight in about 1.6% of
    Delhi's cells and a camera in about 5%, so averaging their zeros in gave
    a mean infrastructure score of 0.09 across the entire city -- an artifact
    of what has been surveyed, not a fact about Delhi. It pushed the
    bootstrapped labels so far that 59% of the city came out "Unsafe", which
    is both alarmist and useless for comparing one route against another.

    Judging a place on what can actually be seen, and falling back to a
    neutral prior only when nothing can, keeps the score meaning
    "infrastructure quality" rather than "OpenStreetMap survey effort".

    `observed=None` means all three are trusted -- which is right for values a
    caller supplied directly from their own observation.
    """
    values = {
        "streetlight_density": streetlight_density,
        "cctv_coverage": cctv_coverage,
        "footpath_quality": footpath_quality,
    }
    if observed is None:
        usable = INFRA_COMPONENT_WEIGHTS
    else:
        usable = {
            name: weight
            for name, weight in INFRA_COMPONENT_WEIGHTS.items()
            if observed.get(name, True)
        }

    total = sum(usable.values())
    if total <= 0:
        return float(min(1.0, max(0.0, prior)))
    return float(sum(weight * values[name] for name, weight in usable.items()) / total)


def isolation_index(crowd_density: float, poi_distances_km: dict[str, float]) -> float:
    """
    How isolated a point is, in [0, 1].

    Sparse foot traffic *combined with* long distances to the nearest
    metro/bus/hospital/police point pushes this toward 1; busy, well-served
    locations trend to 0. The small interaction term matters: a busy street
    with no nearby POIs is not very isolated because of the foot traffic,
    and an empty street next to a metro entrance is not very isolated
    because help is close. Only when both hold does isolation compound,
    which a plain average would miss.
    """
    if poi_distances_km:
        capped = [min(d, POI_DISTANCE_SOFT_CAP_KM) / POI_DISTANCE_SOFT_CAP_KM
                  for d in poi_distances_km.values()]
        distance_factor = sum(capped) / len(capped)
    else:
        distance_factor = 0.5

    crowd_factor = 1.0 - crowd_density
    # Divisor normalises the 0.5 + 0.5 + 0.2 maximum back onto [0, 1].
    return float(
        (0.5 * crowd_factor + 0.5 * distance_factor + 0.2 * crowd_factor * distance_factor) / 1.2
    )


def build_feature_row(
    *,
    hour: int,
    day_of_week: int,
    lighting_score: float,
    crowd_density: float,
    cctv_coverage: float,
    streetlight_density: float,
    footpath_quality: float,
    poi_distances_km: dict[str, float],
    crime_risk_index: float | None = None,
    observed: dict[str, bool] | None = None,
    infra_prior: float = DEFAULT_INFRA_PRIOR,
) -> dict[str, float]:
    """
    Assemble the full feature dict for one segment.

    Includes the two `*_risk` complements and the composite inputs even
    though the model does not consume them -- the rule-based explanations
    and the label-bootstrapping formula both read them.
    """
    infra_score = infra_score_from_components(
        streetlight_density, cctv_coverage, footpath_quality, observed, infra_prior
    )
    row: dict[str, float] = {
        **cyclic_time_features(hour, day_of_week),
        "lighting_score": lighting_score,
        "crowd_density": crowd_density,
        "cctv_coverage": cctv_coverage,
        "streetlight_density": streetlight_density,
        "footpath_quality": footpath_quality,
        "infra_score": infra_score,
        "infra_risk": 1.0 - infra_score,
        "lighting_risk": 1.0 - lighting_score,
        "isolation_index": isolation_index(crowd_density, poi_distances_km),
        "time_of_day_risk": time_of_day_risk(hour),
        "dist_metro_km": poi_distances_km.get("metro", 5.0),
        "dist_bus_km": poi_distances_km.get("bus_stop", 5.0),
        "dist_hospital_km": poi_distances_km.get("hospital", 5.0),
        "dist_police_km": poi_distances_km.get("police", 5.0),
    }
    if crime_risk_index is not None:
        row[CRIME_FEATURE] = crime_risk_index
    return row


def composite_risk(feature_row: dict[str, float], weights: dict[str, float]) -> float:
    """The transparent weighted risk formula used to bootstrap training labels."""
    return float(sum(weight * feature_row[name] for name, weight in weights.items()))
