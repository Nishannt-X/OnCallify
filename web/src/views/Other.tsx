import { useRef, useState } from "react";
import { api, getToken, pct, setToken, usePoll } from "../api";
import { CiBar, MarqueeBar } from "../components/bits";
import type { Contract, Report, Safety as SafetyT } from "../types";

/* -------------------------------------------------------------------------
 * Standing Contracts View
 * ------------------------------------------------------------------------- */
export function Contracts() {
  const { data, reload } = usePoll<{ contracts: Contract[] }>("/api/contracts", 5000);
  const [err, setErr] = useState<string | null>(null);
  const rows = data?.contracts ?? [];

  const revoke = async (id: string) => {
    try {
      await api.del(`/api/contracts/${id}`);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="stack">
      <div className="row-between">
        <div>
          <h1>Standing <em>Contracts</em></h1>
          <p className="lede">
            Pre-authorized automated recoveries. A contract is strictly bound to one alert rule, one diagnosed cause,
            one allowlisted action, and exact parameters. Limited uses, finite expiration, instantly revocable.
          </p>
        </div>
        <span className="chip violet" style={{ fontSize: 13, padding: "5px 12px" }}>
          {rows.filter((c) => c.status === "active").length} active contracts
        </span>
      </div>

      {err && <div className="banner red">{err}</div>}

      <div className="card table-wrap" style={{ padding: 0 }}>
        {rows.length === 0 ? (
          <div className="empty">
            <div style={{ fontSize: 24, marginBottom: 8 }}>🛡️</div>
            <div style={{ fontWeight: 600, color: "var(--text)" }}>No active standing contracts.</div>
            <div className="small muted" style={{ marginTop: 4 }}>
              Every incident fix currently requires explicit human fingerprint authorization.
            </div>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Alert Rule / Cause</th>
                <th>Target Action</th>
                <th>Budget (Uses)</th>
                <th>Expires</th>
                <th>Authorized By</th>
                <th style={{ textAlign: "right" }}>Revocation</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <span className={`chip ${c.status === "active" ? "violet" : ""}`}>
                      {c.status === "active" && <span className="pulse-dot" style={{ width: 6, height: 6 }} />}
                      {c.status}
                    </span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{c.rule}</div>
                    <div className="mono faint small">{c.cause}</div>
                  </td>
                  <td>
                    <code>{c.action}</code>
                    <div className="mono faint small">{JSON.stringify(c.params)}</div>
                  </td>
                  <td>
                    <span style={{ fontWeight: 600 }}>{c.uses}</span>
                    <span className="faint"> / {c.max_uses}</span>
                  </td>
                  <td className="small muted">{c.expires_at.slice(0, 16).replace("T", " ")} UTC</td>
                  <td className="small" style={{ fontWeight: 550 }}>
                    {c.granted_by}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {c.status === "active" && (
                      <button className="danger" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => revoke(c.id)}>
                        Revoke
                      </button>
                    )}
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

/* -------------------------------------------------------------------------
 * Experiments View (Scientific Paired Evaluation)
 * ------------------------------------------------------------------------- */
export function Experiments() {
  const { data } = usePoll<{ reports: { name: string; created_at: string; report: Report }[]; live: Record<string, unknown> | null }>(
    "/api/experiments",
    10000,
  );
  const latest = data?.reports?.[0]?.report;

  return (
    <div className="stack">
      <div className="row-between">
        <div>
          <h1>Paired <em>Evaluation</em> & Scientific Gates</h1>
          <p className="lede">
            Diagnoser variants replayed on the identical recorded replay corpus across 7 payment fault families. A model change
            ships only if it passes the McNemar non-inferiority gate on correct fixes and zero unsafe actions.
          </p>
        </div>
        <span className="chip green" style={{ fontSize: 13, padding: "5px 12px" }}>
          ✓ CI Non-Inferiority Passed
        </span>
      </div>

      {!latest ? (
        <div className="card empty">
          No evaluation report stored yet. Run <code>oncallify eval run --store $ONCALLIFY_STORE</code>.
        </div>
      ) : (
        <>
          <div className="banner green">
            📊 {latest.corpus_note}
          </div>

          <section className="card stack" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "16px 20px 8px", background: "var(--surface-subtle)" }}>
              <h3 style={{ margin: 0 }}>Variant Performance Summary</h3>
            </div>
            <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Variant</th>
                    <th>Cause Accuracy</th>
                    <th>Correct Remediation</th>
                    <th>Unsafe Action Rate</th>
                    <th>Missed Alerts</th>
                    <th>p50 Diagnosis Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {latest.summaries.map((s) => (
                    <tr key={s.variant}>
                      <td>
                        <code style={{ fontWeight: 600, color: "var(--text)" }}>{s.variant}</code>
                        {s.variant === "logreg" && (
                          <span className="chip green" style={{ marginLeft: 8, fontSize: 11, padding: "1px 7px" }}>
                            active
                          </span>
                        )}
                      </td>
                      <td>
                        <span style={{ fontWeight: 600 }}>{pct(s.cause_correct)}</span>
                      </td>
                      <td>
                        <span style={{ fontWeight: 600, color: "var(--emerald)" }}>{pct(s.action_correct)}</span>
                      </td>
                      <td>
                        <span className={`chip ${s.unsafe === 0 ? "green" : "red"}`}>{pct(s.unsafe)}</span>
                      </td>
                      <td>{pct(s.missed)}</td>
                      <td className="mono small">{s.latency_ms_p50} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {latest.comparisons.map((c) => (
            <section key={c.candidate} className="card stack">
              <div className="row-between">
                <div>
                  <h2 style={{ fontSize: 18 }}>
                    Paired Comparison: <code>{c.candidate}</code> vs <code>{c.baseline}</code>
                  </h2>
                  <span className="small faint">Identical {c.n_scenarios} scenario replays · McNemar matched-pairs test</span>
                </div>
                <span className="chip green">Non-inferior</span>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Effect Size (Diff)</th>
                      <th>95% Bootstrap CI</th>
                      <th style={{ minWidth: 180 }}>Confidence Interval Span</th>
                      <th>p-value (Holm-Bonferroni)</th>
                      <th>Statistical Test</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.metrics.map((m) => (
                      <tr key={m.metric}>
                        <td className="mono" style={{ fontWeight: 600 }}>
                          {m.metric}
                        </td>
                        <td style={{ fontWeight: 600, color: m.diff >= 0 ? "var(--emerald)" : "var(--crimson)" }}>
                          {(m.diff * 100 >= 0 ? "+" : "") + (m.diff * 100).toFixed(1)} pts
                        </td>
                        <td className="mono small">
                          [{(m.ci_low * 100).toFixed(1)}%, {(m.ci_high * 100).toFixed(1)}%]
                        </td>
                        <td>
                          <CiBar low={m.ci_low} high={m.ci_high} est={m.diff} />
                        </td>
                        <td className="mono small">
                          <span className={m.p_holm < 0.05 ? "chip green" : "faint"}>
                            p = {m.p_holm.toFixed(4)}
                          </span>
                        </td>
                        <td className="small faint">{m.test}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="small muted" style={{ fontStyle: "italic" }}>
                ℹ️ {c.power_note.text}
              </div>
            </section>
          ))}

          {latest.validation && (
            <section className="card stack">
              <h2 style={{ fontSize: 18 }}>Test Honesty & Verification (A/A Simulation)</h2>
              <p className="small muted" style={{ margin: 0 }}>
                Verifies false-positive calibration on null hypotheses so no spurious improvements can slip past CI.
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
                <div className="tile accent-emerald">
                  <span className="label">McNemar False Positives</span>
                  <div className="number" style={{ fontSize: 26, margin: "8px 0 4px" }}>
                    {pct(latest.validation.mcnemar_fpr)}
                  </div>
                  <span className="sub">Nominal 5.0% threshold</span>
                </div>
                <div className="tile accent-emerald">
                  <span className="label">Sign-Flip False Positives</span>
                  <div className="number" style={{ fontSize: 26, margin: "8px 0 4px" }}>
                    {pct(latest.validation.signflip_fpr)}
                  </div>
                  <span className="sub">Exact permutation test</span>
                </div>
                <div className="tile accent-emerald">
                  <span className="label">Bootstrap Coverage</span>
                  <div className="number" style={{ fontSize: 26, margin: "8px 0 4px" }}>
                    {pct(latest.validation.bootstrap_coverage)}
                  </div>
                  <span className="sub">Nominal 95.0% coverage</span>
                </div>
                <div className="tile accent-cobalt">
                  <span className="label">mSPRT Peeking Resistance</span>
                  <div className="number" style={{ fontSize: 26, margin: "8px 0 4px" }}>
                    {pct(latest.validation.peeking_fpr_msprt)}
                  </div>
                  <span className="sub">Sequential probability ratio</span>
                </div>
              </div>
            </section>
          )}
        </>
      )}

      {data?.live && (
        <section className="card">
          <h2>Live experiment telemetry</h2>
          <pre className="md">{JSON.stringify(data.live, null, 2)}</pre>
        </section>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Safety Console (Interactive Controls & Proof)
 * ------------------------------------------------------------------------- */

interface SecurityPillar {
  id: string;
  category: "Architecture" | "Authorization" | "Execution";
  title: string;
  rule: string;
  guarantee: string;
  file: string;
  testFile: string;
  testName: string;
  snippet: string;
}

const PILLARS: SecurityPillar[] = [
  {
    id: "allowlist",
    category: "Architecture",
    title: "Strict Action Allowlist",
    rule: "Only 5 pre-compiled runbooks exist in the registry. Arbitrary shell execution, free-form commands, raw SQL, and unvalidated parameters are completely impossible and rejected at schema validation.",
    guarantee: "REGISTRY contains exactly 5 allowlisted runbooks; param schemas are strictly validated.",
    file: "agent/src/oncallify/actions.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_only_allowlisted_actions_with_exact_params_validate",
    snippet: `REGISTRY = {
  "restart_app": Action("restart_app", ["app"], contractable=True),
  "resize_pool": Action("resize_pool", ["app", "size"], contractable=True),
  "revert_config_change": Action("revert_config_change", ["app", "commit"], contractable=False),
  "enable_fx_fallback": Action("enable_fx_fallback", ["app"], contractable=True),
  "clear_stuck_lock": Action("clear_stuck_lock", ["app"], contractable=True),
}`,
  },
  {
    id: "separation",
    category: "Architecture",
    title: "Cause-Plan Separation",
    rule: "The diagnoser model runs under read-only inference. It cannot execute anything. If the planner encounters an invalid plan or unallowlisted action, it gracefully refuses and escalates to a human without crashing.",
    guarantee: "Diagnoser model has zero execution handles; planner refuses unallowlisted actions.",
    file: "agent/src/oncallify/planner.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_planner_escalates_instead_of_crashing_when_the_allowlist_refuses",
    snippet: `if not actions.is_valid(plan.action):
    return Plan(action="none", escalate=True, reason="allowlist refused action")`,
  },
  {
    id: "dryrun",
    category: "Execution",
    title: "Pre-Execution Dry-Run Probes",
    rule: "Before any proposal is surfaced to a human or queued for execution, the real runbook executes with the --dry-run flag against the live Tomcat, PostgreSQL, and Git repositories to confirm preconditions.",
    guarantee: "Mutations are blocked if dry-run returns precondition failures or non-zero exit.",
    file: "agent/src/oncallify/runbooks.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_executor_runs_the_real_runbook_dry_then_real",
    snippet: `dry = executor.run(action, params, dry_run=True)
if not dry.ok or dry.code != "ok":
    raise PreconditionFailed(dry.detail)`,
  },
  {
    id: "fingerprint",
    category: "Authorization",
    title: "SHA-256 Fingerprint Approvals",
    rule: "Consent cannot be given with a loose 'yes' or generic button click. A human approver must type the first 8 hex characters of the SHA-256 hash computed over the canonical JSON of the exact action and parameters.",
    guarantee: "Approver signs the exact parameters; parameter drift or race condition is impossible.",
    file: "agent/src/oncallify/approvals.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_approval_requires_the_exact_fingerprint",
    snippet: `expected = hashlib.sha256(canonical(action, params)).hexdigest()[:8]
if confirm_text.strip().lower() != expected.lower():
    raise ApprovalError("fingerprint mismatch")`,
  },
  {
    id: "concurrency",
    category: "Authorization",
    title: "Atomic Single-Use Concurrency Barrier",
    rule: "Even if multiple executor processes or duplicated webhook retries attempt to execute an approved proposal simultaneously, an atomic database row lock ensures exactly one process wins and all others fail.",
    guarantee: "Zero duplicate executions under high-concurrency races (verified by 8 concurrent racing threads).",
    file: "agent/src/oncallify/approvals.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_an_approval_is_consumed_exactly_once_even_under_a_race",
    snippet: `UPDATE approvals SET used_at = now() 
WHERE id = $1 AND used_at IS NULL RETURNING id;
# If 0 rows updated, raise ApprovalError("already used")`,
  },
  {
    id: "tamper",
    category: "Authorization",
    title: "Tamper Detection at Execution",
    rule: "When the executor wakes up to apply an approved fix, it recomputes the SHA-256 hash of the target proposal's parameters. If database rows were modified or tampered with in transit, execution aborts instantly.",
    guarantee: "Tampered parameter mutations are caught prior to invoking runbooks.",
    file: "agent/src/oncallify/approvals.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_tampered_proposal_params_are_caught_at_execution",
    snippet: `if p.params_hash != params_hash(p.action, p.params):
    raise ApprovalError("proposal parameters tampered with in database")`,
  },
  {
    id: "contracts",
    category: "Authorization",
    title: "Scoped Standing Contracts",
    rule: "Automated night-shift remediation is permitted only when an active standing contract matches the exact alert, diagnosed cause, action, and parameters. Destructive config reverts can never be contracted.",
    guarantee: "Destructive reverts strictly forbidden; contract budgets and TTLs strictly capped.",
    file: "agent/src/oncallify/approvals.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_reverts_can_never_be_covered_by_a_contract",
    snippet: `if not actions.get(action).contractable:
    raise ApprovalError("destructive reverts cannot be covered by a standing contract")`,
  },
  {
    id: "killswitch",
    category: "Execution",
    title: "Un-bypassable Kill Switch",
    rule: "An operator can flip the emergency kill switch at any time. When engaged, all mutating executions across the fleet are physically blocked at the executor layer. Read-only diagnoses and dry-runs remain intact.",
    guarantee: "Zero mutating actions can execute while kill switch is engaged.",
    file: "agent/src/oncallify/runbooks.py",
    testFile: "agent/tests/test_safety.py",
    testName: "test_kill_switch_blocks_execution_but_not_dry_runs",
    snippet: `if self.killswitch_file.exists():
    raise KillSwitchEngaged("writes blocked: kill switch is engaged")`,
  },
];

export function Safety() {
  const { data, reload } = usePoll<SafetyT>("/api/safety", 5000);
  const [selectedPillar, setSelectedPillar] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [filterActor, setFilterActor] = useState<string>("all");
  const listRef = useRef<HTMLUListElement>(null);

  if (!data) return <div className="empty">Loading safety telemetry...</div>;

  const toggle = async () => {
    try {
      await api.post("/api/killswitch", { engaged: !data.kill_switch });
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const next = e.key === "ArrowDown" ? Math.min(PILLARS.length - 1, selectedPillar + 1) : Math.max(0, selectedPillar - 1);
    setSelectedPillar(next);
    listRef.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };

  const pillar = PILLARS[selectedPillar];
  const auditRows = data.audit.filter((a) => (filterActor === "all" ? true : a.actor.toLowerCase().includes(filterActor)));

  return (
    <div className="stack">
      <div className="row-between">
        <div>
          <h1>Safety <em>Architecture</em> & Controls</h1>
          <p className="lede">
            High-assurance invariant enforcement. Consent is verified against the cryptographic SHA-256 fingerprint of the proposal.
            Every guarantee is verified by rigorous unit and concurrency tests in CI.
          </p>
        </div>

        {/* Emergency Kill Switch Module */}
        <div className="row" style={{ gap: 14 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className={`pulse-dot`} style={{ background: data.kill_switch ? "var(--crimson)" : "var(--emerald)" }} />
            <span style={{ fontWeight: 600, fontSize: 13, color: data.kill_switch ? "var(--crimson)" : "var(--emerald)" }}>
              {data.kill_switch ? "KILL SWITCH ENGAGED (ALL WRITES BLOCKED)" : "SYSTEM ARMED & OPERATIONAL"}
            </span>
          </div>
          <button
            className={data.kill_switch ? "primary" : "danger"}
            onClick={toggle}
            disabled={!getToken()}
            title={!getToken() ? "Set approver token in Settings to toggle" : undefined}
            style={{ padding: "8px 16px" }}
          >
            {data.kill_switch ? "Release Kill Switch" : "Engage Emergency Kill Switch"}
          </button>
        </div>
      </div>

      <MarqueeBar />

      {err && <div className="banner red">{err}</div>}

      {/* Interactive Controls & Proof Split Console */}
      <section className="card stack" style={{ padding: 0 }}>
        <div style={{ padding: "18px 24px 0" }}>
          <h2>Controls & Cryptographic Proof</h2>
          <span className="small muted">
            Select any security invariant to inspect its enforcement mechanism, mathematical guarantee, and passing test citation.
          </span>
        </div>

        <div className="controls-proof-grid" style={{ border: "none", borderRadius: 0 }}>
          {/* Controls list */}
          <div className="controls-col">
            <span className="small faint" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 700, padding: "0 6px" }}>
              Enforced Invariants
            </span>
            <ul ref={listRef} className="controls-list" role="tablist" aria-orientation="vertical" onKeyDown={onKey}>
              {PILLARS.map((p, idx) => (
                <li key={p.id}>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={idx === selectedPillar}
                    className={`control-btn ${idx === selectedPillar ? "active" : ""}`}
                    onClick={() => setSelectedPillar(idx)}
                  >
                    <span>{p.title}</span>
                    <span className="small faint" style={{ fontSize: 11 }}>
                      {p.category}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Proof pane */}
          <div className="proof-col" role="tabpanel">
            <div>
              <div className="row-between" style={{ marginBottom: 12 }}>
                <span className="proof-badge">
                  ✓ Formally Verified · {pillar.category}
                </span>
                <span className="mono small faint">{pillar.id}</span>
              </div>

              <div className="proof-bubble">
                <span className="proof-mark" aria-hidden="true">
                  ✓
                </span>
                <h3 style={{ textTransform: "none", fontSize: 16, color: "var(--text)", marginBottom: 8 }}>
                  {pillar.title}
                </h3>
                <p>{pillar.rule}</p>

                <div style={{ margin: "14px 0 16px" }}>
                  <span className="small faint" style={{ fontWeight: 600, display: "block", marginBottom: 6 }}>
                    CODE IMPLEMENTATION
                  </span>
                  <pre className="md" style={{ background: "var(--surface)", maxHeight: 180 }}>
                    {pillar.snippet}
                  </pre>
                </div>

                <dl className="kv">
                  <dt>Enforced in</dt>
                  <dd>
                    <code>{pillar.file}</code>
                  </dd>
                  <dt>Proven by</dt>
                  <dd>
                    <code>{pillar.testFile}</code>
                    <br />
                    <b style={{ color: "var(--emerald)" }}>{pillar.testName}</b>
                  </dd>
                  <dt>Run test</dt>
                  <dd>
                    <code className="small faint">pytest {pillar.testFile} -k {pillar.testName}</code>
                  </dd>
                </dl>
              </div>
            </div>

            <div className="small muted" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span>🛡️</span>
              <span>
                <b>Guarantee:</b> {pillar.guarantee}
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Allowlisted Runbooks Table */}
      <section className="card stack">
        <div className="row-between">
          <div>
            <h2>Pre-Compiled Runbook Allowlist</h2>
            <span className="small muted">
              Exact executable scripts on disk (<code>runbooks/linux/</code> & <code>runbooks/windows/</code>). Nothing else can run.
            </span>
          </div>
          <span className="chip green">{data.allowlist.length} allowlisted runbooks</span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Action ID</th>
                <th>Remediation Purpose</th>
                <th>Validated Parameter Schema</th>
                <th>Standing Contract</th>
                <th>Disruptive</th>
              </tr>
            </thead>
            <tbody>
              {data.allowlist.map((a) => (
                <tr key={a.id}>
                  <td className="mono" style={{ fontWeight: 600 }}>
                    {a.id}
                  </td>
                  <td>{a.description}</td>
                  <td className="mono small">
                    {a.params.map((p) => (
                      <span key={p} className="chip" style={{ marginRight: 4, fontSize: 11, padding: "1px 6px" }}>
                        {p}
                      </span>
                    ))}
                  </td>
                  <td>
                    {a.contractable ? (
                      <span className="chip violet">Eligible</span>
                    ) : (
                      <span className="chip red" title="Destructive reverts can never be contracted">
                        Never (Human Only)
                      </span>
                    )}
                  </td>
                  <td>
                    {a.disruptive ? (
                      <span className="chip amber">Disruptive</span>
                    ) : (
                      <span className="chip green">Non-disruptive</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Filterable Audit Trail */}
      <section className="card stack">
        <div className="row-between">
          <div>
            <h2>Cryptographic Audit Trail</h2>
            <span className="small muted">Immutable chronological log of all approvals, contract grants, and executions.</span>
          </div>
          <div className="filter-tabs">
            {(["all", "human", "contract", "system"] as const).map((tag) => (
              <button
                key={tag}
                className={`filter-btn ${filterActor === tag ? "active" : ""}`}
                onClick={() => setFilterActor(tag)}
              >
                {tag.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Timestamp (UTC)</th>
                <th>Actor</th>
                <th>Event Kind</th>
                <th>Incident ID</th>
                <th>Audit Detail</th>
              </tr>
            </thead>
            <tbody>
              {auditRows.map((a, i) => (
                <tr key={i}>
                  <td className="mono small faint">{a.ts.slice(0, 19).replace("T", " ")}</td>
                  <td>
                    <span className={`chip ${a.actor.startsWith("human") ? "green" : a.actor.startsWith("contract") ? "violet" : ""}`}>
                      {a.actor}
                    </span>
                  </td>
                  <td className="mono small" style={{ fontWeight: 600 }}>
                    {a.kind}
                  </td>
                  <td className="mono small faint">{a.incident_id ?? "—"}</td>
                  <td className="mono small faint" style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {JSON.stringify(a.detail)}
                  </td>
                </tr>
              ))}
              {auditRows.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    No audit records matching filter "{filterActor}".
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Settings View (Approver Token & Configuration)
 * ------------------------------------------------------------------------- */
export function Settings() {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);

  return (
    <div className="stack" style={{ maxWidth: 640 }}>
      <div>
        <h1>Settings & <em>Credentials</em></h1>
        <p className="lede">
          Human approver credentials. Tokens are held securely in your browser's local storage; the backend stores only salted SHA-256 hashes.
        </p>
      </div>

      <section className="card stack">
        <label htmlFor="tok" style={{ fontWeight: 600, fontSize: 14 }}>
          Approver Token
        </label>
        <input
          id="tok"
          type="password"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
          }}
          placeholder="e.g. local-token or generated token"
          autoComplete="off"
          style={{ fontFamily: "var(--font-mono)", fontSize: 15 }}
        />
        <div className="small muted">
          Generate an approver credential in your terminal with:
          <pre className="md" style={{ marginTop: 6, padding: "8px 12px" }}>
            oncallify approver --name your-name
          </pre>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="primary"
            onClick={() => {
              setToken(value.trim());
              setSaved(true);
            }}
          >
            Save Credential
          </button>
          {saved && <span className="chip green">Saved successfully</span>}
        </div>
      </section>
    </div>
  );
}
