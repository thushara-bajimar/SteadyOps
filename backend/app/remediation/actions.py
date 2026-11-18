"""The remediation action catalogue (the code half of the allowlist).

An action can be executed only if it is defined here AND enabled in
config/safety_policy.json. Parameters are validated by Pydantic models that
forbid unknown fields, so a proposal cannot smuggle extra arguments.

Category 3 actions are defined so the policy engine can recognise and refuse
them, but they have no executable implementation in this MVP.
"""
from __future__ import annotations

import hashlib
import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..clients import SupervisorError, SupervisorOpsClient, SupervisorReadClient
from ..database.db import Database

NAME = r"^[a-z][a-z0-9\-]{1,40}$"
VALUE = r"^[0-9A-Za-z.\-]{1,32}$"


class StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceParams(StrictParams):
    service: str = Field(pattern=NAME)


class RollbackParams(StrictParams):
    service: str = Field(pattern=NAME)
    target_version: str = Field(pattern=VALUE)


class ConfigParams(StrictParams):
    service: str = Field(pattern=NAME)
    target_config: str = Field(pattern=VALUE)


def read_state(read: SupervisorReadClient, db: Database, service: str) -> dict:
    """Read-only snapshot used by preconditions, dry runs and rollback."""
    statuses = {s["name"]: s for s in read.services()}
    try:
        registry = read.registry(service) if service in statuses else {"versions": [], "configs": {}}
    except SupervisorError:
        registry = {"versions": [], "configs": {}}
    kg = db.query("SELECT kind, value FROM known_good WHERE service=? ORDER BY verified_at DESC", (service,))
    return {
        "service": statuses.get(service),
        "all": statuses,
        "registry": {"versions": registry["versions"], "configs": sorted(registry["configs"])},
        "known_good": {"versions": [r["value"] for r in kg if r["kind"] == "version"],
                       "configs": [r["value"] for r in kg if r["kind"] == "config"]},
    }


def check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


class RemediationAction:
    id: ClassVar[str]
    category: ClassVar[int]
    risk_level: ClassVar[str]
    rollback_available: ClassVar[bool]
    executable: ClassVar[bool] = True
    Params: ClassVar[type[StrictParams]] = ServiceParams

    def validate(self, params: dict) -> StrictParams:
        return self.Params.model_validate(params)

    def preconditions(self, p, state: dict) -> list[dict]:
        svc = state["service"]
        return [check("service_known", svc is not None, f"{p.service} is a managed service" if svc else f"{p.service} is not managed")]

    def expected_impact(self, p) -> str:
        raise NotImplementedError

    def plan(self, p, state: dict) -> dict:
        raise NotImplementedError

    def execute(self, p, ops: SupervisorOpsClient) -> dict:
        raise NotImplementedError

    def rollback(self, p, pre_state: dict, ops: SupervisorOpsClient) -> dict:
        raise NotImplementedError

    def dry_run(self, params: dict, state: dict) -> dict:
        """Produce the execution plan without changing anything."""
        p = self.validate(params)
        plan = self.plan(p, state)
        plan["preconditions"] = self.preconditions(p, state)
        plan["action_id"] = self.id
        plan["params"] = p.model_dump()
        plan["rollback_available"] = self.rollback_available
        plan["side_effects_performed"] = "none (dry run)"
        return plan


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()


class RestartService(RemediationAction):
    id = "restart_service"
    category = 2
    risk_level = "low"
    rollback_available = False  # a restart has no earlier state to return to

    def expected_impact(self, p) -> str:
        return f"{p.service} is unavailable for ~1-3 s while its process restarts. No data or version change."

    def plan(self, p, state):
        s = state["service"] or {}
        return {
            "steps": [f"stop process of {p.service}", f"start {p.service} with the same version "
                      f"{s.get('version')} and config {s.get('config_version')}", "verify health"],
            "current": {"state": s.get("state"), "version": s.get("version"), "config_version": s.get("config_version")},
            "target": {"state": "running", "version": s.get("version"), "config_version": s.get("config_version")},
            "expected_impact": self.expected_impact(p),
            "rollback_plan": "not applicable - restart does not change version or configuration",
        }

    def execute(self, p, ops):
        return ops.restart(p.service)


