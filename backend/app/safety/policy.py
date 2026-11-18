"""The SteadyOps safety policy engine.

This is the final authority over every action. The diagnosis engine can only
*propose*; the executor will only run a proposal for which `evaluate()` in the
`execute` phase returns ALLOW. Nothing a proposal contains (reason text,
confidence, parameters, log content) can switch a check off: the checks are
fixed code driven by a read-only policy file.

Outcomes:
  allow            - every check passed and no approval is needed
  require_approval - checks passed but a human must approve first
  deny             - at least one hard check failed; never executed
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from ..database.db import Database, parse_ts, utcnow
from ..remediation.actions import ACTIONS, RemediationAction, check, plan_hash
from .audit import AuditLog
from .settings_store import SettingsStore


@dataclass
class PolicyDecision:
    outcome: str
    action_id: str
    resource: str | None
    category: int | None
    reasons: list[str] = field(default_factory=list)
    approval_reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    def to_dict(self) -> dict:
        return asdict(self)


class PolicyEngine:
    def __init__(self, policy: dict, db: Database, audit: AuditLog, settings: SettingsStore,
                 actions: dict[str, RemediationAction] | None = None):
        self.policy = policy
        self.db = db
        self.audit = audit
        self.settings = settings
        self.actions = actions if actions is not None else ACTIONS

    # ------------------------------------------------------------------
    def authorize_read(self, action_id: str, incident_id: int | None = None) -> PolicyDecision:
        spec = self.policy["actions"].get(action_id)
        ok = spec is not None and spec.get("category") == 1 and action_id in self.actions
        d = PolicyDecision("allow" if ok else "deny", action_id, None, spec and spec.get("category"),
                           [] if ok else ["not an allowlisted read-only operation"],
                           checks=[check("read_only_allowlisted", ok, "category 1 operation")])
        self._record(d, "plan", incident_id, None)
        return d

    def evaluate(self, action_id: str, resource: str | None, params, *, phase: str, incident_id: int | None = None,
                 proposal_id: int | None = None, confidence: float | None = None, state: dict | None = None,
                 approved: bool = False, dry_run: dict | None = None, record: bool = True) -> PolicyDecision:
        checks: list[dict] = []
        deny: list[str] = []
        need_approval: list[str] = []

        def hard(name: str, passed: bool, detail: str) -> bool:
            checks.append(check(name, passed, detail))
            if not passed:
                deny.append(detail)
            return passed

        spec = self.policy["actions"].get(action_id) if isinstance(action_id, str) else None
        action = self.actions.get(action_id) if isinstance(action_id, str) else None
        category = spec.get("category") if spec else None
        decision = PolicyDecision("deny", str(action_id), resource, category, checks=checks)

        # 1. Allowlist: action must exist in BOTH the code catalogue and the policy file.
        if not hard("action_allowlisted", spec is not None and action is not None,
                    f"action '{action_id}' is {'allowlisted' if spec and action else 'NOT in the action allowlist'}"):
            return self._finish(decision, deny, need_approval, phase, incident_id, proposal_id, record)

        # 2. Forbidden actions are refused even with approval.
        if not hard("action_not_forbidden", not spec.get("forbidden", False),
                    f"'{action_id}' is a forbidden category-{category} operation in this deployment" if spec.get("forbidden")
                    else "action is not forbidden"):
            return self._finish(decision, deny, need_approval, phase, incident_id, proposal_id, record)

        if category == 1:
            checks.append(check("read_only", True, "category 1: read-only, always permitted"))
            return self._finish(decision, deny, need_approval, phase, incident_id, proposal_id, record)

        # 3. Strict parameter validation (unknown fields rejected).
        try:
            p = action.validate(params if isinstance(params, dict) else {})
            hard("params_valid", True, "parameters match the action schema")
        except ValidationError as exc:
            hard("params_valid", False, f"invalid parameters: {exc.errors()[0]['msg']} ({exc.errors()[0]['loc']})")
            return self._finish(decision, deny, need_approval, phase, incident_id, proposal_id, record)

        # 4. Resource allowlist.
        allowed = set(spec.get("resources", []))
        approval_res = set(spec.get("approval_resources", []))
        hard("resource_matches_params", getattr(p, "service", None) == resource,
             f"resource '{resource}' matches the action target")
        if hard("resource_allowlisted", resource in allowed | approval_res,
                f"resource '{resource}' is {'allowlisted' if resource in allowed | approval_res else 'NOT allowlisted'} for {action_id}"):
            if resource in approval_res:
                need_approval.append(f"{resource} is stateful/sensitive: {action_id} on it needs human approval")

        # 5. Emergency stop overrides everything that changes the system.
        es = self.settings.emergency_stop
        hard("emergency_stop_released", not es.get("engaged"),
             f"EMERGENCY STOP is engaged by {es.get('by')}: {es.get('reason')}" if es.get("engaged") else "emergency stop not engaged")

        # 6. Global rate limit.
        rl = self.policy["rate_limit"]
        since = (datetime.now(timezone.utc) - timedelta(seconds=rl["window_s"])).isoformat()
        recent = self.db.scalar("SELECT COUNT(*) FROM remediation_executions WHERE started_at>=?", (since,))
        hard("rate_limit", recent < rl["max_executions"],
             f"{recent}/{rl['max_executions']} executions in the last {rl['window_s']} s")

        # 7. Per-incident bounds: retries, total actions, blast radius.
        if incident_id is not None:
            same = self.db.scalar("SELECT COUNT(*) FROM remediation_executions WHERE incident_id=? AND action_id=?",
                                  (incident_id, action_id))
            max_same = spec.get("max_attempts_per_incident", 1)
            hard("attempt_limit", same < max_same, f"{same}/{max_same} prior attempts of {action_id} for this incident")
            total = self.db.scalar("SELECT COUNT(*) FROM remediation_executions WHERE incident_id=?", (incident_id,))
            hard("incident_action_limit", total < self.policy["max_actions_per_incident"],
                 f"{total}/{self.policy['max_actions_per_incident']} actions already taken for this incident")
            touched = {r["resource"] for r in self.db.query(
                "SELECT DISTINCT resource FROM remediation_executions WHERE incident_id=?", (incident_id,))}
            touched.add(resource)
            hard("blast_radius", len(touched) <= self.policy["max_services_affected_per_incident"],
                 f"{len(touched)} service(s) affected by remediation (max {self.policy['max_services_affected_per_incident']})")

        # 8. Action executability and preconditions.
        hard("executable", action.executable, "action has an executable implementation")
        if state is not None:
            for c in action.preconditions(p, state):
                hard(f"precondition:{c['name']}", c["passed"], c["detail"])

        # 9. Execution requires a fresh dry run of the same plan.
        if phase == "execute":
            if hard("dry_run_present", dry_run is not None, "a dry run was performed before execution"):
                age = (datetime.now(timezone.utc) - parse_ts(dry_run["created_at"])).total_seconds()
                hard("dry_run_fresh", age <= self.policy["dry_run_valid_for_s"],
                     f"dry run is {age:.0f}s old (max {self.policy['dry_run_valid_for_s']}s)")
                hard("dry_run_matches", plan_hash(dry_run["plan"]) == dry_run["plan_hash"]
                     and dry_run["plan"]["params"] == p.model_dump(), "executed parameters equal the dry-run plan")

        # 10. Approval requirements (only matter if nothing is denied).
        if category >= 3:
            need_approval.append(f"category {category} action")
        if confidence is not None and confidence < self.policy["auto_execute_min_confidence"]:
            need_approval.append(f"diagnosis confidence {confidence:.2f} is below the auto-execute threshold "
                                 f"{self.policy['auto_execute_min_confidence']}")
        if self.settings.autonomy_mode == "supervised":
            need_approval.append("autonomy mode is 'supervised': every change needs a human")
        if need_approval:
            checks.append(check("human_approval", approved,
                                "approved by a human" if approved else "awaiting human approval: " + "; ".join(need_approval)))
        decision.approval_reasons = need_approval
        if need_approval and approved:
            need_approval = []
        return self._finish(decision, deny, need_approval, phase, incident_id, proposal_id, record)

    def _finish(self, d: PolicyDecision, deny, need_approval, phase, incident_id, proposal_id, record) -> PolicyDecision:
        d.reasons = list(deny) if deny else list(need_approval)
        d.outcome = "deny" if deny else "require_approval" if need_approval else "allow"
        if record:
            self._record(d, phase, incident_id, proposal_id)
        return d

    def _record(self, d: PolicyDecision, phase: str, incident_id, proposal_id) -> None:
        self.db.insert("policy_decisions", {
            "proposal_id": proposal_id, "incident_id": incident_id, "phase": phase, "action_id": d.action_id[:64],
            "resource": d.resource, "outcome": d.outcome, "reasons_json": d.reasons, "checks_json": d.checks,
            "evaluated_at": utcnow(),
        })
        if d.outcome == "deny":
            self.audit.record("safety-policy", "action_blocked",
                              {"phase": phase, "action_id": d.action_id[:64], "resource": d.resource, "reasons": d.reasons},
                              incident_id)
        elif d.category and d.category > 1:
            self.audit.record("safety-policy", f"policy_{d.outcome}",
                              {"phase": phase, "action_id": d.action_id, "resource": d.resource, "reasons": d.reasons},
                              incident_id)
