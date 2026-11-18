"""Remediation executor: the only code path that changes the demo stack.

Sequence for one proposal:
  policy re-check (execute phase) -> capture pre-state -> bounded execution
  (timeout + retry limit + emergency-stop check before every attempt) ->
  independent verification -> resolve, or roll back (if the system is worse
  and rollback is available) and escalate.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from ..clients import SupervisorError, SupervisorOpsClient, SupervisorReadClient
from ..database.db import Database, loads, utcnow
from ..incidents import IncidentStore
from ..monitoring.collector import Collector
from ..safety.audit import AuditLog
from ..safety.policy import PolicyEngine
from ..safety.settings_store import SettingsStore
from ..verification.verifier import Verifier, health_score
from .actions import ACTIONS, read_state

_execution_lock = threading.Lock()  # one remediation at a time, system-wide


class Executor:
    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore, policy: PolicyEngine,
                 ops: SupervisorOpsClient, read: SupervisorReadClient, collector: Collector, verifier: Verifier,
                 settings: SettingsStore, policy_cfg: dict, actions=None):
        self.db, self.audit, self.incidents, self.policy = db, audit, incidents, policy
        self.ops, self.read, self.collector, self.verifier, self.settings = ops, read, collector, verifier, settings
        self.policy_cfg = policy_cfg
        self.actions = actions if actions is not None else ACTIONS
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="action")

    def latest_dry_run(self, proposal_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM dry_runs WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (proposal_id,))
        if row:
            row["plan"] = loads(row.pop("plan_json"), {})
        return row

    def run(self, proposal_id: int, actor: str) -> str:
        with _execution_lock:
            return self._run(proposal_id, actor)

    def _run(self, proposal_id: int, actor: str) -> str:
        prop = self.db.one("SELECT * FROM remediation_proposals WHERE id=?", (proposal_id,))
        if prop is None or prop["status"] not in ("proposed", "approved"):
            return "skipped"  # only fresh or human-approved proposals can ever execute
        incident = self.incidents.get(prop["incident_id"])
        iid = incident["id"]
        params = loads(prop["params_json"], {})
        action = self.actions[prop["action_id"]]
        approval = self.db.one("SELECT * FROM approvals WHERE proposal_id=?", (proposal_id,))
        approved = bool(approval and approval["decision"] == "approved")
        diag = self.db.one("SELECT confidence FROM diagnoses WHERE id=?", (prop["diagnosis_id"],))

        # -- 1. Final policy gate ------------------------------------------------
        state = read_state(self.read, self.db, prop["resource"])
        decision = self.policy.evaluate(prop["action_id"], prop["resource"], params, phase="execute", incident_id=iid,
                                        proposal_id=proposal_id, confidence=diag and diag["confidence"], state=state,
                                        approved=approved, dry_run=self.latest_dry_run(proposal_id))
        if not decision.allowed:
            self._set_prop(proposal_id, "blocked")
            self.incidents.event(iid, "safety_evaluation", f"Execution blocked by safety policy ({decision.outcome}): "
                                 + "; ".join(decision.reasons), decision.to_dict())
            self.incidents.escalate(iid, "safety policy blocked execution: " + "; ".join(decision.reasons))
            return "blocked"

        # -- 2. Pre-state for rollback and comparison ---------------------------------
        check_set = sorted(set(incident["affected_services"]) | {prop["resource"]}
                           | {d for d, s in state["all"].items() if prop["resource"] in s["depends_on"]})
        pre_obs = self.collector.observe(use_window=False, record=False)
        pre_service = state["service"] or {}
        pre_state = {"version": pre_service.get("version"), "config_version": pre_service.get("config_version"),
                     "process_state": pre_service.get("state"),
                     "health": {s: pre_obs.services[s].health for s in check_set if s in pre_obs.services},
                     "health_score": health_score(pre_obs, check_set)}

        now = utcnow()
        exec_id = self.db.insert("remediation_executions", {
            "proposal_id": proposal_id, "incident_id": iid, "action_id": prop["action_id"], "resource": prop["resource"],
            "status": "running", "attempts": 0, "pre_state_json": pre_state, "started_at": now})
        self._set_prop(proposal_id, "executing")
        self.incidents.set_stage(iid, "executing", f"Executing {prop['action_id']} on {prop['resource']} "
                                 f"(authorised by {'human approval from ' + approval['approver'] if approved else 'safety policy, category 2 auto-permitted'})",
                                 {"params": params, "pre_state": pre_state}, remediation_started_at=now)
        self.audit.record(actor, "remediation_started", {"action_id": prop["action_id"], "resource": prop["resource"],
                                                         "params": params, "execution_id": exec_id}, iid)

        # -- 3. Bounded execution -------------------------------------------------
        spec = self.policy_cfg["actions"][prop["action_id"]]
        p = action.validate(params)
        attempts, output, error, status = 0, None, None, "command_failed"
        for attempt in range(1 + spec.get("max_retries", 0)):
            if self.settings.emergency_stop.get("engaged"):
                status, error = "aborted", "emergency stop engaged before attempt"
                break
            attempts = attempt + 1
            fut = self._pool.submit(action.execute, p, self.ops)
            try:
                output = fut.result(timeout=spec.get("timeout_s", 30))
                status, error = "command_succeeded", None
                break
            except FutureTimeout:
                status, error = "command_failed", f"timed out after {spec.get('timeout_s')}s (not retried: state unknown)"
                break
            except SupervisorError as exc:
                status, error = "command_failed", str(exc)
                self.incidents.event(iid, "executing", f"Attempt {attempts} failed: {exc}")
        finished = utcnow()
        self.db.update("remediation_executions", exec_id, {"status": status, "attempts": attempts,
                                                           "output_json": output or {}, "error": error, "finished_at": finished})
        self.db.update("incidents", iid, {"remediation_completed_at": finished})
        self.audit.record(actor, "remediation_finished", {"execution_id": exec_id, "status": status, "attempts": attempts,
                                                          "error": error}, iid)
        self.collector.reset_window()

        if status == "aborted":
            self._set_prop(proposal_id, "failed")
            self.incidents.escalate(iid, "emergency stop engaged; remediation aborted before execution")
            return "aborted"

        # -- 4. Verification (even if the command "succeeded") -----------------------
        msg = ("Command completed; this is NOT proof of recovery. Verifying..." if status == "command_succeeded"
               else f"Command failed ({error}); checking actual system state anyway...")
        self.incidents.set_stage(iid, "verifying", msg, {"check_set": check_set})
        result = self.verifier.verify(check_set)
        self.db.insert("verifications", {"execution_id": exec_id, "incident_id": iid, "result": result["result"],
                                         "summary": result["summary"], "checks_json": result["checks"],
                                         "started_at": result["started_at"], "finished_at": result["finished_at"]})
        self.audit.record("steadyops-verifier", f"verification_{result['result']}", {"summary": result["summary"]}, iid)

        if result["result"] == "passed":
            self._set_prop(proposal_id, "succeeded")
            self.incidents.resolve(iid, f"Recovered by {prop['action_id']} on {prop['resource']}. Verified: {result['summary']}.")
            return "resolved"

        # -- 5. Failed verification: roll back if worse, then escalate ---------------
        self._set_prop(proposal_id, "failed")
        self.incidents.event(iid, "verifying", f"Verification FAILED: {result['summary']}")
        post_score = health_score(result["final_obs"], check_set)
        worse = post_score < pre_state["health_score"]
        reason = f"remediation {prop['action_id']} did not restore service ({result['summary']})"
        if worse and action.rollback_available:
            self._rollback(exec_id, iid, proposal_id, action, p, pre_state, post_score)
            reason += "; system was worse after the action so it was rolled back"
        elif worse:
            self.db.insert("rollbacks", {"execution_id": exec_id, "incident_id": iid, "status": "skipped",
                                         "reason": "system worse but action has no rollback", "details_json": {},
                                         "started_at": utcnow(), "finished_at": utcnow()})
        self.incidents.escalate(iid, reason)
        return "escalated"

    def _rollback(self, exec_id, iid, proposal_id, action, p, pre_state, post_score) -> None:
        started = utcnow()
        if self.settings.emergency_stop.get("engaged"):
            self.db.insert("rollbacks", {"execution_id": exec_id, "incident_id": iid, "status": "skipped",
                                         "reason": "emergency stop engaged", "details_json": {}, "started_at": started,
                                         "finished_at": utcnow()})
            return
        self.incidents.event(iid, "verifying", f"System is worse than before the action (health score "
                             f"{pre_state['health_score']} -> {post_score}). Rolling back to the pre-action state.")
        try:
            out = action.rollback(p, pre_state, self.ops)
            status, details = "succeeded", {"output": out, "restored": {k: pre_state[k] for k in ("version", "config_version")}}
        except (SupervisorError, NotImplementedError) as exc:
            status, details = "failed", {"error": str(exc)}
        self.collector.reset_window()
        self.db.insert("rollbacks", {"execution_id": exec_id, "incident_id": iid, "status": status,
                                     "reason": f"post-action health score {post_score} < pre-action {pre_state['health_score']}",
                                     "details_json": details, "started_at": started, "finished_at": utcnow()})
        self._set_prop(proposal_id, "rolled_back" if status == "succeeded" else "failed")
        self.audit.record("steadyops-agent", f"rollback_{status}", details, iid)
        self.incidents.event(iid, "verifying", f"Rollback {status}: restored {pre_state.get('version')} / "
                             f"{pre_state.get('config_version')}")

    def _set_prop(self, proposal_id: int, status: str) -> None:
        self.db.execute("UPDATE remediation_proposals SET status=?, updated_at=? WHERE id=?", (status, utcnow(), proposal_id))
