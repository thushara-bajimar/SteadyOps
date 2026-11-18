export const CATEGORY_LABEL: Record<string, string> = {
  bad_deployment: "Bad deployment",
  broken_config: "Broken configuration",
  resource_exhaustion: "Resource exhaustion",
  dependency_failure: "Dependency failure",
  unknown: "Ambiguous",
};

export const LIFECYCLE = [
  { key: "detected", label: "Detection" },
  { key: "investigating", label: "Investigation" },
  { key: "diagnosed", label: "Diagnosis" },
  { key: "safety_evaluation", label: "Safety evaluation" },
  { key: "executing", label: "Approval / Execution" },
  { key: "verifying", label: "Verification" },
  { key: "outcome", label: "Resolution / Escalation" },
] as const;

/** Index of the lifecycle step an incident has reached (for the stepper). */
export function lifecycleIndex(stage: string, status: string): number {
  if (status !== "open") return LIFECYCLE.length - 1;
  if (stage === "awaiting_approval") return 4;
  const i = LIFECYCLE.findIndex((s) => s.key === stage);
  return i < 0 ? 0 : i;
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function secs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v < 60 ? `${v.toFixed(1)} s` : `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
}

export function num(v: number | null | undefined, unit = "", digits = 0): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}${unit}`;
}

export function clock(ts: string | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ago(ts: string | null | undefined, now = Date.now()): string {
  if (!ts) return "—";
  const s = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export function humanize(s: string | null | undefined): string {
  return s ? s.replace(/_/g, " ") : "—";
}
