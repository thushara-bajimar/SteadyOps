"""api-service: the business API of the demo stack.

Depends on database-service (orders) and dependency-service (inventory).

Realistic failure behaviours live in real code paths:

* Version 1.5.0 contains a pricing regression: orders without a discount raise
  a TypeError -> HTTP 500 (roughly half of all orders).
* Startup validates configuration. An invalid value (e.g. DB_POOL_SIZE =
  "unlimited") makes the process exit with code 78 (EX_CONFIG), so the
  supervisor sees a crash loop.
* A bounded memory leak can be switched on by the Failure Lab. It is capped at
  MEMORY_LEAK_CAP_MB and frees itself after MEMORY_LEAK_MAX_S seconds so it can
  never exhaust the host. While leaked memory is high the service adds latency
  to model GC pressure (this latency is simulated and documented as such).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

import httpx  # noqa: E402
from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from demo_common import Uptime, chaos_token_ok, log, run  # noqa: E402

VERSION = os.environ.get("APP_VERSION", "1.4.2")
CONFIG_VERSION = os.environ.get("CONFIG_VERSION", "unknown")
DATABASE_URL = os.environ.get("DATABASE_SERVICE_URL", "http://127.0.0.1:8103")
DEPENDENCY_URL = os.environ.get("DEPENDENCY_SERVICE_URL", "http://127.0.0.1:8104")

MEMORY_LEAK_CAP_MB = 260
MEMORY_LEAK_STEP_MB = 10
MEMORY_LEAK_INTERVAL_S = 0.5
MEMORY_LEAK_MAX_S = 600


# --------------------------------------------------------------------------
# Configuration validation (runs at import time = process start)
# --------------------------------------------------------------------------
def load_config() -> dict:
    raw = os.environ.get("SERVICE_CONFIG", "{}")
    cfg = json.loads(raw)
    errors = []
    pool = cfg.get("DB_POOL_SIZE")
    if not isinstance(pool, int) or not 1 <= pool <= 100:
        errors.append(f"DB_POOL_SIZE must be an integer between 1 and 100, got {pool!r}")
    timeout = cfg.get("REQUEST_TIMEOUT_MS")
    if not isinstance(timeout, int) or not 100 <= timeout <= 10000:
        errors.append(f"REQUEST_TIMEOUT_MS must be an integer between 100 and 10000, got {timeout!r}")
    if errors:
        for err in errors:
            log("FATAL", "invalid configuration", config_version=CONFIG_VERSION, error=err)
        log("FATAL", "refusing to start with invalid configuration", config_version=CONFIG_VERSION, exit_code=78)
        sys.exit(78)
    return cfg


CONFIG = load_config()
TIMEOUT_S = CONFIG["REQUEST_TIMEOUT_MS"] / 1000
log("INFO", "configuration loaded", config_version=CONFIG_VERSION, db_pool_size=CONFIG["DB_POOL_SIZE"])

uptime = Uptime()
app = FastAPI(title="api-service")
client = httpx.Client(timeout=TIMEOUT_S)

_metrics_lock = threading.Lock()
_metrics = {"requests_total": 0, "errors_total": 0}
_latencies: deque[float] = deque(maxlen=200)

_leak_lock = threading.Lock()
_leak: list[bytearray] = []
_leak_state = {"active": False, "started": 0.0}


def leaked_mb() -> int:
    with _leak_lock:
        return len(_leak) * MEMORY_LEAK_STEP_MB


# --------------------------------------------------------------------------
# Request accounting middleware
# --------------------------------------------------------------------------
@app.middleware("http")
async def account(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        with _metrics_lock:
            _metrics["requests_total"] += 1
            if status >= 500:
                _metrics["errors_total"] += 1
            _latencies.append(elapsed)


# --------------------------------------------------------------------------
# Business logic
# --------------------------------------------------------------------------
def apply_discount(unit_price: float, quantity: int, discount):
    subtotal = unit_price * quantity
    if VERSION == "1.5.0":
        # Regression introduced in 1.5.0: the "None means no discount" guard was
        # removed during a refactor, so orders without a discount crash.
        return round(subtotal * (1 - discount), 2)
    return round(subtotal * (1 - (discount or 0)), 2)


def _simulated_gc_pressure() -> None:
    mb = leaked_mb()
    if mb > 120:
        # Model GC pressure: latency grows with leaked memory (bounded).
        time.sleep(min(2.0, (mb - 120) / 100))


@app.get("/health")
def health():
    checks = {}
    for name, url in (("database", DATABASE_URL), ("inventory", DEPENDENCY_URL)):
        try:
            r = client.get(f"{url}/health", timeout=1.0)
            checks[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
        except httpx.HTTPError as exc:
            checks[name] = f"unreachable ({type(exc).__name__})"
    ok = all(v == "ok" for v in checks.values())
    body = {
        "status": "ok" if ok else "degraded",
        "service": "api-service",
        "version": VERSION,
        "config_version": CONFIG_VERSION,
        "checks": checks,
        "uptime_s": uptime.seconds(),
    }
    if not ok:
        log("WARN", "health degraded: dependency check failed", checks=checks)
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/api/orders/{order_id}")
def get_order(order_id: int):
    _simulated_gc_pressure()
    try:
        order = client.get(f"{DATABASE_URL}/orders/{order_id}")
    except httpx.HTTPError as exc:
        log("ERROR", "database request failed", dependency="database-service", error=type(exc).__name__)
        raise HTTPException(503, "database unavailable")
    if order.status_code == 404:
        raise HTTPException(404, "order not found")
    order = order.json()
    try:
        inv = client.get(f"{DEPENDENCY_URL}/inventory/{order['sku']}", timeout=1.5)
        if inv.status_code >= 500:
            log("ERROR", "inventory request failed", dependency="dependency-service", status=inv.status_code)
            raise HTTPException(503, "inventory dependency unavailable")
        stock = inv.json()["in_stock"]
    except httpx.HTTPError as exc:
        log("ERROR", "inventory request failed", dependency="dependency-service", error=type(exc).__name__)
        raise HTTPException(503, "inventory dependency unavailable")
    try:
        total = apply_discount(order["unit_price"], order["quantity"], order["discount"])
    except Exception as exc:  # noqa: BLE001 - we log and convert to a 500
        log(
            "ERROR",
            "unhandled exception in GET /api/orders/{id}",
            order_id=order_id,
            exception=f"{type(exc).__name__}: {exc}",
            location="pricing.apply_discount",
            traceback=traceback.format_exc(limit=2).splitlines()[-1],
        )
        raise HTTPException(500, "internal server error")
    return {"order_id": order_id, "sku": order["sku"], "total": total, "in_stock": stock, "version": VERSION}


@app.get("/metrics")
def metrics():
    with _metrics_lock:
        lat = list(_latencies)
        snapshot = dict(_metrics)
    snapshot.update(
        {
            "latency_p50_ms": round(statistics.median(lat), 1) if lat else None,
            "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 1) if len(lat) >= 20 else None,
            "version": VERSION,
            "config_version": CONFIG_VERSION,
            "uptime_s": uptime.seconds(),
        }
    )
    return snapshot


# --------------------------------------------------------------------------
# Failure Lab hook: bounded memory leak (chaos token required)
# --------------------------------------------------------------------------
def _leak_worker() -> None:
    log("WARN", "cache warmer started (unbounded cache growth)", component="report-cache")
    while True:
        with _leak_lock:
            if not _leak_state["active"]:
                return
            expired = time.time() - _leak_state["started"] > MEMORY_LEAK_MAX_S
            if expired:
                _leak.clear()
                _leak_state["active"] = False
                log("INFO", "memory leak safety timer expired; memory released")
                return
            if len(_leak) * MEMORY_LEAK_STEP_MB < MEMORY_LEAK_CAP_MB:
                _leak.append(bytearray(MEMORY_LEAK_STEP_MB * 1024 * 1024))
                held = len(_leak) * MEMORY_LEAK_STEP_MB
                if held % 50 == 0:
                    log("WARN", "report cache growing without eviction", cache_mb=held)
        time.sleep(MEMORY_LEAK_INTERVAL_S)


@app.post("/internal/chaos/memory-leak")
def chaos_memory_leak(x_chaos_token: str | None = Header(default=None)):
    if not chaos_token_ok(x_chaos_token):
        raise HTTPException(403, "forbidden")
    with _leak_lock:
        if _leak_state["active"]:
            return {"active": True}
        _leak_state.update(active=True, started=time.time())
    threading.Thread(target=_leak_worker, daemon=True).start()
    return {"active": True, "cap_mb": MEMORY_LEAK_CAP_MB}


@app.delete("/internal/chaos/memory-leak")
def chaos_memory_leak_stop(x_chaos_token: str | None = Header(default=None)):
    if not chaos_token_ok(x_chaos_token):
        raise HTTPException(403, "forbidden")
    with _leak_lock:
        _leak_state["active"] = False
        _leak.clear()
    return {"active": False}


if __name__ == "__main__":
    run(app)
