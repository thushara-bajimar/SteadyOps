import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorNote, PageHeader, severityTone, statusTone } from "../components/ui";
import { Incident, api } from "../lib/api";
import { CATEGORY_LABEL, ago, clock, humanize } from "../lib/format";
import { usePoll } from "../lib/usePoll";

const FILTERS = ["all", "open", "escalated", "resolved", "closed"] as const;

export default function Incidents() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const q = filter === "all" ? "" : `?status=${filter}`;
  const { data, error } = usePoll(() => api.get<Incident[]>(`/incidents${q}`), 2000, [filter]);
  return (
    <>
      <PageHeader title="Incident Center" subtitle="Every incident SteadyOps detected, with its current lifecycle stage and diagnosis." />
      <ErrorNote error={error} />
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`btn ${filter === f ? "border-accent text-accent" : ""}`}>{f}</button>
        ))}
      </div>
      <Card bodyClass="overflow-x-auto p-0">
        {data && data.length === 0 ? <div className="p-4"><Empty>No incidents match.</Empty></div> : (
          <table className="tbl min-w-[860px]">
            <thead><tr><th>#</th><th>Status</th><th>Severity</th><th>Incident</th><th>Stage</th><th>Diagnosis</th><th>Confidence</th><th>Detected</th></tr></thead>
            <tbody>
              {(data ?? []).map((i) => (
                <tr key={i.id} className="hover:bg-sunken">
                  <td><Link className="font-medium text-accent hover:underline" to={`/incidents/${i.id}`}>#{i.id}</Link></td>
                  <td><Badge tone={statusTone(i.status)}>{i.status}</Badge></td>
                  <td><Badge tone={severityTone(i.severity)}>{i.severity}</Badge></td>
                  <td className="max-w-sm">
                    <Link to={`/incidents/${i.id}`} className="hover:underline">{i.title}</Link>
                    {i.scenario && <div className="text-xs text-muted">Failure Lab: {humanize(i.scenario)}</div>}
                  </td>
                  <td className="text-ink2">{humanize(i.stage)}</td>
                  <td>{i.category ? `${CATEGORY_LABEL[i.category] ?? i.category} → ${i.root_service}` : "—"}</td>
                  <td className="tabular-nums">{i.confidence != null ? i.confidence.toFixed(2) : "—"}</td>
                  <td className="whitespace-nowrap text-ink2">{clock(i.detected_at)} <span className="text-xs text-muted">({ago(i.detected_at)})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
