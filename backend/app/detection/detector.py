"""Incident detection.

Turns observations into *signals* (threshold crossings), debounces them over
consecutive ticks, de-duplicates against active incidents and attributes the
incident to the most upstream abnormal service in the dependency graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..database.db import Database, parse_ts, utcnow
from ..incidents import IncidentStore
from ..monitoring.collector import Observation, ServiceObservation
from ..safety.audit import AuditLog

SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
SIGNAL_SEVERITY = {
    "service_down": "critical",
    "crash_loop": "critical",
    "health_check_failed": "high",
    "health_degraded": "high",
    "high_error_rate": "high",
    "high_latency": "medium",
    "high_memory": "medium",
    "high_cpu": "medium",
}


def compute_signals(o: ServiceObservation, det: dict) -> list[dict]:
    """Pure function: threshold evaluation for one service observation."""
    sig: list[dict] = []

    def add(kind: str, message: str, value=None, threshold=None):
        sig.append({"service": o.name, "type": kind, "severity": SIGNAL_SEVERITY[kind], "message": message,
                    "value": value, "threshold": threshold})

    if o.process_state is not None and o.process_state != "running":
        add("service_down", f"process state is '{o.process_state}' (last exit code {o.last_exit_code})", o.process_state)
    if o.restarts_recent is not None and o.restarts_recent >= det["crash_loop_restarts"]:
        add("crash_loop", f"{o.restarts_recent} restarts in the last 120s", o.restarts_recent, det["crash_loop_restarts"])
    if o.process_state in (None, "running"):
        if o.health_error is not None:
            add("health_check_failed", f"health check failed: {o.health_error} after {o.response_ms} ms", o.health_error)
        elif o.http_status is not None and (o.http_status != 200 or (o.health_body or {}).get("status") not in ("ok", None)):
            checks = (o.health_body or {}).get("checks")
            add("health_degraded", f"health endpoint returned HTTP {o.http_status}" + (f" checks={checks}" if checks else ""),
                o.http_status)
    # Window error rate must be high AND failures must still be happening now
    # (a single bad tick lingering in the window is not an ongoing incident).
    if (o.error_rate is not None and o.probe_samples >= det["min_probe_samples"]
            and o.error_rate >= det["error_rate_threshold"] and o.tick_failures != 0):
        add("high_error_rate", f"error rate {o.error_rate:.0%} over {o.probe_samples} synthetic requests",
            o.error_rate, det["error_rate_threshold"])
    if (o.latency_p95_ms is not None and o.latency_p95_ms >= det["latency_p95_threshold_ms"]
            and (o.tick_max_ms is None or o.tick_max_ms >= det["latency_p95_threshold_ms"])):
        add("high_latency", f"p95 latency {o.latency_p95_ms:.0f} ms", o.latency_p95_ms, det["latency_p95_threshold_ms"])
    if o.rss_mb is not None and o.rss_mb >= det["memory_rss_threshold_mb"]:
        add("high_memory", f"resident memory {o.rss_mb:.0f} MB", o.rss_mb, det["memory_rss_threshold_mb"])
    if o.cpu_percent is not None and o.cpu_percent >= det["cpu_threshold_percent"]:
        add("high_cpu", f"CPU {o.cpu_percent:.0f}%", o.cpu_percent, det["cpu_threshold_percent"])
    return sig


def pick_root(abnormal: dict[str, list[dict]], deps: dict[str, list[str]]) -> str:
    """Most upstream abnormal service: none of its own dependencies are abnormal."""
    candidates = [s for s in abnormal if not any(d in abnormal for d in deps.get(s, []))] or list(abnormal)

    def rank(s: str):
        return (max(SEVERITY_RANK[x["severity"]] for x in abnormal[s]), s)

    return sorted(candidates, key=rank, reverse=True)[0]


@dataclass
class DetectionResult:
    new_incident_id: int | None = None
    updated_incident_id: int | None = None
    closed_incident_ids: list[int] = field(default_factory=list)
    signals: dict[str, list[dict]] = field(default_factory=dict)


class Detector:
    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore, thresholds: dict):
        self.db = db
        self.audit = audit
        self.incidents = incidents
        self.det = thresholds["detection"]
        self._streak: dict[str, int] = {}
        self._first_abnormal: dict[str, str] = {}
        self._healthy_streak = 0

    def process(self, obs: Observation) -> DetectionResult:
        result = DetectionResult()
        if not obs.supervisor_ok:
            return result  # monitoring is blind to process state; never act on partial data
        deps = {name: o.depends_on for name, o in obs.services.items()}
        for name, o in obs.services.items():
            s = compute_signals(o, self.det)
            result.signals[name] = s
            if s:
                self._streak[name] = self._streak.get(name, 0) + 1
                self._first_abnormal.setdefault(name, obs.ts)
            else:
                self._streak[name] = 0
                self._first_abnormal.pop(name, None)

        abnormal = {n: result.signals[n] for n, c in self._streak.items()
                    if c >= self.det["consecutive_ticks_to_open"] and result.signals.get(n)}
        any_signal = any(result.signals.values())
        self._healthy_streak = 0 if any_signal else self._healthy_streak + 1

        active = self.incidents.active()
        if abnormal:
            if active:
                result.updated_incident_id = self._attach(active[0], abnormal, obs.ts)
            elif not self._in_cooldown():
                result.new_incident_id = self._open(abnormal, deps, obs.ts)
        elif self._healthy_streak >= self.det["auto_close_healthy_ticks"]:
            for inc in active:
                # Only incidents waiting on a human can be closed by observation;
                # the pipeline owns incidents that are executing or verifying.
                if inc["status"] == "escalated" or inc["stage"] == "awaiting_approval":
                    self.incidents.close(inc["id"], "System observed healthy again without SteadyOps executing a fix "
                                                    "(recovered externally or by an operator).", "steadyops-monitor")
                    result.closed_incident_ids.append(inc["id"])
        return result

    def _in_cooldown(self) -> bool:
        last = self.db.one("SELECT resolved_at FROM incidents WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT 1")
        if not last:
            return False
        return datetime.now(timezone.utc) - parse_ts(last["resolved_at"]) < timedelta(seconds=self.det["resolved_cooldown_s"])

    def _attach(self, inc: dict, abnormal: dict[str, list[dict]], ts: str) -> int:
        known = set(inc["affected_services"])
        new = sorted(set(abnormal) - known)
        affected = sorted(known | set(abnormal))
        self.db.update("incidents", inc["id"], {"last_seen_at": ts, "affected_services_json": affected})
        if new:
            self.incidents.event(inc["id"], inc["stage"], f"Additional services affected: {', '.join(new)}",
                                 {"signals": [s for n in new for s in abnormal[n]]})
        return inc["id"]

    def _open(self, abnormal: dict[str, list[dict]], deps: dict[str, list[str]], ts: str) -> int:
        root = pick_root(abnormal, deps)
        all_signals = [s for n in abnormal for s in abnormal[n]]
        severity = max((s["severity"] for s in all_signals), key=lambda x: SEVERITY_RANK[x])
        headline = sorted(abnormal[root], key=lambda s: SEVERITY_RANK[s["severity"]], reverse=True)[0]
        first = min(self._first_abnormal.get(n, ts) for n in abnormal)
        now = utcnow()
        incident_id = self.db.insert("incidents", {
            "status": "open", "stage": "detected", "severity": severity,
            "title": f"{root}: {headline['message']}", "detected_service": root,
            "affected_services_json": sorted(abnormal), "signals_json": all_signals,
            "first_abnormal_at": first, "detected_at": now, "last_seen_at": ts,
        })
        inj = self.db.one(
            "SELECT id FROM failure_injections WHERE incident_id IS NULL AND reset_at IS NULL "
            "ORDER BY injected_at DESC LIMIT 1"
        )
        if inj:
            self.db.execute("UPDATE failure_injections SET incident_id=? WHERE id=?", (incident_id, inj["id"]))
        self.incidents.event(incident_id, "detected",
                             f"Incident detected on {root}: {len(all_signals)} signal(s) across {', '.join(sorted(abnormal))}",
                             {"signals": all_signals})
        self.audit.record("steadyops-monitor", "incident_detected",
                          {"service": root, "severity": severity, "signals": [s["type"] for s in all_signals]}, incident_id)
        return incident_id
