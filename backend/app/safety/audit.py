"""Append-only, hash-chained audit log.

Each record stores sha256(prev_hash + canonical record). SQLite triggers
reject UPDATE and DELETE, and verify_chain() detects any row that was altered
by other means (e.g. editing the database file directly).
"""
from __future__ import annotations

import hashlib
import json
import threading

from ..database.db import Database, utcnow
from .redact import redact

GENESIS = "0" * 64
_chain_lock = threading.Lock()


def _digest(prev_hash: str, ts: str, actor: str, event_type: str, incident_id, payload_json: str) -> str:
    material = json.dumps([prev_hash, ts, actor, event_type, incident_id, payload_json], separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, actor: str, event_type: str, payload: dict, incident_id: int | None = None) -> int:
        payload_json = json.dumps(redact(payload), sort_keys=True, default=str)
        with _chain_lock, self.db.write_lock:
            prev = self.db.scalar("SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1") or GENESIS
            ts = utcnow()
            digest = _digest(prev, ts, actor, event_type, incident_id, payload_json)
            return self.db.insert(
                "audit_events",
                {
                    "ts": ts,
                    "actor": actor,
                    "event_type": event_type,
                    "incident_id": incident_id,
                    "payload_json": payload_json,
                    "prev_hash": prev,
                    "hash": digest,
                },
            )

    def verify_chain(self) -> dict:
        prev = GENESIS
        count = 0
        for row in self.db.query("SELECT * FROM audit_events ORDER BY id"):
            expected = _digest(prev, row["ts"], row["actor"], row["event_type"], row["incident_id"], row["payload_json"])
            if row["prev_hash"] != prev or row["hash"] != expected:
                return {"valid": False, "records_checked": count, "broken_at_id": row["id"]}
            prev = row["hash"]
            count += 1
        return {"valid": True, "records_checked": count, "head": prev}
