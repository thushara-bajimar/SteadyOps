-- SteadyOps SQLite schema.
-- Detection, diagnosis, approval, execution and verification are stored in
-- separate tables so that "an incident was detected", "a cause was proposed",
-- "a human approved", "a command ran" and "recovery was verified" can never be
-- confused with one another.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS services (
    name        TEXT PRIMARY KEY,
    port        INTEGER,
    stateful    INTEGER NOT NULL DEFAULT 0 CHECK (stateful IN (0, 1)),
    first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_dependencies (
    service     TEXT NOT NULL REFERENCES services(name),
    depends_on  TEXT NOT NULL REFERENCES services(name),
    PRIMARY KEY (service, depends_on)
);

-- Change history mirrored from the supervisor (the "deployment system").
CREATE TABLE IF NOT EXISTS deployments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    change_key TEXT UNIQUE,
    service              TEXT NOT NULL REFERENCES services(name),
    from_version         TEXT,
    to_version           TEXT NOT NULL,
    actor                TEXT NOT NULL,
    deployed_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_deployments_service_time ON deployments(service, deployed_at);

CREATE TABLE IF NOT EXISTS config_changes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    change_key TEXT UNIQUE,
    service              TEXT NOT NULL REFERENCES services(name),
    from_config          TEXT,
    to_config            TEXT NOT NULL,
    actor                TEXT NOT NULL,
    changed_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_config_service_time ON config_changes(service, changed_at);

CREATE TABLE IF NOT EXISTS runtime_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    change_key TEXT UNIQUE,
    service              TEXT NOT NULL REFERENCES services(name),
    kind                 TEXT NOT NULL,
    actor                TEXT NOT NULL,
    data_json            TEXT NOT NULL,
    ts                   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runtime_events_service_time ON runtime_events(service, ts);

-- Versions/configs that have been observed healthy for a sustained period.
CREATE TABLE IF NOT EXISTS known_good (
    service      TEXT NOT NULL REFERENCES services(name),
    kind         TEXT NOT NULL CHECK (kind IN ('version', 'config')),
    value        TEXT NOT NULL,
    verified_at  TEXT NOT NULL,
    PRIMARY KEY (service, kind, value)
);

CREATE TABLE IF NOT EXISTS monitoring_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    service         TEXT NOT NULL REFERENCES services(name),
    process_state   TEXT,
    health          TEXT NOT NULL CHECK (health IN ('healthy', 'degraded', 'down', 'unknown')),
    http_status     INTEGER,
    response_ms     REAL,
    error_rate      REAL,
    latency_p95_ms  REAL,
    rss_mb          REAL,
    cpu_percent     REAL,
    restart_count   INTEGER,
    restarts_recent INTEGER,
    version         TEXT,
    config_version  TEXT,
    detail_json     TEXT
);
CREATE INDEX IF NOT EXISTS ix_snapshots_service_time ON monitoring_snapshots(service, ts);

CREATE TABLE IF NOT EXISTS failure_injections (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario           TEXT NOT NULL,
    target_service     TEXT NOT NULL,
    expected_category  TEXT NOT NULL,
    expected_action    TEXT,
    actor              TEXT NOT NULL,
    injected_at        TEXT NOT NULL,
    reset_at           TEXT,
    incident_id        INTEGER REFERENCES incidents(id),
    excluded_reason    TEXT  -- set (audited) when a run is invalid, e.g. the host suspended mid-run
);

CREATE TABLE IF NOT EXISTS incidents (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    status                     TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'escalated', 'closed')),
    stage                      TEXT NOT NULL,
    severity                   TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    title                      TEXT NOT NULL,
    detected_service           TEXT NOT NULL REFERENCES services(name),
    affected_services_json     TEXT NOT NULL,
    signals_json               TEXT NOT NULL,
    root_service               TEXT,
    category                   TEXT,
    first_abnormal_at          TEXT NOT NULL,
    detected_at                TEXT NOT NULL,
    diagnosed_at               TEXT,
    remediation_started_at     TEXT,
    remediation_completed_at   TEXT,
    verified_at                TEXT,
    resolved_at                TEXT,
    escalated_at               TEXT,
    closed_at                  TEXT,
    resolution                 TEXT,
    escalation_reason          TEXT,
    last_seen_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents(status);

CREATE TABLE IF NOT EXISTS incident_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  INTEGER NOT NULL REFERENCES incidents(id),
    ts           TEXT NOT NULL,
    stage        TEXT NOT NULL,
    message      TEXT NOT NULL,
    data_json    TEXT
);
CREATE INDEX IF NOT EXISTS ix_incident_events_incident ON incident_events(incident_id, id);

