from pathlib import Path

from fastapi.testclient import TestClient

from aeris.api.app import create_app
from aeris.config import Settings


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(db_path=tmp_path / "aeris.db", otel_enabled=False, hold_ms=0, human_on_critical=True)
    return TestClient(create_app(settings))


def test_health_and_happy_path(tmp_path: Path):
    with _client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        created = client.post("/missions", json={"objective": "demo", "success_criteria": "done"})
        assert created.status_code == 201
        mission_id = created.json()["mission_id"]
        flight = client.post(
            "/flights",
            json={"mission_id": mission_id, "scenario_id": "happy_path", "mode": "AERIS"},
        )
        assert flight.status_code == 201
        body = flight.json()
        assert body["state"] == "COMPLETED"
        flight_id = body["flight_id"]
        fetched = client.get(f"/flights/{flight_id}")
        assert fetched.status_code == 200
        timeline = client.get(f"/flights/{flight_id}/timeline")
        assert timeline.status_code == 200
        assert len(timeline.json()["events"]) >= 3
        telemetry = client.get(f"/flights/{flight_id}/telemetry")
        assert telemetry.status_code == 200
        listed = client.get("/flights")
        assert any(item["flight_id"] == flight_id for item in listed.json())


def test_latency_reroute_via_api(tmp_path: Path):
    with _client(tmp_path) as client:
        flight = client.post("/flights", json={"scenario_id": "latency_reroute"})
        assert flight.status_code == 201
        body = flight.json()
        assert body["state"] == "COMPLETED"
        assert body["route_changes"] == 1
        hazards = client.get(f"/flights/{body['flight_id']}/hazards")
        types = [event["payload"]["type"] for event in hazards.json()["events"]]
        assert "HIGH_LATENCY" in types


def test_human_reroute_and_abort(tmp_path: Path):
    with _client(tmp_path) as client:
        flight = client.post(
            "/flights",
            json={"scenario_id": "timeout_critical", "human_on_critical": True},
        )
        body = flight.json()
        assert body["state"] == "WAITING_HUMAN"
        flight_id = body["flight_id"]
        bravo = next(route for route in body["plan"]["routes"] if route["name"] == "bravo")
        missing_role = client.post(
            f"/flights/{flight_id}/control/reroute",
            json={"reason": "divert", "route_id": bravo["route_id"], "operator": "atc"},
        )
        assert missing_role.status_code == 422
        resumed = client.post(
            f"/flights/{flight_id}/control/reroute",
            json={"reason": "divert", "route_id": bravo["route_id"], "operator": "atc", "role": "CONTROLLER"},
        )
        assert resumed.status_code == 200
        assert resumed.json()["state"] == "COMPLETED"

        blocked = client.post("/flights", json={"scenario_id": "timeout_critical"})
        abort_id = blocked.json()["flight_id"]
        aborted = client.post(
            f"/flights/{abort_id}/control/abort",
            json={"reason": "stop", "role": "ADMIN"},
        )
        assert aborted.json()["state"] == "ABORTED"

        done = client.post("/flights", json={"scenario_id": "happy_path"})
        conflict = client.post(
            f"/flights/{done.json()['flight_id']}/control/continue",
            json={"reason": "too late", "role": "CONTROLLER"},
        )
        assert conflict.status_code == 409
