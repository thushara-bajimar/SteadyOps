import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApprovalControls } from "./Approvals";
import { Badge, Card, Checks, ErrorNote, Json, Lifecycle, PageHeader, severityTone, statusTone } from "../components/ui";
import { Check, Incident, Signal, api } from "../lib/api";
import { CATEGORY_LABEL, clock, humanize, secs } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Factor { weight: number; text: string; evidence: string; evidence_id: number | null }
interface Diagnosis {
  id: number; category: string; root_service: string; suspected_cause: string; confidence: number; confidence_level: string;
  ambiguous: boolean; supporting: { supporting: Factor[]; contradicting: Factor[]; margin: number };
  alternatives: { category: string; service: string; score: number }[]; recommended: { action_id: string | null; reason: string } | null;
}
interface Proposal {
  id: number; action_id: string; resource: string; params: Record<string, string>; reason: string; risk_level: string;
  expected_impact: string; rollback_available: boolean; status: string; approval_reason: string | null;
  dry_runs: { id: number; created_at: string; plan: { steps: string[]; current: object; target: object; rollback_plan: string; preconditions: Check[] } }[];
  policy_decisions: { id: number; phase: string; outcome: string; reasons: string[]; checks: Check[]; evaluated_at: string }[];
  approval: { decision: string; approver: string; comment: string | null; decided_at: string } | null;
  executions: {
    id: number; status: string; attempts: number; error: string | null; started_at: string; finished_at: string | null;
    pre_state: { health: Record<string, string>; health_score: number; version: string; config_version: string };
    verifications: { result: string; summary: string; checks: { ts: string; healthy: boolean; problems: string[] }[] }[];
    rollbacks: { status: string; reason: string; details: object }[];
  }[];
}
interface Detail {
  incident: Incident & { signals: Signal[] };
  timeline: { id: number; ts: string; stage: string; message: string }[];
  evidence: { id: number; kind: string; source: string; service: string | null; summary: string; data: unknown; collected_at: string }[];
  diagnoses: Diagnosis[]; proposals: Proposal[];
  injection: { scenario: string; injected_at: string; expected_category: string } | null;
  timings: Record<string, string | number | null>;
  summary: { what_happened: string; affected_services: string[]; diagnosis: string | null; evidence: string[]; what_changed: string;
             proposed_action: string | null; why_safe: string | null; after_execution: string | null; resolved: boolean; status_line: string };
}

const OUTCOME_TONE: Record<string, "good" | "warning" | "critical"> = { allow: "good", require_approval: "warning", deny: "critical" };

