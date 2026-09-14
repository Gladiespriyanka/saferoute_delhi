"""HTTP contract: authentication, validation, payload shape, error handling."""
from __future__ import annotations

from tests.conftest import API_KEY

HEADERS = {"x-api-key": API_KEY}
POINT_A = {"point": {"lat": 28.61, "lon": 77.20}}
POINT_B = {"point": {"lat": 28.62, "lon": 77.21}}


# --- Health ----------------------------------------------------------------


def test_health_needs_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_provenance(client):
    """A client must be able to tell what data and model it is talking to."""
    body = client.get("/health").json()
    assert body["model_loaded"] is True
    assert body["feature_source"] in ("openstreetmap", "synthetic")
    assert body["crime_data"]
    assert body["grid_cells"] > 0
    assert set(body["poi_counts"]) == {"metro", "bus_stop", "hospital", "police"}
    assert 0 <= body["test_accuracy"] <= 1
    assert 0 <= body["calibration_error"] <= 1


# --- Authentication --------------------------------------------------------


def test_missing_api_key_is_401(client):
    response = client.post("/predict", json={"segments": [POINT_A], "use_live_context": False})
    assert response.status_code == 401
    assert "x-api-key" in response.json()["detail"]


def test_wrong_api_key_is_403(client):
    """401 vs 403 lets a client distinguish 'no key sent' from 'key rejected'."""
    response = client.post(
        "/predict",
        json={"segments": [POINT_A], "use_live_context": False},
        headers={"x-api-key": "nope"},
    )
    assert response.status_code == 403


def test_every_non_health_route_is_protected(client):
    assert client.post("/compare-routes", json={"routes": {}}).status_code in (401, 422)
    assert client.post("/feedback", json={}).status_code in (401, 422)
    assert client.get("/audits/nearby", params={"lat": 28.6, "lon": 77.2}).status_code == 401


# --- /predict --------------------------------------------------------------


