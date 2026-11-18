"""SteadyOps demo-stack supervisor.

This is the *execution boundary* between SteadyOps and the workload. It plays
the role a container runtime / node agent plays in production, but exposes
only a deliberately tiny API:

  read-only (operator token):  list services, read logs, read change history
  operations (operator token): restart / start a known service,
                               deploy a version listed in stack.json,
                               apply a configuration listed in stack.json
  fault injection (chaos token, Failure Lab only)

There is no endpoint that accepts a command, a path, an image name or any
free-form value. SteadyOps therefore cannot execute arbitrary commands even if
its own logic were compromised; it can only ask for the fixed operations below.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent  # demo-services/
REPO = ROOT.parent
STACK = json.loads((Path(__file__).parent / "stack.json").read_text())
LOG_DIR = Path(os.environ.get("DEMO_LOG_DIR", REPO / "runtime-data" / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CHAOS_TOKEN = os.environ.get("CHAOS_TOKEN", "")
MANAGED = [s for s in os.environ.get("SUPERVISOR_MANAGED", ",".join(STACK["services"])).split(",") if s]
ACTOR_RE = re.compile(r"^[a-z0-9:_.\-]{1,64}$")
CRASH_LOOP_WINDOW_S = 120
CRASH_LOOP_THRESHOLD = 3


def now() -> float:
    return time.time()


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or now(), timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Change history (deployments, config changes, restarts, process exits).
# Fault-injection actions that would be invisible in reality (a leak starting,
# a dependency hanging) are deliberately NOT recorded here, so SteadyOps cannot
# "cheat" by reading the Failure Lab's notes.
# ---------------------------------------------------------------------------
_changes: list[dict] = []
_changes_lock = threading.Lock()


def record_change(service: str, kind: str, actor: str, **data) -> dict:
    with _changes_lock:
        entry = {"id": len(_changes) + 1, "ts": iso(), "service": service, "kind": kind, "actor": actor, **data}
        _changes.append(entry)
        return entry


class ManagedService:
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.spec = spec
        self.version = spec["initial_version"]
        self.config_version = spec["initial_config"]
        self.proc: subprocess.Popen | None = None
        self.ps: psutil.Process | None = None
        self.logs: deque[dict] = deque(maxlen=3000)
        self.restart_times: deque[float] = deque(maxlen=50)
        self.restart_count = 0
        self.state = "stopped"
        self.started_at: float | None = None
        self.last_exit_code: int | None = None
        self.last_exit_at: float | None = None
        self.next_restart_at: float | None = None
        self.stopping = False
        self.provider_outage = False  # dependency-service only
        self.broken_artifacts: set[str] = set()  # versions that fail to start
        self.lock = threading.RLock()
        self.logfile = open(LOG_DIR / f"{name}.log", "a", encoding="utf-8")

    # -- logging -----------------------------------------------------------
    def _append_log(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError
        except ValueError:
            rec = {"ts": iso(), "level": "INFO", "service": self.name, "msg": line[:500], "raw": True}
        self.logs.append(rec)
        self.logfile.write(json.dumps(rec) + "\n")
        self.logfile.flush()

    def _supervisor_log(self, level: str, msg: str, **fields) -> None:
        self._append_log(json.dumps({"ts": iso(), "level": level, "service": self.name, "source": "supervisor", "msg": msg, **fields}))

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._append_log(line)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            if self.version in self.broken_artifacts:
                # Simulates an image/artifact that cannot be started.
                self.last_exit_code = 125
                self.last_exit_at = now()
                self.state = "failed"
                self._supervisor_log("ERROR", "failed to start: artifact checksum mismatch", artifact=f"{self.name}:{self.version}", exit_code=125)
                self._schedule_restart()
                return
            cfg = STACK["configs"][self.name][self.config_version]
            env = dict(os.environ)
            for secret in ("SUPERVISOR_TOKEN",):  # children never see the operator token
                env.pop(secret, None)
            env.update(
                SERVICE_NAME=self.name,
                PORT=str(self.spec["port"]),
                APP_VERSION=self.version,
                CONFIG_VERSION=self.config_version,
                SERVICE_CONFIG=json.dumps(cfg),
                PYTHONUNBUFFERED="1",
            )
            if self.provider_outage:
                env["PROVIDER_OUTAGE"] = "1"
            else:
                env.pop("PROVIDER_OUTAGE", None)
            self.proc = subprocess.Popen(
                [sys.executable, str(ROOT / self.spec["script"])],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(ROOT),
            )
            try:
                self.ps = psutil.Process(self.proc.pid)
                self.ps.cpu_percent(None)
            except psutil.Error:
                self.ps = None
            self.started_at = now()
            self.state = "running"
            self.next_restart_at = None
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            proc = self.proc
            if proc and proc.poll() is None:
                kill_tree(proc)
            self.proc = None
            self.ps = None
            self.state = "stopped"
            self.stopping = False
            self.next_restart_at = None

    def restart(self) -> None:
        self.stop()
        time.sleep(0.3)  # let the OS release the port
        self.start()

    def _schedule_restart(self) -> None:
        # Restart policy "on-failure" with exponential backoff (like Docker/K8s).
        t = now()
        self.restart_times.append(t)
        recent = [x for x in self.restart_times if t - x < CRASH_LOOP_WINDOW_S]
        backoff = min(2 ** max(len(recent) - 1, 0), 8)
        self.next_restart_at = t + backoff
        if len(recent) >= CRASH_LOOP_THRESHOLD:
            self.state = "crash_loop"

    def watchdog_tick(self) -> None:
        with self.lock:
            if self.stopping:
                return
            if self.proc is not None and self.proc.poll() is not None:
                code = self.proc.returncode
                self.last_exit_code = code
                self.last_exit_at = now()
                self.proc = None
                self.ps = None
                self.state = "exited"
                record_change(self.name, "process_exit", "runtime", exit_code=code, version=self.version, config_version=self.config_version)
                self._supervisor_log("ERROR", "process exited unexpectedly", exit_code=code)
                self._schedule_restart()
            if self.proc is None and self.next_restart_at and now() >= self.next_restart_at:
                self.restart_count += 1
                record_change(self.name, "auto_restart", "runtime", attempt=self.restart_count)
                self.start()

    def status(self) -> dict:
        with self.lock:
            rss = cpu = None
            if self.ps is not None:
                rss, cpu = tree_usage(self.ps)
            t = now()
            return {
                "name": self.name,
                "state": self.state,
                "pid": self.proc.pid if self.proc else None,
                "port": self.spec["port"],
                "stateful": self.spec["stateful"],
                "depends_on": self.spec["depends_on"],
                "version": self.version,
                "config_version": self.config_version,
                "restart_count": self.restart_count,
                "restarts_last_120s": len([x for x in self.restart_times if t - x < CRASH_LOOP_WINDOW_S]),
                "started_at": iso(self.started_at) if self.started_at else None,
                "last_exit_code": self.last_exit_code,
                "last_exit_at": iso(self.last_exit_at) if self.last_exit_at else None,
                "rss_mb": rss,
                "cpu_percent": cpu,
            }


def kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and its children (Windows venv launchers spawn a child)."""
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    for p in children:
        try:
            p.terminate()
        except psutil.Error:
            pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    psutil.wait_procs(children, timeout=5)


