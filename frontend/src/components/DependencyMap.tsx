import { Service } from "../lib/api";

const POS: Record<string, [number, number]> = {
  "frontend-service": [90, 70],
  "api-service": [300, 70],
  "database-service": [520, 30],
  "dependency-service": [520, 112],
};
const STROKE: Record<string, string> = {
  healthy: "rgb(var(--good))", degraded: "rgb(var(--warning))", down: "rgb(var(--critical))", unknown: "rgb(var(--muted))",
};
const GLYPH: Record<string, string> = { healthy: "✓", degraded: "!", down: "✕", unknown: "?" };

/** Live service dependency graph; node border + glyph show health. */
export function DependencyMap({ services }: { services: Service[] }) {
  const byName = Object.fromEntries(services.map((s) => [s.name, s]));
  const edges = services.flatMap((s) => s.depends_on.map((d) => [s.name, d] as const));
  return (
    <svg viewBox="0 0 620 145" className="w-full" role="img" aria-label="Service dependency map">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="rgb(var(--muted))" />
        </marker>
      </defs>
      {edges.map(([a, b]) => {
        const [x1, y1] = POS[a] ?? [0, 0];
        const [x2, y2] = POS[b] ?? [0, 0];
        return <line key={a + b} x1={x1 + 75} y1={y1} x2={x2 - 78} y2={y2} stroke="rgb(var(--muted))" strokeWidth="1.5" markerEnd="url(#arr)" />;
      })}
      {Object.entries(POS).map(([name, [x, y]]) => {
        const s = byName[name];
        const h = s?.health ?? "unknown";
        return (
          <g key={name} transform={`translate(${x - 75},${y - 20})`}>
            <rect width="150" height="40" rx="7" fill="rgb(var(--surface))" stroke={STROKE[h]} strokeWidth="2" />
            <text x="12" y="25" fontSize="14" fill={STROKE[h]} fontWeight="700">{GLYPH[h]}</text>
            <text x="28" y="18" fontSize="12" fill="rgb(var(--ink))" fontWeight="600">{name}</text>
            <text x="28" y="32" fontSize="10.5" fill="rgb(var(--ink2))">{h} · v{s?.version ?? "?"}</text>
          </g>
        );
      })}
    </svg>
  );
}
