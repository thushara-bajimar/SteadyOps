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
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
OP = {"operator": "evaluation-harness"}


def wait(pred, timeout: float, every: float = 1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


def main() -> None:
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

    log = []
    for rnd in range(1, args.rounds + 1):
        for sc in args.scenarios.split(","):
            if not wait(quiet, 120):
                print(f"[{sc}] system did not become quiet; resetting")
                c.post("/failure-lab/reset", json=OP)
                wait(quiet, 120)
            time.sleep(args.healthy_baseline_s)
            r = c.post(f"/failure-lab/{sc}/inject", json=OP)
            r.raise_for_status()
            inj = r.json()["injection_id"]
            print(f"round {rnd} [{sc}] injected #{inj}", flush=True)

            def terminal():
                runs = c.get("/analytics").json()["runs"]
                run = next(x for x in runs if x["injection_id"] == inj)
                if run["incident_id"]:
                    inc = c.get(f"/incidents/{run['incident_id']}").json()["incident"]
                    if inc["status"] in ("resolved", "escalated", "closed"):
                        return run
                return None

            run = wait(terminal, 150, 2)
            if run is None:
                runs = c.get("/analytics").json()["runs"]
                run = next(x for x in runs if x["injection_id"] == inj)
            print(f"    -> {run['outcome']} diagnosed={run['diagnosed_category']} action={run['executed_action']} "
                  f"ttd={run['ttd_s']}s ttr={run['ttr_s']}s", flush=True)
            log.append(run)
            c.post("/failure-lab/reset", json=OP).raise_for_status()

    wait(quiet, 120)
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
    lines += ["", "Expected outcomes: `dependency_outage` and `bad_deployment_corrupt_rollback` are designed to end in "
              "escalation (they cannot be fixed safely); for those, escalation is the correct result.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
