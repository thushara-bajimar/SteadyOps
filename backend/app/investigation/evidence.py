"""Evidence collection.

Gathers facts for one incident, stores each item (with its source and a
timestamp) in the `evidence` table, and returns a structured *context* that the
root-cause engine reasons over. Every piece of information comes from a real
read: supervisor state, health endpoints, stored monitoring snapshots, the
change history, or application logs. Nothing is synthesised.

Log lines are treated strictly as data: only structured fields are inspected
and every line is redacted before it is stored.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..clients import SupervisorError, SupervisorReadClient
from ..database.db import Database, loads, parse_ts, utcnow
from ..monitoring.collector import Observation
from ..safety.redact import redact

LEVELS = ("WARN", "ERROR", "FATAL")


def analyse_logs(records: list[dict]) -> dict:
    """Count structured failure indicators in a service's logs."""
    out = {"exceptions": 0, "exception_versions": [], "exception_samples": [], "config_errors": 0,
           "dependency_errors": {}, "memory_warnings": 0, "artifact_errors": 0, "provider_outage": 0,
           "process_exits": 0}
    versions = set()
    for r in records:
        level = str(r.get("level", "")).upper()
        msg = str(r.get("msg", "")).lower()
        if level == "ERROR" and "exception" in r:
            out["exceptions"] += 1
            versions.add(str(r.get("version")))
            if len(out["exception_samples"]) < 3:
                out["exception_samples"].append(str(r.get("exception"))[:200])
        if level == "FATAL" and "configuration" in msg:
            out["config_errors"] += 1
        if r.get("exit_code") == 78:
            out["config_errors"] += 1
        if "dependency" in r and level in ("ERROR", "WARN"):
            dep = str(r["dependency"])
            out["dependency_errors"][dep] = out["dependency_errors"].get(dep, 0) + 1
        if level == "WARN" and ("cache" in msg or "memory" in msg):
            out["memory_warnings"] += 1
        if "artifact" in r:
            out["artifact_errors"] += 1
        if "provider" in r and level == "ERROR":
            out["provider_outage"] += 1
        if "process exited" in msg:
            out["process_exits"] += 1
    out["exception_versions"] = sorted(versions)
    return out


