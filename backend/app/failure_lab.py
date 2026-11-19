"""Failure Lab: controlled, reversible fault injection for live demonstrations.

Every injection is recorded with its timestamp and the *expected* diagnosis
and remediation, so analytics can measure detection time, recovery time and
diagnosis accuracy against ground truth. The expected values are never shown
to the diagnosis engine.
"""
from __future__ import annotations

from .clients import ChaosClient
from .database.db import Database, utcnow
from .safety.audit import AuditLog

SCENARIOS = {
    "bad_deployment": {
        "title": "Bad deployment",
        "description": "CI deploys api-service 1.5.0, which contains a pricing regression: orders without a discount "
                       "raise a TypeError (about half of all requests fail with HTTP 500).",
        "target_service": "api-service",
        "expected_category": "bad_deployment",
        "expected_action": "rollback_deployment",
        "expected_outcome": "resolved",
        "watch_for": "error rate rises, deployment correlated, rollback to 1.4.2, verification passes",
    },
    "broken_config": {
        "title": "Broken configuration",
        "description": "A config sync applies api-cfg-8 with DB_POOL_SIZE='unlimited'. api-service refuses to start "
                       "(exit code 78) and enters a crash loop.",
        "target_service": "api-service",
        "expected_category": "broken_config",
        "expected_action": "restore_config",
        "expected_outcome": "resolved",
        "watch_for": "service down + crash loop, config change correlated, restore api-cfg-7",
    },
    "resource_exhaustion": {
        "title": "Resource exhaustion (memory leak)",
        "description": "An unbounded report cache leaks memory in api-service (bounded at 260 MB, auto-released after "
                       "10 minutes) and latency rises with memory pressure.",
        "target_service": "api-service",
        "expected_category": "resource_exhaustion",
        "expected_action": "restart_service",
        "expected_outcome": "resolved",
        "watch_for": "RSS crosses 200 MB with no recent change, safe restart of api-service",
    },
    "dependency_failure": {
        "title": "Dependency failure (recoverable)",
        "description": "dependency-service (inventory) deadlocks its worker pool: every call stalls and times out. "
                       "api-service is healthy itself but fails requests that need inventory.",
        "target_service": "dependency-service",
        "expected_category": "dependency_failure",
        "expected_action": "restart_service",
        "expected_outcome": "resolved",
        "watch_for": "root cause attributed to dependency-service, api-service NOT restarted",
    },
    "dependency_outage": {
        "title": "Dependency outage (not recoverable -> escalation)",
        "description": "The inventory provider behind dependency-service is down. A restart cannot fix it, so SteadyOps "
                       "must try once, verify, see it fail and wake a human.",
        "target_service": "dependency-service",
        "expected_category": "dependency_failure",
        "expected_action": "restart_service",
        "expected_outcome": "escalated",
        "watch_for": "one bounded restart, verification fails, no retry loop, escalation",
    },
    "bad_deployment_corrupt_rollback": {
        "title": "Bad deployment + corrupted rollback artifact",
        "description": "Same bad 1.5.0 release, but the 1.4.2 artifact is corrupted, so the rollback itself takes the "
                       "service fully down. SteadyOps must notice it made things worse, undo its own action and escalate.",
        "target_service": "api-service",
        "expected_category": "bad_deployment",
        "expected_action": "rollback_deployment",
        "expected_outcome": "escalated",
        "watch_for": "verification fails, health worse than before, automatic rollback to 1.5.0, escalation",
    },
}


class FailureLab:
    def __init__(self, db: Database, audit: AuditLog, chaos: ChaosClient):
        self.db = db
        self.audit = audit
        self.chaos = chaos

    def active(self) -> dict | None:
        return self.db.one("SELECT * FROM failure_injections WHERE reset_at IS NULL ORDER BY id DESC LIMIT 1")

    def inject(self, scenario: str, operator: str) -> dict:
        if scenario not in SCENARIOS:
            raise LookupError("unknown scenario")
        if self.active():
            raise ValueError("another scenario is active; reset it first so measurements stay clean")
        meta = SCENARIOS[scenario]
        self.chaos.inject(scenario)
        inj_id = self.db.insert("failure_injections", {
            "scenario": scenario, "target_service": meta["target_service"], "expected_category": meta["expected_category"],
            "expected_action": meta["expected_action"], "actor": operator, "injected_at": utcnow()})
        self.audit.record(operator, "failure_injected", {"scenario": scenario, "injection_id": inj_id})
        return {"injection_id": inj_id, "scenario": scenario}

    def exclude(self, injection_id: int, operator: str, reason: str) -> dict:
        """Mark a run as invalid for measurement (it stays visible, flagged, and audited)."""
        row = self.db.one("SELECT id, excluded_reason FROM failure_injections WHERE id=?", (injection_id,))
        if row is None:
            raise LookupError("injection not found")
        if row["excluded_reason"]:
            raise ValueError("injection already excluded")
        self.db.execute("UPDATE failure_injections SET excluded_reason=? WHERE id=?", (reason, injection_id))
        self.audit.record(operator, "injection_excluded", {"injection_id": injection_id, "reason": reason})
        return {"injection_id": injection_id, "excluded_reason": reason}

    def reset(self, operator: str) -> dict:
        result = self.chaos.reset()
        now = utcnow()
        self.db.execute("UPDATE failure_injections SET reset_at=? WHERE reset_at IS NULL", (now,))
        self.audit.record(operator, "failure_lab_reset", {"actions": result.get("actions", [])})
        return result
