"""End-to-end service behaviour."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.service import ModelNotTrainedError, SafeRouteService, label_for_score
from tests.conftest import segment

DARK_AND_EMPTY = {
    "lighting_score": 0.02, "crowd_density": 0.01, "cctv_coverage": 0.0,
    "streetlight_density": 0.0, "footpath_quality": 0.05,
}
BRIGHT_AND_BUSY = {
    "lighting_score": 0.98, "crowd_density": 0.95, "cctv_coverage": 0.95,
    "streetlight_density": 0.95, "footpath_quality": 0.95,
}


def test_score_route_returns_a_complete_result(service):
    result = service.score_route(
        [segment(28.6139, 77.2090), segment(28.6200, 77.2150)], None, use_live_context=False
    )
    assert 0.0 <= result["overall_risk_score"] <= 1.0
    assert result["label"] in ("Safe", "Moderate", "Unsafe")
    assert 0.0 <= result["confidence"] <= 1.0
    assert 0.0 <= result["data_coverage"] <= 1.0
    assert len(result["segment_results"]) == 2
    assert result["grouped_reasons"].keys() == {
        "environment", "infrastructure", "history", "time"
    }


def test_label_matches_the_score_thresholds(service):
    result = service.score_route([segment(28.61, 77.20)], None, use_live_context=False)
    assert result["label"] == label_for_score(result["overall_risk_score"])


def test_a_dark_empty_street_scores_worse_than_a_bright_busy_one(service):
    risky = service.score_route(
        [segment(28.55, 77.05, **DARK_AND_EMPTY)], None, use_live_context=False
    )
    safe = service.score_route(
        [segment(28.55, 77.05, **BRIGHT_AND_BUSY)], None, use_live_context=False
    )
    assert risky["overall_risk_score"] > safe["overall_risk_score"]


def test_late_night_scores_worse_than_midday_at_the_same_place(service):
    where = [segment(28.61, 77.20)]
    midnight = service.score_route(
        where, datetime(2026, 6, 1, 2, 0), use_live_context=False
    )
    midday = service.score_route(where, datetime(2026, 6, 1, 13, 0), use_live_context=False)
    assert midnight["overall_risk_score"] > midday["overall_risk_score"]


def test_caller_supplied_attributes_override_the_measured_ones(service):
    measured = service.score_route([segment(28.61, 77.20)], None, use_live_context=False)
    overridden = service.score_route(
        [segment(28.61, 77.20, **DARK_AND_EMPTY)], None, use_live_context=False
    )
    assert overridden["overall_risk_score"] != measured["overall_risk_score"]
    reasons = " ".join(overridden["grouped_reasons"]["environment"])
    assert "supplied by you" in reasons


def test_route_risk_is_pulled_toward_its_worst_segment(service):
    """One dangerous stretch must raise the route above the plain mean."""
    segments = [segment(28.61, 77.20, **BRIGHT_AND_BUSY)] * 3 + [
        segment(28.61, 77.20, **DARK_AND_EMPTY)
    ]
    result = service.score_route(segments, None, use_live_context=False)
    scores = [r["risk_score"] for r in result["segment_results"]]
    assert result["overall_risk_score"] > sum(scores) / len(scores)
    assert result["overall_risk_score"] <= max(scores)
    assert result["worst_segment_index"] == 3


def test_empty_route_is_rejected(service):
    with pytest.raises(ValueError):
        service.score_route([], None, use_live_context=False)


def test_skipping_live_context_makes_no_network_calls(service, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("external API called despite use_live_context=False")

    monkeypatch.setattr("app.service.fetch_weather", explode)
    monkeypatch.setattr("app.service.fetch_traffic_context", explode)
    service.score_route([segment(28.61, 77.20)], None, use_live_context=False)


def test_a_failing_weather_api_does_not_break_a_prediction(service, monkeypatch):
    monkeypatch.setattr(
        "app.service.fetch_weather",
        lambda *_: {"data_available": False, "error": "boom", "notes": [], "adjustment": 0.0},
    )
    monkeypatch.setattr(
        "app.service.fetch_traffic_context",
        lambda *_: {"data_available": False, "congestion_ratio": None, "adjustment": 0.0},
    )
    result = service.score_route(
        [segment(28.61, 77.20), segment(28.62, 77.21)], None, use_live_context=True
    )
    sources = {a["source"]: a for a in result["context_adjustments"]}
    assert sources["weather"]["data_available"] is False
    assert sources["weather"]["adjustment"] == 0.0
    assert "unavailable" in sources["traffic"]["description"]


def test_compare_routes_recommends_the_safer_option(service):
    comparison = service.compare_routes(
        {
            "dark": [segment(28.55, 77.05, **DARK_AND_EMPTY)],
            "bright": [segment(28.55, 77.05, **BRIGHT_AND_BUSY)],
        },
        None,
        use_live_context=False,
    )
    assert comparison["recommended_route"] == "bright"


def test_compare_routes_needs_at_least_two(service):
    with pytest.raises(ValueError):
        service.compare_routes({"only": [segment(28.61, 77.20)]}, None, use_live_context=False)


# --- Crowdsourced audits ---------------------------------------------------


def test_feedback_is_stored_and_findable(service):
    result = service.submit_feedback(28.61, 77.20, rating=1, comment="Felt unsafe", when=None)
    nearby = service.nearby_audits(28.61, 77.20, radius_km=2.0)
    assert any(audit["audit_id"] == result["audit_id"] for audit in nearby)


def test_nearby_audits_respect_the_radius(service):
    service.submit_feedback(28.70, 77.30, rating=2, comment=None, when=None)
    assert service.nearby_audits(28.40, 76.90, radius_km=0.5) == []


def test_nearby_audits_come_back_nearest_first(service):
    service.submit_feedback(28.6100, 77.2000, rating=3, comment=None, when=None)
    service.submit_feedback(28.6150, 77.2000, rating=3, comment=None, when=None)
    distances = [a["distance_km"] for a in service.nearby_audits(28.61, 77.20, radius_km=10)]
    assert distances == sorted(distances)


def test_unsafe_reports_push_an_area_up_and_safe_reports_pull_it_down(service):
    area_before = service._area_audit_adjustment.get("AREA-05-05", 0.0)
    service.submit_feedback(*_point_in("AREA-05-05"), rating=1, comment=None, when=None)
    after_unsafe = service._area_audit_adjustment["AREA-05-05"]
    assert after_unsafe > area_before

    for _ in range(5):
        service.submit_feedback(*_point_in("AREA-05-05"), rating=5, comment=None, when=None)
    assert service._area_audit_adjustment["AREA-05-05"] < after_unsafe


def test_audit_adjustments_survive_a_restart(service):
    """
    Audits were persisted but the running average they fed was in-memory
    only, so every restart silently discarded the community's feedback.
    """
    lat, lon = _point_in("AREA-07-07")
    for _ in range(4):
        service.submit_feedback(lat, lon, rating=1, comment=None, when=None)
    expected = service._area_audit_adjustment["AREA-07-07"]
    assert expected > 0

    restarted = SafeRouteService()
    assert restarted._area_audit_adjustment.get("AREA-07-07") == pytest.approx(expected)


def test_predicting_without_a_model_raises_clearly(service, monkeypatch):
    monkeypatch.setattr(service, "model", None)
    with pytest.raises(ModelNotTrainedError):
        service.score_route([segment(28.61, 77.20)], None, use_live_context=False)


def test_status_reports_provenance(service):
    status = service.status()
    assert status["model_loaded"] is True
    assert status["feature_source"] == "synthetic"
    assert status["model_features"] > 0
    assert 0 <= status["test_accuracy"] <= 1


def _point_in(area_code: str) -> tuple[float, float]:
    from app.geo import cell_center

    _, row, col = area_code.split("-")
    return cell_center(int(row), int(col))
