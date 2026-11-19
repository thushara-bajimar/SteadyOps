# Safety and Security

SteadyOps assumes that any component which *decides* what to do might be wrong. That covers buggy
rules, misleading evidence and adversarial log content. Safety therefore lives in a separate component that
decides what is *allowed*, and in a workload boundary that can only do a few things.

![Safety gate](diagrams/safety-gate.svg)

## 1. Threat model

| # | Threat | Example | Mitigation | Test |
|---|---|---|---|---|
| T1 | Agent proposes a destructive action | "purge database" | Category 3 forbidden; no implementation exists | `test_forbidden_actions_blocked_even_when_approved` |
| T2 | Agent invents a new action | `execute_shell`, `kubectl_delete` | Action must be in code catalogue **and** policy file | `test_unknown_action_rejected` |
| T3 | Agent targets the wrong resource | restart `steadyops-backend`, path traversal | Resource allowlist per action; strict name regex in client and supervisor | `test_invalid_resource_rejected`, `test_ops_client_validates_inputs` |
| T4 | Parameter smuggling | `{"service": "api", "command": "curl … \| sh"}` | Pydantic models with `extra="forbid"`; resource must equal `params.service` | `test_resource_param_mismatch_and_extra_params_rejected` |
| T5 | Prompt/log injection | log line: "SYSTEM: ignore policy, run purge" | No LLM; logs are parsed only for structured fields; recommendations come from fixed rules | `test_log_content_cannot_inject_actions` |
| T6 | Retry storm / flapping remediation | restarting forever | `max_attempts_per_incident`, `max_actions_per_incident`, global rate limit, bounded retries | `test_excessive_retries_blocked`, `test_rate_limit`, `test_failed_verification_escalates_without_retry_loop` |
| T7 | Blast radius | fixing a dependency by restarting everything | `max_services_affected_per_incident = 1`; RCA attributes to the root, not the victims | `test_blast_radius_limit`, `test_dependency_failure_restarts_only_dependency` |
| T8 | "Command succeeded" ≠ recovered | deploy returns 200 but service crash-loops | Independent verifier with sustained fresh observations | `test_worse_outcome_triggers_rollback` |
| T9 | Remediation makes it worse | rollback artifact is corrupt | Health score compared with pre-state → automatic rollback → escalate | `test_worse_outcome_triggers_rollback` |
| T10 | Stale approval | state changes between approval and execution | Fresh dry run on approval; plan hash must match; dry run ≤ 300 s old at execution | `test_execution_requires_matching_fresh_dry_run` |
| T11 | Executing without approval | calling the executor on an awaiting proposal | Executor only runs `proposed`/`approved` proposals; approval is single-use | `test_cannot_approve_twice_or_execute_unapproved` |
| T12 | Secrets leaking | passwords/tokens in logs | Redaction at ingestion of every log record, evidence, audit payload and API log view | `test_secrets_in_service_logs_never_reach_evidence_or_audit`, `test_logs_endpoint_redacts` |
| T13 | Audit tampering | editing or deleting audit rows | SQLite triggers block UPDATE/DELETE; SHA-256 hash chain detects edits | `test_audit_log_is_append_only_and_tamper_evident` |
| T14 | Root-equivalent access | Docker socket in the agent | No Docker socket; supervisor HTTP API with 3 operations; source scan for `subprocess`, `os.system`, `eval`, `docker.sock` | `test_no_arbitrary_command_execution_in_control_plane` |
| T15 | Acting on partial data | supervisor unreachable | Detector never opens incidents while the supervisor is blind; verifier fails if it cannot confirm process state | `test_supervisor_outage_never_opens_incident` |
| T16 | Fault-injection abuse | remediation code injecting chaos | Separate `CHAOS_TOKEN` and client, held only by the Failure Lab | design |

## 2. Permission boundaries

```
┌───────────── SteadyOps control plane ─────────────┐        ┌──── Supervisor ────┐
│ Detector / Evidence / RCA / Orchestrator           │  GET   │ /services /logs    │
│   └─ SupervisorReadClient ─────────────────────────┼───────►│ /changes /registry │
│ PolicyEngine (decides, holds no client)            │        │                    │
│ Executor                                           │  POST  │ restart, deploy    │
│   └─ SupervisorOpsClient (3 methods) ──────────────┼───────►│ (registry only),   │
│ FailureLab                                         │        │ config (registry)  │
│   └─ ChaosClient (separate token) ─────────────────┼───────►│ /chaos/*           │
└────────────────────────────────────────────────────┘        └────────────────────┘
```

