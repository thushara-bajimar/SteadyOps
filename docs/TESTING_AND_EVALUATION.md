# Testing and Evaluation

Everything on this page was produced by running the code in this repository. Raw outputs are in
[`reports/`](../reports/).

## 1. How to reproduce

```bash
# unit, API and security tests (fake world, ~6 s)
cd backend && python -m pytest -q
# real-process integration tests (needs ports 8100-8104 free, ~65 s)
python -m pytest tests/test_integration.py -v -s
# frontend
cd ../frontend && npm test && npm run build
# full evaluation against a live stack (~40 min for 3 rounds)
python scripts/start_local.py --fresh          # terminal 1
python scripts/run_evaluation.py --rounds 3    # terminal 2 -> reports/evaluation_results.{json,md}
```

## 2. Test environment

| Item | Value |
|---|---|
| OS | Windows 11 Home (10.0.26200) |
| Python | 3.14.6 (virtualenv) |
| Node.js | 24.18.0 |
| Sampling interval | 2 s; 4 synthetic transactions per tick |
| Execution | native launcher (`scripts/start_local.py`); Docker not installed |

## 3. Automated test cases

### 3.1 Incident detection (`tests/test_detection.py`)
| Test | Verifies |
|---|---|
| test_healthy_stack_raises_no_incidents | 15 healthy ticks → 0 incidents |
| test_unhealthy_service_opens_incident_after_debounce | 1 bad tick → nothing; 3 → exactly one incident with `high_error_rate` |
| test_duplicate_incidents_are_suppressed | 10 ticks of a crash loop → 1 incident, severity critical |
| test_transient_blip_does_not_open_incident | one bad tick followed by recovery → no incident |
| test_dependency_failure_attributed_to_dependency | root = dependency-service; api-service listed as affected |
| test_supervisor_outage_never_opens_incident | blind monitoring never acts |
| test_signal_thresholds | boundary values; too few samples never signal |
| test_pick_root_prefers_most_upstream | graph attribution |

### 3.2 Root-cause analysis (`tests/test_rca.py`)
| Test | Verifies |
|---|---|
| test_diagnosis_identifies_scenario ×4 | bad deployment, broken config, resource exhaustion and dependency failure each give the correct category, root service, action and target; confidence ≥ 0.7; every factor references stored evidence; alternatives reported |
| test_dependency_failure_never_recommends_restarting_healthy_downstream | action targets the dependency |
| test_evidence_is_stored_with_sources | all 8 evidence kinds stored |
| test_ambiguous_evidence_is_not_guessed | evidence removed → `unknown`, no recommendation |
| test_no_known_good_version_means_no_action | no verified rollback target → no action |
| test_log_content_cannot_inject_actions | malicious log text does not change the recommendation |
| test_logs_from_a_previous_incident_do_not_contaminate_the_next | regression: FATAL logs of an already-fixed incident no longer lower the next diagnosis (fails without the fix) |

### 3.3 Safety policy (`tests/test_policy.py`)
| Test | Verifies |
|---|---|
| test_read_only_operations_allowed | category 1 allowed; a category-2 action is not a "read" |
| test_low_risk_category2_action_allowed | restart api-service allowed |
| test_stateful_resource_requires_approval | database restart → approval; allowed once approved |
| test_forbidden_actions_blocked_even_when_approved | purge/delete/stop denied |
| test_unknown_action_rejected | `execute_shell`, `run_command`, `kubectl_delete`, `""`, `None`, `42` |
| test_invalid_resource_rejected | control plane, supervisor, path traversal, shell metacharacters |
| test_resource_param_mismatch_and_extra_params_rejected | target mismatch, smuggled `command`, `$(reboot)` version |
| test_rollback_only_to_known_good_registered_version | unregistered version denied |
| test_excessive_retries_blocked | second attempt for the same incident denied |
| test_blast_radius_limit | a second service in the same incident denied |
| test_rate_limit | 7th execution in the window denied |
| test_emergency_stop_blocks_all_changes | denied even when approved; reads still allowed; release restores |
| test_supervised_mode_and_low_confidence_require_approval | both rules |
| test_execution_requires_matching_fresh_dry_run | missing, stale or swapped dry run denied |
| test_denials_are_audited | `action_blocked` audit record written |

