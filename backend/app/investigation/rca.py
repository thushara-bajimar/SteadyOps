"""Rule-based root-cause analysis.

`diagnose(context)` is a pure function. For every candidate service it scores
four hypotheses (bad deployment, broken configuration, resource exhaustion,
dependency failure) using weighted evidence factors. Each factor that fires is
recorded with the evidence item that supports it, so the diagnosis is fully
explainable. Contradicting evidence subtracts from a hypothesis.

The engine never claims certainty it does not have: if the best hypothesis is
weak, or not clearly better than the runner-up, the diagnosis is marked
ambiguous and the incident is escalated to a human.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CATEGORIES = ("bad_deployment", "broken_config", "resource_exhaustion", "dependency_failure")
CATEGORY_LABEL = {
    "bad_deployment": "Bad deployment",
    "broken_config": "Broken configuration",
    "resource_exhaustion": "Resource exhaustion",
    "dependency_failure": "Dependency failure",
    "unknown": "Unknown / ambiguous",
}


@dataclass
class Hypothesis:
    category: str
    service: str
    factors: list[dict] = field(default_factory=list)

    def add(self, weight: float, fired: bool, text: str, evidence: str) -> None:
        if fired:
            self.factors.append({"weight": weight, "text": text, "evidence": evidence})

    @property
    def score(self) -> float:
        return round(max(0.0, min(1.0, sum(f["weight"] for f in self.factors))), 2)

    @property
    def supporting(self) -> list[dict]:
        return [f for f in self.factors if f["weight"] > 0]

    @property
    def contradicting(self) -> list[dict]:
        return [f for f in self.factors if f["weight"] < 0]


def _latest_unverified(items: list[dict], key: str, known: list[str]) -> dict | None:
    """The most recent change, if what it introduced has never been verified
    healthy. Older changes are superseded by newer ones and are ignored."""
    if not items:
        return None
    latest = items[-1]
    return latest if latest[key] not in known else None


def _score_service(s: str, ctx: dict, th: dict) -> list[Hypothesis]:
    svc = ctx["services"][s]
    ch = ctx["changes"].get(s, {"deployments": [], "config_changes": [], "runtime_events": []})
    kg = ctx["known_good"].get(s, {"versions": [], "configs": []})
    logs = ctx["log_findings"].get(s, {})
    met = ctx["metrics"].get(s, {})
    deps_unhealthy = [d for d in svc["depends_on"] if ctx["services"].get(d, {}).get("health") not in ("healthy", None)]
    process_up = svc["process_state"] == "running"
    sig = set(svc["signals"])
    unhealthy = svc["health"] != "healthy" or bool(sig)
    ev_logs, ev_ch, ev_kg, ev_h = f"logs:{s}", "changes", "known_good", "health"

    bad_deploy = _latest_unverified(ch["deployments"], "to_version", kg["versions"])
    bad_cfg = _latest_unverified(ch["config_changes"], "to_config", kg["configs"])
    prev_good_version = next((v for v in kg["versions"] if v != svc["version"]), None)
    prev_good_config = next((c for c in kg["configs"] if c != svc["config_version"]), None)

    # --- bad deployment ---------------------------------------------------------
    h1 = Hypothesis("bad_deployment", s)
    h1.add(0.35, bad_deploy is not None,
           f"{s} was deployed to {bad_deploy and bad_deploy['to_version']} (not yet verified healthy) by "
           f"{bad_deploy and bad_deploy['actor']} shortly before the incident", ev_ch)
    h1.add(0.2, unhealthy and process_up and ("high_error_rate" in sig or "health_degraded" in sig),
           f"{s} is running but failing requests (error rate {svc.get('error_rate')})", ev_h)
    h1.add(0.25, logs.get("exceptions", 0) > 0 and svc["version"] in logs.get("exception_versions", []),
           f"{logs.get('exceptions')} unhandled exceptions logged by version {svc['version']}: "
           f"{(logs.get('exception_samples') or [''])[0]}", ev_logs)
    h1.add(0.1, prev_good_version is not None, f"previous version {prev_good_version} is known-good", ev_kg)
    h1.add(0.05, process_up, f"{s} process is up (not a crash loop)", ev_h)
    h1.add(-0.4, bool(deps_unhealthy), f"dependencies {deps_unhealthy} are unhealthy", "dependencies")
    h1.add(-0.3, logs.get("config_errors", 0) > 0, "logs report configuration errors instead", ev_logs)
    h1.add(-0.1, bad_cfg is not None, "a configuration change also happened recently", ev_ch)

    # --- broken configuration ---------------------------------------------------
    h2 = Hypothesis("broken_config", s)
    h2.add(0.35, bad_cfg is not None,
           f"{s} configuration changed to {bad_cfg and bad_cfg['to_config']} (not yet verified healthy) by "
           f"{bad_cfg and bad_cfg['actor']} shortly before the incident", ev_ch)
    h2.add(0.2, "crash_loop" in sig or not process_up,
           f"{s} is not staying up (state={svc['process_state']}, restarts_recent={svc['restarts_recent']})", ev_h)
    h2.add(0.3, logs.get("config_errors", 0) > 0 or svc.get("last_exit_code") == 78,
           f"{s} logged {logs.get('config_errors', 0)} configuration validation errors / exit code "
           f"{svc.get('last_exit_code')}", ev_logs)
    h2.add(0.1, prev_good_config is not None, f"previous configuration {prev_good_config} is known-good", ev_kg)
    h2.add(-0.3, bool(deps_unhealthy), f"dependencies {deps_unhealthy} are unhealthy", "dependencies")
    h2.add(-0.2, bad_deploy is not None and bad_cfg is None, "a deployment (not a config change) happened recently", ev_ch)

    # --- resource exhaustion ----------------------------------------------------
    h3 = Hypothesis("resource_exhaustion", s)
    h3.add(0.4, "high_memory" in sig, f"{s} resident memory {svc.get('rss_mb')} MB is above threshold", f"metrics:{s}")
    h3.add(0.15, (met.get("rss_growth_mb") or 0) >= 50,
           f"memory grew by {met.get('rss_growth_mb')} MB during the observation window", f"metrics:{s}")
    h3.add(0.15, "high_latency" in sig or "high_cpu" in sig,
           f"{s} is slow or CPU-bound (p95 {svc.get('latency_p95_ms')} ms, CPU {svc.get('cpu_percent')}%)", f"metrics:{s}")
    h3.add(0.1, bad_deploy is None and bad_cfg is None and ("high_memory" in sig or "high_cpu" in sig),
           "no unverified deployment or config change explains it", ev_ch)
    h3.add(0.1, logs.get("memory_warnings", 0) > 0, f"{logs.get('memory_warnings')} memory/cache growth warnings in logs", ev_logs)
    h3.add(-0.3, bool(deps_unhealthy), f"dependencies {deps_unhealthy} are unhealthy", "dependencies")
    h3.add(-0.2, bad_deploy is not None, "an unverified deployment happened recently", ev_ch)

    # --- dependency failure (s is the failing dependency) ------------------------
    h4 = Hypothesis("dependency_failure", s)
    impacted = [d for d in svc["dependents"] if d in ctx["incident"]["affected_services"]
                or ctx["services"][d]["health"] != "healthy"]
    blamed_by = [d for d in svc["dependents"] if ctx["log_findings"].get(d, {}).get("dependency_errors", {}).get(s, 0) > 0]
    if svc["dependents"]:
        h4.add(0.3, unhealthy and svc["health"] in ("down", "degraded"),
               f"{s} itself is {svc['health']} ({svc.get('health_error') or svc.get('process_state')})", ev_h)
        h4.add(0.25, bool(impacted), f"downstream services {impacted} are impacted", ev_h)
        h4.add(0.2, bool(blamed_by), f"downstream logs from {blamed_by} report failed calls to {s}",
               f"logs:{blamed_by[0]}" if blamed_by else ev_logs)
        h4.add(0.1, bad_deploy is None and bad_cfg is None, f"no unverified change to {s} (failure is not self-inflicted)", ev_ch)
        h4.add(0.1, not deps_unhealthy, f"{s} has no unhealthy dependencies of its own (it is the most upstream failure)",
               "dependencies")
        h4.add(-0.2, "high_memory" in sig, f"{s} shows memory exhaustion", f"metrics:{s}")
    return [h1, h2, h3, h4]


def confidence_level(score: float) -> str:
    return "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"


def diagnose(ctx: dict, thresholds: dict) -> dict:
    th = thresholds["investigation"]
    hyps: list[Hypothesis] = []
    for s in ctx["candidates"]:
        if s in ctx["services"]:
            hyps.extend(_score_service(s, ctx, th))
    hyps.sort(key=lambda h: h.score, reverse=True)
    best = hyps[0] if hyps else None
    runner = hyps[1] if len(hyps) > 1 else None
    margin = round(best.score - (runner.score if runner else 0.0), 2) if best else 0.0
    refs = ctx.get("evidence_refs", {})

    def fmt(h: Hypothesis) -> dict:
        return {"category": h.category, "service": h.service, "score": h.score,
                "supporting": [dict(f, evidence_id=refs.get(f["evidence"])) for f in h.supporting],
                "contradicting": [dict(f, evidence_id=refs.get(f["evidence"])) for f in h.contradicting]}

    alternatives = [fmt(h) for h in hyps[1:4] if h.score > 0]
    if best is None or best.score < th["ambiguity_min_confidence"] or margin < th["ambiguity_min_margin"]:
        reason = ("no hypothesis is supported by enough evidence" if best is None or best.score < th["ambiguity_min_confidence"]
                  else f"top two explanations are too close (margin {margin})")
        return {
            "category": "unknown", "root_service": best.service if best else None,
            "suspected_cause": f"Cause is ambiguous: {reason}. A human should investigate.",
            "confidence": best.score if best else 0.0, "confidence_level": confidence_level(best.score if best else 0.0),
            "ambiguous": True, "supporting": fmt(best)["supporting"] if best else [], "best": fmt(best) if best else None,
            "alternatives": alternatives if best is None else [fmt(best)] + alternatives, "recommended": None,
            "margin": margin,
        }

    recommended = recommend(best, ctx)
    return {
        "category": best.category, "root_service": best.service,
        "suspected_cause": cause_sentence(best, ctx),
        "confidence": best.score, "confidence_level": confidence_level(best.score), "ambiguous": False,
        "supporting": fmt(best)["supporting"], "contradicting": fmt(best)["contradicting"],
        "best": fmt(best), "alternatives": alternatives, "recommended": recommended, "margin": margin,
    }


def cause_sentence(h: Hypothesis, ctx: dict) -> str:
    s = h.service
    svc = ctx["services"][s]
    if h.category == "bad_deployment":
        return f"Version {svc['version']} of {s}, deployed shortly before the incident, is failing requests."
    if h.category == "broken_config":
        return f"Configuration {svc['config_version']} of {s} is invalid; the service cannot start with it."
    if h.category == "resource_exhaustion":
        return f"{s} is exhausting memory ({svc.get('rss_mb')} MB) and slowing down."
    if h.category == "dependency_failure":
        note = ""
        if ctx["log_findings"].get(s, {}).get("provider_outage"):
            note = " Its own logs report an upstream provider outage, so a restart may not help."
        return f"{s} is failing, and services that depend on it ({', '.join(svc['dependents'])}) are failing as a result.{note}"
    return "Unknown"


def recommend(h: Hypothesis, ctx: dict) -> dict | None:
    s = h.service
    svc = ctx["services"][s]
    kg = ctx["known_good"].get(s, {"versions": [], "configs": []})
    if h.category == "bad_deployment":
        target = next((v for v in kg["versions"] if v != svc["version"]), None)
        if target is None:
            return {"action_id": None, "reason": f"no known-good version of {s} to roll back to"}
        return {"action_id": "rollback_deployment", "resource": s, "params": {"service": s, "target_version": target},
                "reason": f"Roll {s} back from {svc['version']} to last known-good version {target}."}
    if h.category == "broken_config":
        target = next((c for c in kg["configs"] if c != svc["config_version"]), None)
        if target is None:
            return {"action_id": None, "reason": f"no known-good configuration of {s} to restore"}
        return {"action_id": "restore_config", "resource": s, "params": {"service": s, "target_config": target},
                "reason": f"Restore {s} configuration from {svc['config_version']} to last known-good {target}."}
    if h.category == "resource_exhaustion":
        return {"action_id": "restart_service", "resource": s, "params": {"service": s},
                "reason": f"Restart {s} to release leaked memory. The underlying leak still needs a code fix."}
    if h.category == "dependency_failure":
        return {"action_id": "restart_service", "resource": s, "params": {"service": s},
                "reason": f"Restart the failing dependency {s} once (bounded). Healthy downstream services "
                          f"({', '.join(svc['dependents'])}) are left alone."}
    return None
