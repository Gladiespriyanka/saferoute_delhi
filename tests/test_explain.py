"""Explanations: feature contributions and the rule-based reasons."""
from __future__ import annotations

import pytest

from app.explain import (
    _permutation_contributions,
    compute_feature_contributions,
    generate_grouped_reasons,
)
from app.features import build_feature_row

DARK_ISOLATED_LATE = build_feature_row(
    hour=2, day_of_week=5,
    lighting_score=0.05, crowd_density=0.05, cctv_coverage=0.05,
    streetlight_density=0.05, footpath_quality=0.05,
    poi_distances_km={"metro": 4.0, "bus_stop": 3.0, "hospital": 5.0, "police": 4.5},
)
BRIGHT_BUSY_MIDDAY = build_feature_row(
    hour=13, day_of_week=2,
    lighting_score=0.95, crowd_density=0.9, cctv_coverage=0.9,
    streetlight_density=0.9, footpath_quality=0.9,
    poi_distances_km={"metro": 0.2, "bus_stop": 0.1, "hospital": 0.5, "police": 0.4},
)


def _row_for(model, feature_row):
    return {name: feature_row[name] for name in model.feature_columns}


def test_shap_actually_runs_rather_than_silently_falling_back(trained_model):
    """
    Regression test. `_base_estimator` has to unwrap two layers:
    CalibratedClassifierCV's per-fold wrapper, and then scikit-learn 1.6+'s
    FrozenEstimator. FrozenEstimator forwards attribute lookups to what it
    wraps, so probing for `estimators_` finds it and stops one layer early --
    handing TreeExplainer an object it rejects. The failure was swallowed as
    "shap unavailable", so every request quietly used the far slower and less
    faithful permutation fallback while looking fine.
    """
    from app.explain import _SHAP_AVAILABLE, _base_estimator, _shap_contributions

    if not _SHAP_AVAILABLE:  # pragma: no cover - shap is an optional dependency
        pytest.skip("shap is not installed")

    base = _base_estimator(trained_model)
    assert base is not None
    assert type(base).__name__ == "RandomForestClassifier"
    assert _shap_contributions(trained_model, _row_for(trained_model, DARK_ISOLATED_LATE))


def test_contribution_directions_track_the_prediction(trained_model):
    """
    A place the model calls Safe should have features pushing risk *down*,
    and a dangerous one features pushing it up. Getting this backwards -- or
    all one sign regardless of input -- makes the explanation actively
    misleading.
    """
    safe = compute_feature_contributions(
        trained_model, _row_for(trained_model, BRIGHT_BUSY_MIDDAY), top_k=5
    )
    unsafe = compute_feature_contributions(
        trained_model, _row_for(trained_model, DARK_ISOLATED_LATE), top_k=5
    )
    assert sum(c["direction"] == "decreases_risk" for c in safe) >= 3
    assert sum(c["direction"] == "increases_risk" for c in unsafe) >= 3


def test_contributions_are_returned_and_ranked(trained_model):
    contributions = compute_feature_contributions(
        trained_model, _row_for(trained_model, DARK_ISOLATED_LATE), top_k=5
    )
    assert 1 <= len(contributions) <= 5
    magnitudes = [abs(c["contribution"]) for c in contributions]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_contributions_are_labelled_with_a_direction(trained_model):
    for contribution in compute_feature_contributions(
        trained_model, _row_for(trained_model, DARK_ISOLATED_LATE)
    ):
        assert contribution["direction"] in ("increases_risk", "decreases_risk")
        if contribution["contribution"] > 0:
            assert contribution["direction"] == "increases_risk"
        else:
            assert contribution["direction"] == "decreases_risk"


def test_contributions_use_readable_feature_names(trained_model):
    names = {
        c["feature"]
        for c in compute_feature_contributions(
            trained_model, _row_for(trained_model, DARK_ISOLATED_LATE), top_k=20
        )
    }
    assert not names & set(trained_model.feature_columns), "raw column names leaked to the client"


def test_permutation_fallback_works_without_shap(trained_model):
    """The API must still explain itself when shap is missing or broken."""
    contributions = _permutation_contributions(
        trained_model, _row_for(trained_model, DARK_ISOLATED_LATE)
    )
    assert len(contributions) == len(trained_model.feature_columns)
    assert any(abs(c["contribution"]) > 0 for c in contributions)


