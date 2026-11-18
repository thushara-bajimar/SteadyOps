"""Runtime configuration loaded from environment variables and JSON files.

Secrets (supervisor and chaos tokens) come only from the environment and are
never written to the database, logs or API responses.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CONFIG_DIR = BACKEND_DIR / "config"


def _load_json(name: str) -> dict:
    data = json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


@dataclass
class Settings:
    supervisor_url: str = field(default_factory=lambda: os.environ.get("SUPERVISOR_URL", "http://127.0.0.1:8100"))
    supervisor_token: str = field(default_factory=lambda: os.environ.get("SUPERVISOR_TOKEN", ""), repr=False)
    chaos_token: str = field(default_factory=lambda: os.environ.get("CHAOS_TOKEN", ""), repr=False)
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("STEADYOPS_DB_PATH", REPO_DIR / "runtime-data" / "steadyops.db"))
    )
    autonomy_mode: str = field(default_factory=lambda: os.environ.get("STEADYOPS_AUTONOMY_MODE", "auto"))
    run_loop: bool = field(default_factory=lambda: os.environ.get("STEADYOPS_DISABLE_LOOP", "0") != "1")
    service_urls: dict[str, str] = field(
        default_factory=lambda: {
            "frontend-service": os.environ.get("FRONTEND_SERVICE_URL", "http://127.0.0.1:8101"),
            "api-service": os.environ.get("API_SERVICE_URL", "http://127.0.0.1:8102"),
            "database-service": os.environ.get("DATABASE_SERVICE_URL", "http://127.0.0.1:8103"),
            "dependency-service": os.environ.get("DEPENDENCY_SERVICE_URL", "http://127.0.0.1:8104"),
        }
    )
    thresholds: dict = field(default_factory=lambda: _load_json("thresholds.json"))
    policy: dict = field(default_factory=lambda: _load_json("safety_policy.json"))
    frontend_dist: Path = field(default_factory=lambda: REPO_DIR / "frontend" / "dist")

    def __post_init__(self) -> None:
        if self.autonomy_mode not in ("auto", "supervised"):
            self.autonomy_mode = "supervised"
        if os.environ.get("STEADYOPS_MONITOR_INTERVAL_S"):
            self.thresholds["monitor"]["interval_s"] = float(os.environ["STEADYOPS_MONITOR_INTERVAL_S"])