_cpu_cache: dict[int, psutil.Process] = {}


def tree_usage(root: psutil.Process) -> tuple[float | None, float | None]:
    """Resident memory (MB) and CPU% summed over the service's process tree."""
    try:
        procs = [root] + root.children(recursive=True)
        rss = 0.0
        cpu = 0.0
        for p in procs:
            cached = _cpu_cache.setdefault(p.pid, p)
            rss += cached.memory_info().rss
            cpu += cached.cpu_percent(None)
        return round(rss / 1024 / 1024, 1), round(cpu, 1)
    except psutil.Error:
        return None, None


SERVICES: dict[str, ManagedService] = {n: ManagedService(n, STACK["services"][n]) for n in MANAGED}


def watchdog() -> None:
    while True:
        for svc in SERVICES.values():
            try:
                svc.watchdog_tick()
            except Exception as exc:  # noqa: BLE001 - keep the watchdog alive
                print(f"watchdog error for {svc.name}: {exc}", file=sys.stderr)
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
app = FastAPI(title="steadyops-demo-supervisor")


def _check(expected: str, supplied: str | None) -> None:
    if not expected or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(401, "invalid token")


def operator_auth(x_supervisor_token: str | None = Header(default=None)) -> None:
    _check(SUPERVISOR_TOKEN, x_supervisor_token)


