import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import mark_known_good, run_ticks

OP = {"operator": "pytest"}


@pytest.fixture
def client(rt):
    run_ticks(rt, 3)
    mark_known_good(rt)
    app = create_app(rt.settings, runtime=rt)
    with TestClient(app) as c:
        yield c


def test_overview_reflects_real_state(client, rt):
    o = client.get("/api/overview").json()
    assert o["system_status"] == "healthy" and o["services_healthy"] == 4
    rt.world.inject("broken_config")
    run_ticks(rt, 1)
    o = client.get("/api/overview").json()
    assert o["system_status"] == "down"


def test_full_incident_flow_through_api(client, rt):
    assert client.post("/api/failure-lab/bad_deployment/inject", json=OP).status_code == 200
    assert client.post("/api/failure-lab/broken_config/inject", json=OP).status_code == 409, "one scenario at a time"
    _, new = run_ticks(rt, 4)
    rt.orchestrator.run_pipeline(new[0])
    d = client.get(f"/api/incidents/{new[0]}").json()
    assert d["incident"]["status"] == "resolved"
    assert d["summary"]["resolved"] is True and d["summary"]["evidence"]
    assert d["timings"]["time_to_detect_s"] is not None
    stages = [e["stage"] for e in d["timeline"]]
    for s in ("detected", "investigating", "diagnosed", "safety_evaluation", "executing", "verifying", "resolved"):
        assert s in stages
    assert client.get("/api/analytics").json()["summary"]["diagnosis_accuracy"] == 1.0
    assert client.post("/api/failure-lab/reset", json=OP).status_code == 200


def test_input_validation(client):
    assert client.post("/api/failure-lab/nonexistent/inject", json=OP).status_code == 404
    assert client.post("/api/failure-lab/bad_deployment/inject", json={"operator": "x" * 200}).status_code == 422
    assert client.post("/api/failure-lab/bad_deployment/inject", json={**OP, "extra": 1}).status_code == 422
    assert client.post("/api/proposals/999/approve", json=OP).status_code == 404
    assert client.put("/api/settings/autonomy", json={**OP, "mode": "yolo"}).status_code == 422


def test_policy_simulator_never_executes(client, rt):
    for action in ("execute_shell", "purge_database_data", "restart_service"):
        r = client.post("/api/safety/simulate", json={"action_id": action, "resource": "database-service",
                                                      "params": {"service": "database-service"}}).json()
        assert r["outcome"] in ("deny", "require_approval")
    assert rt.world.ops_calls == []


def test_emergency_stop_endpoint(client):
    r = client.post("/api/safety/emergency-stop", json={**OP, "engaged": True, "reason": "test"}).json()
    assert r["engaged"] is True
    s = client.get("/api/safety/status").json()
    assert s["emergency_stop"]["engaged"] and s["audit_chain"]["valid"]


def test_logs_endpoint_redacts(client, rt):
    rt.world.log("api-service", "INFO", "connecting password=topsecret")
    body = client.get("/api/services/api-service/logs").text
    assert "topsecret" not in body
    assert client.get("/api/services/not-a-service/logs").status_code == 404


def test_excluded_run_is_removed_from_metrics_but_kept_visible(client, rt):
    client.post("/api/failure-lab/bad_deployment/inject", json=OP)
    _, new = run_ticks(rt, 4)
    rt.orchestrator.run_pipeline(new[0])
    assert client.get("/api/analytics").json()["summary"]["injections"] == 1
    r = client.post("/api/failure-lab/injections/1/exclude", json={**OP, "reason": "host suspended mid-run"})
    assert r.status_code == 200
    a = client.get("/api/analytics").json()
    assert a["summary"]["injections"] == 0 and a["summary"]["incidents_total"] == 0
    assert a["excluded_runs"][0]["excluded_reason"] == "host suspended mid-run"
    assert client.post("/api/failure-lab/injections/1/exclude", json={**OP, "reason": "again please"}).status_code == 409
    assert client.post("/api/failure-lab/injections/99/exclude", json={**OP, "reason": "missing run"}).status_code == 404
    assert rt.db.one("SELECT * FROM audit_events WHERE event_type='injection_excluded'")
