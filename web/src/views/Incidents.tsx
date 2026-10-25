import { useState } from "react";
import { ago, duration, usePoll } from "../api";
import { HeroTally, StatusChip } from "../components/bits";
import type { IncidentSummary } from "../types";

export function Incidents({ open }: { open: (id: string) => void }) {
  const { data, error } = usePoll<{ incidents: IncidentSummary[] }>("/api/incidents?limit=200", 3000);
  const { data: contractsData } = usePoll<{ contracts: unknown[] }>("/api/contracts", 5000);
  const [filter, setFilter] = useState<"all" | "needs" | "resolved" | "escalated">("all");

  const rows = data?.incidents ?? [];
  const contractsCount = contractsData?.contracts?.length ?? 0;

  const needsApproval = rows.filter((r) => r.status === "awaiting_approval");
  const resolved = rows.filter((r) => r.status === "resolved" || r.status === "self_resolved");
  const escalated = rows.filter((r) => r.status === "escalated");

  const filtered = rows.filter((r) => {
    if (filter === "needs") return r.status === "awaiting_approval";
    if (filter === "resolved") return r.status === "resolved" || r.status === "self_resolved";
    if (filter === "escalated") return r.status === "escalated";
    return true;
  });

  return (
    <div className="stack">
      {/* Editorial Hero Tally Strip */}
      <HeroTally
        resolvedCount={resolved.length}
        needsCount={needsApproval.length}
        contractCount={contractsCount}
        isLive={!error}
      />

      <div className="row-between">
        <div>
          <h1>Production <em>Incidents</em></h1>
          <p className="lede">
            Active telemetry and diagnosis stream for the Tomcat / Java payments ledger. Every incident is diagnosed with paired
            evidence from metrics, log exemplars, and Git commit changes.
          </p>
        </div>

        {/* Tab Filters */}
        <div className="filter-tabs">
          <button
            className={`filter-btn ${filter === "all" ? "active" : ""}`}
            onClick={() => setFilter("all")}
          >
            All Incidents ({rows.length})
          </button>
          <button
            className={`filter-btn ${filter === "needs" ? "active" : ""}`}
            onClick={() => setFilter("needs")}
          >
            Needs Approval ({needsApproval.length})
          </button>
          <button
            className={`filter-btn ${filter === "resolved" ? "active" : ""}`}
            onClick={() => setFilter("resolved")}
          >
            Resolved ({resolved.length})
          </button>
          <button
            className={`filter-btn ${filter === "escalated" ? "active" : ""}`}
            onClick={() => setFilter("escalated")}
          >
            Escalated ({escalated.length})
          </button>
        </div>
      </div>

      {error && <div className="banner red">⚠️ Cannot reach the OnCallify agent API: {error}</div>}

      <div className="card table-wrap" style={{ padding: 0 }}>
        {filtered.length === 0 ? (
          <div className="empty">
            <div style={{ fontSize: 28, marginBottom: 8 }}>🌿</div>
            <div style={{ fontWeight: 600, color: "var(--text)" }}>All quiet on the ledger.</div>
            <div className="small muted" style={{ marginTop: 4 }}>
              {rows.length === 0
                ? "No incidents recorded yet. Transactions flowing normally."
                : `No incidents matching the "${filter}" filter.`}
            </div>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Incident Details</th>
                <th>Diagnosed Root Cause</th>
                <th>Model Variant</th>
                <th>Opened</th>
                <th>Resolution Duration</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => open(r.id)}>
                  <td>
                    <StatusChip status={r.status} />
                  </td>
                  <td>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{r.title}</div>
                    <div className="mono faint small">{r.id} · {r.primary_rule}</div>
                  </td>
                  <td>
                    {r.diagnosis ? (
                      <div className="row" style={{ gap: 8 }}>
                        <code style={{ color: "var(--emerald)", fontWeight: 600 }}>{r.diagnosis.cause}</code>
                        <span className="small faint" style={{ fontWeight: 550 }}>
                          {(r.diagnosis.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    ) : (
                      <span className="chip" style={{ fontSize: 11 }}>
                        Diagnosing...
                      </span>
                    )}
                  </td>
                  <td>
                    {r.variant ? (
                      <span className="mono small faint">{r.variant}</span>
                    ) : (
                      <span className="faint small">—</span>
                    )}
                  </td>
                  <td className="small muted">{ago(r.opened_at)}</td>
                  <td className="small" style={{ fontWeight: 550 }}>
                    {duration(r.opened_at, r.closed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
