import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorNote, PageHeader, severityTone } from "../components/ui";
import { api, getOperator, setOperator } from "../lib/api";
import { CATEGORY_LABEL, clock, humanize } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Row {
  id: number; incident_id: number; title: string; severity: string; action_id: string; resource: string;
  params: Record<string, string>; reason: string; risk_level: string; expected_impact: string; rollback_available: number;
  status: string; approval_reason: string | null; created_at: string;
  dry_run: { steps: string[]; rollback_plan: string } | null;
  diagnosis: { category: string; confidence: number; suspected_cause: string; supporting: string[] } | null;
  approval: { decision: string; approver: string; decided_at: string } | null;
  execution: { status: string; error: string | null } | null;
  rollback: { status: string; reason: string } | null;
}

export function ApprovalControls({ proposalId, onDone }: { proposalId: number; onDone: () => void }) {
  const [name, setName] = useState(getOperator());
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const decide = async (verb: "approve" | "reject") => {
    setBusy(true); setErr(null);
    try {
      setOperator(name);
      await api.post(`/proposals/${proposalId}/${verb}`, { operator: name, comment: comment || null });
      onDone();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input className="input w-40" value={name} onChange={(e) => setName(e.target.value)} aria-label="Your name" placeholder="your name" />
      <input className="input min-w-[12rem] flex-1" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="comment (optional)" />
      <button className="btn btn-primary" disabled={busy || name.trim().length < 2} onClick={() => decide("approve")}>✓ Approve</button>
      <button className="btn btn-danger" disabled={busy || name.trim().length < 2} onClick={() => decide("reject")}>✕ Reject</button>
      {err && <div className="w-full text-xs text-critical">{err}</div>}
    </div>
  );
}

export default function Approvals() {
  const { data, error, refresh } = usePoll(() => api.get<Row[]>("/approvals"), 2000);
  const pending = (data ?? []).filter((r) => r.status === "awaiting_approval");
  const history = (data ?? []).filter((r) => r.status !== "awaiting_approval");
  return (
    <>
      <PageHeader title="Remediation & Approvals"
        subtitle="Proposals the safety policy would not auto-execute. Approval is explicit, named and recorded; execution still re-checks the policy." />
      <ErrorNote error={error} />
      <h2 className="mb-2 text-base font-semibold">Waiting for a human ({pending.length})</h2>
      {pending.length === 0 && <Empty>Nothing needs approval. Switch to <b>supervised</b> mode in Audit &amp; Safety to require approval for every change.</Empty>}
      <div className="space-y-4">
        {pending.map((p) => (
          <Card key={p.id} title={<span>{p.action_id} → {p.resource} <span className="font-normal text-muted">for <Link className="text-accent" to={`/incidents/${p.incident_id}`}>incident #{p.incident_id}</Link></span></span>}
            actions={<Badge tone={severityTone(p.severity)}>{p.severity}</Badge>}>
            <div className="grid gap-4 text-sm md:grid-cols-2">
              <dl className="grid grid-cols-[130px_1fr] gap-2">
                <dt className="label">Proposed</dt><dd className="mono">{p.action_id}({JSON.stringify(p.params)})</dd>
                <dt className="label">Reason</dt><dd>{p.reason}</dd>
                <dt className="label">Affected</dt><dd>{p.resource}</dd>
                <dt className="label">Impact</dt><dd>{p.expected_impact}</dd>
                <dt className="label">Risk</dt><dd>{p.risk_level}</dd>
                <dt className="label">Rollback</dt><dd>{p.rollback_available ? p.dry_run?.rollback_plan : "not applicable"}</dd>
                <dt className="label">Why approval</dt><dd className="text-warning">{p.approval_reason}</dd>
              </dl>
              <div>
                {p.diagnosis && <>
                  <div className="label mb-1">Diagnosis ({CATEGORY_LABEL[p.diagnosis.category]}, confidence {p.diagnosis.confidence.toFixed(2)})</div>
                  <p className="mb-2">{p.diagnosis.suspected_cause}</p>
                  <ul className="mb-3 list-disc pl-5 text-ink2">{p.diagnosis.supporting.map((s, i) => <li key={i}>{s}</li>)}</ul>
                </>}
                {p.dry_run && <><div className="label mb-1">Dry-run plan</div><ol className="list-decimal pl-5">{p.dry_run.steps.map((s, i) => <li key={i}>{s}</li>)}</ol></>}
              </div>
            </div>
            <div className="mt-4 border-t border-line pt-3"><ApprovalControls proposalId={p.id} onDone={refresh} /></div>
          </Card>
        ))}
      </div>

      <h2 className="mb-2 mt-8 text-base font-semibold">All proposals</h2>
      <Card bodyClass="overflow-x-auto p-0">
        <table className="tbl min-w-[860px]">
          <thead><tr><th>#</th><th>Incident</th><th>Action</th><th>Risk</th><th>Status</th><th>Human decision</th><th>Execution</th><th>Rollback</th><th>Created</th></tr></thead>
          <tbody>
            {history.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td>
                <td><Link className="text-accent" to={`/incidents/${p.incident_id}`}>#{p.incident_id}</Link></td>
                <td className="mono">{p.action_id} → {p.resource}</td>
                <td>{p.risk_level}</td>
                <td><Badge tone={p.status === "succeeded" ? "good" : ["blocked", "failed", "rejected", "rolled_back"].includes(p.status) ? "critical" : "neutral"}>{humanize(p.status)}</Badge></td>
                <td className="text-ink2">{p.approval ? `${p.approval.decision} by ${p.approval.approver}` : "auto (policy)"}</td>
                <td className="text-ink2">{p.execution ? humanize(p.execution.status) : "—"}</td>
                <td className="text-ink2">{p.rollback ? p.rollback.status : "—"}</td>
                <td className="whitespace-nowrap text-muted">{clock(p.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
