"""
Explainability layer for SafeRoute Delhi.

Two complementary explanation styles are produced for every prediction:

1. SHAP-based feature contributions - a quantitative, model-faithful
   breakdown of which engineered features pushed the risk score up or down.
   SHAP's TreeExplainer is used against the underlying RandomForest inside
   the CalibratedClassifierCV. If the `shap` package is unavailable (or
   errors out, e.g. version mismatch), we fall back to a permutation-style
   approximation so the API never hard-fails on explanation generation.

2. Rule-based grouped reasons - short, human-readable sentences grouped
   into environment / infrastructure / history / time buckets. These are
   generated directly from the engineered feature values (not the model),
   so they remain stable and easy to QA even as the model is retrained.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.features import MODEL_FEATURE_COLUMNS

try:
    import shap  # type: ignore

    _SHAP_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when shap isn't installed
    _SHAP_AVAILABLE = False

# Building a shap.TreeExplainer walks every tree in the forest to precompute
# structure info -- with 300 trees that's expensive (roughly a couple of
# seconds observed locally) and was previously being redone from scratch
# for every single segment of every single request. Since the underlying
# RandomForest doesn't change between requests (only retraining swaps it),
# we cache one explainer per model instance and reuse it.
_explainer_cache: dict[int, "shap.TreeExplainer"] = {}


def _get_cached_explainer(base_estimator):
    key = id(base_estimator)
    explainer = _explainer_cache.get(key)
    if explainer is None:
        explainer = shap.TreeExplainer(base_estimator)
        # Keep the cache from growing unboundedly across retrains/reloads.
        _explainer_cache.clear()
        _explainer_cache[key] = explainer
    return explainer


_FEATURE_DISPLAY_NAMES = {
    "hour_sin": "time of day (cyclic)",
    "hour_cos": "time of day (cyclic)",
    "dow_sin": "day of week (cyclic)",
    "dow_cos": "day of week (cyclic)",
    "lighting_score": "street lighting",
    "crowd_density": "crowd density",
    "cctv_coverage": "CCTV coverage",
    "streetlight_density": "streetlight density",
    "footpath_quality": "footpath quality",
    "infra_score": "overall infrastructure quality",
    "infra_risk": "infrastructure risk",
    "isolation_index": "isolation (low crowd + far from help)",
    "crime_risk_index": "historical crime risk",
    "time_of_day_risk": "time-of-day risk",
    "lighting_risk": "poor lighting",
    "dist_metro_km": "distance to nearest metro",
    "dist_bus_km": "distance to nearest bus stop",
    "dist_hospital_km": "distance to nearest hospital",
    "dist_police_km": "distance to nearest police post",
}


def _permutation_fallback_contributions(model, feature_row: dict) -> list[dict]:
    """
    A lightweight, dependency-free stand-in for SHAP: perturb each feature
    toward a "neutral" reference value and measure the change in predicted
    risk score. Not as theoretically grounded as SHAP but gives a directionally
    sound, always-available explanation.
    """
    from app.model import predict_segment

    base = predict_segment(model, feature_row)
    base_score = base["risk_score"]

    neutral_reference = {
        "hour_sin": 0.0, "hour_cos": 0.0, "dow_sin": 0.0, "dow_cos": 0.0,
        "lighting_score": 0.6, "crowd_density": 0.5, "cctv_coverage": 0.6,
        "streetlight_density": 0.6, "footpath_quality": 0.6, "infra_score": 0.6,
        "infra_risk": 0.4, "isolation_index": 0.4, "crime_risk_index": 0.3,
        "time_of_day_risk": 0.4, "lighting_risk": 0.4, "dist_metro_km": 1.0,
        "dist_bus_km": 0.5, "dist_hospital_km": 1.5, "dist_police_km": 1.5,
    }

    contributions = []
    for feat in MODEL_FEATURE_COLUMNS:
        perturbed = dict(feature_row)
        perturbed[feat] = neutral_reference.get(feat, feature_row.get(feat, 0))
        perturbed_score = predict_segment(model, perturbed)["risk_score"]
        # If replacing this feature with a neutral value LOWERS the score,
        # then the original value was pushing risk UP.
        delta = base_score - perturbed_score
        contributions.append({"feature": feat, "contribution": float(delta)})
    return contributions


def compute_feature_contributions(model, feature_row: dict, top_k: int = 5) -> list[dict]:
    """
    Return the top_k most influential features for this prediction, each as
    {feature, contribution, direction}. Positive contribution = pushed risk
    up; negative = pushed risk down.
    """
    if _SHAP_AVAILABLE:
        try:
            # CalibratedClassifierCV wraps one RandomForest per CV fold; use
            # the first fold's underlying estimator as a representative
            # explainer target (calibration doesn't change feature ranking).
            base_estimator = model.calibrated_classifiers_[0].estimator
            explainer = _get_cached_explainer(base_estimator)
            X = pd.DataFrame([feature_row])[MODEL_FEATURE_COLUMNS]
            shap_values = explainer.shap_values(X)
            classes = list(getattr(base_estimator, "classes_", []))
            unsafe_idx = classes.index("Unsafe") if "Unsafe" in classes else -1

            # SHAP's return shape here has varied across versions:
            #   - older versions: a list of one (n_samples, n_features) array
            #     per class
            #   - newer versions: a single (n_samples, n_features, n_classes)
            #     ndarray
            # Handling only the first shape (as a prior version of this code
            # did) silently breaks on the second: indexing/iterating it
            # yields per-class arrays instead of scalars, `float(v)` then
            # raises, and every request falls back to the ~20x slower
            # permutation method below. Handle both explicitly instead of
            # relying on a bare `except Exception` to paper over it.
            if isinstance(shap_values, list):
                per_feature = np.asarray(shap_values[unsafe_idx])[0]
            else:
                arr = np.asarray(shap_values)
                if arr.ndim == 3:
                    # (n_samples, n_features, n_classes)
                    per_feature = arr[0, :, unsafe_idx]
                else:
                    # (n_samples, n_features) - already single-class/regression-shaped
                    per_feature = arr[0]

            contributions = [
                {"feature": f, "contribution": float(v)}
                for f, v in zip(MODEL_FEATURE_COLUMNS, per_feature)
            ]
        except Exception:
            contributions = _permutation_fallback_contributions(model, feature_row)
    else:
        contributions = _permutation_fallback_contributions(model, feature_row)

    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    top = contributions[:top_k]
    for c in top:
        c["direction"] = "increases_risk" if c["contribution"] > 0 else "decreases_risk"
        c["feature"] = _FEATURE_DISPLAY_NAMES.get(c["feature"], c["feature"])
    return top


def generate_grouped_reasons(feature_row: dict, crime_context: dict | None = None) -> dict:
    """
    Produce short, human-readable reasons grouped by category, derived
    directly from feature values via simple, auditable thresholds.
    """
    env: list[str] = []
    infra: list[str] = []
    history: list[str] = []
    time_reasons: list[str] = []

    if feature_row["lighting_score"] < 0.35:
        env.append("Street lighting in this stretch is notably poor.")
    elif feature_row["lighting_score"] > 0.75:
        env.append("Street lighting here is generally good.")

    if feature_row["crowd_density"] < 0.25:
        env.append("Foot traffic is typically sparse here, reducing passive surveillance.")
    elif feature_row["crowd_density"] > 0.7:
        env.append("This area usually has a lot of foot traffic.")

    if feature_row["isolation_index"] > 0.6:
        env.append("This point is relatively isolated: far from metro/bus/police/hospital and low crowd density.")

    if feature_row["cctv_coverage"] < 0.3:
        infra.append("CCTV coverage is limited along this segment.")
    if feature_row["streetlight_density"] < 0.3:
        infra.append("Streetlight density is below average.")
    if feature_row["footpath_quality"] < 0.3:
        infra.append("Footpath infrastructure is in poor condition.")
    if feature_row["infra_score"] > 0.75:
        infra.append("Overall infrastructure quality here is strong.")

    if feature_row.get("dist_police_km", 5) > 2.0:
        infra.append("The nearest police post is over 2km away.")
    if feature_row.get("dist_hospital_km", 5) > 3.0:
        infra.append("The nearest hospital is over 3km away.")

    if feature_row["crime_risk_index"] > 0.6:
        history.append("This area has a higher-than-average historical crime index.")
    elif feature_row["crime_risk_index"] < 0.25:
        history.append("This area has a comparatively low historical crime index.")
    if crime_context and crime_context.get("reported_incidents_month", 0) > 20:
        history.append(
            f"Around {crime_context['reported_incidents_month']} incidents were reported here in the last month."
        )
    if crime_context and crime_context.get("recent_audit_count", 0) > 0:
        history.append(
            f"{crime_context['recent_audit_count']} community safety audit(s) have been logged for this area."
        )

    if feature_row["time_of_day_risk"] > 0.7:
        time_reasons.append("This falls within a historically higher-risk night/late-evening window.")
    elif feature_row["time_of_day_risk"] < 0.25:
        time_reasons.append("This falls within a historically lower-risk daytime window.")

    return {
        "environment": env,
        "infrastructure": infra,
        "history": history,
        "time": time_reasons,
    }