### 3.4 Remediation, verification and rollback (`tests/test_remediation.py`)
| Test | Verifies |
|---|---|
| test_dry_run_does_not_modify_system | 0 operations; identical state |
| test_bad_deployment_is_rolled_back_and_verified | exactly one deploy of 1.4.2; verification passed; all timestamps set |
| test_broken_config_restored | config back to api-cfg-7 |
| test_resource_exhaustion_restarts_affected_service_only | only `restart api-service` |
| test_dependency_failure_restarts_only_dependency | only `restart dependency-service` |
| test_failed_verification_escalates_without_retry_loop | outage: one restart, escalated, never resolved, no rollback |
| test_worse_outcome_triggers_rollback | corrupt artifact: deploy 1.4.2 then 1.5.0; rollback succeeded; proposal `rolled_back` |
| test_command_failure_is_retried_boundedly_then_escalated | 1 + max_retries attempts, then escalated |
| test_emergency_stop_prevents_remediation | 0 operations, 0 executions |
| test_supervised_mode_waits_for_approval_then_executes | waits; approved by name → resolved |
| test_rejection_escalates_and_never_executes | 0 operations |
| test_cannot_approve_twice_or_execute_unapproved | executor refuses awaiting proposals; second decision rejected |
| test_recovery_time_is_measured | TTD/TTR computed; diagnosis and action scored correct; success and resolution rates counted |
| test_rollback_metrics_counted | regression for the `NOT IN (NULL)` analytics bug: rollbacks and verification failures are counted |

### 3.5 Security (`tests/test_security.py`) and API (`tests/test_api.py`)
Secret redaction (passwords, tokens, URL credentials, bearer, AWS keys); secrets in service logs never
reach evidence, audit, timeline or diagnoses; tokens hidden from `repr`; audit append-only and
tamper-evident; **no `subprocess` / `os.system` / `eval` / `exec` / docker.sock in `backend/app`**;
the ops client has exactly 3 public methods and validates inputs; no API route contains
exec/shell/command/run; private notes and `.env` are git-ignored, while public docs and
`.env.example` are not. API: overview reflects real state; the full incident flow over HTTP; strict
validation (404/409/422); the policy simulator never executes; emergency stop; log endpoint redaction;
excluded runs leave the metrics but stay listed and audited.

### 3.6 Integration with real processes (`tests/test_integration.py`)
Starts the real supervisor and the four services with random tokens. It waits for known-good
marking, injects through the real chaos API, ticks the real collector and detector, runs the real
pipeline, and asserts the outcome.

### 3.7 Results of the automated tests

Command and result on the test machine:

```
$ cd backend && python -m pytest -q        (demo stopped, so integration tests run)
66 passed, 1 warning in 70.31s (0:01:10)
$ cd frontend && npm test
 Test Files  1 passed (1)
      Tests  4 passed (4)
$ npm run build   ->  tsc strict type check + vite build succeeded
```

## 4. Live evaluation (failure injection)

Method: for each round and scenario, wait for a quiet, healthy system (plus 25 s so known-good
marking is fresh), **wait if the safety rate limit would block the run**, inject through the API, wait
for the incident to reach a terminal state, record, then reset. Ground truth comes from the scenario
definition, which the diagnosis engine never sees.

Generated 2026-11-19T13:53:23 UTC by `scripts/run_evaluation.py --rounds 3` on a fresh database.
Raw data: [`reports/evaluation_results.json`](../reports/evaluation_results.json), [`reports/evaluation_results.md`](../reports/evaluation_results.md).

### Summary

| Metric | Measured value |
|---|---|
| Controlled failure injections | 18 (3 rounds × 6 scenarios) |
| Incidents recorded | 18 |
| Detection rate | 100% |
| **MTTD** (injection → detection) | 5.7 s (median 5.8 s, min 2.1 s, max 10.1 s, n=18) |
| **MTTR** (injection → verified recovery, recoverable runs) | 20.6 s (median 21.6 s, min 15.1 s, max 23.3 s, n=12) |
| Detection → verified recovery | 14.4 s (median 14.6 s, min 11.8 s, max 17.1 s, n=12) |
| Detection → diagnosis | 0.1 s (median 0.1 s, min 0.0 s, max 0.1 s, n=18) |
| Diagnosis accuracy (category **and** root service) | 100% |
| Runs ending in the *expected* outcome (resolve vs. escalate) | 18/18 |
| Remediation success rate (verification passed) | 67% |
| Incorrect-remediation rate | 0% |
| Incident resolution rate (automated) | 67% |
| False-positive incidents | 0 (0%) |
| Human escalation rate | 33% (all from the two escalation scenarios) |
| Rollbacks executed / success rate | 3 / 100% |
| Actions blocked by the safety policy (this run) | 0 |
| Excluded runs | 1 |
| Audit hash chain after evaluation | valid (222 records) |

