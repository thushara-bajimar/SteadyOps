import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { clock } from "../lib/format";

/** Single-series time chart: the title names the series, so no legend box.
 *  Thin 2px line, recessive grid, crosshair tooltip, optional threshold line. */
export function MetricChart({ title, data, dataKey, unit, threshold, format }: {
  title: string; data: object[]; dataKey: string; unit: string; threshold?: number;
  format?: (v: number) => string;
}) {
  const fmt = format ?? ((v: number) => `${Math.round(v)}${unit}`);
  const hasData = data.some((d) => { const v = (d as Record<string, unknown>)[dataKey]; return v !== null && v !== undefined; });
  return (
    <figure className="card p-3">
      <figcaption className="mb-2 flex items-baseline justify-between">
        <span className="text-sm font-medium text-ink">{title}</span>
        {threshold !== undefined && <span className="text-xs text-muted">threshold {fmt(threshold)}</span>}
      </figcaption>
      {hasData ? (
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="ts" tickFormatter={clock} minTickGap={40} tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
                stroke="var(--chart-grid)" />
              <YAxis tickFormatter={fmt} tick={{ fill: "var(--chart-axis)", fontSize: 11 }} stroke="var(--chart-grid)" width={56} />
              <Tooltip
                cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3" }}
                contentStyle={{ background: "var(--chart-surface)", border: "1px solid var(--chart-grid)", borderRadius: 6, fontSize: 12 }}
                labelFormatter={(l) => clock(String(l))}
                formatter={(v) => [fmt(Number(v)), title] as [string, string]}
              />
              {threshold !== undefined && (
                <ReferenceLine y={threshold} stroke="var(--chart-axis)" strokeDasharray="4 4" />
              )}
              <Line type="monotone" dataKey={dataKey} stroke="var(--series-1)" strokeWidth={2} dot={false}
                isAnimationActive={false} connectNulls={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--chart-surface)" }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="flex h-40 items-center justify-center text-sm text-muted">No samples in this window</div>
      )}
    </figure>
  );
}
