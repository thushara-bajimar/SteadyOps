"""Persistent operator-controlled settings: autonomy mode and emergency stop.

Only human-facing API endpoints write these values; the incident pipeline
reads them but has no code path that changes them.
"""
from __future__ import annotations

import json

from ..database.db import Database, utcnow
from .audit import AuditLog

DEFAULTS = {
    "autonomy_mode": "auto",
    "emergency_stop": {"engaged": False, "reason": None, "by": None, "at": None},
}


class SettingsStore:
    def __init__(self, db: Database, audit: AuditLog, autonomy_mode: str = "auto"):
        self.db = db
        self.audit = audit
        if self.db.scalar("SELECT COUNT(*) FROM settings WHERE key='autonomy_mode'") == 0:
            self._write("autonomy_mode", autonomy_mode, "system")
        if self.db.scalar("SELECT COUNT(*) FROM settings WHERE key='emergency_stop'") == 0:
            self._write("emergency_stop", DEFAULTS["emergency_stop"], "system")

    def _write(self, key: str, value, by: str) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value_json, updated_at, updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, json.dumps(value), utcnow(), by),
        )

    def get(self, key: str):
        row = self.db.one("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(row["value_json"]) if row else DEFAULTS.get(key)

    @property
    def autonomy_mode(self) -> str:
        return self.get("autonomy_mode")

    def set_autonomy_mode(self, mode: str, operator: str) -> None:
        if mode not in ("auto", "supervised"):
            raise ValueError("autonomy mode must be 'auto' or 'supervised'")
        old = self.autonomy_mode
        self._write("autonomy_mode", mode, operator)
        self.audit.record(operator, "autonomy_mode_changed", {"from": old, "to": mode})

    @property
    def emergency_stop(self) -> dict:
        return self.get("emergency_stop")

    def set_emergency_stop(self, engaged: bool, operator: str, reason: str | None) -> dict:
        value = {"engaged": bool(engaged), "reason": reason, "by": operator, "at": utcnow()}
        self._write("emergency_stop", value, operator)
        self.audit.record(operator, "emergency_stop_engaged" if engaged else "emergency_stop_released", {"reason": reason})
        return value
