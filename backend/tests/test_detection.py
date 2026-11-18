from app.detection.detector import compute_signals, pick_root
from app.monitoring.collector import ServiceObservation
from conftest import run_ticks


def test_healthy_stack_raises_no_incidents(rt):
    _, new = run_ticks(rt, 15)
    assert new == []
    assert rt.db.scalar("SELECT COUNT(*) FROM incidents") == 0
    assert rt.collector.latest.services["api-service"].health == "healthy"


def test_unhealthy_service_opens_incident_after_debounce(healthy_rt):
    rt = healthy_rt
    rt.world.inject("bad_deployment")
    _, new = run_ticks(rt, 1)
    assert new == [], "a single bad tick must not open an incident (debounce)"
    _, new = run_ticks(rt, 2)
    assert len(new) == 1
    inc = rt.incidents.get(new[0])
    assert inc["detected_service"] == "api-service"
    assert any(s["type"] == "high_error_rate" for s in inc["signals"])


def test_duplicate_incidents_are_suppressed(healthy_rt):
    rt = healthy_rt
    rt.world.inject("broken_config")
    _, new = run_ticks(rt, 10)
    assert len(new) == 1
    assert rt.db.scalar("SELECT COUNT(*) FROM incidents") == 1
    assert rt.incidents.get(new[0])["severity"] == "critical"


def test_transient_blip_does_not_open_incident(healthy_rt):
    rt = healthy_rt
    rt.world.dep_hang = True
    run_ticks(rt, 1)
    rt.world.dep_hang = False
    _, new = run_ticks(rt, 5)
    assert new == []


def test_dependency_failure_attributed_to_dependency(healthy_rt):
    rt = healthy_rt
    rt.world.inject("dependency_failure")
    _, new = run_ticks(rt, 3)
    inc = rt.incidents.get(new[0])
    assert inc["detected_service"] == "dependency-service"
    assert set(inc["affected_services"]) >= {"api-service", "dependency-service"}


def test_supervisor_outage_never_opens_incident(healthy_rt):
    rt = healthy_rt
    rt.world.supervisor_up = False
    _, new = run_ticks(rt, 5)
    assert new == []


def test_signal_thresholds():
    det = {"crash_loop_restarts": 2, "error_rate_threshold": 0.2, "min_probe_samples": 6,
           "latency_p95_threshold_ms": 900, "memory_rss_threshold_mb": 200, "cpu_threshold_percent": 95}
    ok = ServiceObservation(name="x", process_state="running", http_status=200, health_body={"status": "ok"},
                            rss_mb=199, error_rate=0.19, probe_samples=10, latency_p95_ms=899)
    assert compute_signals(ok, det) == []
    bad = ServiceObservation(name="x", process_state="running", http_status=200, rss_mb=201, error_rate=0.2,
                             probe_samples=10)
    assert {s["type"] for s in compute_signals(bad, det)} == {"high_memory", "high_error_rate"}
    few = ServiceObservation(name="x", process_state="running", http_status=200, error_rate=1.0, probe_samples=3)
    assert compute_signals(few, det) == [], "too few samples must not raise an error-rate signal"


def test_pick_root_prefers_most_upstream():
    abnormal = {"api-service": [{"severity": "high"}], "dependency-service": [{"severity": "high"}]}
    deps = {"api-service": ["database-service", "dependency-service"], "dependency-service": []}
    assert pick_root(abnormal, deps) == "dependency-service"
