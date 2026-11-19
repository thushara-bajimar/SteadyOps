"""End-to-end pipeline tests against the simulated stack."""
from app.remediation.actions import ACTIONS, read_state
from conftest import detect


def pipeline(rt, scenario):
    iid = detect(rt, scenario)
    rt.orchestrator.run_pipeline(iid)
    return rt.incidents.get(iid)


def test_dry_run_does_not_modify_system(healthy_rt):
    rt = healthy_rt
    before = [rt.world.status(n) for n in rt.world.svc]
    for aid, params, res in (("restart_service", {"service": "api-service"}, "api-service"),
                             ("rollback_deployment", {"service": "api-service", "target_version": "1.4.2"}, "api-service"),
                             ("restore_config", {"service": "api-service", "target_config": "api-cfg-7"}, "api-service")):
        plan = ACTIONS[aid].dry_run(params, read_state(rt.read, rt.db, res))
        assert plan["side_effects_performed"] == "none (dry run)"
        assert plan["steps"]
    assert rt.world.ops_calls == []
    assert [rt.world.status(n) for n in rt.world.svc] == before


def test_bad_deployment_is_rolled_back_and_verified(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "bad_deployment")
    assert inc["status"] == "resolved", inc
    assert rt.world.svc["api-service"]["version"] == "1.4.2"
    assert rt.world.ops_calls == [("deploy", "api-service", "1.4.2")]
    v = rt.db.one("SELECT * FROM verifications WHERE incident_id=?", (inc["id"],))
    assert v["result"] == "passed"
    for k in ("detected_at", "diagnosed_at", "remediation_started_at", "remediation_completed_at", "verified_at", "resolved_at"):
        assert inc[k], k


def test_broken_config_restored(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "broken_config")
    assert inc["status"] == "resolved"
    assert rt.world.svc["api-service"]["config"] == "api-cfg-7"


def test_resource_exhaustion_restarts_affected_service_only(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "resource_exhaustion")
    assert inc["status"] == "resolved"
    assert rt.world.ops_calls == [("restart", "api-service")]


def test_dependency_failure_restarts_only_dependency(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "dependency_failure")
    assert inc["status"] == "resolved"
    assert rt.world.ops_calls == [("restart", "dependency-service")], "healthy api-service must not be restarted"


def test_failed_verification_escalates_without_retry_loop(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "dependency_outage")
    assert inc["status"] == "escalated"
    assert inc["resolved_at"] is None, "failed verification must never mark the incident resolved"
    assert rt.world.ops_calls == [("restart", "dependency-service")], "exactly one bounded attempt"
    v = rt.db.one("SELECT * FROM verifications WHERE incident_id=?", (inc["id"],))
    assert v["result"] == "failed"
    # Not worse than before, and restart has no rollback -> no rollback performed
    assert rt.db.scalar("SELECT COUNT(*) FROM rollbacks WHERE status='succeeded'") == 0


def test_worse_outcome_triggers_rollback(healthy_rt):
    rt = healthy_rt
    inc = pipeline(rt, "bad_deployment_corrupt_rollback")
    assert inc["status"] == "escalated"
    assert rt.world.ops_calls == [("deploy", "api-service", "1.4.2"), ("deploy", "api-service", "1.5.0")]
    rb = rt.db.one("SELECT * FROM rollbacks WHERE incident_id=?", (inc["id"],))
    assert rb["status"] == "succeeded"
    assert rt.world.svc["api-service"]["version"] == "1.5.0", "pre-action state restored"
    prop = rt.db.one("SELECT status FROM remediation_proposals WHERE incident_id=?", (inc["id"],))
    assert prop["status"] == "rolled_back"


