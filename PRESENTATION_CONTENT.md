# SteadyOps — Presentation Content (12 slides)

All numbers on these slides are measured values from `docs/TESTING_AND_EVALUATION.md`. Screenshots
referenced are real captures in `screenshots/`.

---

## Slide 1 — Title
**SteadyOps: The On-Call Engineer That Doesn't Panic**
Detect. Diagnose. Recover. Without making things worse.
Safety-first AI incident response and automated recovery · Cloud Computing, Cloud Security & MLOps
*Visual:* logo mark (pulse line in a rounded square) + architecture thumbnail.

## Slide 2 — Problem statement
- Outages force engineers to correlate alerts, logs, metrics, deployments and config changes under pressure.
- Small teams often have no dedicated on-call engineer; MTTR is dominated by *investigation*.
- AI agents could help, but unrestricted access can delete resources, restart healthy services,
  leak secrets, or turn a small outage into a big one.
- **Goal:** an agent that can never make an incident catastrophically worse just because it can run commands.

## Slide 3 — Why existing approaches fall short
| Approach | Gap |
|---|---|
| Alerting | detects, doesn't diagnose or fix |
| Restart policies / self-healing | treat symptoms; restart the victims of a dependency failure |
| Scripted auto-remediation | unbounded retries; rarely verifies; no undo |
| LLM agent + shell | can invent destructive commands; prompt injection via logs |
**Common flaw:** the component that decides is the component that acts.

## Slide 4 — Proposed solution
- **Investigate freely, change narrowly.**
- Rule-based, evidence-weighted root-cause engine (4 failure categories) that admits ambiguity.
- Only 3 allowlisted operations: restart service, roll back to a known-good version, restore a known-good config.
- An independent **safety policy engine** has final authority; humans approve sensitive actions; destructive actions are forbidden.
- Recovery is proven by sustained verification; bad outcomes are rolled back and escalated.

## Slide 5 — System architecture
*Visual:* `docs/diagrams/architecture.svg`.
- Demo workload: frontend → api → database + inventory, owned by a token-protected **supervisor** (no Docker socket, no shell).
- Control plane: collector → detector → evidence → RCA → dry run → **policy** → executor → verifier.
- Dashboard: 8 live pages. Storage: SQLite with a hash-chained audit log.

## Slide 6 — Detection and root-cause analysis
- Every 2 s: process state, `/health`, 4 synthetic transactions, RSS/CPU, restarts, change history.
- Signals debounced over 2 ticks; de-duplicated; attributed to the most upstream failing service.
- 8 kinds of sourced evidence; each diagnosis factor links to an evidence item.
- Example: *Bad deployment, confidence 0.95*: deploy 1.5.0 not yet verified · exceptions tagged 1.5.0 · 1.4.2 known-good.
- Ambiguous (score < 0.5 or margin < 0.15) → escalate rather than guess.

## Slide 7 — Safety-first remediation
*Visual:* `docs/diagrams/safety-gate.svg`.
- 14 controls: dry run, action + resource allowlists, least privilege, timeouts, retry limit, rate
  limit, blast radius, preconditions, verification, rollback, approval, append-only audit, emergency stop.
- Category 1 read-only → always; category 2 → auto only if all checks pass; category 3 → approval or forbidden.
- Re-checked immediately before execution; free text can never influence a check.

## Slide 8 — Live failure scenarios
| Scenario | SteadyOps response |
|---|---|
| Bad deployment | roll back to known-good 1.4.2 → verified |
| Broken config (crash loop) | restore known-good config → verified |
| Memory exhaustion | restart the exhausted service → verified |
| Dependency hang | restart the **dependency**, not the healthy API → verified |
| Provider outage | one bounded attempt → verification fails → escalate |
| Corrupted rollback | detects it made things worse → undoes own action → escalate |

## Slide 9 — Dashboard and demonstration
*Visual:* screenshots `incident_resolved.png` (60-second briefing + lifecycle) and `approvals_pending.png`.
- Lifecycle stepper; plain-language briefing; evidence explorer; policy checks; verification strip.
- Failure Lab with reset; supervised-mode approvals; policy simulator; emergency stop.

## Slide 10 — Testing and measured results
- **18 real injected failures** (6 scenarios × 3 rounds), 0 false positives.
- **MTTD 5.7 s** (max 10.1 s) · **MTTR 20.6 s** to *verified* recovery.
- **Diagnosis accuracy 100%** (category and root service).
- **12/12** recoverable runs auto-recovered; **6/6** unrecoverable runs escalated; **3** automatic rollbacks.
- 66 backend tests (incl. 2 real-process integration) + 4 frontend tests passing.
- Observed live: the rate limit blocked runs when the harness went too fast. The policy was not loosened.
*Visual:* `screenshots/analytics.png` plus the per-scenario table from `docs/TESTING_AND_EVALUATION.md`.

## Slide 11 — Advantages, limitations and future scope
**Advantages:** catastrophic actions impossible by construction · explainable · verifies before
claiming success · undoes its own mistakes · runs on a laptop without paid APIs.
**Limitations:** laptop-scale simulation; rules co-designed with scenarios; no authentication;
tamper-evident (not tamper-proof) audit; single node; Docker packaging untested here.
**Future:** Kubernetes executor with scoped service accounts · OIDC + RBAC · anomaly detection ·
OpenTelemetry · LLM restricted to explanations · external WORM audit.

## Slide 12 — Conclusion
- Safety comes from *architecture*, not from trusting the agent's judgement.
- SteadyOps knows which actions are safe, which need approval, when recovery has really succeeded,
  and when to stop and wake a human.
- *Visual:* the lifecycle diagram with "Resolved" and "Escalated" both highlighted as correct outcomes.

---

## Why SteadyOps is safer than an unrestricted autonomous agent (speaker notes)
1. **No command surface:** the agent cannot express "rm -rf". There is no shell, and only 3 typed operations exist.
2. **Separation of powers:** proposing, permitting and executing are different components with different privileges.
3. **Bounded by default:** one attempt, one service, six executions per ten minutes, timeouts.
4. **Evidence over assumptions:** rollback targets must have been *observed* healthy.
5. **Verification over exit codes**, and **rollback over hope**.
6. **Humans stay in charge:** approvals, rejection, emergency stop, and escalation on doubt.
