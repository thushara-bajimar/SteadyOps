import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorNote, PageHeader, Stat } from "../components/ui";
import { Stats, api } from "../lib/api";
import { CATEGORY_LABEL, humanize, pct, secs } from "../lib/format";
import { usePoll } from "../lib/usePoll";

interface Run {
  injection_id: number; scenario: string; incident_id: number | null; expected_category: string; diagnosed_category: string | null;
  diagnosis_correct: boolean | null; executed_action: string | null; action_correct: boolean | null; outcome: string;
  ttd_s: number | null; ttr_s: number | null; time_to_escalate_s: number | null;
}
interface Analytics {
  summary: {
    injections: number; incidents_total: number; mttd_s: Stats; mttr_s: Stats; detect_to_recover_s: Stats; time_to_diagnose_s: Stats;
    detection_rate: number | null; diagnosis_accuracy: number | null; incorrect_remediation_rate: number | null;
    remediation_success_rate: number | null; incident_resolution_rate: number | null; false_positive_rate: number | null;
    false_positives: number; human_escalation_rate: number | null; rollbacks: number; rollback_success_rate: number | null;
    blocked_actions: number; approvals: Record<string, number>;
  };
  by_scenario: Record<string, { runs: number; detected: number; correct_diagnosis: number; resolved: number; escalated: number; mttd_s: number | null; mttr_s: number | null }>;
  runs: Run[];
}

export default function Analytics() {
  const { data, error } = usePoll(() => api.get<Analytics>("/analytics"), 5000);
  if (!data) return <><PageHeader title="Analytics" /><ErrorNote error={error} /></>;
  const s = data.summary;
  const chart = data.runs.filter((r) => r.ttd_s !== null).map((r) => ({
    name: `#${r.injection_id}`, scenario: humanize(r.scenario), detect: r.ttd_s, recover: r.ttr_s,
  }));
  const n = (x: Stats) => (x.n ? `n=${x.n}, median ${secs(x.median)}, max ${secs(x.max)}` : "not measured yet");
  return (
    <>
      <PageHeader title="Analytics" subtitle="Computed from recorded timestamps of real Failure Lab runs. Nothing is estimated; a metric shows “—” until it has been measured." />
      <ErrorNote error={error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Mean time to detect" value={secs(s.mttd_s.mean)} sub={n(s.mttd_s)} />
        <Stat label="Mean time to recover" value={secs(s.mttr_s.mean)} sub={n(s.mttr_s)} />
        <Stat label="Diagnosis accuracy" value={pct(s.diagnosis_accuracy)} sub="category and root service" />
        <Stat label="Remediation success" value={pct(s.remediation_success_rate)} sub="verification passed" />
        <Stat label="Incident resolution rate" value={pct(s.incident_resolution_rate)} sub="automated, verified" />
        <Stat label="Human escalation rate" value={pct(s.human_escalation_rate)} />
        <Stat label="False positives" value={s.false_positives} sub={`${pct(s.false_positive_rate)} of ${s.incidents_total} incidents`} />
        <Stat label="Incorrect remediations" value={pct(s.incorrect_remediation_rate)} />
        <Stat label="Rollbacks" value={s.rollbacks} sub={`success ${pct(s.rollback_success_rate)}`} />
        <Stat label="Blocked by policy" value={s.blocked_actions} />
        <Stat label="Detection rate" value={pct(s.detection_rate)} sub={`${s.injections} injections`} />
        <Stat label="Time to diagnose" value={secs(s.time_to_diagnose_s.mean)} sub="detection → diagnosis" />
      </div>

      <Card title="Per-run detection and recovery time (seconds from injection)" className="mt-4">
        {chart.length === 0 ? <Empty>Run a Failure Lab scenario to populate this chart.</Empty> : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -10 }} barGap={2}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "var(--chart-axis)", fontSize: 11 }} stroke="var(--chart-grid)" />
                <YAxis unit=" s" tick={{ fill: "var(--chart-axis)", fontSize: 11 }} stroke="var(--chart-grid)" />
                <Tooltip cursor={{ fill: "var(--chart-grid)", opacity: 0.4 }}
                  contentStyle={{ background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 6, fontSize: 12 }}
                  labelFormatter={(l, p) => `${l} · ${p?.[0]?.payload?.scenario ?? ""}`}
                  formatter={(v, k) => [v === null || v === undefined ? "not recovered (escalated)" : `${Number(v).toFixed(1)} s`, k === "detect" ? "Time to detect" : "Time to recover"] as [string, string]} />
                <Legend formatter={(k) => <span style={{ color: "rgb(var(--ink2))", fontSize: 12 }}>{k === "detect" ? "Time to detect" : "Time to verified recovery"}</span>} />
                <Bar dataKey="detect" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={18} isAnimationActive={false} />
                <Bar dataKey="recover" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={18} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="mt-2 text-xs text-muted">A missing recovery bar means the incident was escalated rather than auto-recovered. For the outage and corrupted-rollback scenarios that is the intended result.</p>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="By scenario" bodyClass="overflow-x-auto p-0">
          <table className="tbl">
            <thead><tr><th>Scenario</th><th>Runs</th><th>Detected</th><th>Correct dx</th><th>Resolved</th><th>Escalated</th><th>MTTD</th><th>MTTR</th></tr></thead>
            <tbody>{Object.entries(data.by_scenario).map(([k, v]) => (
              <tr key={k}><td>{humanize(k)}</td><td>{v.runs}</td><td>{v.detected}</td><td>{v.correct_diagnosis}</td><td>{v.resolved}</td><td>{v.escalated}</td><td>{secs(v.mttd_s)}</td><td>{secs(v.mttr_s)}</td></tr>
            ))}</tbody>
          </table>
        </Card>
        <Card title="Individual runs (table view)" bodyClass="max-h-96 overflow-auto p-0">
          <table className="tbl">
            <thead><tr><th>#</th><th>Scenario</th><th>Diagnosed</th><th>Action</th><th>Outcome</th><th>TTD</th><th>TTR</th></tr></thead>
            <tbody>{data.runs.slice().reverse().map((r) => (
              <tr key={r.injection_id}>
                <td>{r.incident_id ? <Link className="text-accent" to={`/incidents/${r.incident_id}`}>{r.injection_id}</Link> : r.injection_id}</td>
                <td>{humanize(r.scenario)}</td>
                <td>{r.diagnosed_category ? <>{CATEGORY_LABEL[r.diagnosed_category]} {r.diagnosis_correct ? <span className="text-good">✓</span> : <span className="text-critical">✕</span>}</> : "—"}</td>
                <td className="mono">{r.executed_action ?? "—"}</td>
                <td><Badge tone={r.outcome === "resolved" ? "good" : r.outcome === "escalated" ? "serious" : "neutral"}>{r.outcome}</Badge></td>
                <td className="tabular-nums">{secs(r.ttd_s)}</td><td className="tabular-nums">{secs(r.ttr_s)}</td>
              </tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