class RollbackDeployment(RemediationAction):
    id = "rollback_deployment"
    category = 2
    risk_level = "medium"
    rollback_available = True
    Params = RollbackParams

    def preconditions(self, p, state):
        checks = super().preconditions(p, state)
        svc = state["service"] or {}
        checks += [
            check("target_in_registry", p.target_version in state["registry"]["versions"],
                  f"{p.target_version} exists in the release registry"),
            check("target_known_good", p.target_version in state["known_good"]["versions"],
                  f"{p.target_version} was previously verified healthy"),
            check("target_differs", p.target_version != svc.get("version"),
                  f"current version is {svc.get('version')}"),
        ]
        return checks

    def expected_impact(self, p) -> str:
        return f"{p.service} restarts on version {p.target_version} (~2-3 s unavailable). Changes introduced by the bad release are reverted."

    def plan(self, p, state):
        s = state["service"] or {}
        return {
            "steps": [f"redeploy {p.service}: {s.get('version')} -> {p.target_version}", "verify health and error rate"],
            "current": {"version": s.get("version")},
            "target": {"version": p.target_version},
            "expected_impact": self.expected_impact(p),
            "rollback_plan": f"if recovery verification fails and the service is worse off, redeploy {s.get('version')}",
        }

    def execute(self, p, ops):
        return ops.deploy(p.service, p.target_version)

    def rollback(self, p, pre_state, ops):
        return ops.deploy(p.service, pre_state["version"])


class RestoreConfig(RemediationAction):
    id = "restore_config"
    category = 2
    risk_level = "medium"
    rollback_available = True
    Params = ConfigParams

    def preconditions(self, p, state):
        checks = super().preconditions(p, state)
        svc = state["service"] or {}
        checks += [
            check("target_in_registry", p.target_config in state["registry"]["configs"],
                  f"{p.target_config} exists in the config registry"),
            check("target_known_good", p.target_config in state["known_good"]["configs"],
                  f"{p.target_config} was previously verified healthy"),
            check("target_differs", p.target_config != svc.get("config_version"),
                  f"current config is {svc.get('config_version')}"),
        ]
        return checks

    def expected_impact(self, p) -> str:
        return f"{p.service} restarts with configuration {p.target_config}. No code or data changes."

    def plan(self, p, state):
        s = state["service"] or {}
        return {
            "steps": [f"apply config {p.target_config} to {p.service} (currently {s.get('config_version')})",
                      f"restart {p.service}", "verify health"],
            "current": {"config_version": s.get("config_version")},
            "target": {"config_version": p.target_config},
            "expected_impact": self.expected_impact(p),
            "rollback_plan": f"if verification fails and the service is worse off, re-apply {s.get('config_version')}",
        }

    def execute(self, p, ops):
        return ops.apply_config(p.service, p.target_config)

    def rollback(self, p, pre_state, ops):
        return ops.apply_config(p.service, pre_state["config_version"])


class _Forbidden(RemediationAction):
    category = 3
    risk_level = "critical"
    rollback_available = False
    executable = False

    def expected_impact(self, p) -> str:
        return "destructive or irreversible; never executed automatically"

    def plan(self, p, state):
        return {"steps": [], "expected_impact": self.expected_impact(p), "rollback_plan": "none"}

    def execute(self, p, ops):  # pragma: no cover - guarded by policy
        raise PermissionError(f"{self.id} has no executable implementation")


class StopService(_Forbidden):
    id = "stop_service"


class PurgeDatabaseData(_Forbidden):
    id = "purge_database_data"


class DeleteService(_Forbidden):
    id = "delete_service"


class ModifyPermissions(_Forbidden):
    id = "modify_permissions"

    class Params(StrictParams):
        service: str = Field(pattern=NAME)
        role: str = Field(pattern=VALUE)


class _ReadOnly(RemediationAction):
    category = 1
    risk_level = "none"
    rollback_available = False


class CheckHealth(_ReadOnly):
    id = "check_health"


class ReadLogs(_ReadOnly):
    id = "read_logs"


class CollectMetrics(_ReadOnly):
    id = "collect_metrics"


class InspectDeployments(_ReadOnly):
    id = "inspect_deployments"


ACTIONS: dict[str, RemediationAction] = {
    a.id: a for a in (
        RestartService(), RollbackDeployment(), RestoreConfig(),
        StopService(), PurgeDatabaseData(), DeleteService(), ModifyPermissions(),
        CheckHealth(), ReadLogs(), CollectMetrics(), InspectDeployments(),
    )
}
