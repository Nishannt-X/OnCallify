import { useState } from "react";
import { api, getToken, usePoll } from "../api";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { Confidence, Sparkline, StatusChip } from "../components/bits";
import type { Incident } from "../types";

const METRICS: [string, string, string?][] = [
  ["errors:pool_exhausted", "pool exhausted errors"],
  ["errors:fx_unavailable", "fx unavailable errors"],
  ["errors:fx_timeout", "fx timeout errors"],
  ["errors:db_auth", "db auth errors"],
  ["ledgerline_pool_active", "pool active"],
  ["ledgerline_pool_max", "pool max"],
  ["tomcat_threads_busy", "tomcat threads busy"],
  ["jvm_heap_used_bytes", "heap used", "B"],
  ["transfer_latency_mean_s", "transfer latency", "s"],
  ["scrape_failed", "scrape failed (service silent)"],
];

export function IncidentDetail({ id, back }: { id: string; back: () => void }) {
  const { data: inc, error, reload } = usePoll<Incident>(`/api/incidents/${id}`, 2500);
  const [days, setDays] = useState(7);
  const [uses, setUses] = useState(3);
  const [msg, setMsg] = useState<string | null>(null);
  if (error && !inc) return <div className="error">{error}</div>;
  if (!inc) return <div className="empty">Loading...</div>;

  const d = inc.diagnosis;
  const ev = inc.evidence;
  const resolvedMeanwhile = inc.timeline.some((t) => t.detail.includes("resolved on its own while waiting"));

  const approve = async (confirm: string) => {
    await api.post(`/api/incidents/${inc.id}/approve`, { proposal_id: inc.proposal!.id, confirm });
    reload();
  };
  const reject = async (reason: string) => {
    await api.post(`/api/incidents/${inc.id}/reject`, { proposal_id: inc.proposal!.id, reason });
    reload();
  };
  const grant = async () => {
    try {
      const r = await api.post<{ contract_id: string }>("/api/contracts", {
        incident_id: inc.id,
        days,
        max_uses: uses,
        min_confidence: 0.75,
      });
      setMsg(`Contract ${r.contract_id} granted: the same fault will be fixed without paging anyone.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="stack">
      <div className="row">
        <button onClick={back}>&larr; Incidents</button>
        <h1 style={{ flex: 1 }}>{inc.title}</h1>
        <StatusChip status={inc.status} />
      </div>
      <div className="mono faint">
        {inc.id} &middot; opened {inc.opened_at} {inc.closed_at ? `· closed ${inc.closed_at}` : ""}
      </div>

      {inc.status === "awaiting_approval" && inc.proposal && (
        <ApprovalPanel
          proposal={inc.proposal}
          hasToken={!!getToken()}
          onApprove={approve}
          onReject={reject}
          alertResolvedMeanwhile={resolvedMeanwhile}
        />
      )}

      <div className="grid2">
        <div className="stack">
          {d && (
            <section className="card stack">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>
                  Diagnosis: <code>{d.cause}</code>
                </h2>
                <span className="chip">{d.variant}</span>
              </div>
              <Confidence value={d.confidence} />
              <p style={{ margin: 0 }}>{d.rationale}</p>
              {d.suspect_change && (
                <div className="small">
                  Suspect change: <code>{d.suspect_change}</code>
                </div>
              )}
              {d.notes.length > 0 && <div className="small faint">{d.notes.join(" · ")}</div>}
            </section>
          )}

          {ev && (
            <section className="card stack">
              <h3>Evidence</h3>
              <div className="metric-grid">
                {METRICS.filter(([k]) => ev.metrics[k]?.length).map(([k, label, unit]) => (
                  <Sparkline key={k} points={ev.metrics[k]} label={label} unit={unit ?? ""} />
                ))}
              </div>
              <div>
                <h3 style={{ marginBottom: 6 }}>Errors by kind</h3>
                <div className="row">
                  {Object.entries(ev.logs.by_error_kind).map(([k, n]) => (
                    <span key={k} className="chip">
                      {k} {n}
                    </span>
                  ))}
                  {Object.keys(ev.logs.by_error_kind).length === 0 && <span className="faint">none</span>}
                </div>
              </div>
              <div>
                <h3 style={{ marginBottom: 6 }}>What changed</h3>
                {ev.changes.length === 0 ? (
                  <div className="faint small">No changes in the window.</div>
                ) : (
                  <table>
                    <tbody>
                      {ev.changes.map((c) => (
                        <tr key={c.id}>
                          <td className="mono small">{c.id}</td>
                          <td className="small">
                            <span className="chip">{c.source}</span> {c.summary}
                            <div className="faint">
                              {Object.entries(c.keys)
                                .map(([k, [a, b]]) => `${k}: ${a ?? "∅"} → ${b ?? "∅"}`)
                                .join(" · ")}
                            </div>
                          </td>
                          <td className="small muted">{c.author}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <h3 style={{ marginBottom: 6 }}>Top log templates</h3>
                <table>
                  <tbody>
                    {ev.logs.templates.slice(0, 6).map((t, i) => (
                      <tr key={i}>
                        <td className="small mono">{t.count}</td>
                        <td className="small">
                          <span className={`chip ${t.level === "ERROR" ? "red" : ""}`}>{t.level}</span>{" "}
                          <span className="mono">{t.exemplar}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>

        <div className="stack">
          <section className="card">
            <h3 style={{ marginBottom: 8 }}>Timeline</h3>
            <ul className="timeline">
              {inc.timeline.map((t, i) => (
                <li key={i}>
                  <span className="mono faint">{t.ts.slice(11, 19)}</span>
                  <span className="mono small">{t.kind}</span>
                  <span className="small">{t.detail}</span>
                </li>
              ))}
            </ul>
          </section>

          {inc.verify && (
            <section className="card stack">
              <h3>Verification ({inc.verify.attempts} attempt{inc.verify.attempts === 1 ? "" : "s"})</h3>
              {inc.verify.checks.map((c) => (
                <div key={c.name} className="row small">
                  <span className={`chip ${c.ok ? "green" : "red"}`}>{c.ok ? "PASS" : "FAIL"}</span>
                  <code>{c.name}</code>
                  <span className="muted">{c.detail}</span>
                </div>
              ))}
            </section>
          )}

          {inc.approvals.length > 0 && (
            <section className="card stack">
              <h3>Approvals</h3>
              {inc.approvals.map((a) => (
                <div key={a.id} className="small">
                  <span className={`chip ${a.source === "contract" ? "violet" : "green"}`}>{a.source}</span>{" "}
                  <code>{a.action}</code> by <b>{a.approver}</b> at {a.granted_at.slice(11, 19)}
                  {a.used_at ? " · used" : " · unused"} · evidence <code>{a.evidence_digest}</code>
                </div>
              ))}
            </section>
          )}

          {inc.status === "resolved" && inc.approvals.some((a) => a.source === "human") && (
            <section className="card stack">
              <h3>Don't wake me for this again</h3>
              <p className="small muted" style={{ margin: 0 }}>
                Grant a standing contract: the same alert with the same diagnosis may run exactly this fix, with these
                params, for a limited time and number of uses. Reverts can never be contracted.
              </p>
              <div className="row">
                <label className="small">
                  days <input type="number" min={1} max={30} value={days} onChange={(e) => setDays(+e.target.value)} style={{ width: 70 }} />
                </label>
                <label className="small">
                  uses <input type="number" min={1} max={20} value={uses} onChange={(e) => setUses(+e.target.value)} style={{ width: 70 }} />
                </label>
                <button onClick={grant} disabled={!getToken()}>
                  Grant contract
                </button>
              </div>
              {msg && <div className="small">{msg}</div>}
            </section>
          )}

          {inc.postmortem && (
            <section className="card stack">
              <h3>Postmortem draft</h3>
              <pre className="md">{inc.postmortem}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