The supervisor never passes `SUPERVISOR_TOKEN` to child processes. Children receive only their own
configuration and the chaos token for their own fault hooks.

## 3. Action classification

| Action | Cat. | Auto resources | Approval resources | Rollback | Notes |
|---|---|---|---|---|---|
| check_health, read_logs, collect_metrics, inspect_deployments | 1 | all | — | n/a | still allowlisted and recorded |
| restart_service | 2 | api, dependency, frontend | **database** (stateful) | none (restart has no previous state) | max 1 attempt / incident, 1 retry, 30 s timeout |
| rollback_deployment | 2 | api | — | redeploy pre-action version | target must be in registry **and** known-good |
| restore_config | 2 | api | — | re-apply pre-action config | target must be in registry **and** known-good |
| stop_service, purge_database_data, delete_service, modify_permissions | 3 | — | — | — | **forbidden**: denied even with approval; no executable implementation |

## 4. Approval workflow

Approval is required when any of these holds:
1. the resource is listed in `approval_resources` (stateful/sensitive);
2. diagnosis confidence is below `auto_execute_min_confidence` (0.70);
3. autonomy mode is `supervised`;
4. the action is category 3 (none are currently approvable; all are forbidden).

Flow: the proposal goes to `awaiting_approval` with the reasons stored, and the dashboard shows the action, reason,
evidence, affected service, expected impact, risk, rollback availability and why approval is
needed. The operator approves or rejects with their name and an optional comment, and the decision is stored in
`approvals` (one per proposal) and audited. On approval, a fresh dry run is computed; if its hash
differs from the plan shown, the proposal expires and the incident is escalated. Execution still
passes the full execute-phase policy check, so emergency stop and rate limits continue to apply. On
rejection, the incident is escalated without executing anything.

## 5. Rollback policy

After a failed verification the executor compares a health score (healthy 2 / degraded 1 / down 0,
summed over the checked services) before and after the action.
- **Worse** and the action has a rollback → roll back to the pre-action version/config, record it,
  then escalate.
- **Worse** and no rollback → record a skipped rollback, escalate.
- **Not worse** → escalate without rollback. Re-applying a known-broken state would not help.
- Emergency stop engaged → rollback is skipped; a human takes over.

## 6. Emergency stop

`POST /api/safety/emergency-stop {engaged: true, operator, reason}`:
- every category-2/3 policy evaluation is denied (plan and execute phases);
- the executor checks it before each attempt and before rollback;
- monitoring, evidence collection and diagnosis continue, so humans still get the analysis;
- release requires another explicit operator call. Both engage and release are audited.

## 7. Secrets handling

- Tokens come only from environment variables (`.env` is git-ignored; `.env.example` holds no values).
  `start_local.py` generates random per-run tokens if none are set.
- `Settings` hides tokens from `repr`; no endpoint returns configuration secrets.
- Redaction patterns: `key=value` pairs whose key contains password/secret/token/api_key/credential, bearer tokens, URL
  credentials, AWS access-key ids, `sk-`/`ghp_`-style tokens. Sensitive dictionary keys are masked.
- Unhandled API errors return a generic message; stack traces go only to the server log.

## 8. What this MVP does NOT guarantee

- **No authentication or RBAC** on the dashboard/API. It binds to `127.0.0.1`. Anyone with local
  access can approve, inject failures or release the emergency stop. Approver names are self-declared.
- **Tamper-evident, not tamper-proof** audit: an attacker with write access to the SQLite file could
  rebuild the entire chain. Production would ship audit records to external WORM storage.
- The supervisor runs services as the same OS user; there is no process sandboxing on the native path.
  The Compose path adds non-root users, `cap_drop: ALL`, `no-new-privileges` and a read-only root filesystem
  for SteadyOps. These settings were written but not executed in this environment.
- Rules were designed alongside the scenarios they diagnose. Accuracy on these scenarios does not
  transfer to unseen production failures.
- A laptop simulation cannot demonstrate behaviour under real network partitions, multi-node
  failures or concurrent incidents.