def test_predict_happy_path(client):
    response = client.post(
        "/predict",
        json={"route_id": "test-route", "segments": [POINT_A, POINT_B],
              "use_live_context": False},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["route_id"] == "test-route"
    assert body["label"] in ("Safe", "Moderate", "Unsafe")
    assert 0 <= body["overall_risk_score"] <= 1
    assert 0 <= body["confidence"] <= 1
    assert 0 <= body["data_coverage"] <= 1
    assert len(body["segment_scores"]) == 2
    assert body["worst_segment"]["label"] in ("Safe", "Moderate", "Unsafe")
    assert body["top_feature_contributions"]
    assert set(body["grouped_reasons"]) == {
        "environment", "infrastructure", "history", "time"
    }


def test_predict_returns_per_class_probabilities_that_sum_to_one(client):
    body = client.post(
        "/predict",
        json={"segments": [POINT_A], "use_live_context": False},
        headers=HEADERS,
    ).json()
    probabilities = body["segment_scores"][0]["class_probabilities"]
    assert set(probabilities) == {"Safe", "Moderate", "Unsafe"}
    assert abs(sum(probabilities.values()) - 1.0) < 0.01


def test_predict_rejects_an_empty_segment_list(client):
    response = client.post("/predict", json={"segments": []}, headers=HEADERS)
    assert response.status_code == 422


def test_predict_rejects_an_impossible_coordinate(client):
    response = client.post(
        "/predict",
        json={"segments": [{"point": {"lat": 999, "lon": 77.2}}]},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_predict_rejects_an_out_of_range_override(client):
    response = client.post(
        "/predict",
        json={"segments": [{"point": {"lat": 28.6, "lon": 77.2}, "lighting_score": 4.2}]},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_predict_accepts_a_point_just_outside_the_study_area(client):
    """Routes run past the city edge; that must not be a hard failure."""
    response = client.post(
        "/predict",
        json={"segments": [{"point": {"lat": 28.95, "lon": 77.40}}], "use_live_context": False},
        headers=HEADERS,
    )
    assert response.status_code == 200


# --- /compare-routes -------------------------------------------------------


def test_compare_routes_happy_path(client):
    response = client.post(
        "/compare-routes",
        json={"routes": {"A": [POINT_A], "B": [POINT_B]}, "use_live_context": False},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommended_route"] in ("A", "B")
    assert len(body["results"]) == 2
    recommended = next(r for r in body["results"] if r["route_name"] == body["recommended_route"])
    assert recommended["overall_risk_score"] == min(
        r["overall_risk_score"] for r in body["results"]
    )


def test_compare_routes_needs_at_least_two(client):
    response = client.post(
        "/compare-routes", json={"routes": {"only": [POINT_A]}}, headers=HEADERS
    )
    assert response.status_code == 422


# --- Feedback --------------------------------------------------------------


def test_feedback_round_trips_through_nearby_audits(client):
    submitted = client.post(
        "/feedback",
        json={"point": {"lat": 28.61, "lon": 77.20}, "rating": 2, "comment": "Dim lighting"},
        headers=HEADERS,
    )
    assert submitted.status_code == 200
    audit_id = submitted.json()["audit_id"]

    found = client.get(
        "/audits/nearby",
        params={"lat": 28.61, "lon": 77.20, "radius_km": 2},
        headers=HEADERS,
    )
    assert found.status_code == 200
    assert any(audit["audit_id"] == audit_id for audit in found.json()["audits"])


def test_feedback_rejects_an_out_of_range_rating(client):
    response = client.post(
        "/feedback",
        json={"point": {"lat": 28.61, "lon": 77.20}, "rating": 9},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_nearby_audits_rejects_a_silly_radius(client):
    for radius in (0, -1, 500):
        response = client.get(
            "/audits/nearby",
            params={"lat": 28.61, "lon": 77.20, "radius_km": radius},
            headers=HEADERS,
        )
        assert response.status_code == 422, radius


# --- Reports with reasons, and reports along a route -----------------------


def test_feedback_accepts_structured_reasons(client):
    submitted = client.post(
        "/feedback",
        json={
            "point": {"lat": 28.6400, "lon": 77.2200},
            "rating": 2,
            "reasons": ["poorly_lit", "isolated", "poorly_lit"],
            "comment": "Two lamps out",
        },
        headers=HEADERS,
    )
    assert submitted.status_code == 200

    found = client.get(
        "/audits/nearby",
        params={"lat": 28.6400, "lon": 77.2200, "radius_km": 0.5},
        headers=HEADERS,
    ).json()
    mine = next(a for a in found["audits"] if a["audit_id"] == submitted.json()["audit_id"])
    assert mine["reasons"] == ["poorly_lit", "isolated"]  # de-duplicated, order kept
    assert mine["comment"] == "Two lamps out"


def test_feedback_rejects_an_unknown_reason(client):
    response = client.post(
        "/feedback",
        json={"point": {"lat": 28.64, "lon": 77.22}, "rating": 3, "reasons": ["aliens"]},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_audits_along_route_finds_reports_near_any_point(client):
    far_north = {"lat": 28.8400, "lon": 77.0900}
    client.post(
        "/feedback",
        json={"point": far_north, "rating": 1, "reasons": ["followed"]},
        headers=HEADERS,
    )
    response = client.post(
        "/audits/along-route",
        json={
            "points": [{"lat": 28.6400, "lon": 77.2200}, far_north],
            "radius_km": 0.5,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    reasons = {r for a in body["audits"] for r in a["reasons"]}
    assert {"followed", "poorly_lit"} <= reasons
    assert body["latest"] is not None
    timestamps = [a["timestamp"] for a in body["audits"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audits_along_route_ignores_distant_reports(client):
    response = client.post(
        "/audits/along-route",
        json={"points": [{"lat": 28.4100, "lon": 76.8600}], "radius_km": 0.3},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0
