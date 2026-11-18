"""Start the complete SteadyOps demo locally (no Docker required).

    python scripts/start_local.py            # supervisor + 4 demo services + SteadyOps API/dashboard
    python scripts/start_local.py --fresh    # same, but start with an empty SteadyOps database

Fresh random SUPERVISOR_TOKEN / CHAOS_TOKEN values are generated for every run
unless they are already set in the environment (or in a local .env file).
Press Ctrl+C to stop everything.
"""
from __future__ import annotations

import argparse
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

REPO = Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v and k not in os.environ:
                    os.environ[k] = v


def port_in_use(port: int) -> bool:
    return any(c.status == "LISTEN" and c.laddr.port == port for c in psutil.net_connections("tcp"))


def wait_for(url: str, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.terminate()
        parent.terminate()
        psutil.wait_procs([parent], timeout=8)
    except psutil.Error:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="delete runtime-data/steadyops.db first")
    ap.add_argument("--port", type=int, default=int(os.environ.get("STEADYOPS_PORT", 8010)))
    args = ap.parse_args()
    load_dotenv()
    os.environ.setdefault("SUPERVISOR_TOKEN", secrets.token_urlsafe(24))
    os.environ.setdefault("CHAOS_TOKEN", secrets.token_urlsafe(24))
    runtime = REPO / "runtime-data"
    runtime.mkdir(exist_ok=True)
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            (runtime / f"steadyops.db{suffix}").unlink(missing_ok=True)

    busy = [p for p in (args.port, 8100, 8101, 8102, 8103, 8104) if port_in_use(p)]
    if busy:
        sys.exit(f"Port(s) {busy} already in use. Stop whatever is using them (or pass --port for SteadyOps).")
    procs: list[subprocess.Popen] = []
    try:
        print("Starting demo-stack supervisor (ports 8100-8104)...")
        sup_log = open(runtime / "supervisor.out.log", "a")
        procs.append(subprocess.Popen([sys.executable, str(REPO / "demo-services/supervisor/supervisor.py")],
                                      stdout=sup_log, stderr=subprocess.STDOUT))
        if not wait_for("http://127.0.0.1:8100/healthz"):
            sys.exit("supervisor failed to start - see runtime-data/supervisor.out.log")
        wait_for("http://127.0.0.1:8102/health", 20)
        print(f"Starting SteadyOps control plane on http://127.0.0.1:{args.port} ...")
        api_log = open(runtime / "steadyops.out.log", "a")
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                       "--port", str(args.port), "--log-level", "warning"],
                                      cwd=REPO / "backend", stdout=api_log, stderr=subprocess.STDOUT))
        if not wait_for(f"http://127.0.0.1:{args.port}/api/health"):
            sys.exit("SteadyOps failed to start - see runtime-data/steadyops.out.log")
        dist = (REPO / "frontend/dist/index.html").exists()
        print("\nSteadyOps is running.")
        print(f"  Dashboard : http://127.0.0.1:{args.port}/" + ("" if dist else "  (not built: cd frontend && npm run build)"))
        print(f"  API docs  : http://127.0.0.1:{args.port}/docs")
        print("  Demo shop : http://127.0.0.1:8101/")
        print("Press Ctrl+C to stop.\n")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A component exited unexpectedly; shutting down.")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for p in reversed(procs):
            kill_tree(p)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
