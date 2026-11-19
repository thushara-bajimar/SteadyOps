"""Repeatable evaluation: inject every Failure Lab scenario N times through the
real SteadyOps API, wait for the incident to reach a terminal state, reset,
and record what actually happened.

Requires a running stack (python scripts/start_local.py --fresh).

    python scripts/run_evaluation.py --rounds 3
    python scripts/run_evaluation.py --scenarios bad_deployment,broken_config --rounds 1

Writes reports/evaluation_results.json and reports/evaluation_results.md.
Every number in those files is computed by SteadyOps' /api/analytics endpoint
from recorded timestamps; nothing is estimated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
OP = {"operator": "evaluation-harness"}


class HostSuspended(RuntimeError):
    """The wall clock jumped: the machine slept, so the run's timings are meaningless."""


def wait(pred, timeout: float, every: float = 1.0, max_gap: float = 30.0):
    deadline = time.time() + timeout
    last = time.time()
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(every)
        now = time.time()
        if now - last > every + max_gap:
            raise HostSuspended(f"wall clock jumped {now - last:.0f}s")
        last = now
    return None


def quiet_fn(c):
    def quiet():
        o = c.get("/overview").json()
        return o["system_status"] == "healthy" and o["incidents"]["active"] == 0 and not o["active_injection"]
    return quiet


def keep_awake() -> None:
    """Ask Windows not to sleep while this process runs (released automatically on exit).
    A suspend mid-run would invalidate timings; such runs are also detected and excluded."""
    if sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def main() -> None:
    keep_awake()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010/api")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--scenarios", default="bad_deployment,broken_config,resource_exhaustion,dependency_failure,"
                                           "dependency_outage,bad_deployment_corrupt_rollback")
    ap.add_argument("--healthy-baseline-s", type=float, default=25.0,
                    help="healthy time required before each injection (known-good marking needs 20 s)")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=30)

    def overview():
        return c.get("/overview").json()

    def quiet():
        o = overview()
        return o["system_status"] == "healthy" and o["incidents"]["active"] == 0 and not o["active_injection"]

    rl = c.get("/safety/policy").json()["rate_limit"]

    def respect_rate_limit():
        """The safety policy allows at most rl['max_executions'] remediations per window.
        The harness waits rather than loosening the policy, so every run can act."""
        while True:
            events = c.get("/audit?event_type=remediation_started&limit=100").json()
            now = datetime.now(timezone.utc)
            recent = [e for e in events if (now - datetime.fromisoformat(e["ts"])).total_seconds() < rl["window_s"]]
            if len(recent) < rl["max_executions"]:
                return
            oldest = min(datetime.fromisoformat(e["ts"]) for e in recent)
            wait_s = rl["window_s"] - (now - oldest).total_seconds() + 2
            print(f"    (rate limit {len(recent)}/{rl['max_executions']} in {rl['window_s']}s - waiting {wait_s:.0f}s)", flush=True)
            time.sleep(max(1.0, wait_s))

    log = []
    for rnd in range(1, args.rounds + 1):
        for sc in args.scenarios.split(","):
            for attempt in range(3):
                try:
                    log.append(run_one(c, rnd, sc, quiet, respect_rate_limit, args))
                    break
                except HostSuspended as exc:
                    print(f"    !! host suspended during run ({exc}); excluding it and repeating", flush=True)
                    c.post("/failure-lab/reset", json=OP)
                    wait(quiet, 180)
    finish(c, args)


def run_one(c, rnd, sc, quiet, respect_rate_limit, args):
    """Inject one scenario, wait for a terminal incident state, record, reset."""
    if not wait(quiet, 120):
        print(f"[{sc}] system did not become quiet; resetting")
        c.post("/failure-lab/reset", json=OP)
        wait(quiet, 120)
    respect_rate_limit()
    time.sleep(args.healthy_baseline_s)
    r = c.post(f"/failure-lab/{sc}/inject", json=OP)
    r.raise_for_status()
    inj = r.json()["injection_id"]
    print(f"round {rnd} [{sc}] injected #{inj}", flush=True)

    def terminal():
        run = next(x for x in c.get("/analytics").json()["runs"] if x["injection_id"] == inj)
        if run["incident_id"]:
            inc = c.get(f"/incidents/{run['incident_id']}").json()["incident"]
            if inc["status"] in ("resolved", "escalated", "closed"):
                return run
        return None

    try:
        run = wait(terminal, 150, 2)
    except HostSuspended as exc:
        c.post(f"/failure-lab/injections/{inj}/exclude",
               json={**OP, "reason": f"host suspended mid-run ({exc}); timings invalid"})
        raise
    if run is None:
        run = next(x for x in c.get("/analytics").json()["runs"] if x["injection_id"] == inj)
    print(f"    -> {run['outcome']} diagnosed={run['diagnosed_category']} action={run['executed_action']} "
          f"ttd={run['ttd_s']}s ttr={run['ttr_s']}s", flush=True)
    c.post("/failure-lab/reset", json=OP).raise_for_status()
    return run


