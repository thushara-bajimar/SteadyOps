"""Plain-language incident summary for a tired on-call engineer.

Deterministic templates over stored facts only. (An LLM could be plugged in
here later to rephrase the text, but it would receive only this structured,
already-redacted summary and would have no ability to act.)
"""
from __future__ import annotations

from .rca import CATEGORY_LABEL


def summarize(detail: dict) -> dict:
    inc = detail["incident"]
    diag = detail["diagnoses"][-1] if detail["diagnoses"] else None
    prop = detail["proposals"][-1] if detail["proposals"] else None
    execs = prop["executions"] if prop else []
    ex = execs[-1] if execs else None
    ver = ex["verifications"][-1] if ex and ex["verifications"] else None
    rbs = ex["rollbacks"] if ex else []
    changes = next((e for e in detail["evidence"] if e["kind"] == "recent_changes"), None)

    what = f"{inc['detected_service']} became unhealthy: " + "; ".join(
        sorted({s["message"] for s in inc["signals"] if s["service"] == inc["detected_service"]})[:3])
    out = {
        "what_happened": what,
        "affected_services": inc["affected_services"],
        "diagnosis": None, "evidence": [], "what_changed": changes["summary"] if changes else "not yet collected",
        "proposed_action": None, "why_safe": None, "after_execution": None,
        "resolved": inc["status"] == "resolved",
        "status_line": None,
    }
    if diag:
        out["diagnosis"] = (f"{CATEGORY_LABEL.get(diag['category'], diag['category'])} on {diag['root_service']} - "
                            f"{diag['suspected_cause']} (confidence {diag['confidence']:.2f}, {diag['confidence_level']})")
        out["evidence"] = [f["text"] for f in diag["supporting"]["supporting"]][:6]
    if prop:
        out["proposed_action"] = f"{prop['action_id']} on {prop['resource']} - {prop['reason']}"
        plan_decision = next((p for p in prop["policy_decisions"] if p["phase"] == "plan"), None)
        if plan_decision:
            passed = sum(1 for c in plan_decision["checks"] if c["passed"])
            verdict = {"allow": "auto-permitted (category 2, reversible, allowlisted)",
                       "require_approval": "requires human approval",
                       "deny": "BLOCKED"}[plan_decision["outcome"]]
            out["why_safe"] = (f"Safety policy: {verdict}. {passed}/{len(plan_decision['checks'])} checks passed. "
                               f"Risk {prop['risk_level']}; rollback {'available' if prop['rollback_available'] else 'not applicable'}. "
                               f"Impact: {prop['expected_impact']}")
            if plan_decision["reasons"]:
                out["why_safe"] += " Reasons: " + "; ".join(plan_decision["reasons"])
    if ex:
        txt = f"{ex['status'].replace('_', ' ').capitalize()} after {ex['attempts']} attempt(s)."
        if ver:
            txt += f" Verification {ver['result'].upper()}: {ver['summary']}."
        for rb in rbs:
            txt += f" Rollback {rb['status']}: {rb['reason']}."
        out["after_execution"] = txt
    out["status_line"] = {
        "resolved": "RESOLVED - recovery verified by sustained health checks.",
        "escalated": f"ESCALATED - a human is needed: {inc.get('escalation_reason')}",
        "closed": f"CLOSED - {inc.get('resolution')}",
        "open": f"IN PROGRESS - stage: {inc['stage'].replace('_', ' ')}",
    }[inc["status"]]
    return out
