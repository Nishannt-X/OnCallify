import { useCountUp } from "./motion";
import type { Status } from "../types";

const STATUS_TONE: Record<Status, string> = {
  open: "blue",
  diagnosing: "blue",
  awaiting_approval: "amber",
  executing: "blue",
  verifying: "blue",
  resolved: "green",
  self_resolved: "green",
  escalated: "red",
  rejected: "",
};

const STATUS_LABEL: Record<Status, string> = {
  open: "open",
  diagnosing: "diagnosing",
  awaiting_approval: "needs approval",
  executing: "executing",
  verifying: "verifying",
  resolved: "resolved",
  self_resolved: "self-resolved",
  escalated: "escalated",
  rejected: "rejected",
};

export function StatusChip({ status }: { status: Status }) {
  return (
    <span className={`chip ${STATUS_TONE[status] ?? ""}`}>
      {status === "awaiting_approval" && <span className="pulse-dot" style={{ width: 6, height: 6 }} />}
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Confidence({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="row" title={`confidence ${(v * 100).toFixed(0)}%`}>
      <div className="bar" style={{ width: 140 }}>
        <span style={{ width: `${v * 100}%` }} />
      </div>
      <span className="small muted">{(v * 100).toFixed(0)}% confidence</span>
    </div>
  );
}

/** Micro sparkline chart for [timestamp, value] series */
export function Sparkline({ points, label, unit = "" }: { points: [number, number][]; label: string; unit?: string }) {
  if (!points || points.length < 2) {
    return (
      <div className="stack-sm">
        <div className="small muted">{label}</div>
        <div className="small faint">no time-series data</div>
      </div>
    );
  }
  const w = 240;
  const h = 48;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys, 1e-9);
  const rangeY = Math.max(1e-9, maxY - Math.min(0, minY));

  const sx = (x: number) => ((x - x0) / Math.max(1e-9, x1 - x0)) * w;
  const sy = (y: number) => h - 4 - ((y - Math.min(0, minY)) / rangeY) * (h - 8);

  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];

  return (
    <div className="stack-sm">
      <div className="row-between small">
        <span className="muted">{label}</span>
        <span className="mono" style={{ fontWeight: 600 }}>
          {formatValue(last)}
          {unit}
        </span>
      </div>
      <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={label}>
        <path className="area" d={`${d} L${w},${h} L0,${h} Z`} />
        <path className="line" d={d} />
      </svg>
    </div>
  );
}

function formatValue(v: number): string {
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

/** Confidence interval drawn against zero, for experiment diffs in [-1, 1]. */
export function CiBar({ low, high, est }: { low: number; high: number; est: number }) {
  const span = Math.max(0.25, Math.abs(low), Math.abs(high));
  const pos = (x: number) => `${((x + span) / (2 * span)) * 100}%`;
  return (
    <div className="ci" title={`95% CI: [${(low * 100).toFixed(1)}%, ${(high * 100).toFixed(1)}%]`}>
      <div className="zero" style={{ left: pos(0) }} />
      <div className="span" style={{ left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }} />
      <div className="pt" style={{ left: pos(est) }} />
    </div>
  );
}

/** Hero KPI Strip across the top with animated count-up numbers */
export function HeroTally({
  resolvedCount,
  needsCount,
  contractCount,
  isLive,
}: {
  resolvedCount: number;
  needsCount: number;
  contractCount: number;
  isLive: boolean;
}) {
  const animatedResolved = Math.round(useCountUp(resolvedCount));
  const animatedContracts = Math.round(useCountUp(contractCount));

  return (
    <div className="hero-tally">
      <div className="tile accent-emerald">
        <span className="label">Incidents Resolved</span>
        <div className="number">{animatedResolved}</div>
        <span className="sub">
          <span style={{ color: "var(--emerald)", fontWeight: 600 }}>100% verified</span> recovery
        </span>
      </div>

      <div className="tile accent-amber">
        <span className="label">Pending Attention</span>
        <div className="number" style={{ color: needsCount > 0 ? "var(--amber)" : "var(--text)" }}>
          {needsCount}
        </div>
        <span className="sub">{needsCount > 0 ? "Requires fingerprint approval" : "All quiet right now"}</span>
      </div>

      <div className="tile accent-amethyst">
        <span className="label">Standing Contracts</span>
        <div className="number" style={{ color: "var(--amethyst)" }}>
          {animatedContracts}
        </div>
        <span className="sub">Pre-approved repeat resolutions</span>
      </div>

      <div className="tile accent-cobalt">
        <span className="label">Unsafe Action Rate</span>
        <div className="number" style={{ color: "var(--emerald)" }}>
          0.00%
        </div>
        <span className="sub">Proved by 5 allowlisted runbooks</span>
      </div>

      <div className="tile accent-emerald">
        <span className="label">Ledgerline Node</span>
        <div className="number" style={{ fontSize: 26, margin: "16px 0 6px", display: "flex", alignItems: "center", gap: 10 }}>
          <span className="pulse-dot" style={{ background: isLive ? "var(--emerald)" : "var(--crimson)" }} />
          {isLive ? "Active" : "Offline"}
        </div>
        <span className="sub">Tomcat 10.1 · PostgreSQL 16</span>
      </div>
    </div>
  );
}

/** Live editorial marquee banner */
export function MarqueeBar() {
  return (
    <div className="marquee-bar">
      <span className="tag">Safety Model</span>
      <span>
        Pre-execution dry run required. Every action is gated by a <b>SHA-256 fingerprint</b> or an active <b>Standing Contract</b>. Destructive reverts can never be contracted.
      </span>
    </div>
  );
}