def finish(c, args):
    wait(quiet_fn(c), 120)
    analytics = c.get("/analytics").json()
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "rounds": args.rounds,
           "scenarios": args.scenarios.split(","), "analytics": analytics,
           "audit_chain": c.get("/audit/verify").json()}
    reports = REPO / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "evaluation_results.json").write_text(json.dumps(out, indent=2))
    (reports / "evaluation_results.md").write_text(render_md(out))
    print("\nWrote reports/evaluation_results.json and reports/evaluation_results.md")


def fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v}"


def render_md(out: dict) -> str:
    s = out["analytics"]["summary"]
    lines = [
        "# SteadyOps Evaluation Results", "",
        f"Generated {out['generated_at']} by `scripts/run_evaluation.py` ({out['rounds']} round(s) per scenario).",
        "All values are computed from recorded timestamps in the SteadyOps database.", "",
        "## Summary", "", "| Metric | Value |", "|---|---|",
        f"| Injections | {s['injections']} |",
        f"| Incidents recorded | {s['incidents_total']} |",
        f"| Detection rate | {fmt(s['detection_rate'], True)} |",
        f"| MTTD (injection -> detection), mean / median / max | {s['mttd_s']['mean']} s / {s['mttd_s']['median']} s / {s['mttd_s']['max']} s |",
        f"| MTTR (injection -> verified recovery), mean / median / max | {s['mttr_s']['mean']} s / {s['mttr_s']['median']} s / {s['mttr_s']['max']} s (n={s['mttr_s']['n']}) |",
        f"| Detection -> verified recovery, mean | {s['detect_to_recover_s']['mean']} s |",
        f"| Time to diagnose, mean | {s['time_to_diagnose_s']['mean']} s |",
        f"| Diagnosis accuracy (category + service) | {fmt(s['diagnosis_accuracy'], True)} |",
        f"| Remediation success rate (verification passed) | {fmt(s['remediation_success_rate'], True)} |",
        f"| Incorrect-remediation rate | {fmt(s['incorrect_remediation_rate'], True)} |",
        f"| Incident resolution rate (automated) | {fmt(s['incident_resolution_rate'], True)} |",
        f"| False-positive incidents | {s['false_positives']} ({fmt(s['false_positive_rate'], True)}) |",
        f"| Human escalation rate | {fmt(s['human_escalation_rate'], True)} |",
        f"| Rollbacks executed / success rate | {s['rollbacks']} / {fmt(s['rollback_success_rate'], True)} |",
        f"| Actions blocked by safety policy | {s['blocked_actions']} {s['blocked_by_phase']} |",
        f"| Audit hash chain | {'valid' if out['audit_chain']['valid'] else 'BROKEN'} ({out['audit_chain']['records_checked']} records) |",
        "", "## Per scenario", "",
        "| Scenario | Runs | Detected | Correct diagnosis | Resolved | Escalated | MTTD (s) | MTTR (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, v in out["analytics"]["by_scenario"].items():
        lines.append(f"| {name} | {v['runs']} | {v['detected']} | {v['correct_diagnosis']} | {v['resolved']} | "
                     f"{v['escalated']} | {fmt(v['mttd_s'])} | {fmt(v['mttr_s'])} |")
    lines += ["", "## Individual runs", "",
              "| # | Scenario | Diagnosed | Action | Outcome | TTD (s) | TTR (s) | Escalated after (s) |",
              "|---|---|---|---|---|---|---|---|"]
    for r in out["analytics"]["runs"]:
        lines.append(f"| {r['injection_id']} | {r['scenario']} | {r['diagnosed_category']} | {r['executed_action']} | "
                     f"{r['outcome']} | {fmt(r['ttd_s'])} | {fmt(r['ttr_s'])} | {fmt(r['time_to_escalate_s'])} |")
    ex = out["analytics"].get("excluded_runs", [])
    lines += ["", "## Excluded runs", ""]
    if ex:
        lines += ["| # | Scenario | Reason |", "|---|---|---|"]
        lines += [f"| {e['injection_id']} | {e['scenario']} | {e['excluded_reason']} |" for e in ex]
    else:
        lines.append("None.")
    lines += ["", "Expected outcomes: `dependency_outage` and `bad_deployment_corrupt_rollback` are designed to end in "
              "escalation (they cannot be fixed safely); for those, escalation is the correct result.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
