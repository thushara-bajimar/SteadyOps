import { Link } from "react-router-dom";
import { DependencyMap } from "../components/DependencyMap";
import { Badge, Card, Empty, ErrorNote, HealthBadge, PageHeader, Stat, healthTone } from "../components/ui";
import { Service, Stats, api } from "../lib/api";
import { ago, clock, humanize, pct, secs } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Overview {
  system_status: string; services_total: number; services_healthy: number;
  incidents: { active: number; open: number; escalated: number; resolved: number; closed: number };
  pending_approvals: number; services: Service[];
  recent_events: { id: number; incident_id: number; ts: string; stage: string; message: string; title: string }[];
  emergency_stop: { engaged: boolean; reason: string | null; by: string | null };
  autonomy_mode: string; active_injection: { scenario: string; injected_at: string } | null;
  stats: { mttd_s: Stats; mttr_s: Stats; diagnosis_accuracy: number | null; remediation_success_rate: number | null;
           injections: number; blocked_actions: number };
}

export default function Overview() {
  const { data, error } = usePoll(() => api.get<Overview>("/overview"), 2000);
  if (!data) return <><PageHeader title="Overview" /><ErrorNote error={error} />{!error && <Empty>Loading live state…</Empty>}</>;
  const s = data.stats;
  return (
    <>
      <PageHeader title="Overview" subtitle="Live state of the monitored stack. Every number here comes from real observations and recorded incidents." />
      <ErrorNote error={error} />
      {data.emergency_stop.engaged && (
        <div className="mb-4 rounded-md border border-critical/50 bg-critical/10 px-4 py-3 text-sm text-critical">
          ✕ <b>Emergency stop engaged</b> by {data.emergency_stop.by}: {data.emergency_stop.reason}. SteadyOps will observe and diagnose but will not change anything.
        </div>
      )}
      {data.active_injection && (
        <div className="mb-4 rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-ink">
          <span className="text-warning">!</span> Failure Lab scenario <b>{humanize(data.active_injection.scenario)}</b> injected {ago(data.active_injection.injected_at)}.{" "}
          <Link className="text-accent underline" to="/failure-lab">Manage</Link>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Stat label="System" value={data.system_status} tone={healthTone(data.system_status)} />
        <Stat label="Healthy services" value={`${data.services_healthy}/${data.services_total}`} />
        <Stat label="Active incidents" value={data.incidents.active} tone={data.incidents.active ? "serious" : undefined}
          sub={`${data.incidents.escalated} escalated`} />
        <Stat label="Pending approvals" value={data.pending_approvals} tone={data.pending_approvals ? "warning" : undefined} />
        <Stat label="MTTD" value={secs(s.mttd_s.mean)} sub={s.mttd_s.n ? `n=${s.mttd_s.n} measured` : "no runs yet"} />
        <Stat label="MTTR" value={secs(s.mttr_s.mean)} sub={s.mttr_s.n ? `n=${s.mttr_s.n} verified recoveries` : "no recoveries yet"} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Service dependencies" className="lg:col-span-2">
          <DependencyMap services={data.services} />
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {data.services.map((sv) => (
              <div key={sv.name} className="flex items-center justify-between rounded-md border border-line px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{sv.name}</div>
                  <div className="text-xs text-muted">v{sv.version} · {sv.config_version} · {sv.response_ms ?? "—"} ms</div>
                </div>
                <HealthBadge health={sv.health} />
              </div>
            ))}
          </div>
        </Card>
        <Card title="Recovery statistics">
          <dl className="space-y-2 text-sm">
            {([
              ["Resolved incidents", data.incidents.resolved],
              ["Escalated to humans", data.incidents.escalated + data.incidents.closed],
              ["Diagnosis accuracy", pct(s.diagnosis_accuracy)],
              ["Remediation success", pct(s.remediation_success_rate)],
              ["Actions blocked by policy", s.blocked_actions],
              ["Failure injections", s.injections],
            ] as const).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-line/60 pb-1.5"><dt className="text-ink2">{k}</dt><dd className="font-medium tabular-nums">{v}</dd></div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">Mode: <b>{data.autonomy_mode}</b>. Rates show “—” until something has actually been measured.</p>
        </Card>
      </div>

      <Card title="Recent incident events" className="mt-4" bodyClass="p-0">
        {data.recent_events.length === 0 ? <div className="p-4"><Empty>No incidents yet. Use the Failure Lab to inject a controlled failure.</Empty></div> : (
          <table className="tbl">
            <tbody>
              {data.recent_events.map((e) => (
                <tr key={e.id}>
                  <td className="w-24 whitespace-nowrap text-muted">{clock(e.ts)}</td>
                  <td className="w-40"><Badge tone={e.stage === "resolved" ? "good" : e.stage === "escalated" ? "critical" : "neutral"}>{humanize(e.stage)}</Badge></td>
                  <td><Link className="text-accent hover:underline" to={`/incidents/${e.incident_id}`}>#{e.incident_id}</Link> <span className="text-ink2">{e.message}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
