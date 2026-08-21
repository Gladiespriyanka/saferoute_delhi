import pytest
from fastapi.testclient import TestClient

from app.main import app, service

client = TestClient(app)
HEADERS = {"x-api-key": "demo-key-123"}


@pytest.fixture(scope="module", autouse=True)
def ensure_model():
    if service.model is None:
        service.bootstrap()


def test_health_no_auth_required():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_predict_requires_api_key():
    payload = {"segments": [{"point": {"lat": 28.61, "lon": 77.20}}], "use_live_context": False}
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 401


def test_predict_rejects_bad_api_key():
    payload = {"segments": [{"point": {"lat": 28.61, "lon": 77.20}}], "use_live_context": False}
    resp = client.post("/predict", json=payload, headers={"x-api-key": "wrong"})
    assert resp.status_code == 403


def test_predict_happy_path():
    payload = {
        "route_id": "test-route-1",
        "segments": [{"point": {"lat": 28.61, "lon": 77.20}}, {"point": {"lat": 28.62, "lon": 77.21}}],
        "use_live_context": False,
    }
    resp = client.post("/predict", json=payload, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] in ("Safe", "Moderate", "Unsafe")
    assert 0 <= body["overall_risk_score"] <= 1
    assert len(body["segment_scores"]) == 2


def test_predict_validates_bad_input():
    payload = {"segments": []}  # min_length=1 violated
    resp = client.post("/predict", json=payload, headers=HEADERS)
    assert resp.status_code == 422


def test_compare_routes():
    payload = {
        "routes": {
            "A": [{"point": {"lat": 28.61, "lon": 77.20}}],
            "B": [{"point": {"lat": 28.55, "lon": 77.05}}],
        },
        "use_live_context": False,
    }
    resp = client.post("/compare-routes", json=payload, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_route"] in ("A", "B")
    assert len(body["results"]) == 2


def test_feedback_and_nearby_audits():
    fb_payload = {"point": {"lat": 28.61, "lon": 77.20}, "rating": 2, "comment": "Dim lighting"}
    resp = client.post("/feedback", json=fb_payload, headers=HEADERS)
    assert resp.status_code == 200
    audit_id = resp.json()["audit_id"]

    resp2 = client.get("/audits/nearby", params={"lat": 28.61, "lon": 77.20, "radius_km": 2}, headers=HEADERS)
    assert resp2.status_code == 200
    body = resp2.json()
    assert any(a["audit_id"] == audit_id for a in body["audits"])
