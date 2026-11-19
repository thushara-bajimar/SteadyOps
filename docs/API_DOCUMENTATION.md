# SteadyOps API

Base URL: `http://127.0.0.1:8010/api`. JSON in, JSON out. Interactive OpenAPI docs: `http://127.0.0.1:8010/docs`.

Request bodies are validated strictly: unknown fields → `422`. Operator names must match
`^[A-Za-z0-9 _.\-]{2,40}$`.

## Error format

```json
{ "detail": "human-readable message" }          // 404, 409, 502, 503
{ "detail": [ { "loc": [...], "msg": "...", "type": "..." } ] }   // 422 validation
{ "detail": "internal error" }                  // 500 (no stack traces are returned)
```

| Code | Meaning |
|---|---|
| 404 | unknown incident / proposal / scenario / service |
| 409 | state conflict (proposal not awaiting approval; scenario already active; incident not escalated) |
| 422 | invalid or extra fields |
| 502 | supervisor rejected or was unreachable during injection/reset |
| 503 | supervisor unreachable while reading logs |

---

## Monitoring

### `GET /health`
Control-plane liveness.
```json
{"status":"ok","monitor_ticks":17,"last_tick_at":"2026-11-18T17:35:04.246+00:00","last_tick_error":null,"supervisor_ok":true}
```

### `GET /overview`
System status, healthy count, incident counts, pending approvals, last 15 incident events, per-service
state, emergency stop, autonomy mode, active injection and the analytics summary.

### `GET /services`
```json
[{"name":"api-service","health":"healthy","process_state":"running","version":"1.4.2","config_version":"api-cfg-7",
  "http_status":200,"response_ms":43.1,"health_error":null,"checks":{"database":"ok","inventory":"ok"},
  "error_rate":0.0,"latency_p95_ms":18.2,"rss_mb":64.0,"cpu_percent":0.4,"restart_count":0,"restarts_recent":0,
  "depends_on":["database-service","dependency-service"],"stateful":false,
  "known_good_versions":["1.4.2"],"known_good_configs":["api-cfg-7"]}]
```

### `GET /services/{name}/metrics?minutes=10`
Time series (≤ 600 points) of `ts, health, response_ms, error_rate, latency_p95_ms, rss_mb, cpu_percent, restarts_recent`.

### `GET /services/{name}/logs?lines=50`
Last *n* (≤ 200) structured log records, **redacted**.

## Incidents

### `GET /incidents?status=open|escalated|resolved|closed&limit=100`
List with `id, status, stage, severity, title, detected_service, root_service, category, confidence, scenario, timestamps…`.

### `GET /incidents/{id}`
Full record:
```json
{
  "incident": {"id":1,"status":"resolved","stage":"resolved","severity":"high","signals":[...], "...": "..."},
  "timeline": [{"ts":"…","stage":"detected","message":"Incident detected on api-service: …"}],
  "evidence": [{"id":1,"kind":"service_health","source":"health endpoints + supervisor","summary":"…","data":{...}}],
  "diagnoses": [{"category":"bad_deployment","root_service":"api-service","confidence":0.95,"confidence_level":"high",
                 "ambiguous":false,"supporting":{"supporting":[{"weight":0.35,"text":"…","evidence_id":5}],"contradicting":[]},
                 "alternatives":[{"category":"dependency_failure","service":"api-service","score":0.4}],
                 "recommended":{"action_id":"rollback_deployment","params":{"service":"api-service","target_version":"1.4.2"}}}],
  "proposals": [{"action_id":"rollback_deployment","status":"succeeded","dry_runs":[...],"policy_decisions":[...],
                 "approval":null,"executions":[{"status":"command_succeeded","verifications":[...],"rollbacks":[]}]}],
  "injection": {"scenario":"bad_deployment","injected_at":"…"},
  "timings": {"time_to_detect_s":5.33,"time_to_diagnose_s":0.07,"time_to_recover_s":18.05, "...":"..."},
  "summary": {"what_happened":"…","diagnosis":"…","evidence":["…"],"what_changed":"…","proposed_action":"…",
              "why_safe":"…","after_execution":"…","resolved":true,"status_line":"RESOLVED - …"}
}
```

### `POST /incidents/{id}/close`
Body `{"operator":"alice","note":"rolled back DB driver manually"}`. Only for `escalated` incidents (else 409).

## Approvals

### `GET /approvals`
Proposals (pending first) with `params, reason, risk_level, expected_impact, approval_reason, dry_run, diagnosis, approval, execution, rollback`.

### `POST /proposals/{id}/approve` · `POST /proposals/{id}/reject`
Body `{"operator":"alice","comment":"optional"}`.
- approve → `{"status":"approved"}` (execution starts asynchronously) or `{"status":"expired"}` if the refreshed dry run differs.
- reject → `{"status":"rejected"}`; the incident is escalated.
- `409` if the proposal is not `awaiting_approval`.

```bash
curl -X POST localhost:8010/api/proposals/7/approve -H 'Content-Type: application/json' \
     -d '{"operator":"alice","comment":"config diff looks right"}'
```

## Failure Lab

### `GET /failure-lab/scenarios`
`{"scenarios":[{id,title,description,target_service,expected_category,expected_action,expected_outcome,watch_for}], "active":…, "history":[…]}`