### Per scenario

| Scenario | Runs | Detected | Correct diagnosis | Resolved | Escalated | MTTD | MTTR |
|---|---|---|---|---|---|---|---|
| bad_deployment | 3 | 3 | 3 | 3 | 0 | 3.3 s | 16.2 s |
| broken_config | 3 | 3 | 3 | 3 | 0 | 4.9 s | 21.6 s |
| resource_exhaustion | 3 | 3 | 3 | 3 | 0 | 9.7 s | 21.8 s |
| dependency_failure | 3 | 3 | 3 | 3 | 0 | 6.7 s | 22.9 s |
| dependency_outage | 3 | 3 | 3 | 0 | 3 | 6.2 s | n/a |
| bad_deployment_corrupt_rollback | 3 | 3 | 3 | 0 | 3 | 3.4 s | n/a |

### Individual runs

| # | Scenario | Diagnosed | Action executed | Outcome | TTD | TTR | Escalated after |
|---|---|---|---|---|---|---|---|
| 1 | bad_deployment | bad_deployment ✓ | rollback_deployment | resolved | 5.1 s | 17.7 s | n/a |
| 2 | broken_config | broken_config ✓ | restore_config | resolved | 4.0 s | 20.2 s | n/a |
| 3 | resource_exhaustion | resource_exhaustion ✓ | restart_service | resolved | 9.4 s | 21.2 s | n/a |
| 4 | dependency_failure | dependency_failure ✓ | restart_service | resolved | 7.0 s | 23.0 s | n/a |
| 5 | dependency_outage | dependency_failure ✓ | restart_service | escalated | 5.8 s | n/a | 58.1 s |
| 7 | bad_deployment_corrupt_rollback | bad_deployment ✓ | rollback_deployment | escalated | 2.1 s | n/a | 50.6 s |
| 8 | bad_deployment | bad_deployment ✓ | rollback_deployment | resolved | 2.6 s | 15.7 s | n/a |
| 9 | broken_config | broken_config ✓ | restore_config | resolved | 5.9 s | 23.0 s | n/a |
| 10 | resource_exhaustion | resource_exhaustion ✓ | restart_service | resolved | 9.6 s | 21.7 s | n/a |
| 11 | dependency_failure | dependency_failure ✓ | restart_service | resolved | 7.1 s | 23.3 s | n/a |
| 12 | dependency_outage | dependency_failure ✓ | restart_service | escalated | 6.0 s | n/a | 56.7 s |
| 13 | bad_deployment_corrupt_rollback | bad_deployment ✓ | rollback_deployment | escalated | 2.6 s | n/a | 51.1 s |
| 14 | bad_deployment | bad_deployment ✓ | rollback_deployment | resolved | 2.1 s | 15.1 s | n/a |
| 15 | broken_config | broken_config ✓ | restore_config | resolved | 4.9 s | 21.6 s | n/a |
| 16 | resource_exhaustion | resource_exhaustion ✓ | restart_service | resolved | 10.1 s | 22.4 s | n/a |
| 17 | dependency_failure | dependency_failure ✓ | restart_service | resolved | 6.0 s | 22.3 s | n/a |
| 18 | dependency_outage | dependency_failure ✓ | restart_service | escalated | 6.8 s | n/a | 55.5 s |
| 19 | bad_deployment_corrupt_rollback | bad_deployment ✓ | rollback_deployment | escalated | 5.5 s | n/a | 54.4 s |

**Interpretation.**

- All 18 injected failures were detected; there were 0 false-positive incidents.
- Every diagnosis named the correct category and root service (100%). For the dependency
  scenarios this means the incident was attributed to `dependency-service`, and the healthy-but-affected
  `api-service` was never restarted.