def test_explanations_survive_a_broken_shap(trained_model, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("shap version mismatch")

    monkeypatch.setattr("app.explain._shap_contributions", explode)
    assert compute_feature_contributions(
        trained_model, _row_for(trained_model, DARK_ISOLATED_LATE)
    )


# --- Rule-based reasons ----------------------------------------------------


def test_a_dark_isolated_point_is_flagged_everywhere(trained_model):
    reasons = generate_grouped_reasons(DARK_ISOLATED_LATE, {"osm_coverage": 0.8})
    assert any("lit" in r for r in reasons["environment"])
    assert any("CCTV" in r for r in reasons["infrastructure"])
    assert any("isolated" in r for r in reasons["environment"])
    assert any("late-night" in r for r in reasons["time"])


def test_a_bright_busy_midday_point_reads_positively():
    reasons = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, {"osm_coverage": 0.9})
    assert any("well covered" in r for r in reasons["environment"])
    assert any("daytime" in r for r in reasons["time"])
    assert not any("isolated" in r for r in reasons["environment"])


def test_reasons_say_mapped_not_absent():
    """
    OSM under-mapping is common in Delhi, so the wording must not claim there
    is no CCTV -- only that none is recorded.
    """
    reasons = generate_grouped_reasons(DARK_ISOLATED_LATE, {})
    text = " ".join(sum(reasons.values(), []))
    assert "mapped" in text or "recorded" in text


def test_thin_map_coverage_is_disclosed():
    reasons = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, {"osm_coverage": 0.05})
    assert any("coverage is thin" in r for r in reasons["history"])


def test_missing_crime_data_is_disclosed():
    reasons = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, {"crime_data_available": False})
    assert any("No district crime statistics" in r for r in reasons["history"])


def test_user_overrides_are_disclosed():
    reasons = generate_grouped_reasons(
        BRIGHT_BUSY_MIDDAY, {"overridden_attributes": ["lighting_score"]}
    )
    assert any("supplied by you" in r for r in reasons["environment"])


def test_audit_count_is_pluralised():
    one = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, {"recent_audit_count": 1})
    many = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, {"recent_audit_count": 3})
    assert any("1 community safety report " in r for r in one["history"])
    assert any("3 community safety reports" in r for r in many["history"])


@pytest.mark.parametrize("context", [None, {}])
def test_reasons_tolerate_missing_context(context):
    reasons = generate_grouped_reasons(BRIGHT_BUSY_MIDDAY, context)
    assert set(reasons) == {"environment", "infrastructure", "history", "time"}


# --- Merging reasons across segments ---------------------------------------


def test_merged_route_reasons_never_contradict_themselves():
    """
    A route whose first half is a busy market and second half is deserted used
    to report both "Few shops or amenities are mapped nearby" and "Plenty of
    shops and amenities nearby" in the same list -- each true of a different
    segment, but read as a flat contradiction. At most one statement per topic
    survives the merge now, taken from the worst segment.
    """
    from app.explain import generate_reason_items, merge_reason_items

    deserted = generate_reason_items(DARK_ISOLATED_LATE, {"osm_coverage": 0.8})
    busy = generate_reason_items(BRIGHT_BUSY_MIDDAY, {"osm_coverage": 0.8})

    merged = merge_reason_items([deserted, busy])
    environment = merged["environment"]
    assert any("Few shops" in r for r in environment)
    assert not any("Plenty of shops" in r for r in environment)
    # And the time bucket picks one window, not both.
    assert len(merged["time"]) == 1


def test_merge_keeps_the_worst_segments_wording_first():
    from app.explain import generate_reason_items, merge_reason_items

    merged = merge_reason_items(
        [
            generate_reason_items(BRIGHT_BUSY_MIDDAY, {}),
            generate_reason_items(DARK_ISOLATED_LATE, {}),
        ]
    )
    assert any("Plenty of shops" in r for r in merged["environment"])
    assert any("daytime" in r for r in merged["time"])


def test_merge_of_nothing_still_returns_every_group():
    from app.explain import merge_reason_items

    assert merge_reason_items([]) == {
        "environment": [],
        "infrastructure": [],
        "history": [],
        "time": [],
    }


def test_reason_items_carry_a_topic():
    from app.explain import generate_reason_items

    for items in generate_reason_items(DARK_ISOLATED_LATE, {}).values():
        for item in items:
            assert item["topic"] and item["text"]
