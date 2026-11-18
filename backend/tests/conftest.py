"""Test fixtures: an in-memory simulation of the demo stack.

`World` models the same behaviours as the real demo services (bad release,
invalid config crash loop, memory leak, hung or out-of-service dependency,
corrupted artifact). The real SteadyOps components (collector, detector,
evidence, RCA, policy, executor, verifier) run unmodified against it.
"""
from __future__ import annotations

import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients import SupervisorError  # noqa: E402
from app.config import Settings  # noqa: E402
from app.runtime import build_runtime  # noqa: E402

DEPS = {"frontend-service": ["api-service"], "api-service": ["database-service", "dependency-service"],
        "database-service": [], "dependency-service": []}
INITIAL = {"frontend-service": ("1.0.3", "web-cfg-1"), "api-service": ("1.4.2", "api-cfg-7"),
           "database-service": ("2.1.0", "db-cfg-1"), "dependency-service": ("3.0.1", "inv-cfg-1")}
REGISTRY = {"api-service": {"versions": ["1.4.2", "1.5.0"], "configs": {"api-cfg-7": {}, "api-cfg-8": {}}},
            "frontend-service": {"versions": ["1.0.3"], "configs": {"web-cfg-1": {}}},
            "database-service": {"versions": ["2.1.0"], "configs": {"db-cfg-1": {}}},
            "dependency-service": {"versions": ["3.0.1"], "configs": {"inv-cfg-1": {}}}}


def iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class World:
    def __init__(self):
        self.started_at = iso()
        self.svc = {n: {"version": v, "config": c, "restart_count": 0, "pid": 100 + i}
                    for i, (n, (v, c)) in enumerate(INITIAL.items())}
        self.leak = False
        self.dep_hang = False
        self.dep_outage = False
        self.broken_artifacts: set[str] = set()
        self.logs = {n: [] for n in INITIAL}
        self.changes: list[dict] = []
        self.ops_calls: list[tuple] = []
        self.supervisor_up = True
        self.fail_ops = False
        self._ids = itertools.count(1)
        for n, (v, _) in INITIAL.items():
            self._change(n, "deployment", "bootstrap", from_version=None, to_version=v)

    # -- helpers -------------------------------------------------------------
    def _change(self, service, kind, actor, **data):
        self.changes.append({"id": next(self._ids), "ts": iso(), "service": service, "kind": kind, "actor": actor, **data})

    def log(self, service, level, msg, **fields):
        self.logs[service].append({"ts": iso(), "level": level, "service": service,
                                   "version": self.svc[service]["version"], "msg": msg, **fields})

    def api_crashing(self) -> int | None:
        a = self.svc["api-service"]
        if a["version"] in self.broken_artifacts:
            return 125
        if a["config"] == "api-cfg-8":
            return 78
        return None

    def dep_failing(self) -> bool:
        return self.dep_hang or self.dep_outage

    # -- fault injection -----------------------------------------------------
    def inject(self, scenario: str):
        if scenario in ("bad_deployment", "bad_deployment_corrupt_rollback"):
            if scenario.endswith("corrupt_rollback"):
                self.broken_artifacts.add("1.4.2")
            self._deploy("api-service", "1.5.0", "ci-pipeline")
            for i in (2, 4, 6):
                self.log("api-service", "ERROR", "unhandled exception in GET /api/orders/{id}", order_id=i,
                         exception="TypeError: unsupported operand", location="pricing.apply_discount")
        elif scenario == "broken_config":
            self._config("api-service", "api-cfg-8", "config-sync")
            self.log("api-service", "FATAL", "invalid configuration", error="DB_POOL_SIZE must be an integer")
            self.log("api-service", "FATAL", "refusing to start with invalid configuration", exit_code=78)
        elif scenario == "resource_exhaustion":
            self.leak = True
            self.log("api-service", "WARN", "report cache growing without eviction", cache_mb=150)
        elif scenario in ("dependency_failure", "dependency_outage"):
            self.dep_hang = True
            self.dep_outage = scenario == "dependency_outage"
            for _ in range(3):
                self.log("api-service", "ERROR", "inventory request failed", dependency="dependency-service",
                         error="ReadTimeout")
        else:
            raise ValueError(scenario)

    # -- supervisor surface ----------------------------------------------------
    def _deploy(self, service, version, actor):
        prev = self.svc[service]["version"]
        self.svc[service]["version"] = version
        self._change(service, "deployment", actor, from_version=prev, to_version=version)

    def _config(self, service, cfg, actor):
        prev = self.svc[service]["config"]
        self.svc[service]["config"] = cfg
        self._change(service, "config_change", actor, from_config=prev, to_config=cfg)

    def status(self, n: str) -> dict:
        s = self.svc[n]
        state, exit_code, restarts, rss = "running", None, 0, 60.0
        if n == "api-service":
            code = self.api_crashing()
            if code:
                state, exit_code, restarts = "crash_loop", code, 4
            if self.leak:
                rss = 250.0
        return {"name": n, "state": state, "pid": s["pid"], "port": 0, "stateful": n == "database-service",
                "depends_on": DEPS[n], "version": s["version"], "config_version": s["config"],
                "restart_count": s["restart_count"], "restarts_last_120s": restarts, "started_at": self.started_at,
                "last_exit_code": exit_code, "last_exit_at": None, "rss_mb": None if state != "running" else rss,
                "cpu_percent": 1.0}

    def health(self, n: str) -> dict:
        if n == "api-service" and self.api_crashing():
            return {"ok": False, "status": None, "error": "ConnectError", "ms": 2.0, "body": None}
        if n == "dependency-service" and self.dep_failing():
            if self.dep_outage and not self.dep_hang:
                return {"ok": False, "status": 503, "error": None, "ms": 3.0, "body": {"detail": "upstream provider unavailable"}}
            return {"ok": False, "status": None, "error": "ReadTimeout", "ms": 2500.0, "body": None}
        if n == "api-service" and self.dep_failing():
            body = {"status": "degraded", "checks": {"database": "ok", "inventory": "unreachable"}}
            return {"ok": False, "status": 503, "error": None, "ms": 1000.0, "body": body}
        return {"ok": True, "status": 200, "error": None, "ms": 5.0, "body": {"status": "ok"}}

    def order(self, order_id: int) -> dict:
        if self.api_crashing():
            return {"ok": False, "status": None, "ms": 2.0}
        if self.dep_failing():
            return {"ok": False, "status": 503, "ms": 1500.0}
        if self.svc["api-service"]["version"] == "1.5.0" and order_id % 2 == 0:
            return {"ok": False, "status": 500, "ms": 8.0}
        return {"ok": True, "status": 200, "ms": 1200.0 if self.leak else 10.0}