def test_command_failure_is_retried_boundedly_then_escalated(healthy_rt):
    rt = healthy_rt
    rt.world.fail_ops = True
    inc = pipeline(rt, "resource_exhaustion")
    assert inc["status"] == "escalated"
    max_retries = rt.settings.policy["actions"]["restart_service"]["max_retries"]
    assert len(rt.world.ops_calls) == 1 + max_retries
    ex = rt.db.one("SELECT * FROM remediation_executions WHERE incident_id=?", (inc["id"],))
    assert ex["status"] == "command_failed"


def test_emergency_stop_prevents_remediation(healthy_rt):
    rt = healthy_rt
    rt.store.set_emergency_stop(True, "alice", "hands off")
    inc = pipeline(rt, "bad_deployment")
    assert inc["status"] == "escalated"
    assert rt.world.ops_calls == []
    assert rt.db.scalar("SELECT COUNT(*) FROM remediation_executions") == 0


def test_supervised_mode_waits_for_approval_then_executes(healthy_rt):
    rt = healthy_rt
    rt.store.set_autonomy_mode("supervised", "alice")
    inc = pipeline(rt, "broken_config")
    assert inc["stage"] == "awaiting_approval" and inc["status"] == "open"
    assert rt.world.ops_calls == []
    prop = rt.db.one("SELECT * FROM remediation_proposals WHERE incident_id=?", (inc["id"],))
    assert "supervised" in prop["approval_reason"]
    res =rt.orchestrator.decide(prop["id"], True, "alice", "looks right")
    assert res["status"] == "approved"
    rt.orchestrator._pool.shutdown(wait=True)
    assert rt.incidents.get(inc["id"])["status"] == "resolved"
    appr = rt.db.one("SELECT * FROM approvals WHERE proposal_id=?", (prop["id"],))
    assert appr["approver"] == "alice" and appr["decision"] == "approved"


def test_rejection_escalates_and_never_executes(healthy_rt):
    rt = healthy_rt
    rt.store.set_autonomy_mode("supervised", "alice")
    inc = pipeline(rt, "bad_deployment")
    prop = rt.db.one("SELECT * FROM remediation_proposals WHERE incident_id=?", (inc["id"],))
    rt.orchestrator.decide(prop["id"], False, "bob", "want to look first")
    assert rt.incidents.get(inc["id"])["status"] == "escalated"
    assert rt.world.ops_calls == []


def test_cannot_approve_twice_or_execute_unapproved(healthy_rt):
    rt = healthy_rt
    rt.store.set_autonomy_mode("supervised", "alice")
    inc = pipeline(rt, "bad_deployment")
    prop = rt.db.one("SELECT * FROM remediation_proposals WHERE incident_id=?", (inc["id"],))
    assert rt.executor.run(prop["id"], "mallory") == "skipped", "awaiting_approval proposals cannot be executed directly"
    rt.orchestrator.decide(prop["id"], False, "bob", None)
    import pytest

    with pytest.raises(ValueError):
        rt.orchestrator.decide(prop["id"], True, "mallory", None)
    assert rt.world.ops_calls == []


def test_recovery_time_is_measured(healthy_rt):
    from app import analytics

    rt = healthy_rt
    pipeline(rt, "bad_deployment")
    s = analytics.compute(rt.db)
    run = s["runs"][0]
    assert run["diagnosis_correct"] is True and run["action_correct"] is True
    assert run["ttd_s"] is not None and run["ttr_s"] is not None and run["ttr_s"] >= run["ttd_s"]
    assert s["summary"]["false_positives"] == 0
    assert s["summary"]["remediation_success_rate"] == 1.0, "verifications must be counted when nothing is excluded"
    assert s["summary"]["incident_resolution_rate"] == 1.0


def test_rollback_metrics_counted(healthy_rt):
    from app import analytics

    pipeline(healthy_rt, "bad_deployment_corrupt_rollback")
    s = analytics.compute(healthy_rt.db)["summary"]
    assert s["rollbacks"] == 1 and s["rollback_success_rate"] == 1.0
    assert s["remediation_success_rate"] == 0.0
