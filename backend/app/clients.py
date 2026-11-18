"""HTTP clients for the demo stack.

Three deliberately separate clients enforce least privilege inside SteadyOps:

* SupervisorReadClient  - GET-only (category 1). Used by monitoring and
                          evidence collection.
* SupervisorOpsClient   - exposes exactly three mutating methods with fixed
                          shapes. Only the remediation executor holds one.
* ChaosClient           - fault injection, used only by the Failure Lab.

None of them has a method that accepts a command, a path or arbitrary JSON.
"""
from __future__ import annotations

import re

import httpx

SERVICE_RE = re.compile(r"^[a-z][a-z0-9\-]{1,40}$")
VALUE_RE = re.compile(r"^[0-9A-Za-z.\-]{1,32}$")


class SupervisorError(RuntimeError):
    pass


def _check_service(name: str) -> str:
    if not SERVICE_RE.match(name or ""):
        raise ValueError(f"invalid service name: {name!r}")
    return name


class SupervisorReadClient:
    def __init__(self, base_url: str, token: str, timeout: float = 3.0):
        self._http = httpx.Client(base_url=base_url, headers={"X-Supervisor-Token": token}, timeout=timeout)

    def _get(self, path: str, **params):
        try:
            r = self._http.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupervisorError(f"supervisor read failed: {type(exc).__name__}") from exc

    def services(self) -> list[dict]:
        return self._get("/services")

    def logs(self, service: str, lines: int = 100) -> list[dict]:
        return self._get(f"/services/{_check_service(service)}/logs", lines=lines)

    def changes(self, limit: int = 500) -> list[dict]:
        return self._get("/changes", limit=limit)

    def registry(self, service: str) -> dict:
        return self._get(f"/registry/{_check_service(service)}")


class SupervisorOpsClient:
    """The ONLY object in SteadyOps able to change the demo stack."""

    ACTOR = "steadyops-agent"

    def __init__(self, base_url: str, token: str, timeout: float = 20.0):
        self._http = httpx.Client(
            base_url=base_url, headers={"X-Supervisor-Token": token, "X-Actor": self.ACTOR}, timeout=timeout
        )

    def _post(self, path: str, body: dict | None = None) -> dict:
        try:
            r = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise SupervisorError(f"supervisor unreachable: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise SupervisorError(f"supervisor rejected operation: HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def restart(self, service: str) -> dict:
        return self._post(f"/services/{_check_service(service)}/restart")

    def deploy(self, service: str, version: str) -> dict:
        if not VALUE_RE.match(version or ""):
            raise ValueError("invalid version")
        return self._post(f"/services/{_check_service(service)}/deploy", {"version": version})

    def apply_config(self, service: str, config_version: str) -> dict:
        if not VALUE_RE.match(config_version or ""):
            raise ValueError("invalid config version")
        return self._post(f"/services/{_check_service(service)}/config", {"config_version": config_version})


class ChaosClient:
    SCENARIOS = {
        "bad_deployment",
        "bad_deployment_corrupt_rollback",
        "broken_config",
        "resource_exhaustion",
        "dependency_failure",
        "dependency_outage",
    }

    def __init__(self, base_url: str, token: str, timeout: float = 20.0):
        self._http = httpx.Client(base_url=base_url, headers={"X-Chaos-Token": token}, timeout=timeout)

    def inject(self, scenario: str) -> dict:
        if scenario not in self.SCENARIOS:
            raise ValueError("unknown scenario")
        r = self._http.post(f"/chaos/{scenario}")
        if r.status_code >= 400:
            raise SupervisorError(f"injection failed: HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def reset(self) -> dict:
        r = self._http.post("/chaos-reset")
        if r.status_code >= 400:
            raise SupervisorError(f"reset failed: HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


class ServiceProbe:
    """Plain HTTP probes against the services' own health / API endpoints."""

    def __init__(self, urls: dict[str, str], health_timeout: float):
        self.urls = urls
        self._http = httpx.Client(timeout=health_timeout)

    def health(self, service: str) -> dict:
        url = self.urls.get(service)
        if not url:
            return {"ok": False, "status": None, "error": "no url configured", "ms": None, "body": None}
        import time

        start = time.perf_counter()
        try:
            r = self._http.get(f"{url}/health")
            ms = round((time.perf_counter() - start) * 1000, 1)
            try:
                body = r.json()
            except ValueError:
                body = None
            return {"ok": r.status_code == 200, "status": r.status_code, "error": None, "ms": ms, "body": body}
        except httpx.HTTPError as exc:
            ms = round((time.perf_counter() - start) * 1000, 1)
            return {"ok": False, "status": None, "error": type(exc).__name__, "ms": ms, "body": None}

    def synthetic_order(self, order_id: int) -> dict:
        """One synthetic user transaction against api-service."""
        import time

        start = time.perf_counter()
        try:
            r = self._http.get(f"{self.urls['api-service']}/api/orders/{order_id}", timeout=3.0)
            ms = round((time.perf_counter() - start) * 1000, 1)
            return {"ok": r.status_code < 500, "status": r.status_code, "ms": ms}
        except httpx.HTTPError as exc:
            ms = round((time.perf_counter() - start) * 1000, 1)
            return {"ok": False, "status": None, "ms": ms, "error": type(exc).__name__}
