"""Evaluation metrics computed exclusively from recorded timestamps and outcomes.

No value here is estimated or hard-coded. If nothing has been measured yet,
the metric is returned as null rather than as a flattering default.
"""
from __future__ import annotations

from statistics import mean, median

from .database.db import Database, seconds_between


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _stats(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {"n": len(values), "mean": round(mean(values), 2), "median": round(median(values), 2),
            "min": round(min(values), 2), "max": round(max(values), 2)}


def compute(db: Database) -> dict:
    runs = db.query(
        "SELECT f.*, i.detected_at, i.diagnosed_at, i.remediation_started_at, i.remediation_completed_at, i.resolved_at, "
        "i.escalated_at, i.closed_at, i.status AS incident_status, i.category AS diagnosed_category, "
        "i.root_service AS diagnosed_service FROM failure_injections f LEFT JOIN incidents i ON i.id=f.incident_id ORDER BY f.id")
    per_run = []
    for r in runs:
        execs = db.query("SELECT action_id, resource FROM remediation_executions WHERE incident_id=? ORDER BY id",
                         (r["incident_id"],)) if r["incident_id"] else []
        first = execs[0] if execs else None
        outcome = ("not_detected" if not r["incident_id"] else
                   "resolved" if r["incident_status"] == "resolved" else
                   "escalated" if r["escalated_at"] else r["incident_status"])
        per_run.append({
            "injection_id": r["id"], "scenario": r["scenario"], "incident_id": r["incident_id"], "injected_at": r["injected_at"],
            "expected_category": r["expected_category"], "diagnosed_category": r["diagnosed_category"],
            "diagnosis_correct": (r["diagnosed_category"] == r["expected_category"]
                                  and r["diagnosed_service"] == r["target_service"]) if r["diagnosed_category"] else None,
            "expected_action": r["expected_action"], "executed_action": first and first["action_id"],
            "action_correct": (first["action_id"] == r["expected_action"] and first["resource"] == r["target_service"]) if first else None,
            "outcome": outcome,
            "ttd_s": seconds_between(r["injected_at"], r["detected_at"]),
            "time_to_diagnose_s": seconds_between(r["detected_at"], r["diagnosed_at"]),
            "remediation_duration_s": seconds_between(r["remediation_started_at"], r["remediation_completed_at"]),
            "ttr_s": seconds_between(r["injected_at"], r["resolved_at"]),
            "detect_to_recover_s": seconds_between(r["detected_at"], r["resolved_at"]),
            "time_to_escalate_s": seconds_between(r["injected_at"], r["escalated_at"]),
        })

    incidents = db.query("SELECT id, status, escalated_at FROM incidents")
    linked = {r["incident_id"] for r in runs if r["incident_id"]}
    diagnosed = [p for p in per_run if p["diagnosis_correct"] is not None]
    acted = [p for p in per_run if p["action_correct"] is not None]
    closed_runs = [p for p in per_run if p["outcome"] in ("resolved", "escalated", "closed", "not_detected")]
    ver = db.query("SELECT result FROM verifications")
    rb = db.query("SELECT status FROM rollbacks WHERE status != 'skipped'")
    blocked = db.query("SELECT phase, COUNT(*) AS n FROM policy_decisions WHERE outcome='deny' GROUP BY phase")
    approvals = db.query("SELECT decision, COUNT(*) AS n FROM approvals GROUP BY decision")
    by_scenario = {}
    for p in per_run:
        s = by_scenario.setdefault(p["scenario"], {"runs": 0, "detected": 0, "correct_diagnosis": 0, "resolved": 0,
                                                    "escalated": 0, "ttd": [], "ttr": []})
        s["runs"] += 1
        s["detected"] += 1 if p["incident_id"] else 0
        s["correct_diagnosis"] += 1 if p["diagnosis_correct"] else 0
        s["resolved"] += 1 if p["outcome"] == "resolved" else 0
        s["escalated"] += 1 if p["outcome"] == "escalated" else 0
        s["ttd"].append(p["ttd_s"])
        s["ttr"].append(p["ttr_s"])
    for s in by_scenario.values():
        s["mttd_s"] = _stats(s.pop("ttd"))["mean"]
        s["mttr_s"] = _stats(s.pop("ttr"))["mean"]

    return {
        "summary": {
            "injections": len(runs),
            "incidents_total": len(incidents),
            "mttd_s": _stats([p["ttd_s"] for p in per_run]),
            "mttr_s": _stats([p["ttr_s"] for p in per_run]),
            "detect_to_recover_s": _stats([p["detect_to_recover_s"] for p in per_run]),
            "time_to_diagnose_s": _stats([p["time_to_diagnose_s"] for p in per_run]),
            "detection_rate": _rate(sum(1 for p in per_run if p["incident_id"]), len(closed_runs)),
            "diagnosis_accuracy": _rate(sum(1 for p in diagnosed if p["diagnosis_correct"]), len(diagnosed)),
            "incorrect_remediation_rate": _rate(sum(1 for p in acted if not p["action_correct"]), len(acted)),
            "remediation_success_rate": _rate(sum(1 for v in ver if v["result"] == "passed"), len(ver)),
            "incident_resolution_rate": _rate(sum(1 for i in incidents if i["status"] == "resolved"),
                                              sum(1 for i in incidents if i["status"] != "open")),
            "false_positive_rate": _rate(sum(1 for i in incidents if i["id"] not in linked), len(incidents)),
            "false_positives": sum(1 for i in incidents if i["id"] not in linked),
            "human_escalation_rate": _rate(sum(1 for i in incidents if i["escalated_at"]), len(incidents)),
            "rollbacks": len(rb),
            "rollback_success_rate": _rate(sum(1 for r in rb if r["status"] == "succeeded"), len(rb)),
            "blocked_actions": sum(b["n"] for b in blocked),
            "blocked_by_phase": {b["phase"]: b["n"] for b in blocked},
            "approvals": {a["decision"]: a["n"] for a in approvals},
        },
        "by_scenario": by_scenario,
        "runs": per_run,
    }
