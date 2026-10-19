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
  return <span className={`chip ${STATUS_TONE[status] ?? ""}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Confidence({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="row" title={`confidence ${(v * 100).toFixed(0)}%`}>
      <div className="bar" style={{ width: 120 }}>
        <span style={{ width: `${v * 100}%` }} />
      </div>
      <span className="small muted">{(v * 100).toFixed(0)}%</span>
    </div>
  );
}

/** Tiny inline chart for one metric series ([t, v] points). */
export function Sparkline({ points, label, unit = "" }: { points: [number, number][]; label: string; unit?: string }) {
  if (!points || points.length < 2) {
    return (
      <div>
        <div className="small muted">{label}</div>
        <div className="small faint">no data</div>
      </div>
    );
  }
  const w = 200;
  const h = 44;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y1 = Math.max(...ys, 1e-9);
  const sx = (x: number) => ((x - x0) / Math.max(1e-9, x1 - x0)) * w;
  const sy = (y: number) => h - 2 - (y / y1) * (h - 4);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  return (
    <div>
      <div className="row small" style={{ justifyContent: "space-between" }}>
        <span className="muted">{label}</span>
        <span className="mono">
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
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

/** Confidence interval drawn against zero, for experiment diffs in [-1, 1]. */
export function CiBar({ low, high, est }: { low: number; high: number; est: number }) {
  const span = Math.max(0.25, Math.abs(low), Math.abs(high));
  const pos = (x: number) => `${((x + span) / (2 * span)) * 100}%`;
  return (
    <div className="ci" title={`[${(low * 100).toFixed(1)}, ${(high * 100).toFixed(1)}]`}>
      <div className="zero" style={{ left: pos(0) }} />
      <div className="span" style={{ left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }} />
      <div className="pt" style={{ left: pos(est) }} />
    </div>
  );
}
