# SteadyOps Evaluation Results

Generated 2026-11-19T13:53:23.600839+00:00 by `scripts/run_evaluation.py` (3 round(s) per scenario).
All values are computed from recorded timestamps in the SteadyOps database.

## Summary

| Metric | Value |
|---|---|
| Injections | 18 |
| Incidents recorded | 18 |
| Detection rate | 100.0% |
| MTTD (injection -> detection), mean / median / max | 5.7 s / 5.83 s / 10.12 s |
| MTTR (injection -> verified recovery), mean / median / max | 20.6 s / 21.64 s / 23.32 s (n=12) |
| Detection -> verified recovery, mean | 14.45 s |
| Time to diagnose, mean | 0.06 s |
| Diagnosis accuracy (category + service) | 100.0% |
| Remediation success rate (verification passed) | 66.7% |
| Incorrect-remediation rate | 0.0% |
| Incident resolution rate (automated) | 66.7% |
| False-positive incidents | 0 (0.0%) |
| Human escalation rate | 33.3% |
| Rollbacks executed / success rate | 3 / 100.0% |
| Actions blocked by safety policy | 0 {} |
| Audit hash chain | valid (222 records) |

## Per scenario

| Scenario | Runs | Detected | Correct diagnosis | Resolved | Escalated | MTTD (s) | MTTR (s) |
|---|---|---|---|---|---|---|---|
| bad_deployment | 3 | 3 | 3 | 3 | 0 | 3.28 | 16.16 |
| broken_config | 3 | 3 | 3 | 3 | 0 | 4.92 | 21.61 |
| resource_exhaustion | 3 | 3 | 3 | 3 | 0 | 9.72 | 21.75 |
| dependency_failure | 3 | 3 | 3 | 3 | 0 | 6.68 | 22.88 |
| dependency_outage | 3 | 3 | 3 | 0 | 3 | 6.18 | n/a |
| bad_deployment_corrupt_rollback | 3 | 3 | 3 | 0 | 3 | 3.41 | n/a |

## Individual runs

| # | Scenario | Diagnosed | Action | Outcome | TTD (s) | TTR (s) | Escalated after (s) |
|---|---|---|---|---|---|---|---|
| 1 | bad_deployment | bad_deployment | rollback_deployment | resolved | 5.1 | 17.72 | n/a |
| 2 | broken_config | broken_config | restore_config | resolved | 4.01 | 20.24 | n/a |
| 3 | resource_exhaustion | resource_exhaustion | restart_service | resolved | 9.41 | 21.21 | n/a |
| 4 | dependency_failure | dependency_failure | restart_service | resolved | 6.95 | 23.03 | n/a |
| 5 | dependency_outage | dependency_failure | restart_service | escalated | 5.79 | n/a | 58.08 |
| 7 | bad_deployment_corrupt_rollback | bad_deployment | rollback_deployment | escalated | 2.11 | n/a | 50.56 |
| 8 | bad_deployment | bad_deployment | rollback_deployment | resolved | 2.65 | 15.7 | n/a |
| 9 | broken_config | broken_config | restore_config | resolved | 5.86 | 23.01 | n/a |
| 10 | resource_exhaustion | resource_exhaustion | restart_service | resolved | 9.64 | 21.7 | n/a |
| 11 | dependency_failure | dependency_failure | restart_service | resolved | 7.12 | 23.32 | n/a |
| 12 | dependency_outage | dependency_failure | restart_service | escalated | 5.97 | n/a | 56.67 |
| 13 | bad_deployment_corrupt_rollback | bad_deployment | rollback_deployment | escalated | 2.59 | n/a | 51.08 |
| 14 | bad_deployment | bad_deployment | rollback_deployment | resolved | 2.08 | 15.07 | n/a |
| 15 | broken_config | broken_config | restore_config | resolved | 4.89 | 21.59 | n/a |
| 16 | resource_exhaustion | resource_exhaustion | restart_service | resolved | 10.12 | 22.35 | n/a |
| 17 | dependency_failure | dependency_failure | restart_service | resolved | 5.97 | 22.29 | n/a |
| 18 | dependency_outage | dependency_failure | restart_service | escalated | 6.77 | n/a | 55.52 |
| 19 | bad_deployment_corrupt_rollback | bad_deployment | rollback_deployment | escalated | 5.52 | n/a | 54.41 |

## Excluded runs

| # | Scenario | Reason |
|---|---|---|
| 6 | bad_deployment_corrupt_rollback | host suspended mid-run (wall clock jumped 8579s); timings invalid |

Expected outcomes: `dependency_outage` and `bad_deployment_corrupt_rollback` are designed to end in escalation (they cannot be fixed safely); for those, escalation is the correct result.
