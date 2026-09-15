"""Smart Escort Mode: session lifecycle, owner-token auth, and the public read."""
from __future__ import annotations

from datetime import timedelta

from tests.conftest import API_KEY

HEADERS = {"x-api-key": API_KEY}
START_BODY = {
    "destination": {"point": {"lat": 28.60, "lon": 77.20}, "label": "Home"},
    "check_in_interval_seconds": 120,
}


def _start(client):
    response = client.post("/escort/start", json=START_BODY, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    return body["trip_id"], body["owner_token"]


def test_start_requires_api_key(client):
    response = client.post("/escort/start", json=START_BODY)
    assert response.status_code == 401


def test_start_returns_distinct_trip_and_owner_token(client):
    trip_id, owner_token = _start(client)
    assert trip_id and owner_token
    assert trip_id != owner_token


def test_status_is_public_no_api_key_needed(client):
    trip_id, _ = _start(client)
    response = client.get(f"/escort/{trip_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_unknown_trip_is_404(client):
    response = client.get("/escort/does-not-exist")
    assert response.status_code == 404


def test_write_without_owner_token_is_forbidden(client):
    trip_id, _ = _start(client)
    response = client.post(
        f"/escort/{trip_id}/position", json={"point": {"lat": 28.61, "lon": 77.21}}, headers=HEADERS
    )
    assert response.status_code == 403


def test_write_with_wrong_owner_token_is_forbidden(client):
    trip_id, _ = _start(client)
    response = client.post(
        f"/escort/{trip_id}/position",
        json={"point": {"lat": 28.61, "lon": 77.21}},
        headers={**HEADERS, "x-owner-token": "wrong"},
    )
    assert response.status_code == 403


def test_position_update_is_visible_on_public_status(client):
    trip_id, owner_token = _start(client)
    client.post(
        f"/escort/{trip_id}/position",
        json={"point": {"lat": 28.61, "lon": 77.21}, "risk_label": "Moderate", "risk_score": 0.4, "progress_fraction": 0.5},
        headers={**HEADERS, "x-owner-token": owner_token},
    )
    body = client.get(f"/escort/{trip_id}").json()
    assert body["last_point"] == {"lat": 28.61, "lon": 77.21}
    assert body["risk_label"] == "Moderate"
    assert body["progress_fraction"] == 0.5


def test_checkin_not_ok_sets_alert(client):
    trip_id, owner_token = _start(client)
    response = client.post(
        f"/escort/{trip_id}/checkin", json={"ok": False}, headers={**HEADERS, "x-owner-token": owner_token}
    )
    assert response.json()["status"] == "alert"


def test_checkin_ok_clears_alert_and_reschedules(client):
    trip_id, owner_token = _start(client)
    auth = {**HEADERS, "x-owner-token": owner_token}
    client.post(f"/escort/{trip_id}/checkin", json={"ok": False}, headers=auth)
    response = client.post(f"/escort/{trip_id}/checkin", json={"ok": True}, headers=auth)
    body = response.json()
    assert body["status"] == "active"
    assert body["next_check_in_due_at"] is not None


def test_missed_checkin_sets_alert(client):
    trip_id, owner_token = _start(client)
    response = client.post(
        f"/escort/{trip_id}/missed-checkin", headers={**HEADERS, "x-owner-token": owner_token}
    )
    assert response.json()["status"] == "alert"


def test_sos_sets_alert_and_records_point(client):
    trip_id, owner_token = _start(client)
    response = client.post(
        f"/escort/{trip_id}/sos",
        json={"point": {"lat": 28.65, "lon": 77.25}},
        headers={**HEADERS, "x-owner-token": owner_token},
    )
    body = response.json()
    assert body["status"] == "alert"
    assert body["last_point"] == {"lat": 28.65, "lon": 77.25}
    assert body["events"][-1]["kind"] == "sos"


def test_end_marks_ended_and_further_writes_fail(client):
    trip_id, owner_token = _start(client)
    auth = {**HEADERS, "x-owner-token": owner_token}
    response = client.post(f"/escort/{trip_id}/end", headers=auth)
    assert response.json()["status"] == "ended"

    # Still readable (so a companion sees "walk ended"), but no longer writable.
    assert client.get(f"/escort/{trip_id}").json()["status"] == "ended"
    response = client.post(f"/escort/{trip_id}/position", json={"point": {"lat": 28.6, "lon": 77.2}}, headers=auth)
    assert response.status_code == 404


def test_events_timeline_accumulates(client):
    trip_id, owner_token = _start(client)
    auth = {**HEADERS, "x-owner-token": owner_token}
    client.post(f"/escort/{trip_id}/checkin", json={"ok": True}, headers=auth)
    client.post(f"/escort/{trip_id}/sos", headers=auth)
    events = client.get(f"/escort/{trip_id}").json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["started", "checkin_ok", "sos"]


def test_expired_session_is_pruned(client, service):
    trip_id, _ = _start(client)
    session = service._escorts[trip_id]
    session["last_update_at"] = session["last_update_at"] - timedelta(days=1)
    response = client.get(f"/escort/{trip_id}")
    assert response.status_code == 404
