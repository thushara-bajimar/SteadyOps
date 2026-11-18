from app.database.db import utcnow
from app.remediation.actions import ACTIONS, plan_hash, read_state
from conftest import detect


def ev(rt, action, resource, params, **kw):
    state = read_state(rt.read, rt.db, resource) if resource in rt.world.svc else None
    return rt.policy.evaluate(action, resource, params, phase=kw.pop("phase", "plan"), state=state, **kw)


def test_read_only_operations_allowed(healthy_rt):
    for a in ("check_health", "read_logs", "collect_metrics", "inspect_deployments"):
        assert healthy_rt.policy.authorize_read(a).outcome == "allow"
    assert healthy_rt.policy.authorize_read("restart_service").outcome == "deny"


def test_low_risk_category2_action_allowed(healthy_rt):
    d = ev(healthy_rt, "restart_service", "api-service", {"service": "api-service"}, confidence=0.9)
    assert d.outcome == "allow", d.reasons


def test_stateful_resource_requires_approval(healthy_rt):
    d = ev(healthy_rt, "restart_service", "database-service", {"service": "database-service"}, confidence=0.9)
    assert d.outcome == "require_approval"
    d2 = ev(healthy_rt, "restart_service", "database-service", {"service": "database-service"}, confidence=0.9, approved=True)
    assert d2.outcome == "allow"


def test_forbidden_actions_blocked_even_when_approved(healthy_rt):
    for action in ("purge_database_data", "delete_service", "stop_service"):
        d = ev(healthy_rt, action, "database-service", {"service": "database-service"}, approved=True)
        assert d.outcome == "deny"
        assert "forbidden" in d.reasons[0]


def test_unknown_action_rejected(healthy_rt):
    for action in ("execute_shell", "run_command", "kubectl_delete", "", None, 42):
        d = healthy_rt.policy.evaluate(action, "api-service", {"cmd": "rm -rf /"}, phase="plan")
        assert d.outcome == "deny"
        assert d.checks[0]["name"] == "action_allowlisted" and not d.checks[0]["passed"]


def test_invalid_resource_rejected(healthy_rt):
    for res in ("steadyops-backend", "supervisor", "../../etc", "api-service; rm -rf /"):
        d = healthy_rt.policy.evaluate("restart_service", res, {"service": res}, phase="plan")
        assert d.outcome == "deny"


def test_resource_param_mismatch_and_extra_params_rejected(healthy_rt):
    d = ev(healthy_rt, "restart_service", "api-service", {"service": "dependency-service"})
    assert d.outcome == "deny"
    d = ev(healthy_rt, "restart_service", "api-service", {"service": "api-service", "command": "bash -c 'curl evil'"})
    assert d.outcome == "deny" and "invalid parameters" in d.reasons[0]
    d = ev(healthy_rt, "rollback_deployment", "api-service", {"service": "api-service", "target_version": "$(reboot)"})
    assert d.outcome == "deny"


def test_rollback_only_to_known_good_registered_version(healthy_rt):
    rt = healthy_rt
    rt.world.svc["api-service"]["version"] = "1.5.0"
    assert ev(rt, "rollback_deployment", "api-service", {"service": "api-service", "target_version": "1.4.2"}).outcome == "allow"
    d = ev(rt, "rollback_deployment", "api-service", {"service": "api-service", "target_version": "0.0.1"})
    assert d.outcome == "deny" and any("registry" in r for r in d.reasons)


def _fake_execution(rt, iid, action="restart_service", resource="api-service"):
    pid = rt.db.insert("remediation_proposals", {"incident_id": iid, "action_id": action, "resource": resource,
                                                 "params_json": {"service": resource}, "reason": "t", "risk_level": "low",
                                                 "expected_impact": "t", "rollback_available": 0, "status": "succeeded",
                                                 "created_at": utcnow(), "updated_at": utcnow()})
    rt.db.insert("remediation_executions", {"proposal_id": pid, "incident_id": iid, "action_id": action,
                                            "resource": resource, "status": "command_succeeded", "attempts": 1,
                                            "pre_state_json": {}, "started_at": utcnow()})


