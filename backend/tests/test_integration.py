"""Integration test against REAL processes: the supervisor and the four demo
services are started as subprocesses, faults are injected through the real
chaos API, and the real SteadyOps pipeline detects, diagnoses, remediates and
verifies. Skipped automatically if the demo ports are already in use (e.g. a
demo is running) - stop it first to run this test.

    cd backend && python -m pytest tests/test_integration.py -v
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from app import analytics
from app.config import Settings
from app.runtime import build_runtime

REPO = Path(__file__).resolve().parents[2]
PORTS = (8100, 8101, 8102, 8103, 8104)

pytestmark = pytest.mark.integration


def _busy() -> list[int]:
    return [c.laddr.port for c in psutil.net_connections("tcp") if c.status == "LISTEN" and c.laddr.port in PORTS]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    if _busy():
        pytest.skip(f"demo ports busy {_busy()}; stop the running demo to run integration tests")
    env = dict(os.environ, SUPERVISOR_TOKEN=secrets.token_urlsafe(16), CHAOS_TOKEN=secrets.token_urlsafe(16),
               DEMO_LOG_DIR=str(tmp_path_factory.mktemp("logs")))
    proc = subprocess.Popen([sys.executable, str(REPO / "demo-services/supervisor/supervisor.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get("http://127.0.0.1:8102/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("demo stack did not start")
        os.environ["SUPERVISOR_TOKEN"], os.environ["CHAOS_TOKEN"] = env["SUPERVISOR_TOKEN"], env["CHAOS_TOKEN"]
        s = Settings()
        s.db_path = tmp_path_factory.mktemp("db") / "it.db"
        s.run_loop = False
        s.thresholds["investigation"]["known_good_after_s"] = 4
        rt = build_runtime(s)
        yield rt
    finally:
        parent = psutil.Process(proc.pid)
        for ch in parent.children(recursive=True):
            ch.terminate()
        parent.terminate()
        psutil.wait_procs([parent], timeout=10)


def _tick_until(rt, pred, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = rt.orchestrator.tick()
        if pred(res):
            return res
        time.sleep(rt.settings.thresholds["monitor"]["interval_s"])
    return None


def _scenario(rt, scenario, expect_category, expect_status):
    _tick_until(rt, lambda r: False, timeout=8)  # healthy baseline -> known-good marking
    assert rt.db.scalar("SELECT COUNT(*) FROM known_good WHERE service='api-service'") >= 2
    rt.lab.inject(scenario, "integration-test")
    res = _tick_until(rt, lambda r: r.new_incident_id is not None, timeout=60)
    assert res is not None, f"{scenario} not detected"
    iid = res.new_incident_id
    rt.orchestrator._pool.shutdown(wait=True)  # wait for the auto-started pipeline
    from concurrent.futures import ThreadPoolExecutor

    rt.orchestrator._pool = ThreadPoolExecutor(max_workers=2)
    inc = rt.incidents.get(iid)
    assert inc["category"] == expect_category
    assert inc["status"] == expect_status, inc
    rt.lab.reset("integration-test")
    _tick_until(rt, lambda r: not any(r.signals.values()), timeout=40)
    return inc


def test_bad_deployment_end_to_end(live):
    inc = _scenario(live, "bad_deployment", "bad_deployment", "resolved")
    run = analytics.compute(live.db)["runs"][-1]
    assert run["ttd_s"] < 30 and run["ttr_s"] < 90
    print(f"\nbad_deployment: TTD={run['ttd_s']}s TTR={run['ttr_s']}s")


def test_dependency_failure_end_to_end(live):
    inc = _scenario(live, "dependency_failure", "dependency_failure", "resolved")
    ex = live.db.query("SELECT action_id, resource FROM remediation_executions WHERE incident_id=?", (inc["id"],))
    assert ex == [{"action_id": "restart_service", "resource": "dependency-service"}]