CREATE TABLE IF NOT EXISTS evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id   INTEGER NOT NULL REFERENCES incidents(id),
    kind          TEXT NOT NULL,
    source        TEXT NOT NULL,
    service       TEXT,
    summary       TEXT NOT NULL,
    data_json     TEXT NOT NULL,
    collected_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_incident ON evidence(incident_id);

CREATE TABLE IF NOT EXISTS diagnoses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id         INTEGER NOT NULL REFERENCES incidents(id),
    category            TEXT NOT NULL,
    root_service        TEXT,
    suspected_cause     TEXT NOT NULL,
    confidence          REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    confidence_level    TEXT NOT NULL CHECK (confidence_level IN ('high', 'medium', 'low')),
    ambiguous           INTEGER NOT NULL CHECK (ambiguous IN (0, 1)),
    supporting_json     TEXT NOT NULL,
    alternatives_json   TEXT NOT NULL,
    recommended_json    TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_diagnoses_incident ON diagnoses(incident_id);

CREATE TABLE IF NOT EXISTS remediation_proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id         INTEGER NOT NULL REFERENCES incidents(id),
    diagnosis_id        INTEGER REFERENCES diagnoses(id),
    action_id           TEXT NOT NULL,
    resource            TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    risk_level          TEXT NOT NULL,
    expected_impact     TEXT NOT NULL,
    rollback_available  INTEGER NOT NULL CHECK (rollback_available IN (0, 1)),
    status              TEXT NOT NULL CHECK (status IN ('proposed', 'awaiting_approval', 'approved', 'rejected',
                                     'blocked', 'executing', 'succeeded', 'failed', 'rolled_back', 'expired')),
    approval_reason     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_proposals_incident ON remediation_proposals(incident_id);
CREATE INDEX IF NOT EXISTS ix_proposals_status ON remediation_proposals(status);

CREATE TABLE IF NOT EXISTS dry_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id  INTEGER NOT NULL REFERENCES remediation_proposals(id),
    plan_json    TEXT NOT NULL,
    plan_hash    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id   INTEGER REFERENCES remediation_proposals(id),
    incident_id   INTEGER REFERENCES incidents(id),
    phase         TEXT NOT NULL CHECK (phase IN ('plan', 'execute', 'simulation')),
    action_id     TEXT NOT NULL,
    resource      TEXT,
    outcome       TEXT NOT NULL CHECK (outcome IN ('allow', 'require_approval', 'deny')),
    reasons_json  TEXT NOT NULL,
    checks_json   TEXT NOT NULL,
    evaluated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_policy_outcome ON policy_decisions(outcome);

CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id  INTEGER NOT NULL UNIQUE REFERENCES remediation_proposals(id),
    decision     TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    approver     TEXT NOT NULL,
    comment      TEXT,
    decided_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remediation_executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id     INTEGER NOT NULL REFERENCES remediation_proposals(id),
    incident_id     INTEGER NOT NULL REFERENCES incidents(id),
    action_id       TEXT NOT NULL,
    resource        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('running', 'command_succeeded', 'command_failed', 'aborted')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    pre_state_json  TEXT NOT NULL,
    output_json     TEXT,
    error           TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_executions_time ON remediation_executions(started_at);

CREATE TABLE IF NOT EXISTS verifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  INTEGER NOT NULL REFERENCES remediation_executions(id),
    incident_id   INTEGER NOT NULL REFERENCES incidents(id),
    result        TEXT NOT NULL CHECK (result IN ('passed', 'failed')),
    summary       TEXT NOT NULL,
    checks_json   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rollbacks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  INTEGER NOT NULL REFERENCES remediation_executions(id),
    incident_id   INTEGER NOT NULL REFERENCES incidents(id),
    status        TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'skipped')),
    reason        TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);

-- Append-only, hash-chained audit log. UPDATE and DELETE are rejected by
-- triggers; each row commits to the previous row's hash.
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    actor         TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    incident_id   INTEGER,
    payload_json  TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_audit_type ON audit_events(event_type);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS incident_events_no_update BEFORE UPDATE ON incident_events
BEGIN SELECT RAISE(ABORT, 'incident_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS incident_events_no_delete BEFORE DELETE ON incident_events
BEGIN SELECT RAISE(ABORT, 'incident_events is append-only'); END;
