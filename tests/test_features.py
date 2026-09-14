"""Feature-engineering invariants."""
from __future__ import annotations

import math

import pytest

from app.features import (
    BASE_FEATURE_COLUMNS,
    build_feature_row,
    composite_risk,
    composite_weights,
    cyclic_time_features,
    infra_score_from_components,
    isolation_index,
    model_feature_columns,
    time_of_day_risk,
)


def test_cyclic_features_stay_in_range():
    for hour in range(24):
        feats = cyclic_time_features(hour, 3)
        assert math.isclose(feats["hour_sin"] ** 2 + feats["hour_cos"] ** 2, 1.0, abs_tol=1e-9)


def test_cyclic_encoding_wraps_around_midnight():
    """23:00 and 00:00 must be closer than 00:00 and 12:00 -- the whole point."""

    def distance(a, b):
        return math.hypot(a["hour_sin"] - b["hour_sin"], a["hour_cos"] - b["hour_cos"])

    at_2300 = cyclic_time_features(23, 0)
    at_0000 = cyclic_time_features(0, 0)
    at_1200 = cyclic_time_features(12, 0)
    assert distance(at_2300, at_0000) < distance(at_0000, at_1200)


def test_cyclic_encoding_wraps_around_the_week():
    def distance(a, b):
        return math.hypot(a["dow_sin"] - b["dow_sin"], a["dow_cos"] - b["dow_cos"])

    sunday = cyclic_time_features(12, 6)
    monday = cyclic_time_features(12, 0)
    wednesday = cyclic_time_features(12, 3)
    assert distance(sunday, monday) < distance(monday, wednesday)


@pytest.mark.parametrize("hour", range(24))
def test_time_of_day_risk_is_bounded(hour):
    assert 0.0 <= time_of_day_risk(hour) <= 1.0


def test_time_of_day_risk_peaks_at_night():
    assert time_of_day_risk(2) > time_of_day_risk(12)
    assert time_of_day_risk(23) > time_of_day_risk(9)


def test_time_of_day_risk_is_continuous_across_midnight():
    assert time_of_day_risk(23.9) == pytest.approx(time_of_day_risk(24.0), abs=0.02)
    assert time_of_day_risk(24.0) == pytest.approx(time_of_day_risk(0.0), abs=1e-9)


def test_infra_score_is_bounded_and_monotone():
    assert 0 <= infra_score_from_components(0.8, 0.6, 0.9) <= 1
    assert infra_score_from_components(0, 0, 0) == pytest.approx(0.0)
    assert infra_score_from_components(1, 1, 1) == pytest.approx(1.0)
    assert infra_score_from_components(0.9, 0.5, 0.5) > infra_score_from_components(0.1, 0.5, 0.5)


def test_isolation_is_high_when_sparse_and_far():
    far = {"metro": 3.0, "bus_stop": 3.0, "hospital": 3.0, "police": 3.0}
    near = {"metro": 0.1, "bus_stop": 0.1, "hospital": 0.1, "police": 0.1}
    assert isolation_index(0.05, far) > isolation_index(0.95, near)


def test_isolation_stays_in_the_unit_interval_at_the_extremes():
    far = {"metro": 9.0, "bus_stop": 9.0, "hospital": 9.0, "police": 9.0}
    near = dict.fromkeys(far, 0.0)
    assert isolation_index(0.0, far) == pytest.approx(1.0)
    assert isolation_index(1.0, near) == pytest.approx(0.0)


def test_isolation_compounds_rather_than_averaging():
    """Empty *and* far must exceed what a plain average of the two would give."""
    far = {"metro": 3.0, "bus_stop": 3.0, "hospital": 3.0, "police": 3.0}
    near = dict.fromkeys(far, 0.0)
    both_bad = isolation_index(0.0, far)
    one_bad = max(isolation_index(0.0, near), isolation_index(1.0, far))
    assert both_bad > 2 * one_bad


def test_feature_row_contains_every_model_column():
    row = build_feature_row(
        hour=22,
        day_of_week=5,
        lighting_score=0.4,
        crowd_density=0.2,
        cctv_coverage=0.3,
        streetlight_density=0.3,
        footpath_quality=0.5,
        poi_distances_km={"metro": 1.2, "bus_stop": 0.4, "hospital": 2.1, "police": 1.8},
    )
    for column in BASE_FEATURE_COLUMNS:
        assert column in row


def test_crime_feature_appears_only_when_supplied():
    kwargs = {
        "hour": 10, "day_of_week": 1, "lighting_score": 0.5, "crowd_density": 0.5,
        "cctv_coverage": 0.5, "streetlight_density": 0.5, "footpath_quality": 0.5,
        "poi_distances_km": {},
    }
    assert "crime_risk_index" not in build_feature_row(**kwargs)
    assert "crime_risk_index" in build_feature_row(**kwargs, crime_risk_index=0.4)


