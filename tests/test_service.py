import pytest

from app.schemas import GeoPoint, RouteSegmentInput
from app.service import SafeRouteService


@pytest.fixture(scope="module")
def service():
    svc = SafeRouteService(auto_bootstrap=True)
    if svc.model is None:
        svc.bootstrap()
    return svc


def make_segment(lat, lon, **kwargs):
    return RouteSegmentInput(point=GeoPoint(lat=lat, lon=lon), **kwargs)


def test_score_route_returns_valid_structure(service):
    segments = [make_segment(28.6139, 77.2090), make_segment(28.6200, 77.2150)]
    result = service.score_route(segments, None, use_live_context=False)
    assert 0.0 <= result["overall_risk_score"] <= 1.0
    assert result["label"] in ("Safe", "Moderate", "Unsafe")
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["segment_results"]) == 2
    assert "grouped_reasons" in result


def test_score_route_with_explicit_bad_attributes_is_riskier(service):
    dark_isolated = [
        make_segment(
            28.55, 77.05,
            lighting_score=0.05, crowd_density=0.02, cctv_coverage=0.0,
            streetlight_density=0.0, footpath_quality=0.1,
        )
    ]
    bright_busy = [
        make_segment(
            28.55, 77.05,
            lighting_score=0.95, crowd_density=0.9, cctv_coverage=0.9,
            streetlight_density=0.9, footpath_quality=0.9,
        )
    ]
    risky = service.score_route(dark_isolated, None, use_live_context=False)
    safe = service.score_route(bright_busy, None, use_live_context=False)
    assert risky["overall_risk_score"] > safe["overall_risk_score"]


def test_compare_routes_recommends_lower_risk(service):
    routes = {
        "dark": [make_segment(28.55, 77.05, lighting_score=0.05, crowd_density=0.02,
                               cctv_coverage=0.0, streetlight_density=0.0, footpath_quality=0.1)],
        "bright": [make_segment(28.55, 77.05, lighting_score=0.95, crowd_density=0.9,
                                 cctv_coverage=0.9, streetlight_density=0.9, footpath_quality=0.9)],
    }
    comparison = service.compare_routes(routes, None, use_live_context=False)
    assert comparison["recommended_route"] == "bright"


def test_submit_and_query_feedback(service):
    result = service.submit_feedback(28.61, 77.20, rating=1, comment="Felt unsafe", when=None)
    assert "audit_id" in result
    nearby = service.nearby_audits(28.61, 77.20, radius_km=2.0)
    assert any(a["audit_id"] == result["audit_id"] for a in nearby)


def test_missing_attributes_fall_back_to_synthetic(service):
    segments = [make_segment(28.61, 77.20)]  # no optional attrs supplied
    result = service.score_route(segments, None, use_live_context=False)
    assert result["overall_risk_score"] is not None
