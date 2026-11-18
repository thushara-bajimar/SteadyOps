"""Post-remediation verification.

A successful command is not proof of recovery. The verifier takes fresh,
independent observations and passes only when, for a sustained period:

  * every checked service (the target, the originally affected services and
    the target's dependents) shows no detection signal at all, and
  * end-to-end synthetic transactions all succeed.

It must see `required_consecutive_healthy` healthy observations spanning at
least `stabilization_s` seconds before `timeout_s` elapses.
"""
from __future__ import annotations

import time

from ..database.db import utcnow
from ..detection.detector import compute_signals
from ..monitoring.collector import Collector, Observation

HEALTH_SCORE = {"healthy": 2, "degraded": 1, "down": 0, "unknown": 0}


def health_score(obs: Observation, services: list[str]) -> int:
    return sum(HEALTH_SCORE.get(obs.services[s].health, 0) for s in services if s in obs.services)


class Verifier:
    def __init__(self, collector: Collector, thresholds: dict, sleep=time.sleep, clock=time.monotonic):
        self.collector = collector
        self.v = thresholds["verification"]
        self.det = thresholds["detection"]
        self.sleep = sleep
        self.clock = clock

    def evaluate_once(self, obs: Observation, services: list[str]) -> dict:
        problems = []
        if not obs.supervisor_ok:
            problems.append("supervisor unreachable - cannot confirm process state")
        for s in services:
            o = obs.services.get(s)
            if o is None:
                problems.append(f"{s}: not observed")
                continue
            # Error-rate signals need a window; it is checked separately below.
            for sig in compute_signals(o, dict(self.det, min_probe_samples=10**9)):
                problems.append(f"{s}: {sig['message']}")
        api = obs.services.get("api-service")
        if api is not None and api.error_rate is not None and api.error_rate > 0:
            problems.append(f"synthetic transactions failing ({api.error_rate:.0%} of {api.probe_samples})")
        return {"ts": obs.ts, "healthy": not problems, "problems": problems,
                "health": {s: obs.services[s].health for s in services if s in obs.services}}

    def verify(self, services: list[str]) -> dict:
        started_at = utcnow()
        start = self.clock()
        checks: list[dict] = []
        streak_start: float | None = None
        streak = 0
        last_obs: Observation | None = None
        while True:
            obs = self.collector.observe(use_window=False, record=True)
            last_obs = obs
            c = self.evaluate_once(obs, services)
            checks.append(c)
            now = self.clock()
            if c["healthy"]:
                if streak == 0:
                    streak_start = now
                streak += 1
                if streak >= self.v["required_consecutive_healthy"] and now - streak_start >= self.v["stabilization_s"]:
                    return {"result": "passed", "started_at": started_at, "finished_at": utcnow(), "checks": checks,
                            "summary": f"{streak} consecutive healthy observations over {now - streak_start:.0f}s; "
                                       f"all synthetic transactions succeeded", "final_obs": last_obs}
            else:
                streak, streak_start = 0, None
            if now - start >= self.v["timeout_s"]:
                last = checks[-1]["problems"] if checks else []
                return {"result": "failed", "started_at": started_at, "finished_at": utcnow(), "checks": checks,
                        "summary": f"not stable within {self.v['timeout_s']:.0f}s; last problems: {'; '.join(last[:3])}",
                        "final_obs": last_obs}
            self.sleep(self.v["check_interval_s"])
