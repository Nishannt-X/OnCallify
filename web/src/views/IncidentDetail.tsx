import { useState } from "react";
import { api, getToken, usePoll } from "../api";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { Confidence, Sparkline, StatusChip } from "../components/bits";
import type { Incident } from "../types";

const METRICS: [string, string, string?][] = [
  ["errors:pool_exhausted", "Pool Exhausted Errors"],
  ["errors:fx_unavailable", "FX Unavailable Errors"],
  ["errors:fx_timeout", "FX Timeout Errors"],
  ["errors:db_auth", "DB Auth Errors"],
  ["ledgerline_pool_active", "Active Pool Connections"],
  ["ledgerline_pool_max", "Max Pool Limit"],
  ["tomcat_threads_busy", "Tomcat Threads Busy"],
  ["jvm_heap_used_bytes", "JVM Heap Memory", "B"],
  ["transfer_latency_mean_s", "Transfer Latency", "s"],
  ["scrape_failed", "Health Probe Scrape Failed"],
];

export function IncidentDetail({ id, back }: { id: string; back: () => void }) {
  const { data: inc, error, reload } = usePoll<Incident>(`/api/incidents/${id}`, 2500);
  const [days, setDays] = useState(7);
  const [uses, setUses] = useState(3);
  const [msg, setMsg] = useState<string | null>(null);

  if (error && !inc) return <div className="banner red">{error}</div>;
  if (!inc) return <div className="empty">Loading incident telemetry...</div>;

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
      setMsg(`✓ Contract ${r.contract_id} granted: identical future alerts will recover autonomously.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="stack">
      {/* Top action & header */}
      <div className="row-between">
        <div className="row" style={{ gap: 12 }}>
          <button onClick={back} style={{ padding: "6px 12px" }}>
            ← All Incidents
          </button>
          <div>
            <h1 style={{ fontSize: 26 }}>{inc.title}</h1>
            <div className="mono faint small" style={{ marginTop: 2 }}>
              {inc.id} · opened {inc.opened_at.replace("T", " ").slice(0, 19)} UTC
              {inc.closed_at ? ` · resolved ${inc.closed_at.replace("T", " ").slice(0, 19)} UTC` : ""}
            </div>
          </div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <a
            href={`#/copilot/${inc.id}`}
            className="btn"
            style={{ padding: "6px 12px", fontSize: 13, textDecoration: "none" }}
            title="Open conversational Copilot for this incident"
          >
            💬 Ask Granite Copilot
          </a>
          <StatusChip status={inc.status} />
        </div>
      </div>

      {/* Proposed Fix Panel */}
      {inc.status === "awaiting_approval" && inc.proposal && (
        <ApprovalPanel
          proposal={inc.proposal}
          hasToken={!!getToken()}
          onApprove={approve}
          onReject={reject}
          alertResolvedMeanwhile={resolvedMeanwhile}
        />
      )}

      {/* Main 2-column layout */}
      <div className="grid2">
        {/* Left Column: Root Cause & Diagnostic Evidence */}
        <div className="stack">
          {d && (
            <section className="card stack">
              <div className="row-between">
                <div>
                  <h3 style={{ marginBottom: 4 }}>Root Cause Diagnosis</h3>
                  <code style={{ fontSize: 17, color: "var(--emerald)", fontWeight: 700 }}>{d.cause}</code>
                </div>
                <span className="chip green" style={{ fontSize: 12 }}>
                  Model: {d.variant}
                </span>
              </div>

              <Confidence value={d.confidence} />

              <div style={{ background: "var(--surface-subtle)", padding: 14, borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                <div className="small faint" style={{ fontWeight: 600, textTransform: "uppercase", marginBottom: 4 }}>
                  Inference Rationale
                </div>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>{d.rationale}</p>
              </div>

              {d.suspect_change && (
                <div className="small">
                  <b>Suspect Git Change:</b> <code>{d.suspect_change}</code>
                </div>
              )}

              {d.notes.length > 0 && (
                <div className="small faint">
                  <b>Telemetry Notes:</b> {d.notes.join(" · ")}
                </div>
              )}
            </section>
          )}

          {ev && (
            <section className="card stack">
              <div className="row-between">
                <h2>Diagnostic Evidence Window</h2>
                <span className="mono small faint">
                  {ev.window_start.slice(11, 19)} → {ev.window_end.slice(11, 19)} UTC
                </span>
              </div>

              {/* Sparklines */}
              <div>
                <h3 style={{ marginBottom: 10 }}>Time-Series Health Telemetry</h3>
                <div className="metric-grid">
                  {METRICS.filter(([k]) => ev.metrics[k]?.length).map(([k, label, unit]) => (
                    <div key={k} style={{ background: "var(--surface-subtle)", padding: 12, borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                      <Sparkline points={ev.metrics[k]} label={label} unit={unit ?? ""} />
                    </div>
                  ))}
                </div>
              </div>

              {/* Log Error Breakdown */}
              <div>
                <h3 style={{ marginBottom: 8 }}>Error Frequency Distribution</h3>
                <div className="row" style={{ gap: 8 }}>
                  {Object.entries(ev.logs.by_error_kind).map(([k, n]) => (
                    <span key={k} className="chip red">
                      {k}: <b>{n}</b>
                    </span>
                  ))}
                  {Object.keys(ev.logs.by_error_kind).length === 0 && <span className="faint small">No error signatures detected</span>}
                </div>
              </div>

              {/* Config & Deployment Changes */}
              <div>
                <h3 style={{ marginBottom: 8 }}>Correlated Configuration Changes</h3>
                {ev.changes.length === 0 ? (
                  <div className="faint small">No code or configuration changes observed in this window.</div>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Commit SHA</th>
                          <th>Change Summary & Diff</th>
                          <th>Author</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ev.changes.map((c) => (
                          <tr key={c.id}>
                            <td className="mono small" style={{ fontWeight: 600 }}>
                              {c.id}
                            </td>
                            <td className="small">
                              <span className="chip" style={{ marginRight: 6 }}>
                                {c.source}
                              </span>
                              <b>{c.summary}</b>
                              <div className="faint mono" style={{ marginTop: 4 }}>
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
                  </div>
                )}
              </div>

              {/* Log exemplar templates */}
              <div>
                <h3 style={{ marginBottom: 8 }}>Clustered Log Exemplar Templates</h3>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Count</th>
                        <th>Level</th>
                        <th>Template Exemplar</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ev.logs.templates.slice(0, 6).map((t, i) => (
                        <tr key={i}>
                          <td className="mono small" style={{ fontWeight: 600 }}>
                            {t.count}×
                          </td>
                          <td>
                            <span className={`chip ${t.level === "ERROR" ? "red" : ""}`}>{t.level}</span>
                          </td>
                          <td className="mono small faint" style={{ wordBreak: "break-all" }}>
                            {t.exemplar}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          )}
        </div>

        {/* Right Column: Timeline, Verification & Approvals */}
        <div className="stack">
          {/* Incident Timeline */}
          <section className="card stack">
            <h3 style={{ margin: 0 }}>Chronological Timeline</h3>
            <ul className="timeline">
              {inc.timeline.map((t, i) => (
                <li key={i}>
                  <span className="mono faint">{t.ts.slice(11, 19)}</span>
                  <span className="mono small" style={{ fontWeight: 600, color: "var(--emerald)" }}>
                    {t.kind}
                  </span>
                  <span className="small">{t.detail}</span>
                </li>
              ))}
            </ul>
          </section>

          {/* Recovery Verification */}
          {inc.verify && (
            <section className="card stack">
              <div className="row-between">
                <h3 style={{ margin: 0 }}>Automated Recovery Verification</h3>
                <span className={`chip ${inc.verify.ok ? "green" : "red"}`}>
                  {inc.verify.ok ? "ALL CHECKS PASSED" : "FAILED"}
                </span>
              </div>
              <span className="small faint">
                Executed in {inc.verify.attempts} verification attempt{inc.verify.attempts === 1 ? "" : "s"}
              </span>
              <div className="stack-sm">
                {inc.verify.checks.map((c) => (
                  <div key={c.name} className="row-between" style={{ background: "var(--surface-subtle)", padding: "8px 12px", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className={`chip ${c.ok ? "green" : "red"}`} style={{ fontSize: 11 }}>
                        {c.ok ? "✓ PASS" : "✕ FAIL"}
                      </span>
                      <code>{c.name}</code>
                    </div>
                    <span className="small muted">{c.detail}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Approvals Record */}
          {inc.approvals.length > 0 && (
            <section className="card stack">
              <h3 style={{ margin: 0 }}>Authorization Record</h3>
              <div className="stack-sm">
                {inc.approvals.map((a) => (
                  <div key={a.id} className="small" style={{ background: "var(--surface-subtle)", padding: "10px 12px", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                    <div className="row-between" style={{ marginBottom: 4 }}>
                      <span className={`chip ${a.source === "contract" ? "violet" : "green"}`}>
                        {a.source.toUpperCase()} APPROVAL
                      </span>
                      <span className="mono faint">{a.granted_at.slice(11, 19)} UTC</span>
                    </div>
                    <div>
                      Authorized <code>{a.action}</code> by <b>{a.approver}</b>
                    </div>
                    <div className="mono faint small" style={{ marginTop: 2 }}>
                      Digest: {a.evidence_digest} · {a.used_at ? "Executed" : "Unused"}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Standing Contract Grant Form */}
          {inc.status === "resolved" && inc.approvals.some((a) => a.source === "human") && (
            <section className="card stack" style={{ border: "1.5px solid var(--amethyst)", background: "linear-gradient(180deg, var(--amethyst-soft) 0%, var(--surface) 100%)" }}>
              <div className="row" style={{ gap: 8 }}>
                <span style={{ fontSize: 20 }}>🛡️</span>
                <h3 style={{ margin: 0, color: "var(--amethyst)", textTransform: "none", fontSize: 16 }}>
                  Don't Wake Anyone For This Again
                </h3>
              </div>
              <p className="small muted" style={{ margin: 0 }}>
                Authorize a Standing Contract: identical future occurrences with the same diagnosed root cause will apply this exact fix autonomously without paging the on-call engineer.
              </p>
              <div className="row" style={{ gap: 12 }}>
                <label className="small" style={{ fontWeight: 600 }}>
                  TTL (Days)
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={days}
                    onChange={(e) => setDays(+e.target.value)}
                    style={{ width: 75, marginLeft: 6 }}
                  />
                </label>
                <label className="small" style={{ fontWeight: 600 }}>
                  Max Uses
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={uses}
                    onChange={(e) => setUses(+e.target.value)}
                    style={{ width: 75, marginLeft: 6 }}
                  />
                </label>
                <button className="primary" onClick={grant} disabled={!getToken()} style={{ background: "var(--amethyst)", borderColor: "var(--amethyst)" }}>
                  Grant Standing Contract
                </button>
              </div>
              {msg && <div className="banner green" style={{ fontSize: 13 }}>{msg}</div>}
            </section>
          )}

          {/* Postmortem Draft */}
          {inc.postmortem && (
            <section className="card stack">
              <h3 style={{ margin: 0 }}>Automated Postmortem Draft</h3>
              <pre className="md">{inc.postmortem}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