export default function IncidentDetail() {
  const { id } = useParams();
  const { data, error, refresh } = usePoll(() => api.get<Detail>(`/incidents/${id}`), 2000, [id]);
  const [openEv, setOpenEv] = useState<number | null>(null);
  if (!data) return <><PageHeader title={`Incident #${id}`} /><ErrorNote error={error} /></>;
  const { incident: inc, summary: sm, timings: t } = data;
  const diag = data.diagnoses.at(-1);
  const prop = data.proposals.at(-1);
  const evById = Object.fromEntries(data.evidence.map((e) => [e.id, e]));

  return (
    <>
      <div className="mb-2 text-sm"><Link to="/incidents" className="text-accent hover:underline">← Incident Center</Link></div>
      <PageHeader
        title={`Incident #${inc.id}: ${inc.title}`}
        subtitle={<span className="flex flex-wrap gap-2">
          <Badge tone={statusTone(inc.status)}>{inc.status}</Badge>
          <Badge tone={severityTone(inc.severity)}>{inc.severity}</Badge>
          {data.injection && <Badge tone="neutral">Failure Lab: {humanize(data.injection.scenario)}</Badge>}
        </span>}
      />
      <ErrorNote error={error} />
      <Card className="mb-4"><Lifecycle stage={inc.stage} status={inc.status} /></Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="60-second briefing" className="lg:col-span-2">
          <p className={`mb-3 rounded-md px-3 py-2 text-sm font-medium ${sm.resolved ? "bg-good/10 text-good" : inc.status === "escalated" ? "bg-critical/10 text-critical" : "bg-accent/10 text-accent"}`}>
            {sm.status_line}
          </p>
          <dl className="grid gap-3 text-sm sm:grid-cols-[170px_1fr]">
            <dt className="label pt-0.5">What happened</dt><dd>{sm.what_happened}</dd>
            <dt className="label pt-0.5">Affected</dt><dd>{sm.affected_services.join(", ")}</dd>
            <dt className="label pt-0.5">Diagnosis</dt><dd>{sm.diagnosis ?? "investigating…"}</dd>
            <dt className="label pt-0.5">Evidence</dt>
            <dd><ul className="list-disc space-y-0.5 pl-5">{sm.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul></dd>
            <dt className="label pt-0.5">What changed</dt><dd className="text-ink2">{sm.what_changed}</dd>
            <dt className="label pt-0.5">Proposed action</dt><dd>{sm.proposed_action ?? "none"}</dd>
            <dt className="label pt-0.5">Why it's safe</dt><dd>{sm.why_safe ?? "—"}</dd>
            <dt className="label pt-0.5">After execution</dt><dd>{sm.after_execution ?? "—"}</dd>
          </dl>
        </Card>
        <Card title="Timestamps (measured)">
          <dl className="space-y-1.5 text-sm">
            {([["Failure injected", t.injected_at], ["First abnormal sample", t.first_abnormal_at], ["Detected", t.detected_at],
               ["Diagnosed", t.diagnosed_at], ["Remediation started", t.remediation_started_at], ["Remediation finished", t.remediation_completed_at],
               ["Recovery verified", t.verified_at], ["Escalated", t.escalated_at]] as const).map(([k, v]) => (
              <div key={k} className="flex justify-between"><dt className="text-ink2">{k}</dt><dd className="mono">{clock(v as string)}</dd></div>
            ))}
          </dl>
          <div className="mt-3 grid grid-cols-3 gap-2 border-t border-line pt-3 text-center">
            <div><div className="label">Detect</div><div className="font-semibold">{secs(t.time_to_detect_s as number)}</div></div>
            <div><div className="label">Diagnose</div><div className="font-semibold">{secs(t.time_to_diagnose_s as number)}</div></div>
            <div><div className="label">Recover</div><div className="font-semibold">{secs(t.time_to_recover_s as number)}</div></div>
          </div>
          {inc.status === "escalated" && <CloseIncident id={inc.id} onDone={refresh} />}
        </Card>
      </div>

      {diag && (
        <Card title="Root-cause analysis" className="mt-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Badge tone={diag.ambiguous ? "critical" : "accent"}>{CATEGORY_LABEL[diag.category]}</Badge>
            <span className="text-sm">root service <b>{diag.root_service}</b></span>
            <div className="flex items-center gap-2 text-sm">
              confidence
              <div className="h-2 w-32 overflow-hidden rounded bg-sunken"><div className="h-full bg-accent" style={{ width: `${diag.confidence * 100}%` }} /></div>
              <b className="tabular-nums">{diag.confidence.toFixed(2)}</b> ({diag.confidence_level})
            </div>
            <span className="text-xs text-muted">margin over runner-up {diag.supporting.margin?.toFixed(2)}</span>
          </div>
          <p className="mb-3 text-sm">{diag.suspected_cause}</p>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <div className="label mb-1">Supporting evidence</div>
              <ul className="space-y-1 text-sm">
                {diag.supporting.supporting.map((f, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mono w-10 shrink-0 text-good">+{f.weight.toFixed(2)}</span>
                    <span>{f.text} {f.evidence_id && <button className="text-xs text-accent hover:underline" onClick={() => setOpenEv(f.evidence_id)}>[evidence #{f.evidence_id}]</button>}</span>
                  </li>
                ))}
              </ul>
              {diag.supporting.contradicting.length > 0 && <>
                <div className="label mb-1 mt-3">Contradicting evidence</div>
                <ul className="space-y-1 text-sm">{diag.supporting.contradicting.map((f, i) => (
                  <li key={i} className="flex gap-2"><span className="mono w-10 shrink-0 text-critical">{f.weight.toFixed(2)}</span>{f.text}</li>))}</ul>
              </>}
            </div>
            <div>
              <div className="label mb-1">Alternative explanations considered</div>
              <table className="tbl"><tbody>
                {diag.alternatives.map((a, i) => (
                  <tr key={i}><td>{CATEGORY_LABEL[a.category]}</td><td className="text-ink2">{a.service}</td><td className="mono text-right">{a.score.toFixed(2)}</td></tr>
                ))}
              </tbody></table>
            </div>
          </div>
        </Card>
      )}

      {prop && (
        <Card title={`Remediation: ${prop.action_id} → ${prop.resource}`} className="mt-4"
          actions={<Badge tone={prop.status === "succeeded" ? "good" : ["failed", "blocked", "rejected", "rolled_back"].includes(prop.status) ? "critical" : "warning"}>{humanize(prop.status)}</Badge>}>
          <p className="mb-2 text-sm">{prop.reason}</p>
          <p className="mb-3 text-sm text-ink2">Risk <b>{prop.risk_level}</b> · rollback {prop.rollback_available ? "available" : "not applicable"} · {prop.expected_impact}</p>
          {prop.status === "awaiting_approval" && (
            <div className="mb-4 rounded-md border border-warning/50 bg-warning/10 p-3">
              <div className="mb-2 text-sm"><b className="text-warning">! Human approval required:</b> {prop.approval_reason}</div>
              <ApprovalControls proposalId={prop.id} onDone={refresh} />
            </div>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <div className="label mb-1">Dry run (no side effects)</div>
              {prop.dry_runs.at(-1) && <>
                <ol className="mb-2 list-decimal pl-5 text-sm">{prop.dry_runs.at(-1)!.plan.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                <div className="text-xs text-ink2">Rollback plan: {prop.dry_runs.at(-1)!.plan.rollback_plan}</div>
              </>}
            </div>
            <div>
              {prop.policy_decisions.map((d) => (
                <details key={d.id} className="mb-2" open={d.phase === "execute" || prop.policy_decisions.length === 1}>
                  <summary className="cursor-pointer text-sm">
                    Policy decision ({d.phase}) <Badge tone={OUTCOME_TONE[d.outcome]}>{humanize(d.outcome)}</Badge>{" "}
                    <span className="text-xs text-muted">{d.checks.filter((c) => c.passed).length}/{d.checks.length} checks</span>
                  </summary>
                  <div className="mt-2"><Checks checks={d.checks} /></div>
                </details>
              ))}
            </div>
          </div>
          {prop.approval && (
            <p className="mt-3 text-sm">Human decision: <b>{prop.approval.decision}</b> by {prop.approval.approver} at {clock(prop.approval.decided_at)}{prop.approval.comment ? ` — “${prop.approval.comment}”` : ""}</p>
          )}
          {prop.executions.map((x) => (
            <div key={x.id} className="mt-4 border-t border-line pt-3">
              <div className="text-sm">Execution #{x.id}: <b>{humanize(x.status)}</b> after {x.attempts} attempt(s), {clock(x.started_at)} → {clock(x.finished_at)}
                {x.error && <span className="text-critical"> — {x.error}</span>}</div>
              <div className="text-xs text-muted">Pre-action health: {Object.entries(x.pre_state.health ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")} (score {x.pre_state.health_score})</div>
              {x.verifications.map((v, i) => (
                <div key={i} className="mt-2">
                  <div className="text-sm">Verification <Badge tone={v.result === "passed" ? "good" : "critical"}>{v.result}</Badge> {v.summary}</div>
                  <div className="mt-1 flex flex-wrap gap-1" aria-label="verification observations">
                    {v.checks.map((c, j) => (
                      <span key={j} title={c.problems.join("\n") || "healthy"}
                        className={`inline-block h-4 w-4 rounded-sm text-center text-[10px] leading-4 text-white ${c.healthy ? "bg-good" : "bg-critical"}`}>{c.healthy ? "✓" : "✕"}</span>
                    ))}
                  </div>
                </div>
              ))}
              {x.rollbacks.map((r, i) => (
                <div key={i} className="mt-2 text-sm">Rollback <Badge tone={r.status === "succeeded" ? "warning" : "critical"}>{r.status}</Badge> {r.reason}</div>
              ))}
            </div>
          ))}
        </Card>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Timeline" bodyClass="p-0">
          <ol className="divide-y divide-line/60">
            {data.timeline.map((e) => (
              <li key={e.id} className="flex gap-3 px-4 py-2 text-sm">
                <span className="mono w-20 shrink-0 text-muted">{clock(e.ts)}</span>
                <span className="w-28 shrink-0"><Badge tone={e.stage === "resolved" ? "good" : e.stage === "escalated" ? "critical" : "neutral"} icon={false}>{humanize(e.stage)}</Badge></span>
                <span className="text-ink2">{e.message}</span>
              </li>
            ))}
          </ol>
        </Card>
        <Card title={`Evidence collected (${data.evidence.length})`} bodyClass="p-0">
          <ul className="divide-y divide-line/60">
            {data.evidence.map((e) => (
              <li key={e.id} className="px-4 py-2 text-sm">
                <button className="w-full text-left" onClick={() => setOpenEv(openEv === e.id ? null : e.id)}>
                  <span className="mono text-muted">#{e.id}</span> <b>{humanize(e.kind)}</b>{e.service && <span className="text-ink2"> · {e.service}</span>}
                  <span className="text-xs text-muted"> · {e.source}</span>
                  <div className="text-ink2">{e.summary}</div>
                </button>
                {openEv === e.id && <div className="mt-2"><Json value={evById[e.id].data} /></div>}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}

function CloseIncident({ id, onDone }: { id: number; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="label mb-1">Human follow-up</div>
      <textarea className="input mb-2 w-full" rows={2} placeholder="What did you do? (required)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn" disabled={note.trim().length < 3} onClick={async () => {
        try { await api.post(`/incidents/${id}/close`, { operator: localStorage.getItem("steadyops.operator") || "on-call engineer", note }); onDone(); }
        catch (e) { setErr(String(e)); }
      }}>Close incident</button>
      {err && <div className="mt-1 text-xs text-critical">{err}</div>}
    </div>
  );
}
