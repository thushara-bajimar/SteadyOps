"""database-service: a small SQLite-backed order store.

It is the stateful tier of the demo stack. SteadyOps policy treats restarts of
this service as sensitive (human approval required) because it holds data.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

from fastapi import FastAPI, HTTPException  # noqa: E402

from demo_common import Uptime, log, run  # noqa: E402

DATA_DIR = Path(os.environ.get("DEMO_DATA_DIR", Path(__file__).resolve().parents[2] / "runtime-data" / "demo-db"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "orders.db"

SEED = [
    (1, "SKU-100", 2, 19.99, 0.10),
    (2, "SKU-200", 1, 249.00, None),
    (3, "SKU-300", 5, 4.50, 0.05),
    (4, "SKU-400", 1, 89.00, None),
    (5, "SKU-500", 3, 12.25, 0.15),
    (6, "SKU-600", 2, 33.00, None),
]

_lock = threading.Lock()
uptime = Uptime()
app = FastAPI(title="database-service")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _lock, _conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, sku TEXT NOT NULL,"
            " quantity INTEGER NOT NULL, unit_price REAL NOT NULL, discount REAL)"
        )
        if conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", SEED)
    log("INFO", "database ready", path=DB_FILE.name, orders=len(SEED))


_init()


@app.get("/health")
def health():
    try:
        with _conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": "database-service", "uptime_s": uptime.seconds()}
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log("ERROR", "health check query failed", error=str(exc))
        raise HTTPException(503, "database unavailable")


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "order not found")
    return dict(row)


@app.get("/orders")
def list_orders():
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id")]


if __name__ == "__main__":
    run(app)
