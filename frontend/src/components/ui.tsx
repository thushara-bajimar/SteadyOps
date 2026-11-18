import { ReactNode } from "react";
import { Health } from "../lib/api";
import { LIFECYCLE, lifecycleIndex } from "../lib/format";

type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "accent";

const TONE: Record<Tone, string> = {
  good: "text-good border-good/40 bg-good/10",
  warning: "text-warning border-warning/40 bg-warning/10",
  serious: "text-serious border-serious/40 bg-serious/10",
  critical: "text-critical border-critical/40 bg-critical/10",
  neutral: "text-ink2 border-line bg-sunken",
  accent: "text-accent border-accent/40 bg-accent/10",
};

// Status never relies on colour alone: every badge carries an icon glyph and a word.
const ICON: Record<Tone, string> = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "•", accent: "◆" };

export function Badge({ tone = "neutral", children, icon = true }: { tone?: Tone; children: ReactNode; icon?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-medium ${TONE[tone]}`}>
      {icon && <span aria-hidden>{ICON[tone]}</span>}
      {children}
    </span>
  );
}

export function healthTone(h: Health | string | null | undefined): Tone {
  return h === "healthy" ? "good" : h === "degraded" ? "warning" : h === "down" ? "critical" : "neutral";
}

export function HealthBadge({ health }: { health: Health | string }) {
  return <Badge tone={healthTone(health)}>{health}</Badge>;
}

export function statusTone(status: string): Tone {
  return status === "resolved" ? "good" : status === "escalated" ? "critical" : status === "closed" ? "neutral" : "accent";
}

export function severityTone(sev: string): Tone {
  return sev === "critical" ? "critical" : sev === "high" ? "serious" : sev === "medium" ? "warning" : "neutral";
}

export function Card({ title, actions, children, className = "", bodyClass = "p-4" }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title !== undefined && (
        <header className="card-h">
          <h2 className="card-t">{title}</h2>
          {actions}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  const color = tone && tone !== "neutral" ? TONE[tone].split(" ")[0] : "text-ink";
  return (
    <div className="card px-4 py-3">
      <div className="label">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink2">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="mb-4 rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-sm text-critical">✕ {error}</div>;
}

export function Lifecycle({ stage, status }: { stage: string; status: string }) {
  const idx = lifecycleIndex(stage, status);
  const outcome = status === "resolved" ? "Resolved" : status === "escalated" ? "Escalated" : status === "closed" ? "Closed" : null;
  return (
    <ol className="flex flex-wrap gap-y-2 text-xs">
      {LIFECYCLE.map((s, i) => {
        const done = i < idx || (i === idx && status !== "open");
        const current = i === idx && status === "open";
        const label = i === LIFECYCLE.length - 1 && outcome ? outcome : s.key === "executing" && stage === "awaiting_approval" ? "Awaiting approval" : s.label;
        const bad = i === LIFECYCLE.length - 1 && status === "escalated";
        return (
          <li key={s.key} className="flex items-center">
            <span className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium ${
              bad ? "border-critical/50 bg-critical/10 text-critical"
                : done ? "border-good/40 bg-good/10 text-good"
                : current ? "border-accent bg-accent/10 text-accent"
                : "border-line text-muted"}`}>
              <span aria-hidden>{bad ? "✕" : done ? "✓" : current ? "●" : "○"}</span>{label}
            </span>
            {i < LIFECYCLE.length - 1 && <span className="mx-1 h-px w-3 bg-line" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

export function Checks({ checks }: { checks: { name: string; passed: boolean; detail: string }[] }) {
  return (
    <ul className="space-y-1 text-sm">
      {checks.map((c, i) => (
        <li key={i} className="flex gap-2">
          <span className={c.passed ? "text-good" : "text-critical"} aria-label={c.passed ? "passed" : "failed"}>
            {c.passed ? "✓" : "✕"}
          </span>
          <span><span className="mono text-ink2">{c.name}</span> <span className="text-ink2">— {c.detail}</span></span>
        </li>
      ))}
    </ul>
  );
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="mono max-h-80 overflow-auto rounded-md bg-sunken p-3 text-ink2">{JSON.stringify(value, null, 2)}</pre>
  );
}