def test_feature_contract_tracks_crime_availability():
    assert "crime_risk_index" not in model_feature_columns(with_crime=False)
    assert "crime_risk_index" in model_feature_columns(with_crime=True)


def test_composite_weights_always_sum_to_one():
    """Dropping crime must redistribute its weight, not shrink the scale."""
    for with_crime in (True, False):
        assert sum(composite_weights(with_crime).values()) == pytest.approx(1.0)


def test_composite_risk_is_bounded_by_its_inputs():
    weights = composite_weights(with_crime=False)
    worst = dict.fromkeys(weights, 1.0)
    best = dict.fromkeys(weights, 0.0)
    assert composite_risk(worst, weights) == pytest.approx(1.0)
    assert composite_risk(best, weights) == pytest.approx(0.0)


# --- Lighting blend --------------------------------------------------------


def test_lighting_rests_on_tags_where_no_lamps_are_mapped():
    """
    OpenStreetMap has only a few hundred `highway=street_lamp` nodes for all
    of Delhi, so most places have none. A fixed blend would drag every one of
    them toward darkness on the strength of a zero that means "unsurveyed",
    not "unlit".
    """
    from app.features import blend_lighting

    assert blend_lighting(0.8, 0.0, lamp_count=0) == pytest.approx(0.8)
    assert blend_lighting(0.2, 0.9, lamp_count=0) == pytest.approx(0.2)


def test_mapped_lamps_earn_their_way_into_the_lighting_score():
    from app.features import LAMP_EVIDENCE_SATURATION, blend_lighting

    no_evidence = blend_lighting(0.5, 1.0, lamp_count=0)
    some_evidence = blend_lighting(0.5, 1.0, lamp_count=2)
    full_evidence = blend_lighting(0.5, 1.0, lamp_count=LAMP_EVIDENCE_SATURATION)
    assert no_evidence < some_evidence < full_evidence


def test_lamp_weight_saturates():
    from app.features import LAMP_EVIDENCE_SATURATION, blend_lighting

    at_saturation = blend_lighting(0.5, 1.0, lamp_count=LAMP_EVIDENCE_SATURATION)
    far_beyond = blend_lighting(0.5, 1.0, lamp_count=500)
    assert at_saturation == pytest.approx(far_beyond)


@pytest.mark.parametrize("lamp_count", [0, 1, 5, 100])
@pytest.mark.parametrize("lit_share,density", [(0, 0), (1, 1), (0, 1), (1, 0), (0.4, 0.7)])
def test_lighting_stays_in_the_unit_interval(lit_share, density, lamp_count):
    from app.features import blend_lighting

    assert 0.0 <= blend_lighting(lit_share, density, lamp_count) <= 1.0


# --- Infrastructure score --------------------------------------------------


def test_unobserved_infra_components_are_excluded_not_scored_as_zero():
    """
    Regression test for the bug that skewed the whole city. OpenStreetMap
    maps a streetlight in ~1.6% of Delhi's cells and a camera in ~5%, so
    averaging their zeros in put mean infrastructure quality at 0.09 across
    the entire city and pushed 59% of it into the "Unsafe" bucket -- a
    measure of survey effort, not of Delhi.
    """
    from app.features import infra_score_from_components

    nothing_mapped = {
        "streetlight_density": False,
        "cctv_coverage": False,
        "footpath_quality": True,
    }
    good_footpaths = infra_score_from_components(0.0, 0.0, 0.9, nothing_mapped)
    scored_as_zeros = infra_score_from_components(0.0, 0.0, 0.9)

    assert good_footpaths == pytest.approx(0.9)
    assert scored_as_zeros == pytest.approx(0.27)
    assert good_footpaths > scored_as_zeros


def test_infra_falls_back_to_the_prior_when_nothing_was_observed():
    from app.features import infra_score_from_components

    none_observed = dict.fromkeys(
        ("streetlight_density", "cctv_coverage", "footpath_quality"), False
    )
    assert infra_score_from_components(0.0, 0.0, 0.0, none_observed, prior=0.42) == pytest.approx(
        0.42
    )


def test_infra_trusts_every_component_when_observation_is_unspecified():
    """A value a caller supplied first-hand is trusted, not treated as missing."""
    from app.features import infra_score_from_components

    assert infra_score_from_components(1.0, 1.0, 1.0) == pytest.approx(1.0)
    assert infra_score_from_components(0.0, 0.0, 0.0) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "observed",
    [
        None,
        {"streetlight_density": True, "cctv_coverage": False, "footpath_quality": True},
        dict.fromkeys(("streetlight_density", "cctv_coverage", "footpath_quality"), False),
    ],
)
def test_infra_score_stays_in_the_unit_interval(observed):
    from app.features import infra_score_from_components

    for values in [(0, 0, 0), (1, 1, 1), (0.3, 0.9, 0.1)]:
        assert 0.0 <= infra_score_from_components(*values, observed) <= 1.0
