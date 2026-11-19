# Prompt for a presentation-generation tool

Copy everything below the line into your slide-generation tool (Gamma, Beautiful.ai, Canva Docs-to-Deck,
PowerPoint Copilot, Google Slides "Help me create", etc.). Upload the listed image files from this
repository when the tool asks for assets.

---

Create a polished, technically accurate **12-slide** presentation for an engineering project review
titled **"SteadyOps: The On-Call Engineer That Doesn't Panic"** with the subtitle **"Detect. Diagnose.
Recover. Without making things worse."** The audience is engineering faculty and technical
evaluators. The presentation must explain clearly **why SteadyOps is safer than an unrestricted
autonomous operations agent**.

## Visual design direction
- Style: calm, professional cloud-operations look. Clean white or very light warm-grey background
  (#F4F4F1), dark text (#0B0B0B), secondary text (#52514E). Primary accent blue (#2A78D6 / deep
  #1C5CAB). Use status colours only for status: green #0CA30C (healthy/resolved), amber #EDA100
  (approval/warning), red #D03B3B (blocked/escalated), always with an icon (✓ ! ✕) and a word, never colour alone.
- Fonts: Inter or Segoe UI. Titles 36–40 pt semibold; body 20–24 pt; never below 16 pt.
- Layout: consistent 12-column grid, generous margins, a single key message per slide written as the
  slide title (an assertion, not a label). **Maximum ~35 words of body text per slide**; move detail to speaker notes.
- No stock photos, no 3D, no decorative gradients or clip-art robots. Diagrams and real screenshots only.
- Charts: flat, thin, labelled directly, no dual axes, no 3D.
- Add slide numbers and a small "SteadyOps" footer.

## Assets to use (real files, do not invent screenshots)
- `docs/diagrams/architecture.svg`: system architecture
- `docs/diagrams/incident-lifecycle.svg`: incident lifecycle
- `docs/diagrams/safety-gate.svg`: safety policy gate
- `screenshots/overview.png`, `screenshots/incident_resolved.png`, `screenshots/incident_rollback.png`,
  `screenshots/approvals_pending.png`, `screenshots/incident_awaiting_approval.png`, `screenshots/failure_lab.png`, `screenshots/safety.png`,
  `screenshots/analytics.png`
If a tool cannot import SVG, use the PNG renders in `reports/assets/` with the same names.

## Slide-by-slide content

1. **Title.** Title, subtitle, "Safety-first AI incident response and automated recovery",
   domain line "Cloud Computing · Cloud Security · MLOps". Small architecture thumbnail on the right.

2. **"Outages are slow to diagnose, and dangerous to automate."** Three icon rows: investigation
   dominates MTTR; small teams lack on-call; unrestricted agents can delete data, restart healthy
   services, leak secrets. Bottom banner: "An agent must never make an incident catastrophically worse
   just because it can run commands."

3. **"Existing tools either don't fix, or fix recklessly."** A 4-row comparison table (Alerting /
   Restart policies / Scripted remediation / LLM agent with shell) with one gap each. Highlight the
   common flaw: "the component that decides is the component that acts".

4. **"SteadyOps investigates freely but changes narrowly."** Three columns: *Investigate* (read-only,
   evidence-based RCA), *Gate* (independent safety policy, human approval), *Prove* (verification,
   rollback, escalation). Show the three allowed operations as chips: restart · roll back to known-good · restore known-good config.

5. **"Deciding, permitting and acting are separate components."** Full-width architecture diagram.
   Callouts: "no Docker socket, no shell", "3 fixed POST operations", "hash-chained audit".

6. **"Every diagnosis cites its evidence and admits uncertainty."** Left: detection pipeline (2 s
   sampling → signals → 2-tick debounce → root attribution). Right: an evidence card for the real
   bad-deployment example: "+0.35 deploy 1.5.0 not yet verified · +0.25 exceptions tagged 1.5.0 ·
   +0.20 failing while running · +0.10 1.4.2 known-good → confidence 0.95". Footer: "Score < 0.5 or
   margin < 0.15 → escalate instead of guessing."

7. **"The safety engine, not the agent, has the final word."** The safety-gate diagram plus a 3-tier
   category table (read-only → always; safeguarded → auto if all checks pass; destructive → approval or
   forbidden). Mention: re-checked right before execution; emergency stop overrides everything.

8. **"Six live failure scenarios, two of which *should* end with a human."** A 6-row table: Bad deployment →
   rollback ✓; Broken config → restore ✓; Memory exhaustion → restart ✓; Dependency hang → restart
   dependency (not the API) ✓; Provider outage → one try then escalate !; Corrupted rollback → undo own
   action then escalate !.

9. **"An engineer understands the incident in under a minute."** Two screenshots side by side
   (incident detail with lifecycle and 60-second briefing; pending approval card). Three callouts:
   lifecycle stepper, policy checks, verification strip.

10. **"Measured on real injected failures."** Use exactly the numbers from the table below (from
    `docs/TESTING_AND_EVALUATION.md`). Show them as large stat tiles (MTTD, MTTR, diagnosis accuracy,
    false positives) plus a small table, and the analytics screenshot. Add a footnote: "18 controlled runs on a
    laptop; escalation is the correct outcome for 6 of them."
    
    | Metric | Value |
    |---|---|
    | Injected failures (6 scenarios × 3) | 18 |
    | Detected | 100% · MTTD 5.7 s |
    | Correct root cause (category + service) | 100% |
    | Recoverable runs auto-recovered & verified | 12/12 · MTTR 20.6 s |
    | Unrecoverable runs escalated (as designed) | 6/6 |
    | False-positive incidents | 0 |
    | Rollbacks after a harmful action | 3 (100% succeeded) |

11. **"Strong guarantees, honest limits."** Two columns. Advantages: catastrophic actions impossible by
    construction; explainable; verifies before success; undoes own mistakes; no paid APIs.
    Limitations: laptop-scale; rules co-designed with scenarios; no authentication; tamper-evident audit;
    single node; Docker packaging untested. Future work as a row of chips: Kubernetes executor, OIDC/RBAC,
    anomaly detection, OpenTelemetry, LLM-for-explanations-only, WORM audit.

12. **"It knows when to act, and when to wake a human."** Closing statement plus the lifecycle diagram, with
    Resolved and Escalated both marked as correct outcomes. Thank-you line and repository URL.

## Speaker notes to include (why it is safer than an unrestricted agent)
1. No command surface: there is no shell and only three typed operations.
2. Separation of powers between proposing, permitting and executing.
3. Bounded by default: one attempt, one service, six executions per ten minutes, timeouts.
4. Rollback targets must have been observed healthy.
5. Success means sustained verification, not a zero exit code; worse outcomes are rolled back.
6. Humans approve, reject, stop, and are called whenever the system is unsure.

## Constraints
- Do **not** invent metrics, logos, customer names or screenshots. Use only the numbers and images provided.
- Keep terminology consistent: "safety policy engine", "known-good", "verification", "escalation".
- Export both 16:9 PPTX and PDF.