def chaos_auth(x_chaos_token: str | None = Header(default=None)) -> None:
    _check(CHAOS_TOKEN, x_chaos_token)


def actor_of(x_actor: str | None) -> str:
    actor = (x_actor or "unknown").lower()
    if not ACTOR_RE.match(actor):
        raise HTTPException(422, "invalid actor")
    return actor


def get_service(name: str) -> ManagedService:
    if name not in SERVICES:
        raise HTTPException(404, f"unknown service: {name}")
    return SERVICES[name]


class DeployRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.\-]+$")


class ConfigRequest(BaseModel):
    config_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.\-]+$")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "managed": list(SERVICES)}


@app.get("/services", dependencies=[Depends(operator_auth)])
def list_services():
    return [s.status() for s in SERVICES.values()]


@app.get("/services/{name}/logs", dependencies=[Depends(operator_auth)])
def service_logs(name: str, lines: int = 100):
    svc = get_service(name)
    lines = max(1, min(lines, 500))
    return list(svc.logs)[-lines:]


@app.get("/changes", dependencies=[Depends(operator_auth)])
def changes(limit: int = 200):
    limit = max(1, min(limit, 1000))
    with _changes_lock:
        return _changes[-limit:]


@app.get("/registry/{name}", dependencies=[Depends(operator_auth)])
def registry(name: str):
    get_service(name)
    return {"versions": STACK["versions"][name], "configs": STACK["configs"][name]}


@app.post("/services/{name}/restart", dependencies=[Depends(operator_auth)])
def op_restart(name: str, x_actor: str | None = Header(default=None)):
    actor = actor_of(x_actor)
    svc = get_service(name)
    record_change(name, "restart", actor, version=svc.version)
    svc.restart()
    return svc.status()


@app.post("/services/{name}/deploy", dependencies=[Depends(operator_auth)])
def op_deploy(name: str, req: DeployRequest, x_actor: str | None = Header(default=None)):
    actor = actor_of(x_actor)
    svc = get_service(name)
    if req.version not in STACK["versions"][name]:
        raise HTTPException(422, f"version {req.version} is not in the release registry")
    previous = svc.version
    svc.stop()
    svc.version = req.version
    record_change(name, "deployment", actor, from_version=previous, to_version=req.version)
    svc.restart_times.clear()
    svc.start()
    return svc.status()


@app.post("/services/{name}/config", dependencies=[Depends(operator_auth)])
def op_config(name: str, req: ConfigRequest, x_actor: str | None = Header(default=None)):
    actor = actor_of(x_actor)
    svc = get_service(name)
    if req.config_version not in STACK["configs"][name]:
        raise HTTPException(422, f"config {req.config_version} is not in the config registry")
    previous = svc.config_version
    svc.stop()
    svc.config_version = req.config_version
    record_change(name, "config_change", actor, from_config=previous, to_config=req.config_version)
    svc.restart_times.clear()
    svc.start()
    return svc.status()


# ---------------------------------------------------------------------------
# Fault injection (Failure Lab). Separate token; never used by remediation.
# ---------------------------------------------------------------------------
_chaos_state: dict[str, bool] = {}


