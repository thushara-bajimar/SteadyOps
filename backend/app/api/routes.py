"""REST API consumed by the dashboard.

There is deliberately no endpoint that executes an arbitrary action: humans
can approve/reject an existing proposal, toggle autonomy mode, engage the
emergency stop, inject/reset Failure Lab scenarios, and *simulate* a policy
decision (which never executes anything).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import analytics
from ..clients import SupervisorError
from ..database.db import loads, seconds_between
from ..failure_lab import SCENARIOS
from ..investigation.explain import summarize

router = APIRouter(prefix="/api")
OPERATOR = r"^[A-Za-z0-9 _.\-]{2,40}$"


def rt(request: Request):
    return request.app.state.rt


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorBody(Body):
    operator: str = Field(pattern=OPERATOR)


class DecisionBody(OperatorBody):
    comment: str | None = Field(default=None, max_length=500)


class EmergencyBody(OperatorBody):
    engaged: bool
    reason: str | None = Field(default=None, max_length=300)


class AutonomyBody(OperatorBody):
    mode: str = Field(pattern=r"^(auto|supervised)$")


class SimulateBody(Body):
    action_id: str = Field(max_length=64)
    resource: str | None = Field(default=None, max_length=64)
    params: dict = Field(default_factory=dict)


class CloseBody(OperatorBody):
    note: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
@router.get("/health")
def health(request: Request):
    r = rt(request)
    o = r.orchestrator
    return {"status": "ok", "monitor_ticks": o.ticks, "last_tick_at": o.last_tick_at, "last_tick_error": o.last_tick_error,
            "supervisor_ok": r.collector.latest.supervisor_ok if r.collector.latest else None}


def _services_view(r) -> list[dict]:
    obs = r.collector.latest
    if obs is None:
        return []
    out = []
    for name, o in obs.services.items():
        kg = r.db.query("SELECT kind, value FROM known_good WHERE service=? ORDER BY verified_at DESC", (name,))
        out.append({
            "name": name, "health": o.health, "process_state": o.process_state, "version": o.version,
            "config_version": o.config_version, "http_status": o.http_status, "response_ms": o.response_ms,
            "health_error": o.health_error, "checks": (o.health_body or {}).get("checks"), "error_rate": o.error_rate,
            "latency_p95_ms": o.latency_p95_ms, "rss_mb": o.rss_mb, "cpu_percent": o.cpu_percent,
            "restart_count": o.restart_count, "restarts_recent": o.restarts_recent, "depends_on": o.depends_on,
            "stateful": o.stateful, "known_good_versions": [k["value"] for k in kg if k["kind"] == "version"],
            "known_good_configs": [k["value"] for k in kg if k["kind"] == "config"],
        })
    order = ["frontend-service", "api-service", "database-service", "dependency-service"]
    return sorted(out, key=lambda s: order.index(s["name"]) if s["name"] in order else 99)


@router.get("/overview")
def overview(request: Request):
    r = rt(request)
    services = _services_view(r)
    counts = {row["status"]: row["n"] for row in r.db.query("SELECT status, COUNT(*) n FROM incidents GROUP BY status")}
    pending = r.db.scalar("SELECT COUNT(*) FROM remediation_proposals WHERE status='awaiting_approval'")
    events = r.db.query("SELECT e.*, i.title FROM incident_events e JOIN incidents i ON i.id=e.incident_id "
                        "ORDER BY e.id DESC LIMIT 15")
    healthy = sum(1 for s in services if s["health"] == "healthy")
    return {
        "system_status": "unknown" if not services else "healthy" if healthy == len(services)
        else "down" if any(s["health"] == "down" for s in services) else "degraded",
        "services_total": len(services), "services_healthy": healthy,
        "incidents": {"active": counts.get("open", 0) + counts.get("escalated", 0), "open": counts.get("open", 0),
                      "escalated": counts.get("escalated", 0), "resolved": counts.get("resolved", 0),
                      "closed": counts.get("closed", 0)},
        "pending_approvals": pending, "recent_events": events, "services": services,
        "emergency_stop": r.store.emergency_stop, "autonomy_mode": r.store.autonomy_mode,
        "active_injection": r.lab.active(),
        "stats": analytics.compute(r.db)["summary"],
        "supervisor_ok": r.collector.latest.supervisor_ok if r.collector.latest else None,
    }


@router.get("/services")
def services(request: Request):
    return _services_view(rt(request))


@router.get("/services/{name}/metrics")
def service_metrics(name: str, request: Request, minutes: int = 10):
    r = rt(request)
    minutes = max(1, min(minutes, 120))
    rows = r.db.query(
        "SELECT ts, health, response_ms, error_rate, latency_p95_ms, rss_mb, cpu_percent, restarts_recent "
        "FROM monitoring_snapshots WHERE service=? AND ts >= strftime('%Y-%m-%dT%H:%M:%f', 'now', ?) ORDER BY ts",
        (name, f"-{minutes} minutes"))
    return rows[-600:]


@router.get("/services/{name}/logs")
def service_logs(name: str, request: Request, lines: int = 50):
    from ..safety.redact import redact

    r = rt(request)
    if name not in {s["name"] for s in _services_view(r)}:
        raise HTTPException(404, "unknown service")
    try:
        return redact(r.read.logs(name, lines=max(1, min(lines, 200))))
    except SupervisorError as exc:
        raise HTTPException(503, str(exc))


@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, limit: int = 100):
    r = rt(request)
    sql = ("SELECT i.*, (SELECT confidence FROM diagnoses d WHERE d.incident_id=i.id ORDER BY id DESC LIMIT 1) AS confidence, "
           "(SELECT scenario FROM failure_injections f WHERE f.incident_id=i.id) AS scenario FROM incidents i")
    params: list = []
    if status:
        sql += " WHERE i.status=?"
        params.append(status)
    rows = r.db.query(sql + " ORDER BY i.id DESC LIMIT ?", (*params, max(1, min(limit, 500))))
    for row in rows:
        row["affected_services"] = loads(row.pop("affected_services_json"), [])
        row.pop("signals_json", None)
    return rows


def incident_detail(r, iid: int) -> dict:
    inc = r.incidents.get(iid)
    if inc is None:
        raise HTTPException(404, "incident not found")
    events = r.db.query("SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (iid,))
    for e in events:
        e["data"] = loads(e.pop("data_json"), {})
    evidence = r.db.query("SELECT * FROM evidence WHERE incident_id=? ORDER BY id", (iid,))
    for e in evidence:
        e["data"] = loads(e.pop("data_json"), {})
    diags = r.db.query("SELECT * FROM diagnoses WHERE incident_id=? ORDER BY id", (iid,))
    for d in diags:
        d["supporting"] = loads(d.pop("supporting_json"), {})
        d["alternatives"] = loads(d.pop("alternatives_json"), [])
        d["recommended"] = loads(d.pop("recommended_json"), None)
        d["ambiguous"] = bool(d["ambiguous"])
    props = r.db.query("SELECT * FROM remediation_proposals WHERE incident_id=? ORDER BY id", (iid,))
    for p in props:
        p["params"] = loads(p.pop("params_json"), {})
        p["rollback_available"] = bool(p["rollback_available"])
        p["dry_runs"] = [dict(x, plan=loads(x.pop("plan_json"), {})) for x in
                         r.db.query("SELECT * FROM dry_runs WHERE proposal_id=? ORDER BY id", (p["id"],))]
        p["policy_decisions"] = [dict(x, reasons=loads(x.pop("reasons_json"), []), checks=loads(x.pop("checks_json"), []))
                                 for x in r.db.query("SELECT * FROM policy_decisions WHERE proposal_id=? ORDER BY id", (p["id"],))]
        p["approval"] = r.db.one("SELECT * FROM approvals WHERE proposal_id=?", (p["id"],))
        execs = r.db.query("SELECT * FROM remediation_executions WHERE proposal_id=? ORDER BY id", (p["id"],))
        for x in execs:
            x["pre_state"] = loads(x.pop("pre_state_json"), {})
            x["output"] = loads(x.pop("output_json"), {})
            x["verifications"] = [dict(v, checks=loads(v.pop("checks_json"), [])) for v in
                                  r.db.query("SELECT * FROM verifications WHERE execution_id=? ORDER BY id", (x["id"],))]
            x["rollbacks"] = [dict(v, details=loads(v.pop("details_json"), {})) for v in
                              r.db.query("SELECT * FROM rollbacks WHERE execution_id=? ORDER BY id", (x["id"],))]
        p["executions"] = execs
    injection = r.db.one("SELECT * FROM failure_injections WHERE incident_id=?", (iid,))
    t0 = injection["injected_at"] if injection else None
    detail = {
        "incident": inc, "timeline": events, "evidence": evidence, "diagnoses": diags, "proposals": props,
        "injection": injection,
        "timings": {
            "injected_at": t0, "first_abnormal_at": inc["first_abnormal_at"], "detected_at": inc["detected_at"],
            "diagnosed_at": inc["diagnosed_at"], "remediation_started_at": inc["remediation_started_at"],
            "remediation_completed_at": inc["remediation_completed_at"], "verified_at": inc["verified_at"],
            "resolved_at": inc["resolved_at"], "escalated_at": inc["escalated_at"],
            "time_to_detect_s": seconds_between(t0, inc["detected_at"]),
            "time_to_diagnose_s": seconds_between(inc["detected_at"], inc["diagnosed_at"]),
            "time_to_recover_s": seconds_between(t0 or inc["detected_at"], inc["resolved_at"]),
        },
    }
    detail["summary"] = summarize(detail)
    return detail


@router.get("/incidents/{iid}")
def get_incident(iid: int, request: Request):
    return incident_detail(rt(request), iid)


@router.post("/incidents/{iid}/close")
def close_incident(iid: int, body: CloseBody, request: Request):
    r = rt(request)
    inc = r.incidents.get(iid)
    if inc is None:
        raise HTTPException(404, "incident not found")
    if inc["status"] != "escalated":
        raise HTTPException(409, "only escalated incidents can be closed by an operator")
    r.incidents.close(iid, f"Closed by {body.operator}: {body.note}", body.operator)
    return {"status": "closed"}


@router.get("/approvals")
def approvals(request: Request):
    r = rt(request)
    rows = r.db.query("SELECT p.*, i.title, i.severity FROM remediation_proposals p JOIN incidents i ON i.id=p.incident_id "
                      "ORDER BY CASE WHEN p.status='awaiting_approval' THEN 0 ELSE 1 END, p.id DESC LIMIT 100")
    for p in rows:
        p["params"] = loads(p.pop("params_json"), {})
        dr = r.db.one("SELECT plan_json FROM dry_runs WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (p["id"],))
        p["dry_run"] = loads(dr["plan_json"], {}) if dr else None
        diag = r.db.one("SELECT category, confidence, suspected_cause, supporting_json FROM diagnoses WHERE id=?",
                        (p["diagnosis_id"],))
        if diag:
            diag["supporting"] = [f["text"] for f in loads(diag.pop("supporting_json"), {}).get("supporting", [])]
        p["diagnosis"] = diag
        p["approval"] = r.db.one("SELECT * FROM approvals WHERE proposal_id=?", (p["id"],))
        ex = r.db.one("SELECT status, error, started_at, finished_at FROM remediation_executions WHERE proposal_id=? "
                      "ORDER BY id DESC LIMIT 1", (p["id"],))
        p["execution"] = ex
        p["rollback"] = r.db.one("SELECT rb.status, rb.reason FROM rollbacks rb JOIN remediation_executions x ON "
                                 "x.id=rb.execution_id WHERE x.proposal_id=? ORDER BY rb.id DESC LIMIT 1", (p["id"],))
    return rows


def _decide(pid: int, body: DecisionBody, request: Request, approve: bool):
    try:
        return rt(request).orchestrator.decide(pid, approve, body.operator, body.comment)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/proposals/{pid}/approve")
def approve(pid: int, body: DecisionBody, request: Request):
    return _decide(pid, body, request, True)


@router.post("/proposals/{pid}/reject")
def reject(pid: int, body: DecisionBody, request: Request):
    return _decide(pid, body, request, False)


# -- Failure Lab ---------------------------------------------------------------
@router.get("/failure-lab/scenarios")
def scenarios(request: Request):
    r = rt(request)
    active = r.lab.active()
    history = r.db.query("SELECT f.*, i.status AS incident_status FROM failure_injections f LEFT JOIN incidents i "
                         "ON i.id=f.incident_id ORDER BY f.id DESC LIMIT 20")
    return {"scenarios": [dict(v, id=k) for k, v in SCENARIOS.items()], "active": active, "history": history}


@router.post("/failure-lab/{scenario}/inject")
def inject(scenario: str, body: OperatorBody, request: Request):
    try:
        return rt(request).lab.inject(scenario, body.operator)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except SupervisorError as exc:
        raise HTTPException(502, str(exc))


class ExcludeBody(OperatorBody):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/failure-lab/injections/{injection_id}/exclude")
def exclude_injection(injection_id: int, body: ExcludeBody, request: Request):
    """Flag a run as invalid for measurement (e.g. the host suspended). Audited; never deletes data."""
    try:
        return rt(request).lab.exclude(injection_id, body.operator, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/failure-lab/reset")
def reset(body: OperatorBody, request: Request):
    try:
        return rt(request).lab.reset(body.operator)
    except SupervisorError as exc:
        raise HTTPException(502, str(exc))


# -- Safety --------------------------------------------------------------------
@router.get("/safety/status")
def safety_status(request: Request):
    r = rt(request)
    blocked = r.db.query("SELECT * FROM policy_decisions WHERE outcome='deny' ORDER BY id DESC LIMIT 50")
    for b in blocked:
        b["reasons"] = loads(b.pop("reasons_json"), [])
        b["checks"] = loads(b.pop("checks_json"), [])
    counts = {row["outcome"]: row["n"] for row in r.db.query("SELECT outcome, COUNT(*) n FROM policy_decisions GROUP BY outcome")}
    return {"emergency_stop": r.store.emergency_stop, "autonomy_mode": r.store.autonomy_mode,
            "decision_counts": counts, "blocked": blocked, "audit_chain": r.audit.verify_chain()}


@router.get("/safety/policy")
def safety_policy(request: Request):
    r = rt(request)
    out = dict(r.settings.policy)
    out["actions"] = {k: dict(v, implemented=k in r.actions and r.actions[k].executable) for k, v in out["actions"].items()}
    return out


@router.post("/safety/emergency-stop")
def emergency_stop(body: EmergencyBody, request: Request):
    return rt(request).store.set_emergency_stop(body.engaged, body.operator, body.reason)


@router.post("/safety/simulate")
def simulate(body: SimulateBody, request: Request):
    """Evaluate a hypothetical proposal through the real policy engine. Never executes."""
    r = rt(request)
    from ..remediation.actions import read_state

    state = None
    if body.resource and body.action_id in r.actions:
        try:
            state = read_state(r.read, r.db, body.resource)
        except SupervisorError:
            state = None
    d = r.policy.evaluate(body.action_id, body.resource, body.params, phase="simulation", state=state)
    r.audit.record("policy-simulator", "policy_simulation", {"action_id": body.action_id[:64], "resource": body.resource,
                                                             "outcome": d.outcome})
    return d.to_dict()


@router.get("/settings")
def get_settings(request: Request):
    r = rt(request)
    return {"autonomy_mode": r.store.autonomy_mode, "thresholds": r.settings.thresholds}


@router.put("/settings/autonomy")
def set_autonomy(body: AutonomyBody, request: Request):
    rt(request).store.set_autonomy_mode(body.mode, body.operator)
    return {"autonomy_mode": body.mode}


@router.get("/audit")
def audit(request: Request, limit: int = 200, event_type: str | None = None):
    r = rt(request)
    sql, params = "SELECT * FROM audit_events", []
    if event_type:
        sql += " WHERE event_type=?"
        params.append(event_type)
    rows = r.db.query(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(limit, 1000))))
    for row in rows:
        row["payload"] = loads(row.pop("payload_json"), {})
    return rows


@router.get("/audit/verify")
def audit_verify(request: Request):
    return rt(request).audit.verify_chain()


@router.get("/analytics")
def get_analytics(request: Request):
    return analytics.compute(rt(request).db)


@router.get("/actions")
def actions(request: Request):
    r = rt(request)
    return [{"id": k, "category": v.get("category"), "description": v.get("description"),
             "forbidden": v.get("forbidden", False), "resources": v.get("resources", []),
             "approval_resources": v.get("approval_resources", [])} for k, v in r.settings.policy["actions"].items()]
