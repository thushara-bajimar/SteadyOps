// Thin typed client for the SteadyOps REST API. Every value the dashboard
// shows comes from these calls - nothing is mocked or hard-coded.

export type Health = "healthy" | "degraded" | "down" | "unknown";

export interface Service {
  name: string; health: Health; process_state: string | null; version: string | null; config_version: string | null;
  http_status: number | null; response_ms: number | null; health_error: string | null; checks: Record<string, string> | null;
  error_rate: number | null; latency_p95_ms: number | null; rss_mb: number | null; cpu_percent: number | null;
  restart_count: number | null; restarts_recent: number | null; depends_on: string[]; stateful: boolean;
  known_good_versions: string[]; known_good_configs: string[];
}

export interface Incident {
  id: number; status: "open" | "resolved" | "escalated" | "closed"; stage: string; severity: string; title: string;
  detected_service: string; root_service: string | null; category: string | null; affected_services: string[];
  first_abnormal_at: string; detected_at: string; diagnosed_at: string | null; remediation_started_at: string | null;
  resolved_at: string | null; escalated_at: string | null; closed_at: string | null; resolution: string | null;
  escalation_reason: string | null; confidence?: number | null; scenario?: string | null; signals?: Signal[];
}

export interface Signal { service: string; type: string; severity: string; message: string }

export interface Check { name: string; passed: boolean; detail: string }

export interface Stats { n: number; mean: number | null; median: number | null; min: number | null; max: number | null }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) {
    const detail = data?.detail;
    throw new ApiError(r.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? r.statusText));
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string) => req<T>("GET", path),
  post: <T,>(path: string, body: unknown) => req<T>("POST", path, body),
  put: <T,>(path: string, body: unknown) => req<T>("PUT", path, body),
};

// The approving operator's name is a per-browser convenience only.
export function getOperator(): string {
  try { return localStorage.getItem("steadyops.operator") || "on-call engineer"; } catch { return "on-call engineer"; }
}
export function setOperator(name: string) {
  try { localStorage.setItem("steadyops.operator", name); } catch { /* storage unavailable */ }
}
