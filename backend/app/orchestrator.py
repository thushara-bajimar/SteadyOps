"""Incident orchestrator.

Runs the monitor loop and drives each incident through:
Detection -> Investigation -> Diagnosis -> Safety Evaluation ->
Approval/Execution -> Verification -> Resolution/Escalation.

Fail-safe rule: any unexpected error inside the pipeline escalates the
incident to a human. The pipeline never "guesses" its way forward.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .database.db import utcnow
from .investigation.rca import CATEGORY_LABEL, diagnose
from .remediation.actions import plan_hash, read_state

log = logging.getLogger("steadyops.orchestrator")
READ_ACTIONS = ("check_health", "read_logs", "collect_metrics", "inspect_deployments")


class Orchestrator:
    def __init__(self, rt):
        self.rt = rt
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")
        self._running: set[int] = set()
        self._lock = threading.Lock()
        self.last_tick_at: str | None = None
        self.last_tick_error: str | None = None
        self.ticks = 0

    # -- monitor loop -------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        interval = self.rt.settings.thresholds["monitor"]["interval_s"]
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
                self.last_tick_error = None
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                self.last_tick_error = f"{type(exc).__name__}: {exc}"
                log.exception("monitor tick failed")
            if self.ticks % 900 == 0:
                self.rt.collector.prune()
            self._stop.wait(max(0.1, interval - (time.monotonic() - started)))

    def tick(self):
        obs = self.rt.collector.observe()
        res = self.rt.detector.process(obs)
        self.ticks += 1
        self.last_tick_at = utcnow()
        if res.new_incident_id:
            self.submit(self.run_pipeline, res.new_incident_id)
        return res

    def submit(self, fn, incident_id: int, *args):
        with self._lock:
            if incident_id in self._running:
                return None
            self._running.add(incident_id)

        def wrapped():
            try:
                fn(incident_id, *args)
            except Exception as exc:  # noqa: BLE001
                log.exception("pipeline failed for incident %s", incident_id)
                inc = self.rt.incidents.get(incident_id)
                if inc and inc["status"] == "open":
                    self.rt.incidents.escalate(incident_id, f"internal error in SteadyOps pipeline ({type(exc).__name__}); "
                                                            "failing safe - no further automated action")
            finally:
                with self._lock:
                    self._running.discard(incident_id)

        return self._pool.submit(wrapped)

    # -- pipeline -------------------------------------------------------------------
    def run_pipeline(self, iid: int) -> None:
        rt = self.rt
        inc = rt.incidents.get(iid)
        rt.incidents.set_stage(iid, "investigating", "Collecting evidence: health, metrics, logs, recent changes, dependencies")
        for a in READ_ACTIONS:
            rt.policy.authorize_read(a, iid)
        obs = rt.collector.latest or rt.collector.observe(record=False)
        ctx = rt.evidence.collect(inc, obs)
        n_evidence = rt.db.scalar("SELECT COUNT(*) FROM evidence WHERE incident_id=?", (iid,))
        rt.incidents.event(iid, "investigating", f"Collected {n_evidence} evidence items")

        d = diagnose(ctx, rt.settings.thresholds)
        diag_id = rt.db.insert("diagnoses", {
            "incident_id": iid, "category": d["category"], "root_service": d["root_service"],
            "suspected_cause": d["suspected_cause"], "confidence": d["confidence"],
            "confidence_level": d["confidence_level"], "ambiguous": int(d["ambiguous"]),
            "supporting_json": {"supporting": d["supporting"], "contradicting": d.get("contradicting", []), "best": d["best"],
                                "margin": d["margin"]},
            "alternatives_json": d["alternatives"], "recommended_json": d["recommended"], "created_at": utcnow()})
        rt.db.update("incidents", iid, {"category": d["category"], "root_service": d["root_service"], "diagnosed_at": utcnow()})
        rt.incidents.set_stage(iid, "diagnosed",
                               f"Diagnosis: {CATEGORY_LABEL[d['category']]} on {d['root_service']} "
                               f"(confidence {d['confidence']:.2f}, {d['confidence_level']}). {d['suspected_cause']}",
                               {"diagnosis_id": diag_id})
        rt.audit.record("steadyops-agent", "diagnosis_made", {"category": d["category"], "root_service": d["root_service"],
                                                              "confidence": d["confidence"]}, iid)

        if d["ambiguous"]:
            rt.incidents.escalate(iid, f"diagnosis is ambiguous (confidence {d['confidence']:.2f}, margin {d['margin']:.2f}); "
                                       "not acting on a guess")
            return
        rec = d["recommended"]
        if not rec or not rec.get("action_id"):
            rt.incidents.escalate(iid, f"no safe remediation available: {(rec or {}).get('reason', 'unsupported category')}")
            return

        action = rt.actions[rec["action_id"]]
        now = utcnow()
        pid = rt.db.insert("remediation_proposals", {
            "incident_id": iid, "diagnosis_id": diag_id, "action_id": rec["action_id"], "resource": rec["resource"],
            "params_json": rec["params"], "reason": rec["reason"], "risk_level": action.risk_level,
            "expected_impact": action.expected_impact(action.validate(rec["params"])),
            "rollback_available": int(action.rollback_available), "status": "proposed", "created_at": now, "updated_at": now})
        rt.audit.record("steadyops-agent", "remediation_proposed", {"proposal_id": pid, "action_id": rec["action_id"],
                                                                    "resource": rec["resource"], "params": rec["params"]}, iid)
        self._dry_run_and_decide(iid, pid, d["confidence"])

    def _dry_run_and_decide(self, iid: int, pid: int, confidence: float) -> None:
        rt = self.rt
        prop = rt.db.one("SELECT * FROM remediation_proposals WHERE id=?", (pid,))
        from .database.db import loads

        params = loads(prop["params_json"], {})
        action = rt.actions[prop["action_id"]]
        state = read_state(rt.read, rt.db, prop["resource"])
        plan = action.dry_run(params, state)
        rt.db.insert("dry_runs", {"proposal_id": pid, "plan_json": plan, "plan_hash": plan_hash(plan), "created_at": utcnow()})
        rt.incidents.set_stage(iid, "safety_evaluation", f"Dry run of {prop['action_id']}: " + " -> ".join(plan["steps"]),
                               {"plan": plan})

        decision = rt.policy.evaluate(prop["action_id"], prop["resource"], params, phase="plan", incident_id=iid,
                                      proposal_id=pid, confidence=confidence, state=state)
        if decision.outcome == "deny":
            rt.db.execute("UPDATE remediation_proposals SET status='blocked', updated_at=? WHERE id=?", (utcnow(), pid))
            rt.incidents.event(iid, "safety_evaluation", "Safety policy DENIED the proposal: " + "; ".join(decision.reasons),
                               decision.to_dict())
            rt.incidents.escalate(iid, "proposed remediation blocked by safety policy: " + "; ".join(decision.reasons))
            return
        if decision.outcome == "require_approval":
            reason = "; ".join(decision.reasons)
            rt.db.execute("UPDATE remediation_proposals SET status='awaiting_approval', approval_reason=?, updated_at=? WHERE id=?",
                          (reason, utcnow(), pid))
            rt.incidents.set_stage(iid, "awaiting_approval", f"Waiting for human approval: {reason}", decision.to_dict())
            rt.audit.record("safety-policy", "approval_requested", {"proposal_id": pid, "reason": reason}, iid)
            return
        rt.incidents.event(iid, "safety_evaluation", f"Safety policy ALLOWED {prop['action_id']} on {prop['resource']} "
                           f"({len(decision.checks)} checks passed)", decision.to_dict())
        rt.executor.run(pid, "steadyops-agent")

    # -- human decisions --------------------------------------------------------------
    def decide(self, proposal_id: int, approve: bool, approver: str, comment: str | None) -> dict:
        rt = self.rt
        prop = rt.db.one("SELECT * FROM remediation_proposals WHERE id=?", (proposal_id,))
        if prop is None:
            raise LookupError("proposal not found")
        if prop["status"] != "awaiting_approval":
            raise ValueError(f"proposal is '{prop['status']}', not awaiting approval")
        iid = prop["incident_id"]
        decision = "approved" if approve else "rejected"
        rt.db.insert("approvals", {"proposal_id": proposal_id, "decision": decision, "approver": approver,
                                   "comment": comment, "decided_at": utcnow()})
        rt.audit.record(approver, f"proposal_{decision}", {"proposal_id": proposal_id, "comment": comment}, iid)
        if not approve:
            rt.db.execute("UPDATE remediation_proposals SET status='rejected', updated_at=? WHERE id=?", (utcnow(), proposal_id))
            rt.incidents.event(iid, "awaiting_approval", f"{approver} REJECTED the proposal: {comment or 'no comment'}")
            rt.incidents.escalate(iid, f"operator {approver} rejected the proposed remediation", actor=approver)
            return {"status": "rejected"}

        # Re-run the dry run: the approved plan must still describe reality.
        params = loads_params(prop)
        action = rt.actions[prop["action_id"]]
        old = rt.executor.latest_dry_run(proposal_id)
        state = read_state(rt.read, rt.db, prop["resource"])
        plan = action.dry_run(params, state)
        new_hash = plan_hash(plan)
        rt.db.insert("dry_runs", {"proposal_id": proposal_id, "plan_json": plan, "plan_hash": new_hash, "created_at": utcnow()})
        if old and old["plan_hash"] != new_hash:
            rt.incidents.event(iid, "awaiting_approval", "System state changed since the plan was shown; the refreshed dry "
                               "run differs from what was approved", {"approved_plan": old["plan"], "current_plan": plan})
            rt.db.execute("UPDATE remediation_proposals SET status='expired', updated_at=? WHERE id=?", (utcnow(), proposal_id))
            rt.incidents.escalate(iid, "approved plan no longer matches system state; re-assessment required", actor=approver)
            return {"status": "expired"}
        rt.db.execute("UPDATE remediation_proposals SET status='approved', updated_at=? WHERE id=?", (utcnow(), proposal_id))
        rt.incidents.event(iid, "awaiting_approval", f"{approver} APPROVED {prop['action_id']} on {prop['resource']}"
                           + (f": {comment}" if comment else ""))
        self.submit(lambda i, p=proposal_id, a=approver: rt.executor.run(p, a), iid)
        return {"status": "approved"}


def loads_params(prop: dict) -> dict:
    from .database.db import loads

    return loads(prop["params_json"], {})
