"""Thin, thread-safe SQLite access layer (stdlib sqlite3, WAL mode)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def seconds_between(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return round((parse_ts(b) - parse_ts(a)).total_seconds(), 2)


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        self.init_schema()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        self.conn().executescript(SCHEMA.read_text(encoding="utf-8"))
        # Lightweight migration for databases created before a column existed.
        cols = {r[1] for r in self.conn().execute("PRAGMA table_info(failure_injections)")}
        if "excluded_reason" not in cols:
            self.conn().execute("ALTER TABLE failure_injections ADD COLUMN excluded_reason TEXT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.write_lock:
            return self.conn().execute(sql, tuple(params))

    def insert(self, table: str, row: dict) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        values = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in row.values()]
        with self.write_lock:
            cur = self.conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values)
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, fields: dict) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        values = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in fields.values()]
        with self.write_lock:
            self.conn().execute(f"UPDATE {table} SET {sets} WHERE id=?", [*values, row_id])

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.conn().execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.conn().execute(sql, tuple(params)).fetchone()
        return row[0] if row else None


def loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