def _internal_post(svc: ManagedService, path: str, method: str = "POST") -> None:
    host = os.environ.get("CHILD_HOST", "127.0.0.1")
    url = f"http://{host}:{svc.spec['port']}{path}"
    try:
        httpx.request(method, url, headers={"X-Chaos-Token": CHAOS_TOKEN}, timeout=3)
    except httpx.HTTPError as exc:
        raise HTTPException(409, f"{svc.name} not reachable for fault injection: {type(exc).__name__}")


@app.get("/chaos/status", dependencies=[Depends(chaos_auth)])
def chaos_status():
    return dict(_chaos_state)


@app.post("/chaos/{scenario}", dependencies=[Depends(chaos_auth)])
def chaos_inject(scenario: str):
    api = SERVICES.get("api-service")
    dep = SERVICES.get("dependency-service")
    if scenario == "bad_deployment":
        op_deploy("api-service", DeployRequest(version="1.5.0"), "ci-pipeline")
    elif scenario == "bad_deployment_corrupt_rollback":
        api.broken_artifacts.add("1.4.2")
        op_deploy("api-service", DeployRequest(version="1.5.0"), "ci-pipeline")
    elif scenario == "broken_config":
        op_config("api-service", ConfigRequest(config_version="api-cfg-8"), "config-sync")
    elif scenario == "resource_exhaustion":
        _internal_post(api, "/internal/chaos/memory-leak")
    elif scenario == "dependency_failure":
        _internal_post(dep, "/internal/chaos/hang")
    elif scenario == "dependency_outage":
        dep.provider_outage = True
        _internal_post(dep, "/internal/chaos/hang")
    else:
        raise HTTPException(404, "unknown scenario")
    _chaos_state[scenario] = True
    return {"injected": scenario}


@app.post("/chaos-reset", dependencies=[Depends(chaos_auth)])
def chaos_reset():
    """Restore the baseline: initial versions/configs, no leaks, no outages."""
    actions = []
    for svc in SERVICES.values():
        svc.broken_artifacts.clear()
        spec = svc.spec
        needs_restart = False
        if svc.name == "dependency-service" and (svc.provider_outage or _chaos_state.get("dependency_failure") or _chaos_state.get("dependency_outage")):
            svc.provider_outage = False
            needs_restart = True
        if svc.version != spec["initial_version"]:
            op_deploy(svc.name, DeployRequest(version=spec["initial_version"]), "failure-lab-reset")
            actions.append(f"redeployed {svc.name}:{spec['initial_version']}")
            continue
        if svc.config_version != spec["initial_config"]:
            op_config(svc.name, ConfigRequest(config_version=spec["initial_config"]), "failure-lab-reset")
            actions.append(f"restored {svc.name} config {spec['initial_config']}")
            continue
        if svc.name == "api-service" and _chaos_state.get("resource_exhaustion") and svc.proc:
            try:
                _internal_post(svc, "/internal/chaos/memory-leak", method="DELETE")
                actions.append("released leaked memory in api-service")
            except HTTPException:
                needs_restart = True
        if needs_restart or svc.proc is None:
            op_restart(svc.name, "failure-lab-reset")
            actions.append(f"restarted {svc.name}")
        svc.restart_times.clear()
    _chaos_state.clear()
    return {"reset": True, "actions": actions}


def main() -> None:
    if not SUPERVISOR_TOKEN or not CHAOS_TOKEN:
        print("SUPERVISOR_TOKEN and CHAOS_TOKEN must be set; refusing to start.", file=sys.stderr)
        sys.exit(2)
    for name in ["database-service", "dependency-service", "api-service", "frontend-service"]:
        if name in SERVICES:
            SERVICES[name].start()
            record_change(name, "deployment", "bootstrap", from_version=None, to_version=SERVICES[name].version)
    threading.Thread(target=watchdog, daemon=True).start()
    port = int(os.environ.get("SUPERVISOR_PORT", "8100"))
    host = os.environ.get("SUPERVISOR_BIND", "127.0.0.1")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    finally:
        for svc in SERVICES.values():
            svc.stop()


if __name__ == "__main__":
    main()
