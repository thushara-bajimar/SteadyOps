"""Builds and wires every SteadyOps component (dependency injection root)."""
from __future__ import annotations

from dataclasses import dataclass

from .clients import ChaosClient, ServiceProbe, SupervisorOpsClient, SupervisorReadClient
from .config import Settings
from .database.db import Database
from .detection.detector import Detector
from .failure_lab import FailureLab
from .incidents import IncidentStore
from .investigation.evidence import EvidenceCollector
from .monitoring.collector import Collector
from .orchestrator import Orchestrator
from .remediation.actions import ACTIONS
from .remediation.executor import Executor
from .safety.audit import AuditLog
from .safety.policy import PolicyEngine
from .safety.settings_store import SettingsStore
from .verification.verifier import Verifier


@dataclass
class Runtime:
    settings: Settings
    db: Database
    audit: AuditLog
    store: SettingsStore
    incidents: IncidentStore
    read: SupervisorReadClient
    probe: ServiceProbe
    collector: Collector
    detector: Detector
    evidence: EvidenceCollector
    policy: PolicyEngine
    executor: Executor
    lab: FailureLab
    actions: dict
    orchestrator: Orchestrator | None = None


def build_runtime(settings: Settings, read=None, ops=None, chaos=None, probe=None, verifier_kwargs=None) -> Runtime:
    db = Database(settings.db_path)
    audit = AuditLog(db)
    store = SettingsStore(db, audit, settings.autonomy_mode)
    incidents = IncidentStore(db, audit)
    read = read or SupervisorReadClient(settings.supervisor_url, settings.supervisor_token)
    ops = ops or SupervisorOpsClient(settings.supervisor_url, settings.supervisor_token)
    chaos = chaos or ChaosClient(settings.supervisor_url, settings.chaos_token)
    probe = probe or ServiceProbe(settings.service_urls, settings.thresholds["monitor"]["health_timeout_s"])
    collector = Collector(db, read, probe, settings.thresholds)
    detector = Detector(db, audit, incidents, settings.thresholds)
    evidence = EvidenceCollector(db, read, settings.thresholds)
    policy = PolicyEngine(settings.policy, db, audit, store)
    verifier = Verifier(collector, settings.thresholds, **(verifier_kwargs or {}))
    executor = Executor(db, audit, incidents, policy, ops, read, collector, verifier, store, settings.policy)
    lab = FailureLab(db, audit, chaos)
    rt = Runtime(settings, db, audit, store, incidents, read, probe, collector, detector, evidence, policy, executor,
                 lab, ACTIONS)
    rt.orchestrator = Orchestrator(rt)
    return rt
