"""dependency-service ("inventory"): a downstream dependency of api-service.

Two controlled failure modes exist, both switched on only by the Failure Lab:

* hang     - the worker pool "deadlocks": every request stalls then returns 503.
             The state is in memory, so a restart genuinely clears it.
* outage   - PROVIDER_OUTAGE=1 in the environment simulates an upstream
             provider outage. A restart does NOT fix it, which exercises the
             escalation path.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

from fastapi import FastAPI, Header, HTTPException  # noqa: E402

from demo_common import Uptime, chaos_token_ok, log, run  # noqa: E402

STOCK = {"SKU-100": 42, "SKU-200": 3, "SKU-300": 120, "SKU-400": 0, "SKU-500": 17, "SKU-600": 8}

uptime = Uptime()
app = FastAPI(title="dependency-service")
_state = {"hung": False}
_lock = threading.Lock()
PROVIDER_OUTAGE = os.environ.get("PROVIDER_OUTAGE") == "1"

if PROVIDER_OUTAGE:
    log("ERROR", "upstream inventory provider unreachable; serving errors", provider="inventory-provider")


def _guard() -> None:
    if PROVIDER_OUTAGE:
        log("ERROR", "upstream inventory provider unreachable", provider="inventory-provider")
        raise HTTPException(503, "upstream provider unavailable")
    if _state["hung"]:
        log("WARN", "worker pool exhausted; request stalled", stalled_ms=4000)
        time.sleep(4)  # longer than any client timeout used by callers
        raise HTTPException(503, "worker pool exhausted")


@app.get("/health")
def health():
    _guard()
    return {"status": "ok", "service": "dependency-service", "uptime_s": uptime.seconds()}


@app.get("/inventory/{sku}")
def inventory(sku: str):
    _guard()
    if sku not in STOCK:
        raise HTTPException(404, "unknown sku")
    return {"sku": sku, "in_stock": STOCK[sku]}


@app.post("/internal/chaos/hang")
def chaos_hang(x_chaos_token: str | None = Header(default=None)):
    if not chaos_token_ok(x_chaos_token):
        raise HTTPException(403, "forbidden")
    with _lock:
        _state["hung"] = True
    log("ERROR", "worker pool deadlock detected; requests are stalling")
    return {"hung": True}


if __name__ == "__main__":
    run(app)
