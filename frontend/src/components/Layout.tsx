import { ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../lib/api";
import { usePoll } from "../lib/usePoll";
import { Badge, healthTone } from "./ui";

interface Header {
  system_status: string; incidents: { active: number }; pending_approvals: number;
  emergency_stop: { engaged: boolean }; autonomy_mode: string; supervisor_ok: boolean | null;
}

const NAV = [
  ["/", "Overview"],
  ["/services", "Services"],
  ["/incidents", "Incident Center"],
  ["/approvals", "Remediation & Approvals"],
  ["/failure-lab", "Failure Lab"],
  ["/safety", "Audit & Safety"],
  ["/analytics", "Analytics"],
] as const;

export function Layout({ children }: { children: ReactNode }) {
  const { data, error } = usePoll(() => api.get<Header>("/overview"), 3000);
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen md:flex">
      <aside className={`${open ? "block" : "hidden"} border-b border-line bg-surface md:block md:w-60 md:shrink-0 md:border-b-0 md:border-r`}>
        <div className="hidden items-center gap-2 px-5 py-5 md:flex">
          <Logo />
          <div>
            <div className="font-semibold leading-tight text-ink">SteadyOps</div>
            <div className="text-[11px] text-muted">The on-call engineer that doesn't panic</div>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5 px-3 pb-4">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}
              className={({ isActive }) => `flex items-center justify-between rounded-md px-3 py-2 text-sm ${
                isActive ? "bg-accent/10 font-medium text-accent" : "text-ink2 hover:bg-sunken"}`}>
              {label}
              {to === "/approvals" && data && data.pending_approvals > 0 && (
                <span className="rounded-full bg-warning/20 px-1.5 text-xs font-semibold text-warning">{data.pending_approvals}</span>
              )}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-line bg-surface/95 px-4 py-2.5 backdrop-blur md:px-6">
          <button className="btn md:hidden" onClick={() => setOpen(!open)} aria-label="Toggle navigation">☰</button>
          <div className="flex items-center gap-2 md:hidden"><Logo /><span className="font-semibold">SteadyOps</span></div>
          <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
            {error && <Badge tone="critical">API unreachable</Badge>}
            {data && (
              <>
                <Badge tone={healthTone(data.system_status)}>system {data.system_status}</Badge>
                {data.supervisor_ok === false && <Badge tone="critical">supervisor unreachable</Badge>}
                <Badge tone={data.incidents.active ? "serious" : "neutral"}>{data.incidents.active} active incident(s)</Badge>
                <Badge tone={data.autonomy_mode === "auto" ? "accent" : "warning"}>
                  {data.autonomy_mode === "auto" ? "auto-remediation" : "supervised mode"}
                </Badge>
                {data.emergency_stop.engaged && <Badge tone="critical">EMERGENCY STOP</Badge>}
              </>
            )}
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden>
      <rect width="32" height="32" rx="7" fill="rgb(var(--accent))" />
      <path d="M5 17h6l3-7 4 13 3-6h6" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
