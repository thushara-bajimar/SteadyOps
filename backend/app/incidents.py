"""Incident persistence helpers shared by detector, pipeline and API."""
from __future__ import annotations

from .database.db import Database, loads, utcnow
from .safety.audit import AuditLog

OPEN_STATUSES = ("open", "escalated")
STAGES = [
    "detected", "investigating", "diagnosed", "safety_evaluation", "awaiting_approval",
    "executing", "verifying", "resolved", "escalated", "closed",
]


class IncidentStore:
    def __init__(self, db: Database, audit: AuditLog):
        self.db = db
        self.audit = audit

    def get(self, incident_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM incidents WHERE id=?", (incident_id,))
        if row:
            row["affected_services"] = loads(row.pop("affected_services_json"), [])
            row["signals"] = loads(row.pop("signals_json"), [])
        return row

    def active(self) -> list[dict]:
        rows = self.db.query("SELECT id FROM incidents WHERE status IN ('open','escalated') ORDER BY id")
        return [self.get(r["id"]) for r in rows]

    def event(self, incident_id: int, stage: str, message: str, data: dict | None = None) -> None:
        self.db.insert("incident_events", {"incident_id": incident_id, "ts": utcnow(), "stage": stage,
                                           "message": message, "data_json": data or {}})

    def set_stage(self, incident_id: int, stage: str, message: str, data: dict | None = None, **fields) -> None:
        self.db.update("incidents", incident_id, {"stage": stage, **fields})
        self.event(incident_id, stage, message, data)

    def escalate(self, incident_id: int, reason: str, actor: str = "steadyops-agent") -> None:
        now = utcnow()
        self.db.update("incidents", incident_id, {"status": "escalated", "stage": "escalated", "escalated_at": now,
                                                   "escalation_reason": reason})
        self.event(incident_id, "escalated", f"Escalated to on-call human: {reason}")
        self.audit.record(actor, "incident_escalated", {"reason": reason}, incident_id)
        self.db.execute(
            "UPDATE remediation_proposals SET status='expired', updated_at=? WHERE incident_id=? AND status IN ('proposed','awaiting_approval')",
            (now, incident_id),
        )

    def resolve(self, incident_id: int, resolution: str) -> None:
        now = utcnow()
        self.db.update("incidents", incident_id, {"status": "resolved", "stage": "resolved", "resolved_at": now,
                                                   "verified_at": now, "resolution": resolution})
        self.event(incident_id, "resolved", resolution)
        self.audit.record("steadyops-agent", "incident_resolved", {"resolution": resolution}, incident_id)

    def close(self, incident_id: int, resolution: str, actor: str) -> None:
        now = utcnow()
        self.db.update("incidents", incident_id, {"status": "closed", "stage": "closed", "closed_at": now,
                                                   "resolution": resolution})
        self.event(incident_id, "closed", resolution)
        self.audit.record(actor, "incident_closed", {"resolution": resolution}, incident_id)
        self.db.execute(
            "UPDATE remediation_proposals SET status='expired', updated_at=? WHERE incident_id=? AND status IN ('proposed','awaiting_approval')",
            (now, incident_id),
        )