- The 12 recoverable runs were all recovered automatically and verified. MTTR averaged 20.6 s;
  roughly 8–10 s of that is the deliberate stabilisation window the verifier requires before declaring recovery.
- The 6 escalations are the two scenarios designed to be unrecoverable. In `dependency_outage` exactly one
  bounded restart was attempted before escalating. In `bad_deployment_corrupt_rollback` the rollback itself crash-looped,
  SteadyOps detected that health had dropped, rolled back to the pre-action version (rollbacks: 3, success 100%),
  then escalated. Their verification failures account for the remediation success rate of 67%.
- Resource exhaustion has the slowest detection (9.7 s) because the leak
  must grow past the 200 MB threshold; bad deployments are fastest (the error rate crosses 20% within ~2 samples).
- Detection → diagnosis takes about 0.1 s: rule-based RCA over stored evidence is effectively instantaneous.

### Excluded runs

| # | Scenario | Reason |
|---|---|---|
| 6 | bad_deployment_corrupt_rollback | host suspended mid-run (wall clock jumped 8579s); timings invalid |

**Evaluation history (all attempts, kept for transparency).**

1. *Unpaced harness.* The first attempt injected faster than the policy's global rate limit (6 executions / 600 s).
   Runs 7–10 were diagnosed correctly, but the safety policy denied execution (`6/6 executions in the last 600 s`)
   and escalated them. This is the control working as designed. The harness was changed to wait for the window;
   the policy was not loosened. Records: `reports/rate_limit_observation_*`.
2. *Host suspend.* The second attempt ran overnight. The laptop suspended right after one injection and resumed 7 h 20 min
   later, so that run's timings were meaningless. The attempt was discarded, the harness gained wall-clock-jump
   detection (affected runs are excluded with an audited reason and repeated), and it now keeps Windows awake
   only while it runs.
3. *Clean run on the previous code.* 18 injections, detection rate 100%, diagnosis accuracy 100%, 0 false positives,
   MTTD 5.6 s, MTTR 21.0 s. Afterwards a back-to-back demo exposed defect #5
   (stale logs from a fixed incident lowering the next diagnosis), which was fixed.
4. *Final run on the final code* (fresh database): the results reported above. The laptop suspended
   during this attempt too (run #6). The harness detected the clock jump, excluded that run with an audited
   reason (see *Excluded runs*), and the evaluation was completed with `--resume`, which keeps the valid
   runs already recorded and runs only the missing ones.


## 5. Safety tests observed live

| Control | Live observation |
|---|---|
| Global rate limit | **First evaluation attempt** (unpaced harness, 10 injections in ~13 min): runs 1–6 executed and behaved correctly; runs 7–10 (bad_deployment, broken_config, resource_exhaustion, dependency_failure) were diagnosed correctly but the plan-phase policy **denied** execution with `6/6 executions in the last 600 s`, and the incidents were escalated. Raw records: `reports/rate_limit_observation_*`. The harness now waits for the window instead of the policy being loosened. |
| Rollback | `bad_deployment_corrupt_rollback`: every run rolled back to the pre-action version after the health score dropped, then escalated |
| Bounded attempts | `dependency_outage`: exactly one restart per run, then escalation; no retry loop |
| Blast radius | `dependency_failure`: api-service (affected but healthy itself) was never restarted |
| Audit integrity | hash chain verified valid after the evaluation (see the table above) |

## 6. Known failures and limitations of the evaluation

- The scenarios and the diagnosis rules were developed together. 100% accuracy here says the rules
  match these failure signatures; it does not predict accuracy on unseen production incidents.
- Each scenario ran 3 times on one machine. Timing varies with machine load (for example, the
  memory leak takes longer to cross the threshold).
- MTTD has a floor of roughly one to two sampling intervals (2 s ticks, 2-tick debounce, 6-sample
  minimum for error rate). MTTR is dominated by the deliberate verification window (≥ 8 s of stability)
  and, for escalations, by the 45 s verification timeout.
- The detection rate counts only injected failures; organic false positives were measured as incidents
  without an injection (0 in the reported run), but over a short period.
- Docker Compose packaging was not executed.
