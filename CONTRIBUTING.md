# Contributing to SteadyOps

## Project structure

```
backend/                 SteadyOps control plane (FastAPI)
  app/
    api/routes.py        REST API (no "execute" endpoint, by design)
    monitoring/          collector: observations every tick
    detection/           signals, debouncing, de-duplication, root attribution
    investigation/       evidence collection, rule-based RCA, plain-language summary
    remediation/         action catalogue (allowlist) and the executor
    safety/              policy engine, audit chain, redaction, operator settings
    verification/        post-action verification
    database/            schema.sql + thin sqlite3 wrapper
    orchestrator.py      monitor loop + incident pipeline + approvals
    failure_lab.py       fault-injection scenarios (ground truth)
    analytics.py         metrics from recorded timestamps
    runtime.py           wiring / dependency injection
  config/                thresholds.json, safety_policy.json (read-only at runtime)
  tests/                 pytest suite (fake world + real-process integration)
demo-services/           the monitored workload + supervisor (execution boundary)
frontend/                React + Vite + TypeScript + Tailwind + Recharts dashboard
infrastructure/          Docker Compose and Dockerfiles
scripts/                 start_local.py, stop_local.py, run_evaluation.py, capture_screenshots.py, build_pdfs.py
docs/                    architecture, safety, API, testing docs + SVG diagrams
reports/                 evaluation results and generated PDFs
```

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
python scripts/start_local.py --fresh                  # backend + demo stack on :8010
cd frontend && npm run dev                             # optional: hot-reload UI on :5173
```

## Testing

```bash
cd backend
python -m pytest -q -m "not integration"   # fast suite: fake world, ~6 s
python -m pytest tests/test_integration.py -v   # real processes; ports 8100-8104 must be free
cd ../frontend && npm test && npm run build
```

Full evaluation against a running stack:
```bash
python scripts/run_evaluation.py --rounds 3
```

## Coding conventions

- **Python**: type hints, `from __future__ import annotations`, small pure functions where
  possible (`compute_signals`, `diagnose` are pure and unit-tested). Standard library first.
- **Never** add `subprocess`, `os.system`, `eval`/`exec` or Docker-socket access under `backend/app`.
  `test_no_arbitrary_command_execution_in_control_plane` enforces this.
- **New remediation actions** need: a `StrictParams` model (`extra="forbid"`), preconditions, a
  side-effect-free `plan()`, `execute()` using only `SupervisorOpsClient`, `rollback()` if
  meaningful, an entry in `config/safety_policy.json`, and tests for allow/deny paths.
- **Never fabricate data** in the dashboard. Show `—` when nothing has been measured.
- Every state transition writes an `incident_events` row; every decision or action writes an audit record.
- **TypeScript**: strict mode; data types mirror API responses in `src/lib/api.ts`; status is never
  conveyed by colour alone (icon + word).
- Commits: conventional style (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).

## Secrets and private files

- Never commit `.env` files or tokens. `.env.example` holds names only.
- `private-notes/` is personal study material and is git-ignored. Do not force-add it.