### `POST /failure-lab/{scenario}/inject`
Body `{"operator":"alice"}`. Scenarios: `bad_deployment`, `broken_config`, `resource_exhaustion`,
`dependency_failure`, `dependency_outage`, `bad_deployment_corrupt_rollback`. `409` if a scenario is already active.

### `POST /failure-lab/injections/{id}/exclude`
Body `{"operator":"alice","reason":"host suspended mid-run; timings invalid"}` (reason 5–300 chars).
Flags a run as invalid for measurement. Nothing is deleted: the run is listed under `excluded_runs`
in `/analytics`, removed from the metrics, and the decision is audited (`injection_excluded`).
`404` unknown run, `409` already excluded. The evaluation harness calls this automatically when it
detects that the host suspended (a wall-clock jump) during a run.

### `POST /failure-lab/reset`
Body `{"operator":"alice"}`. Restores the initial versions/configs, stops the leak, clears outages, and restarts as needed.

## Safety

### `GET /safety/status`
Emergency stop, autonomy mode, decision counts, last 50 denials, audit-chain verification.

### `GET /safety/policy`
The loaded (read-only) policy with an `implemented` flag per action.

### `POST /safety/emergency-stop`
Body `{"operator":"alice","engaged":true,"reason":"investigating manually"}` → `{"engaged":true,"reason":…,"by":"alice","at":…}`.

### `POST /safety/simulate`
Evaluates a hypothetical proposal through the real policy engine. **Never executes.**
```bash
curl -X POST localhost:8010/api/safety/simulate -H 'Content-Type: application/json' \
     -d '{"action_id":"execute_shell","resource":"api-service","params":{"cmd":"rm -rf /"}}'
```
```json
{"outcome":"deny","action_id":"execute_shell","resource":"api-service","category":null,
 "reasons":["action 'execute_shell' is NOT in the action allowlist"],"approval_reasons":[],
 "checks":[{"name":"action_allowlisted","passed":false,"detail":"action 'execute_shell' is NOT in the action allowlist"}]}
```

### `GET /settings` · `PUT /settings/autonomy`
Body `{"operator":"alice","mode":"supervised"}` (`auto` | `supervised`).

## Audit and analytics

### `GET /audit?limit=200&event_type=action_blocked`
Newest first: `id, ts, actor, event_type, incident_id, payload, prev_hash, hash`.

### `GET /audit/verify`
`{"valid":true,"records_checked":412,"head":"9f3c…"}`, or `{"valid":false,"broken_at_id":57}`.

### `GET /analytics`
Real excerpt from the final evaluation database (`reports/evaluation_results.json`):
```json
{
 "summary": {
  "injections": 18,
  "mttd_s": {
   "n": 18,
   "mean": 5.7,
   "median": 5.83,
   "min": 2.08,
   "max": 10.12
  },
  "mttr_s": {
   "n": 12,
   "mean": 20.6,
   "median": 21.64,
   "min": 15.07,
   "max": 23.32
  },
  "diagnosis_accuracy": 1.0,
  "remediation_success_rate": 0.667,
  "false_positives": 0,
  "rollbacks": 3,
  "blocked_actions": 0,
  "...": "..."
 },
 "by_scenario": {
  "bad_deployment": {
   "runs": 3,
   "detected": 3,
   "correct_diagnosis": 3,
   "resolved": 3,
   "escalated": 0,
   "mttd_s": 3.28,
   "mttr_s": 16.16
  },
  "...": "..."
 },
 "runs": [
  {
   "injection_id": 1,
   "scenario": "bad_deployment",
   "incident_id": 1,
   "injected_at": "2026-11-19T02:52:20.685+00:00",
   "expected_category": "bad_deployment",
   "diagnosed_category": "bad_deployment",
   "diagnosis_correct": true,
   "expected_action": "rollback_deployment",
   "executed_action": "rollback_deployment",
   "action_correct": true,
   "outcome": "resolved",
   "ttd_s": 5.1,
   "time_to_diagnose_s": 0.06,
   "remediation_duration_s": 0.05,
   "ttr_s": 17.72,
   "detect_to_recover_s": 12.62,
   "time_to_escalate_s": null
  },
  "..."
 ],
 "excluded_runs": [
  {
   "injection_id": 6,
   "scenario": "bad_deployment_corrupt_rollback",
   "injected_at": "2026-11-19T02:57:03.537+00:00",
   "incident_id": 6,
   "excluded_reason": "host suspended mid-run (wall clock jumped 8579s); timings invalid"
  }
 ]
}
```

### `GET /actions`
The allowlist catalogue: `id, category, description, forbidden, resources, approval_resources`.

---

## Supervisor API (internal, demo workload)

Token header `X-Supervisor-Token`; actor header `X-Actor` (`^[a-z0-9:_.\-]{1,64}$`).

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | no auth |
| GET | `/services`, `/services/{name}/logs?lines=`, `/changes?limit=`, `/registry/{name}` | read |
| POST | `/services/{name}/restart` | — |
| POST | `/services/{name}/deploy` `{"version":"1.4.2"}` | 422 unless in `stack.json` |
| POST | `/services/{name}/config` `{"config_version":"api-cfg-7"}` | 422 unless in `stack.json` |
| POST | `/chaos/{scenario}`, `/chaos-reset`, GET `/chaos/status` | header `X-Chaos-Token` instead |
