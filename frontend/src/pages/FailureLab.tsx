import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorNote, PageHeader, statusTone } from "../components/ui";
import { api, getOperator } from "../lib/api";
import { CATEGORY_LABEL, ago, clock, humanize } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Scenario {
  id: string; title: string; description: string; target_service: string; expected_category: string;
  expected_action: string; expected_outcome: string; watch_for: string;
}
interface Injection { id: number; scenario: string; injected_at: string; reset_at: string | null; incident_id: number | null; incident_status: string | null }
interface LabState { scenarios: Scenario[]; active: Injection | null; history: Injection[] }

export default function FailureLab() {
  const { data, error, refresh } = usePoll(() => api.get<LabState>("/failure-lab/scenarios"), 2000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setMsg(null);
    try { await fn(); await refresh(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };
  const active = data?.active;
  const reset = () => act("reset", () => api.post("/failure-lab/reset", { operator: getOperator() }));
  return (
    <>
      <PageHeader title="Failure Lab"
        subtitle="Inject controlled, bounded, reversible failures into the demo stack and watch SteadyOps respond. One scenario at a time keeps measurements clean."
        actions={<button className="btn" disabled={busy !== null} onClick={reset}>↺ Reset all to healthy baseline</button>} />
      <ErrorNote error={error ?? msg} />
      {active && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-sm">
          <span><span className="text-warning">!</span> Active: <b>{humanize(active.scenario)}</b> since {clock(active.injected_at)} ({ago(active.injected_at)}).{" "}
            {active.incident_id ? <Link className="text-accent underline" to={`/incidents/${active.incident_id}`}>Follow incident #{active.incident_id} →</Link> : "Waiting for detection…"}</span>
          <button className="btn" disabled={busy !== null} onClick={reset}>↺ Reset</button>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {(data?.scenarios ?? []).map((s) => (
          <Card key={s.id} title={s.title} actions={<Badge tone={s.expected_outcome === "resolved" ? "good" : "serious"}>expect: {s.expected_outcome}</Badge>}>
            <p className="mb-3 text-sm text-ink2">{s.description}</p>
            <dl className="mb-3 grid grid-cols-[130px_1fr] gap-1 text-sm">
              <dt className="label">Target</dt><dd>{s.target_service}</dd>
              <dt className="label">Ground truth</dt><dd>{CATEGORY_LABEL[s.expected_category]} → {s.expected_action}</dd>
              <dt className="label">Watch for</dt><dd className="text-ink2">{s.watch_for}</dd>
            </dl>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={busy !== null || !!active}
                onClick={() => act(s.id, () => api.post(`/failure-lab/${s.id}/inject`, { operator: getOperator() }))}>
                {busy === s.id ? "Injecting…" : "⚡ Inject failure"}
              </button>
              <button className="btn" disabled={busy !== null || active?.scenario !== s.id} onClick={reset}>↺ Reset</button>
            </div>
          </Card>
        ))}
      </div>
      <Card title="How to run a demonstration" className="mt-4">
        <ol className="list-decimal space-y-1 pl-5 text-sm text-ink2">
          <li>Wait until the header shows <b>system healthy</b> for ~20 s (SteadyOps needs to observe the current versions as known-good).</li>
          <li>Click <b>Inject failure</b> on a scenario, then open the incident link that appears above.</li>
          <li>Watch the lifecycle stepper: detection (~4-10 s), investigation, diagnosis, safety evaluation, execution, verification.</li>
          <li>For the approval workflow, switch to <b>supervised</b> mode in Audit &amp; Safety first; the proposal then waits in Remediation &amp; Approvals.</li>
          <li>Click <b>Reset</b> when done. Escalated incidents close automatically once the system is observed healthy again.</li>
        </ol>
        <p className="mt-2 text-xs text-muted">The diagnosis engine never sees the “ground truth” shown here; it is used only by Analytics to score accuracy.</p>
      </Card>
      <Card title="Injection history" className="mt-4" bodyClass="p-0">
        <table className="tbl">
          <thead><tr><th>#</th><th>Scenario</th><th>Injected</th><th>Reset</th><th>Incident</th></tr></thead>
          <tbody>
            {(data?.history ?? []).map((h) => (
              <tr key={h.id}>
                <td>{h.id}</td><td>{humanize(h.scenario)}</td><td className="text-ink2">{clock(h.injected_at)}</td>
                <td className="text-ink2">{clock(h.reset_at)}</td>
                <td>{h.incident_id ? <Link className="text-accent" to={`/incidents/${h.incident_id}`}>#{h.incident_id} <Badge tone={statusTone(h.incident_status ?? "")}>{h.incident_status}</Badge></Link> : <span className="text-muted">not detected</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
