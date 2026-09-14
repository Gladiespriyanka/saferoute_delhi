"""
Explainability.

Two complementary explanations accompany every prediction:

1. **Feature contributions** -- a quantitative, model-faithful breakdown of
   what pushed this segment's risk up or down, via SHAP's TreeExplainer
   against the RandomForest inside the calibrated wrapper. If `shap` is not
   installed or errors out, a permutation-style approximation takes over so
   a request never fails merely because explainability tooling is missing.

2. **Grouped reasons** -- short sentences bucketed into environment /
   infrastructure / history / time, generated from the feature values by
   simple auditable thresholds rather than from the model. They stay stable
   and easy to QA across retrains, and they are what actually gets read.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:
    import shap  # type: ignore

    _SHAP_AVAILABLE = True
except Exception:  # pragma: no cover - only when shap isn't installed
    _SHAP_AVAILABLE = False

try:  # scikit-learn >= 1.6
    from sklearn.frozen import FrozenEstimator

    _FROZEN_TYPES: tuple[type, ...] = (FrozenEstimator,)
except ImportError:  # pragma: no cover - scikit-learn < 1.6
    _FROZEN_TYPES = ()

# Constructing a TreeExplainer walks every tree in the forest to precompute
# structure information. With 400 trees that takes on the order of seconds,
# and it was previously redone from scratch for every segment of every
# request. The underlying forest only changes when a new model is loaded, so
# one explainer is cached per estimator identity.
_explainer_cache: dict[int, object] = {}

FEATURE_DISPLAY_NAMES = {
    "hour_sin": "time of day",
    "hour_cos": "time of day",
    "dow_sin": "day of week",
    "dow_cos": "day of week",
    "lighting_score": "street lighting",
    "crowd_density": "surrounding activity",
    "cctv_coverage": "CCTV coverage",
    "streetlight_density": "streetlight density",
    "footpath_quality": "footpath provision",
    "infra_score": "overall infrastructure quality",
    "isolation_index": "isolation (quiet and far from help)",
    "crime_risk_index": "district crime history",
    "time_of_day_risk": "time-of-day risk",
    "dist_metro_km": "distance to nearest metro",
    "dist_bus_km": "distance to nearest bus stop",
    "dist_hospital_km": "distance to nearest hospital",
    "dist_police_km": "distance to nearest police station",
}

# Reference values a feature is perturbed toward in the fallback explainer:
# roughly a median, unremarkable Delhi location at an unremarkable hour.
_NEUTRAL_REFERENCE = {
    "hour_sin": 0.0, "hour_cos": 0.0, "dow_sin": 0.0, "dow_cos": 0.0,
    "lighting_score": 0.5, "crowd_density": 0.5, "cctv_coverage": 0.4,
    "streetlight_density": 0.5, "footpath_quality": 0.5, "infra_score": 0.5,
    "isolation_index": 0.4, "crime_risk_index": 0.5, "time_of_day_risk": 0.4,
    "dist_metro_km": 1.0, "dist_bus_km": 0.4, "dist_hospital_km": 1.2,
    "dist_police_km": 1.5,
}


def _base_estimator(model):
    """
    Unwrap down to the raw RandomForest, or None if there isn't one.

    There are two layers to get through. CalibratedClassifierCV holds its
    per-fold wrappers in `calibrated_classifiers_`, and since scikit-learn
    1.6 the thing inside those is a `FrozenEstimator`, not the forest
    itself. Stopping at the first `.estimator` therefore hands SHAP a
    FrozenEstimator, which TreeExplainer rejects outright -- so *every*
    request fell through to the much slower permutation fallback, silently,
    because the failure was swallowed as "shap unavailable".
    """
    estimator = model.estimator
    calibrated = getattr(estimator, "calibrated_classifiers_", None)
    if calibrated:
        estimator = getattr(calibrated[0], "estimator", None)

    # FrozenEstimator must be unwrapped by type, not by probing attributes:
    # it forwards `__getattr__` to the estimator it wraps, so it answers
    # `hasattr(x, "estimators_")` with True while still being the wrong
    # object to hand TreeExplainer.
    for _ in range(4):
        if not isinstance(estimator, _FROZEN_TYPES):
            break
        estimator = getattr(estimator, "estimator", None)

    return estimator if estimator is not None and hasattr(estimator, "estimators_") else None


def _cached_explainer(base_estimator):
    key = id(base_estimator)
    explainer = _explainer_cache.get(key)
    if explainer is None:
        explainer = shap.TreeExplainer(base_estimator)
        # One model is live at a time; clearing first keeps the cache from
        # growing across retrains and reloads.
        _explainer_cache.clear()
        _explainer_cache[key] = explainer
    return explainer


def _shap_contributions(model, feature_row: dict) -> list[dict] | None:
    """SHAP values for the 'Unsafe' class, or None if unavailable."""
    base_estimator = _base_estimator(model)
    if base_estimator is None:
        return None

    classes = list(getattr(base_estimator, "classes_", []))
    if "Unsafe" not in classes:
        # Explaining an arbitrary class would silently mislabel every
        # direction in the response, which is worse than not explaining.
        log.debug("Base estimator has no 'Unsafe' class; skipping SHAP.")
        return None
    unsafe_index = classes.index("Unsafe")

    explainer = _cached_explainer(base_estimator)
    X = pd.DataFrame([feature_row])[model.feature_columns]
    values = explainer.shap_values(X)

    # SHAP's return shape varies by version: older releases give a list of
    # one (n_samples, n_features) array per class, newer ones a single
    # (n_samples, n_features, n_classes) ndarray. Handling only one shape
    # means the other silently falls through to the ~20x slower fallback on
    # every request, so both are handled explicitly.
    if isinstance(values, list):
        per_feature = np.asarray(values[unsafe_index])[0]
    else:
        array = np.asarray(values)
        per_feature = array[0, :, unsafe_index] if array.ndim == 3 else array[0]

    return [
        {"feature": name, "contribution": float(value)}
        for name, value in zip(model.feature_columns, per_feature, strict=False)
    ]


def _permutation_contributions(model, feature_row: dict) -> list[dict]:
    """
    Dependency-free stand-in for SHAP: move each feature to a neutral
    reference value and measure how the predicted risk changes.

    Less theoretically grounded than SHAP, but directionally sound and
    always available.
    """
    base_score = model.score_rows([feature_row])[0]["risk_score"]
    perturbed_rows = []
    for name in model.feature_columns:
        row = dict(feature_row)
        row[name] = _NEUTRAL_REFERENCE.get(name, feature_row.get(name, 0.0))
        perturbed_rows.append(row)

    # One batched predict rather than one call per feature.
    scored = model.score_rows(perturbed_rows)
    return [
        # Lowering the score by neutralising a feature means its real value
        # was pushing risk up.
        {"feature": name, "contribution": float(base_score - result["risk_score"])}
        for name, result in zip(model.feature_columns, scored, strict=False)
    ]


def compute_feature_contributions(model, feature_row: dict, top_k: int = 5) -> list[dict]:
    """
    The `top_k` most influential features for this prediction, each as
    {feature, contribution, direction}. Positive contribution pushed risk up.
    """
    contributions = None
    if _SHAP_AVAILABLE:
        try:
            contributions = _shap_contributions(model, feature_row)
        except Exception as exc:  # noqa: BLE001 - explanations must never fail a request
            log.debug("SHAP explanation failed (%s); using permutation fallback.", exc)
    if contributions is None:
        contributions = _permutation_contributions(model, feature_row)

    contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    return [
        {
            "feature": FEATURE_DISPLAY_NAMES.get(item["feature"], item["feature"]),
            "contribution": round(item["contribution"], 5),
            "direction": "increases_risk" if item["contribution"] > 0 else "decreases_risk",
        }
        for item in contributions[:top_k]
    ]


def generate_reason_items(feature_row: dict, context: dict | None = None) -> dict:
    """
    Human-readable reasons grouped by category, each tagged with the topic it
    is about, from simple auditable thresholds on the feature values.

    The topic tag exists so that merging several segments' reasons into one
    route-level list can keep at most one statement per topic. Without it a
    route produced things like "Few shops or amenities are mapped nearby" and
    "Plenty of shops and amenities nearby" side by side -- each true of a
    different segment, but read as a flat contradiction.

    Phrasing matters here too: OSM under-mapping is common in Delhi, so these
    say "no mapped CCTV" rather than "no CCTV", and lighting is only
    described where it was actually measured.
    """
    context = context or {}
    environment: list[dict] = []
    infrastructure: list[dict] = []
    history: list[dict] = []
    time_reasons: list[dict] = []

    def say(bucket: list[dict], topic: str, text: str) -> None:
        bucket.append({"topic": topic, "text": text})

    # Only speak about lighting where it was actually measured. OpenStreetMap
    # carries a `lit` tag for roughly a tenth of mapped Delhi; everywhere else
    # the score is the city-wide prior, and reporting that as "well lit" would
    # be asserting something nobody ever recorded.
    lighting_observed = context.get("lighting_observed")
    if context.get("overridden_lighting"):
        if feature_row["lighting_score"] < 0.35:
            say(environment, "lighting", "You reported this stretch as poorly lit.")
        elif feature_row["lighting_score"] > 0.75:
            say(environment, "lighting", "You reported this stretch as well lit.")
    elif lighting_observed is False:
        say(
            environment,
            "lighting",
            "No lighting data for this stretch, so it is scored on the city average.",
        )
    elif feature_row["lighting_score"] < 0.35:
        say(environment, "lighting", "Little of this stretch is recorded as lit.")
    elif feature_row["lighting_score"] > 0.75:
        say(environment, "lighting", "This stretch is well covered by recorded street lighting.")

    if feature_row["crowd_density"] < 0.25:
        say(
            environment,
            "crowd",
            "Few shops or amenities nearby, so this is usually a quiet street.",
        )
    elif feature_row["crowd_density"] > 0.7:
        say(
            environment,
            "crowd",
            "Plenty of shops and amenities nearby, so this is normally busy.",
        )

    if feature_row["isolation_index"] > 0.6:
        say(
            environment,
            "isolation",
            "Fairly isolated: quiet, and a way from the nearest metro, bus stop, hospital "
            "or police station.",
        )

    if feature_row["cctv_coverage"] < 0.3:
        say(infrastructure, "cctv", "Little mapped CCTV coverage along this segment.")
    if feature_row["streetlight_density"] > 0.5:
        say(
            infrastructure,
            "streetlights",
            "Streetlights are densely mapped along this stretch.",
        )
    if feature_row["footpath_quality"] < 0.3:
        say(infrastructure, "footpath", "Few dedicated footpaths are mapped here.")
    if feature_row["infra_score"] > 0.75:
        say(
            infrastructure,
            "infra_overall",
            "Overall walking infrastructure here is well provisioned.",
        )

    if feature_row.get("dist_police_km", 0) > 2.0:
        say(
            infrastructure,
            "police_distance",
            f"Nearest police station is about {feature_row['dist_police_km']:.1f} km away.",
        )
    if feature_row.get("dist_hospital_km", 0) > 3.0:
        say(
            infrastructure,
            "hospital_distance",
            f"Nearest hospital is about {feature_row['dist_hospital_km']:.1f} km away.",
        )
    if feature_row.get("dist_metro_km", 0) < 0.5:
        say(infrastructure, "metro_distance", "A metro station is within walking distance.")

    crime_risk = feature_row.get("crime_risk_index")
    if crime_risk is not None:
        if crime_risk > 0.6:
            say(history, "crime", "This district reports a higher-than-average crime rate.")
        elif crime_risk < 0.3:
            say(history, "crime", "This district reports a comparatively low crime rate.")
    elif context.get("crime_data_available") is False:
        say(
            history,
            "crime",
            "No district crime statistics are configured; this score reflects streets and "
            "isolation only.",
        )

    audit_count = context.get("recent_audit_count", 0)
    if audit_count:
        say(
            history,
            "audits",
            f"{audit_count} community safety report"
            f"{'' if audit_count == 1 else 's'} logged for this area.",
        )

    coverage = context.get("osm_coverage")
    if coverage is not None and coverage < 0.25:
        say(
            history,
            "coverage",
            "Map coverage is thin here, so these details are less reliable than usual.",
        )

    if context.get("overridden_attributes"):
        say(
            environment,
            "overrides",
            "Some values here were supplied by you rather than measured: "
            + ", ".join(
                FEATURE_DISPLAY_NAMES.get(name, name)
                for name in context["overridden_attributes"]
            )
            + ".",
        )

    if feature_row["time_of_day_risk"] > 0.7:
        say(
            time_reasons,
            "time",
            "This falls inside the late-night window, historically the riskiest.",
        )
    elif feature_row["time_of_day_risk"] < 0.25:
        say(time_reasons, "time", "This falls inside a daytime window, historically the safest.")

    return {
        "environment": environment,
        "infrastructure": infrastructure,
        "history": history,
        "time": time_reasons,
    }


def generate_grouped_reasons(feature_row: dict, context: dict | None = None) -> dict:
    """Plain-text form of `generate_reason_items`, for a single segment."""
    return {
        group: [item["text"] for item in items]
        for group, items in generate_reason_items(feature_row, context).items()
    }


def merge_reason_items(per_segment: list[dict]) -> dict:
    """
    Collapse several segments' reasons into one route-level list.

    Segments are expected worst-first, and at most one statement per topic
    survives -- so a route whose first half is busy and second half deserted
    reports the more relevant of the two rather than both, which read as a
    contradiction.
    """
    merged: dict[str, list[str]] = {
        "environment": [], "infrastructure": [], "history": [], "time": []
    }
    seen: set[tuple[str, str]] = set()
    for items in per_segment:
        for group, entries in items.items():
            for entry in entries:
                key = (group, entry["topic"])
                if key in seen:
                    continue
                seen.add(key)
                merged[group].append(entry["text"])
    return merged