def test_excessive_retries_blocked(healthy_rt):
    rt = healthy_rt
    iid = detect(rt, "resource_exhaustion")
    _fake_execution(rt, iid)
    d = ev(rt, "restart_service", "api-service", {"service": "api-service"}, incident_id=iid)
    assert d.outcome == "deny"
    assert any("prior attempts" in r for r in d.reasons)


def test_blast_radius_limit(healthy_rt):
    rt = healthy_rt
    iid = detect(rt, "dependency_failure")
    _fake_execution(rt, iid, resource="dependency-service")
    d = ev(rt, "restart_service", "api-service", {"service": "api-service"}, incident_id=iid)
    assert d.outcome == "deny"
    assert any("service(s) affected" in r for r in d.reasons)


def test_rate_limit(healthy_rt):
    rt = healthy_rt
    iid = detect(rt, "dependency_failure")
    for _ in range(rt.settings.policy["rate_limit"]["max_executions"]):
        _fake_execution(rt, iid, resource="frontend-service")
    d = ev(rt, "restart_service", "api-service", {"service": "api-service"})
    assert d.outcome == "deny" and any("executions in the last" in r for r in d.reasons)


def test_emergency_stop_blocks_all_changes(healthy_rt):
    rt = healthy_rt
    rt.store.set_emergency_stop(True, "alice", "investigating manually")
    d = ev(rt, "restart_service", "api-service", {"service": "api-service"}, approved=True)
    assert d.outcome == "deny" and "EMERGENCY STOP" in d.reasons[0]
    assert rt.policy.authorize_read("read_logs").outcome == "allow", "reads still work during emergency stop"
    rt.store.set_emergency_stop(False, "alice", "done")
    assert ev(rt, "restart_service", "api-service", {"service": "api-service"}).outcome == "allow"


def test_supervised_mode_and_low_confidence_require_approval(healthy_rt):
    rt = healthy_rt
    assert ev(rt, "restart_service", "api-service", {"service": "api-service"}, confidence=0.5).outcome == "require_approval"
    rt.store.set_autonomy_mode("supervised", "alice")
    assert ev(rt, "restart_service", "api-service", {"service": "api-service"}, confidence=0.99).outcome == "require_approval"


def test_execution_requires_matching_fresh_dry_run(healthy_rt):
    rt = healthy_rt
    params = {"service": "api-service"}
    d = ev(rt, "restart_service", "api-service", params, phase="execute")
    assert d.outcome == "deny" and any("dry run" in r for r in d.reasons)
    plan = ACTIONS["restart_service"].dry_run(params, read_state(rt.read, rt.db, "api-service"))
    good = {"plan": plan, "plan_hash": plan_hash(plan), "created_at": utcnow()}
    assert ev(rt, "restart_service", "api-service", params, phase="execute", dry_run=good).outcome == "allow"
    stale = dict(good, created_at="2000-01-01T00:00:00+00:00")
    assert ev(rt, "restart_service", "api-service", params, phase="execute", dry_run=stale).outcome == "deny"
    other = ACTIONS["restart_service"].dry_run({"service": "frontend-service"}, read_state(rt.read, rt.db, "frontend-service"))
    swapped = {"plan": other, "plan_hash": plan_hash(other), "created_at": utcnow()}
    assert ev(rt, "restart_service", "api-service", params, phase="execute", dry_run=swapped).outcome == "deny"


def test_denials_are_audited(healthy_rt):
    rt = healthy_rt
    rt.policy.evaluate("execute_shell", "api-service", {}, phase="simulation")
    row = rt.db.one("SELECT * FROM audit_events WHERE event_type='action_blocked' ORDER BY id DESC")
    assert row and "execute_shell" in row["payload_json"]