class FakeRead:
    def __init__(self, w: World):
        self.w = w

    def _up(self):
        if not self.w.supervisor_up:
            raise SupervisorError("supervisor read failed: ConnectError")

    def services(self):
        self._up()
        return [self.w.status(n) for n in INITIAL]

    def logs(self, service, lines=100):
        self._up()
        return self.w.logs[service][-lines:]

    def changes(self, limit=500):
        self._up()
        return self.w.changes[-limit:]

    def registry(self, service):
        self._up()
        return REGISTRY[service]


class FakeOps:
    def __init__(self, w: World):
        self.w = w

    def _guard(self, *call):
        self.w.ops_calls.append(call)
        if self.w.fail_ops:
            raise SupervisorError("supervisor rejected operation: HTTP 500")

    def restart(self, service):
        self._guard("restart", service)
        self.w.svc[service]["restart_count"] += 1
        self.w.svc[service]["pid"] += 1000
        if service == "api-service":
            self.w.leak = False
        if service == "dependency-service":
            self.w.dep_hang = False  # outage flag persists across restarts
        self.w._change(service, "restart", "steadyops-agent")
        return self.w.status(service)

    def deploy(self, service, version):
        self._guard("deploy", service, version)
        self.w._deploy(service, version, "steadyops-agent")
        self.w.leak = False
        return self.w.status(service)

    def apply_config(self, service, config_version):
        self._guard("apply_config", service, config_version)
        self.w._config(service, config_version, "steadyops-agent")
        return self.w.status(service)


class FakeChaos:
    SCENARIOS = {"bad_deployment", "bad_deployment_corrupt_rollback", "broken_config", "resource_exhaustion",
                 "dependency_failure", "dependency_outage"}

    def __init__(self, w: World):
        self.w = w

    def inject(self, scenario):
        self.w.inject(scenario)
        return {"injected": scenario}

    def reset(self):
        w = self.w
        w.leak = w.dep_hang = w.dep_outage = False
        w.broken_artifacts.clear()
        for n, (v, c) in INITIAL.items():
            if w.svc[n]["version"] != v:
                w._deploy(n, v, "failure-lab-reset")
            if w.svc[n]["config"] != c:
                w._config(n, c, "failure-lab-reset")
        return {"reset": True, "actions": []}


class FakeProbe:
    def __init__(self, w: World):
        self.w = w
        self.urls = {n: f"http://fake/{n}" for n in INITIAL}

    def health(self, service):
        return self.w.health(service)

    def synthetic_order(self, order_id):
        return self.w.order(order_id)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, dt):
        self.t += dt

    def __call__(self):
        return self.t


@pytest.fixture
def world():
    return World()


@pytest.fixture
def rt(tmp_path, world):
    settings = Settings()
    settings.db_path = tmp_path / "test.db"
    settings.run_loop = False
    settings.autonomy_mode = "auto"
    clock = FakeClock()
    r = build_runtime(settings, read=FakeRead(world), ops=FakeOps(world), chaos=FakeChaos(world),
                      probe=FakeProbe(world), verifier_kwargs={"sleep": clock.sleep, "clock": clock})
    r.world = world
    return r


def mark_known_good(rt):
    for n, (v, c) in INITIAL.items():
        rt.db.execute("INSERT OR IGNORE INTO services(name, port, stateful, first_seen) VALUES (?,?,?,?)", (n, None, 0, iso()))
        rt.db.execute("INSERT OR IGNORE INTO known_good VALUES (?,?,?,?)", (n, "version", v, iso()))
        rt.db.execute("INSERT OR IGNORE INTO known_good VALUES (?,?,?,?)", (n, "config", c, iso()))


def run_ticks(rt, n: int):
    """Run n monitor ticks synchronously; return the last DetectionResult and any new incident ids."""
    new = []
    res = None
    for _ in range(n):
        obs = rt.collector.observe()
        res = rt.detector.process(obs)
        if res.new_incident_id:
            new.append(res.new_incident_id)
    return res, new


@pytest.fixture
def healthy_rt(rt):
    run_ticks(rt, 3)
    mark_known_good(rt)
    return rt


def detect(rt, scenario: str) -> int:
    """Inject a scenario through the Failure Lab and tick until an incident opens."""
    rt.lab.inject(scenario, "pytest")
    _, new = run_ticks(rt, 4)
    assert new, f"no incident detected for {scenario}"
    return new[0]
