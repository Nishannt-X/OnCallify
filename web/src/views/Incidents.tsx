import { ago, duration, usePoll } from "../api";
import { StatusChip } from "../components/bits";
import type { IncidentSummary } from "../types";

export function Incidents({ open }: { open: (id: string) => void }) {
  const { data, error } = usePoll<{ incidents: IncidentSummary[] }>("/api/incidents?limit=200");
  const rows = data?.incidents ?? [];
  const needs = rows.filter((r) => r.status === "awaiting_approval").length;
  const resolved = rows.filter((r) => r.status === "resolved" || r.status === "self_resolved").length;
  const escalated = rows.filter((r) => r.status === "escalated").length;
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Incidents</h1>
        <div className="row">
          <span className="chip amber">{needs} need approval</span>
          <span className="chip green">{resolved} resolved</span>
          <span className="chip red">{escalated} escalated</span>
        </div>
      </div>
      {error && <div className="error">Cannot reach the agent API: {error}</div>}
      <div className="card table-wrap">
        {rows.length === 0 ? (
          <div className="empty">No incidents yet. Everything is quiet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Incident</th>
                <th>Diagnosis</th>
                <th>Variant</th>
                <th>Opened</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => open(r.id)}>
                  <td>
                    <StatusChip status={r.status} />
                  </td>
                  <td>
                    <div>{r.title}</div>
                    <div className="mono faint">{r.id}</div>
                  </td>
                  <td>
                    {r.diagnosis ? (
                      <>
                        <code>{r.diagnosis.cause}</code>{" "}
                        <span className="faint small">{Math.round(r.diagnosis.confidence * 100)}%</span>
                      </>
                    ) : (
                      <span className="faint">pending</span>
                    )}
                  </td>
                  <td className="mono small">{r.variant ?? ""}</td>
                  <td className="small muted">{ago(r.opened_at)}</td>
                  <td className="small">{duration(r.opened_at, r.closed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
