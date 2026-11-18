import inspect
import re
import subprocess
from pathlib import Path

import pytest

from app.clients import SupervisorOpsClient
from app.safety.redact import redact, redact_text
from conftest import detect

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def test_redaction_of_secrets():
    s = redact_text("connecting with password=hunter2 token: abc123 url=postgres://app:s3cr3t@db:5432/x "
                    "Authorization: Bearer eyJhbGciOi.payload key AKIAABCDEFGHIJKLMNOP sk-live_abcdefghijklmnop")
    for secret in ("hunter2", "abc123", "s3cr3t", "eyJhbGciOi", "AKIAABCDEFGHIJKLMNOP", "sk-live_abcdefghijklmnop"):
        assert secret not in s
    d = redact({"DB_PASSWORD": "pw", "api_key": "k", "nested": [{"client_secret": "x"}], "passed": True, "version": "1.4.2"})
    assert d["DB_PASSWORD"] == "[REDACTED]" and d["api_key"] == "[REDACTED]" and d["nested"][0]["client_secret"] == "[REDACTED]"
    assert d["passed"] is True and d["version"] == "1.4.2"


def test_secrets_in_service_logs_never_reach_evidence_or_audit(healthy_rt):
    rt = healthy_rt
    rt.world.log("api-service", "ERROR", "db login failed password=SuperSecret99", dependency="database-service",
                 exception="AuthError: token=tok_live_123456")
    iid = detect(rt, "bad_deployment")
    rt.orchestrator.run_pipeline(iid)
    dump = " ".join(str(r) for t in ("evidence", "audit_events", "incident_events", "diagnoses")
                    for r in rt.db.query(f"SELECT * FROM {t}"))
    assert "SuperSecret99" not in dump and "tok_live_123456" not in dump


def test_supervisor_tokens_are_not_exposed(rt, monkeypatch):
    s = rt.settings
    s.supervisor_token = "very-secret-token-value"
    assert "very-secret-token-value" not in repr(s)


def test_audit_log_is_append_only_and_tamper_evident(healthy_rt):
    import sqlite3

    rt = healthy_rt
    rt.audit.record("tester", "test_event", {"x": 1})
    rt.audit.record("tester", "test_event", {"x": 2})
    assert rt.audit.verify_chain()["valid"]
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("UPDATE audit_events SET payload_json='{}' WHERE id=1")
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("DELETE FROM audit_events")
    # Someone with raw file access drops the trigger and edits a row: detected.
    rt.db.execute("DROP TRIGGER audit_no_update")
    rt.db.execute("UPDATE audit_events SET payload_json='{\"x\": 999}' WHERE id=(SELECT MAX(id) FROM audit_events)")
    assert rt.audit.verify_chain()["valid"] is False


def test_no_arbitrary_command_execution_in_control_plane():
    banned = [r"\bsubprocess\b", r"os\.system", r"os\.popen", r"shell\s*=\s*True", r"\beval\(", r"\bexec\(",
              r"docker\.sock", r"pty\."]
    for path in (BACKEND / "app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for pat in banned:
            assert not re.search(pat, src), f"{pat} found in {path.relative_to(BACKEND)}"


def test_ops_client_exposes_only_fixed_operations():
    public = {n for n, _ in inspect.getmembers(SupervisorOpsClient, inspect.isfunction) if not n.startswith("_")}
    assert public == {"restart", "deploy", "apply_config"}


def test_ops_client_validates_inputs():
    c = SupervisorOpsClient("http://127.0.0.1:1", "t")
    for bad in ("../etc/passwd", "api service", "API", "x" * 60, "a;rm -rf /"):
        with pytest.raises(ValueError):
            c.restart(bad)
    with pytest.raises(ValueError):
        c.deploy("api-service", "1.0; reboot")


def test_api_has_no_execute_endpoint():
    from app.main import create_app

    paths = {getattr(r, 'path', '') for r in create_app().routes}
    paths |= {getattr(r, 'path', '') for r in __import__('app.api.routes', fromlist=['router']).router.routes}
    assert not [p for p in paths if re.search(r"exec|shell|command|run", p)], paths


def test_private_notes_are_git_ignored():
    if subprocess.run(["git", "rev-parse"], cwd=REPO, capture_output=True).returncode != 0:
        pytest.skip("not a git repository")
    probe = "private-notes/PROJECT_EXPLANATION.md"
    r = subprocess.run(["git", "check-ignore", "-q", probe], cwd=REPO)
    assert r.returncode == 0, "private-notes/ must be ignored by git"
    tracked = subprocess.run(["git", "ls-files", "private-notes"], cwd=REPO, capture_output=True, text=True).stdout
    assert tracked.strip() == "", f"private notes are tracked: {tracked}"
    for public in ("README.md", "TECHNICAL_DOCUMENTATION.md", "docs/ARCHITECTURE.md"):
        assert subprocess.run(["git", "check-ignore", "-q", public], cwd=REPO).returncode == 1, f"{public} must stay public"


def test_env_files_ignored_but_example_kept():
    r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO)
    assert r.returncode == 0
    r = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO)
    assert r.returncode == 1
