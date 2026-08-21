import math

from app.features import (
    build_feature_row,
    cyclic_time_features,
    infra_score_from_components,
    isolation_index,
    time_of_day_risk,
)


def test_cyclic_time_features_range():
    for h in range(24):
        feats = cyclic_time_features(h, 3)
        assert -1.0001 <= feats["hour_sin"] <= 1.0001
        assert -1.0001 <= feats["hour_cos"] <= 1.0001


def test_cyclic_time_wraps_around_midnight():
    # 23:00 and 00:00 should be much "closer" in cyclic space than 00:00 and 12:00
    f_2300 = cyclic_time_features(23, 0)
    f_0000 = cyclic_time_features(0, 0)
    f_1200 = cyclic_time_features(12, 0)

    def dist(a, b):
        return math.hypot(a["hour_sin"] - b["hour_sin"], a["hour_cos"] - b["hour_cos"])

    assert dist(f_2300, f_0000) < dist(f_0000, f_1200)


def test_time_of_day_risk_bounds():
    for h in range(24):
        r = time_of_day_risk(h)
        assert 0.0 <= r <= 1.0


def test_time_of_day_risk_night_higher_than_midday():
    assert time_of_day_risk(2) > time_of_day_risk(12)


def test_infra_score_bounds():
    score = infra_score_from_components(0.8, 0.6, 0.9)
    assert 0 <= score <= 1


def test_isolation_index_high_when_sparse_and_far():
    high = isolation_index(0.05, {"metro": 3.0, "bus_stop": 3.0, "hospital": 3.0, "police": 3.0})
    low = isolation_index(0.95, {"metro": 0.1, "bus_stop": 0.1, "hospital": 0.1, "police": 0.1})
    assert high > low
    assert 0 <= high <= 1
    assert 0 <= low <= 1


def test_build_feature_row_has_all_expected_keys():
    row = build_feature_row(
        hour=22,
        day_of_week=5,
        lighting_score=0.4,
        crowd_density=0.2,
        cctv_coverage=0.3,
        streetlight_density=0.3,
        footpath_quality=0.5,
        crime_risk_index=0.6,
        poi_distances_km={"metro": 1.2, "bus_stop": 0.4, "hospital": 2.1, "police": 1.8},
    )
    for key in [
        "hour_sin", "hour_cos", "dow_sin", "dow_cos", "infra_score", "infra_risk",
        "isolation_index", "crime_risk_index", "time_of_day_risk", "lighting_risk",
        "dist_metro_km", "dist_bus_km", "dist_hospital_km", "dist_police_km",
    ]:
        assert key in row
