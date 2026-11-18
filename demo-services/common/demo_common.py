"""Shared helpers for the SteadyOps demo services.

Every demo service writes one JSON object per line to stdout. The supervisor
captures stdout, so these lines become the "application logs" that SteadyOps
collects as evidence. Nothing here is SteadyOps logic - it is the workload
being monitored.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown-service")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(level: str, msg: str, **fields) -> None:
    record = {
        "ts": now_iso(),
        "level": level.upper(),
        "service": SERVICE_NAME,
        "version": os.environ.get("APP_VERSION", "n/a"),
        "msg": msg,
    }
    record.update(fields)
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()


def chaos_token_ok(supplied: str | None) -> bool:
    """Constant-time comparison of the chaos token (Failure Lab only)."""
    expected = os.environ.get("CHAOS_TOKEN", "")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode(), supplied.encode())


class Uptime:
    def __init__(self) -> None:
        self.started = time.time()

    def seconds(self) -> float:
        return round(time.time() - self.started, 1)


def run(app, port_env: str = "PORT", default_port: int = 0) -> None:
    import uvicorn

    port = int(os.environ.get(port_env, default_port))
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    log("INFO", "service starting", port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
