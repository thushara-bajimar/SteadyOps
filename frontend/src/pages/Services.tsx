import { useState } from "react";
import { DependencyMap } from "../components/DependencyMap";
import { MetricChart } from "../components/MetricChart";
import { Card, ErrorNote, HealthBadge, PageHeader } from "../components/ui";
import { Service, api } from "../lib/api";
import { clock, num, pct } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Sample { ts: string; response_ms: number | null; error_rate: number | null; latency_p95_ms: number | null; rss_mb: number | null; cpu_percent: number | null }
interface LogLine { ts: string; level: string; msg: string; [k: string]: unknown }

export default function Services() {
  const { data, error } = usePoll(() => api.get<Service[]>("/services"), 2000);
  const { data: settings } = usePoll(() => api.get<{ thresholds: { detection: Record<string, number> } }>("/settings"), 60000);
  const [sel, setSel] = useState("api-service");
  const { data: series } = usePoll(() => api.get<Sample[]>(`/services/${sel}/metrics?minutes=10`), 3000, [sel]);
  const { data: logs } = usePoll(() => api.get<LogLine[]>(`/services/${sel}/logs?lines=40`), 3000, [sel]);
  const det = settings?.thresholds.detection;
  const rows = series ?? [];

  return (
    <>
      <PageHeader title="Service Monitoring" subtitle="Health, response time, error rate and resource use for every monitored service, sampled every 2 s." />
      <ErrorNote error={error} />
      <Card title="Dependency map" className="mb-4">{data && <DependencyMap services={data} />}</Card>
      <Card title="Monitored services" bodyClass="overflow-x-auto">
        <table className="tbl min-w-[900px]">
          <thead><tr>
            <th>Service</th><th>Health</th><th>Process</th><th>Version / config</th><th>Resp.</th><th>Error rate</th>
            <th>p95</th><th>RSS</th><th>CPU</th><th>Restarts</th><th>Depends on</th>
          </tr></thead>
          <tbody>
            {(data ?? []).map((s) => (
              <tr key={s.name} onClick={() => setSel(s.name)} className={`cursor-pointer ${sel === s.name ? "bg-accent/5" : "hover:bg-sunken"}`}>
                <td className="font-medium">{s.name}{s.stateful && <span className="ml-1 text-xs text-muted">(stateful)</span>}</td>
                <td><HealthBadge health={s.health} />{s.health_error && <div className="text-xs text-critical">{s.health_error}</div>}</td>
                <td className="text-ink2">{s.process_state}</td>
                <td className="mono">{s.version} / {s.config_version}</td>
                <td className="tabular-nums">{num(s.response_ms, " ms")}</td>
                <td className="tabular-nums">{s.error_rate === null ? "—" : pct(s.error_rate)}</td>
                <td className="tabular-nums">{num(s.latency_p95_ms, " ms")}</td>
                <td className="tabular-nums">{num(s.rss_mb, " MB")}</td>
                <td className="tabular-nums">{num(s.cpu_percent, "%")}</td>
                <td className="tabular-nums">{s.restart_count} {s.restarts_recent ? <span className="text-critical">({s.restarts_recent} recent)</span> : null}</td>
                <td className="text-xs text-ink2">{s.depends_on.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-4 py-2 text-xs text-muted">Error rate and p95 come from synthetic order transactions against api-service. Click a row to inspect it.</p>
      </Card>

      <h2 className="mb-2 mt-6 text-base font-semibold">{sel} — last 10 minutes</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MetricChart title="Health-check response time" data={rows} dataKey="response_ms" unit=" ms" />
        <MetricChart title="Resident memory" data={rows} dataKey="rss_mb" unit=" MB" threshold={det?.memory_rss_threshold_mb} />
        <MetricChart title="CPU" data={rows} dataKey="cpu_percent" unit="%" />
        {sel === "api-service" && <>
          <MetricChart title="Synthetic error rate" data={rows} dataKey="error_rate" unit="" threshold={det?.error_rate_threshold}
            format={(v) => `${Math.round(v * 100)}%`} />
          <MetricChart title="Synthetic p95 latency" data={rows} dataKey="latency_p95_ms" unit=" ms" threshold={det?.latency_p95_threshold_ms} />
        </>}
      </div>

      <Card title={`Recent logs — ${sel} (redacted)`} className="mt-4" bodyClass="max-h-96 overflow-auto p-0">
        <table className="tbl">
          <tbody>
            {(logs ?? []).slice().reverse().map((l, i) => {
              const { ts, level, msg, service: _s, version: _v, ...rest } = l;
              void _s; void _v;
              return (
                <tr key={i}>
                  <td className="mono w-24 whitespace-nowrap text-muted">{clock(ts)}</td>
                  <td className={`mono w-16 ${level === "ERROR" || level === "FATAL" ? "text-critical" : level === "WARN" ? "text-warning" : "text-muted"}`}>{level}</td>
                  <td className="mono break-all">{msg} <span className="text-muted">{Object.keys(rest).length ? JSON.stringify(rest) : ""}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </>
  );
}