class EvidenceCollector:
    def __init__(self, db: Database, read: SupervisorReadClient, thresholds: dict):
        self.db = db
        self.read = read
        self.t = thresholds["investigation"]

    def _store(self, incident_id: int, kind: str, source: str, summary: str, data, service: str | None = None) -> int:
        return self.db.insert("evidence", {"incident_id": incident_id, "kind": kind, "source": source, "service": service,
                                           "summary": summary, "data_json": redact(data), "collected_at": utcnow()})

    def _last_change_before(self, service: str, ts: str) -> list[str]:
        row = self.db.one(
            "SELECT MAX(t) AS t FROM ("
            " SELECT deployed_at AS t FROM deployments WHERE service=? AND deployed_at<=?"
            " UNION ALL SELECT changed_at FROM config_changes WHERE service=? AND changed_at<=?"
            " UNION ALL SELECT ts FROM runtime_events WHERE service=? AND ts<=? AND kind='restart')",
            (service, ts, service, ts, service, ts))
        return [row["t"]] if row and row["t"] else []

    def collect(self, incident: dict, obs: Observation) -> dict:
        iid = incident["id"]
        refs: dict[str, int] = {}
        services = obs.services
        affected = set(incident["affected_services"]) | {incident["detected_service"]}
        candidates = set(affected)
        for s in list(affected):
            if s in services:
                candidates |= set(services[s].depends_on)
        dependents = {n: [m for m, o in services.items() if n in o.depends_on] for n in services}

        # 1. Current health of every service --------------------------------
        health = {n: {"health": o.health, "process_state": o.process_state, "http_status": o.http_status,
                      "health_error": o.health_error, "response_ms": o.response_ms,
                      "checks": (o.health_body or {}).get("checks"), "version": o.version,
                      "config_version": o.config_version, "restarts_recent": o.restarts_recent,
                      "last_exit_code": o.last_exit_code, "rss_mb": o.rss_mb, "cpu_percent": o.cpu_percent,
                      "error_rate": o.error_rate, "latency_p95_ms": o.latency_p95_ms}
                  for n, o in services.items()}
        unhealthy = [n for n, h in health.items() if h["health"] != "healthy"]
        refs["health"] = self._store(iid, "service_health", "health endpoints + supervisor",
                                     f"{len(services) - len(unhealthy)}/{len(services)} services healthy; "
                                     f"unhealthy: {', '.join(unhealthy) or 'none'}", health)

        # 2. Detector signals -------------------------------------------------
        refs["signals"] = self._store(iid, "signals", "detector",
                                      "; ".join(f"{s['service']}: {s['message']}" for s in incident["signals"])[:500],
                                      incident["signals"])

        # 3. Metric history for candidate services ---------------------------
        since = (parse_ts(incident["first_abnormal_at"]) - timedelta(seconds=60)).isoformat()
        metrics = {}
        for s in sorted(candidates):
            rows = self.db.query(
                "SELECT ts, health, error_rate, latency_p95_ms, rss_mb, cpu_percent, restarts_recent, response_ms "
                "FROM monitoring_snapshots WHERE service=? AND ts>=? ORDER BY ts", (s, since))
            rss = [r["rss_mb"] for r in rows if r["rss_mb"] is not None]
            metrics[s] = {"samples": len(rows), "rss_first_mb": rss[0] if rss else None, "rss_max_mb": max(rss) if rss else None,
                          "rss_growth_mb": round(max(rss) - rss[0], 1) if rss else 0.0, "series": rows[-40:]}
            refs[f"metrics:{s}"] = self._store(
                iid, "metrics", "monitoring_snapshots",
                f"{s}: {len(rows)} samples; RSS {metrics[s]['rss_first_mb']}->{metrics[s]['rss_max_mb']} MB", metrics[s], s)

        # 4. Recent changes ---------------------------------------------------
        lookback = (datetime.now(timezone.utc) - timedelta(seconds=self.t["change_lookback_s"])).isoformat()
        changes = {}
        for s in sorted(candidates):
            changes[s] = {
                "deployments": self.db.query("SELECT service, from_version, to_version, actor, deployed_at FROM deployments "
                                             "WHERE service=? AND deployed_at>=? ORDER BY deployed_at", (s, lookback)),
                "config_changes": self.db.query("SELECT service, from_config, to_config, actor, changed_at FROM config_changes "
                                                "WHERE service=? AND changed_at>=? ORDER BY changed_at", (s, lookback)),
                "runtime_events": [dict(r, data=loads(r.pop("data_json"), {})) for r in self.db.query(
                    "SELECT kind, actor, data_json, ts FROM runtime_events WHERE service=? AND ts>=? ORDER BY ts DESC LIMIT 10",
                    (s, lookback))],
            }
        events = []
        for svc_name, c in changes.items():
            for d in c["deployments"]:
                events.append((d["deployed_at"], f"{svc_name} deployed {d['from_version']}->{d['to_version']} by {d['actor']} at {d['deployed_at'][11:19]}"))
            for d in c["config_changes"]:
                events.append((d["changed_at"], f"{svc_name} config {d['from_config']}->{d['to_config']} by {d['actor']} at {d['changed_at'][11:19]}"))
        events.sort(reverse=True)
        # Most recent first; the full history stays in the evidence data.
        summary = [e[1] for e in events[:3]] + ([f"(+{len(events) - 3} earlier changes in the lookback window)"] if len(events) > 3 else [])
        refs["changes"] = self._store(iid, "recent_changes", "deployment & config history",
                                      "; ".join(summary) or "no deployments or config changes in the lookback window", changes)

        # 5. Known-good versions/configs ------------------------------------
        known_good = {}
        for s in sorted(candidates):
            rows = self.db.query("SELECT kind, value, verified_at FROM known_good WHERE service=? ORDER BY verified_at DESC", (s,))
            known_good[s] = {"versions": [r["value"] for r in rows if r["kind"] == "version"],
                             "configs": [r["value"] for r in rows if r["kind"] == "config"]}
        refs["known_good"] = self._store(iid, "known_good", "verified healthy history",
                                         "; ".join(f"{s}: versions {v['versions']} configs {v['configs']}" for s, v in known_good.items()),
                                         known_good)

        # 6. Logs (redacted, filtered) ----------------------------------------
        logs, findings = {}, {}
        window_start = (parse_ts(incident["first_abnormal_at"]) - timedelta(seconds=90)).isoformat()
        for s in sorted(candidates):
            # Logs written before the service's most recent deliberate change (deploy, config change,
            # operator/agent restart) describe a PREVIOUS state - e.g. the already-fixed crash loop of an
            # earlier incident - and must not count as evidence about the current one. Crash-loop
            # auto-restarts and process exits are not changes, so they do not move the window.
            log_since = max([window_start] + self._last_change_before(s, incident["detected_at"]))
            try:
                records = self.read.logs(s, lines=self.t["log_lines_per_service"])
            except SupervisorError as exc:
                records = []
                self._store(iid, "logs", "supervisor", f"{s}: log collection failed ({exc})", {}, s)
            recent = [redact(r) for r in records if str(r.get("ts", "")) >= log_since]  # redact at ingestion
            findings[s] = analyse_logs(recent)
            notable = [redact(r) for r in recent if str(r.get("level", "")).upper() in LEVELS]
            logs[s] = notable[-self.t["max_log_evidence_lines"]:]
            f = findings[s]
            refs[f"logs:{s}"] = self._store(
                iid, "logs", "application logs", f"{s}: {len(notable)} warning/error lines; exceptions={f['exceptions']}, "
                f"config_errors={f['config_errors']}, dependency_errors={f['dependency_errors']}", {"lines": logs[s], "findings": f}, s)

        # 7. Dependency status --------------------------------------------------
        dep_status = {s: {d: health.get(d, {}).get("health", "unknown") for d in services[s].depends_on}
                      for s in sorted(affected) if s in services}
        refs["dependencies"] = self._store(iid, "dependency_status", "dependency graph + health",
                                           "; ".join(f"{s} -> {v}" for s, v in dep_status.items()), dep_status)

        # 8. Previous remediation attempts ---------------------------------------
        hour = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        prev = self.db.query("SELECT incident_id, action_id, resource, status, started_at FROM remediation_executions "
                             "WHERE started_at>=? ORDER BY started_at DESC LIMIT 10", (hour,))
        refs["previous"] = self._store(iid, "previous_remediations", "SteadyOps execution history",
                                       f"{len(prev)} remediation(s) in the last hour", prev)

        return {
            "incident": {k: incident[k] for k in ("id", "detected_service", "affected_services", "signals",
                                                  "first_abnormal_at", "detected_at")},
            "services": {n: dict(health[n], depends_on=services[n].depends_on, dependents=dependents.get(n, []),
                                 stateful=services[n].stateful,
                                 signals=[s["type"] for s in incident["signals"] if s["service"] == n])
                         for n in services},
            "candidates": sorted(candidates),
            "metrics": {s: {k: v for k, v in m.items() if k != "series"} for s, m in metrics.items()},
            "changes": changes,
            "known_good": known_good,
            "log_findings": findings,
            "previous_remediations": prev,
            "evidence_refs": refs,
        }
