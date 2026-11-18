"""Monitoring: one observation of the whole stack per tick.

Sources (all read-only, category 1):
  * supervisor /services   - process state, restarts, RSS, CPU, version, config
  * GET <service>/health   - availability, response time, dependency checks
  * synthetic transactions - real GET /api/orders/{id} calls -> error rate, p95
  * supervisor /changes    - deployments, config changes, process exits
"""
from __future__ import annotations

import itertools
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from ..clients import ServiceProbe, SupervisorError, SupervisorReadClient
from ..database.db import Database, utcnow

PROCESS_UP_STATES = {"running"}


@dataclass
class ServiceObservation:
    name: str
    process_state: str | None = None
    version: str | None = None
    config_version: str | None = None
    restart_count: int | None = None
    restarts_recent: int | None = None
    last_exit_code: int | None = None
    rss_mb: float | None = None
    cpu_percent: float | None = None
    depends_on: list[str] = field(default_factory=list)
    stateful: bool = False
    health: str = "unknown"
    http_status: int | None = None
    health_error: str | None = None
    response_ms: float | None = None
    health_body: dict | None = None
    error_rate: float | None = None
    latency_p95_ms: float | None = None
    probe_samples: int = 0
    tick_failures: int | None = None  # failed probes in the latest tick only
    tick_max_ms: float | None = None  # slowest probe in the latest tick


@dataclass
class Observation:
    ts: str
    supervisor_ok: bool
    services: dict[str, ServiceObservation]
    error: str | None = None

    def to_dict(self) -> dict:
        return {"ts": self.ts, "supervisor_ok": self.supervisor_ok, "error": self.error,
                "services": {k: asdict(v) for k, v in self.services.items()}}


def p95(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, int(round(len(ordered) * 0.95)) - 1)], 1)


class Collector:
    def __init__(self, db: Database, read: SupervisorReadClient, probe: ServiceProbe, thresholds: dict):
        self.db = db
        self.read = read
        self.probe = probe
        self.t = thresholds
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="probe")
        self._order_ids = itertools.cycle(range(1, 7))
        self._window: deque[list[dict]] = deque(maxlen=self.t["monitor"]["probe_window_ticks"])
        self._window_lock = threading.Lock()
        self._healthy_since: dict[str, tuple[datetime, str, str]] = {}
        self.latest: Observation | None = None

    # ------------------------------------------------------------------
    def reset_window(self) -> None:
        """Drop probe history (called after a remediation so stale errors
        recorded before the fix do not re-trigger detection)."""
        with self._window_lock:
            self._window.clear()

    def observe(self, use_window: bool = True, record: bool = True) -> Observation:
        ts = utcnow()
        try:
            sup = self.read.services()
            supervisor_ok, error = True, None
        except SupervisorError as exc:
            sup, supervisor_ok, error = [], False, str(exc)

        services: dict[str, ServiceObservation] = {}
        for s in sup:
            services[s["name"]] = ServiceObservation(
                name=s["name"], process_state=s["state"], version=s["version"], config_version=s["config_version"],
                restart_count=s["restart_count"], restarts_recent=s["restarts_last_120s"],
                last_exit_code=s["last_exit_code"], rss_mb=s["rss_mb"], cpu_percent=s["cpu_percent"],
                depends_on=list(s["depends_on"]), stateful=bool(s["stateful"]),
            )
        if not supervisor_ok:
            # Fall back to the configured service list so health is still probed.
            for name in self.probe.urls:
                services[name] = ServiceObservation(name=name)

        names = list(services)
        n_probes = self.t["monitor"]["probe_requests_per_tick"]
        health_futs = {n: self._pool.submit(self.probe.health, n) for n in names}
        probe_futs = [self._pool.submit(self.probe.synthetic_order, next(self._order_ids)) for _ in range(n_probes)]
        probes = [f.result() for f in probe_futs]

        for n in names:
            h = health_futs[n].result()
            o = services[n]
            o.http_status, o.health_error, o.response_ms, o.health_body = h["status"], h["error"], h["ms"], h["body"]

        with self._window_lock:
            if use_window:
                self._window.append(probes)
                samples = [p for tick in self._window for p in tick]
            else:
                samples = probes
        api = services.get("api-service")
        if api is not None and samples:
            api.probe_samples = len(samples)
            api.tick_failures = sum(1 for p in probes if not p["ok"])
            api.tick_max_ms = max((p["ms"] for p in probes), default=None)
            api.error_rate = round(sum(1 for p in samples if not p["ok"]) / len(samples), 3)
            api.latency_p95_ms = p95([p["ms"] for p in samples])

        det = dict(self.t["detection"])
        if not use_window:
            det["min_probe_samples"] = min(det["min_probe_samples"], len(samples) or 1)
        for o in services.values():
            o.health = self._classify(o, det)

        obs = Observation(ts=ts, supervisor_ok=supervisor_ok, services=services, error=error)
        if record:
            self._record(obs)
            self.latest = obs
        return obs

    @staticmethod
    def _classify(o: ServiceObservation, det: dict) -> str:
        if o.process_state is not None and o.process_state not in PROCESS_UP_STATES:
            return "down"
        if o.health_error is not None or o.http_status is None:
            return "down"
        body_status = (o.health_body or {}).get("status")
        if o.http_status != 200 or body_status not in ("ok", None):
            return "degraded"
        if (o.error_rate is not None and o.probe_samples >= det["min_probe_samples"]
                and o.error_rate >= det["error_rate_threshold"] and o.tick_failures != 0):
            return "degraded"
        if (o.latency_p95_ms is not None and o.latency_p95_ms >= det["latency_p95_threshold_ms"]
                and (o.tick_max_ms is None or o.tick_max_ms >= det["latency_p95_threshold_ms"])):
            return "degraded"
        if o.rss_mb is not None and o.rss_mb >= det["memory_rss_threshold_mb"]:
            return "degraded"
        return "healthy"

    # ------------------------------------------------------------------
    def _record(self, obs: Observation) -> None:
        for o in obs.services.values():
            self.db.execute(
                "INSERT OR IGNORE INTO services(name, port, stateful, first_seen) VALUES (?,?,?,?)",
                (o.name, None, int(o.stateful), obs.ts),
            )
        for o in obs.services.values():
            for dep in o.depends_on:
                self.db.execute(
                    "INSERT OR IGNORE INTO services(name, port, stateful, first_seen) VALUES (?,?,?,?)", (dep, None, 0, obs.ts)
                )
                self.db.execute("INSERT OR IGNORE INTO service_dependencies(service, depends_on) VALUES (?,?)", (o.name, dep))
            detail = {"health_body": o.health_body, "health_error": o.health_error, "last_exit_code": o.last_exit_code,
                      "probe_samples": o.probe_samples}
            self.db.insert("monitoring_snapshots", {
                "ts": obs.ts, "service": o.name, "process_state": o.process_state, "health": o.health,
                "http_status": o.http_status, "response_ms": o.response_ms, "error_rate": o.error_rate,
                "latency_p95_ms": o.latency_p95_ms, "rss_mb": o.rss_mb, "cpu_percent": o.cpu_percent,
                "restart_count": o.restart_count, "restarts_recent": o.restarts_recent, "version": o.version,
                "config_version": o.config_version, "detail_json": detail,
            })
        if obs.supervisor_ok:
            self.sync_changes()
            self._update_known_good(obs)

    def sync_changes(self) -> None:
        try:
            changes = self.read.changes(limit=500)
        except SupervisorError:
            return
        for c in changes:
            key = f"{c['ts']}#{c['id']}"
            svc = c["service"]
            self.db.execute("INSERT OR IGNORE INTO services(name, port, stateful, first_seen) VALUES (?,?,?,?)", (svc, None, 0, c["ts"]))
            if c["kind"] == "deployment":
                self.db.execute(
                    "INSERT OR IGNORE INTO deployments(change_key, service, from_version, to_version, actor, deployed_at) VALUES (?,?,?,?,?,?)",
                    (key, svc, c.get("from_version"), c["to_version"], c["actor"], c["ts"]),
                )
            elif c["kind"] == "config_change":
                self.db.execute(
                    "INSERT OR IGNORE INTO config_changes(change_key, service, from_config, to_config, actor, changed_at) VALUES (?,?,?,?,?,?)",
                    (key, svc, c.get("from_config"), c["to_config"], c["actor"], c["ts"]),
                )
            else:
                import json

                data = {k: v for k, v in c.items() if k not in ("id", "ts", "service", "kind", "actor")}
                self.db.execute(
                    "INSERT OR IGNORE INTO runtime_events(change_key, service, kind, actor, data_json, ts) VALUES (?,?,?,?,?,?)",
                    (key, svc, c["kind"], c["actor"], json.dumps(data), c["ts"]),
                )

    def _update_known_good(self, obs: Observation) -> None:
        need = timedelta(seconds=self.t["investigation"]["known_good_after_s"])
        now = datetime.now(timezone.utc)
        for o in obs.services.values():
            if o.health != "healthy" or not o.version:
                self._healthy_since.pop(o.name, None)
                continue
            since = self._healthy_since.get(o.name)
            if since is None or since[1] != o.version or since[2] != o.config_version:
                self._healthy_since[o.name] = (now, o.version, o.config_version)
                continue
            if now - since[0] >= need:
                for kind, value in (("version", o.version), ("config", o.config_version)):
                    self.db.execute(
                        "INSERT INTO known_good(service, kind, value, verified_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(service, kind, value) DO UPDATE SET verified_at=excluded.verified_at",
                        (o.name, kind, value, obs.ts),
                    )

    def prune(self, keep_hours: int = 12) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).isoformat()
        self.db.execute("DELETE FROM monitoring_snapshots WHERE ts < ?", (cutoff,))
