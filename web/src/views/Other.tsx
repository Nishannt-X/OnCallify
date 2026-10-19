import { useState } from "react";
import { api, getToken, pct, setToken, usePoll } from "../api";
import { CiBar } from "../components/bits";
import type { Contract, Report, Safety as SafetyT } from "../types";

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
      <h1>Standing contracts</h1>
      <p className="muted" style={{ margin: 0 }}>
        Pre-approvals for one alert, one diagnosis, one action with exact params. Limited uses, limited days, revocable.
      </p>
      {err && <div className="error">{err}</div>}
      <div className="card table-wrap">
        {rows.length === 0 ? (
          <div className="empty">No contracts. Every fix needs a human.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Alert / cause</th>
                <th>Action</th>
                <th>Uses</th>
                <th>Expires</th>
                <th>Granted by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <span className={`chip ${c.status === "active" ? "violet" : ""}`}>{c.status}</span>
                  </td>
                  <td className="small">
                    {c.rule}
                    <div className="mono faint">{c.cause}</div>
                  </td>
                  <td>
                    <code>{c.action}</code> <span className="mono faint small">{JSON.stringify(c.params)}</span>
                  </td>
                  <td>
                    {c.uses} / {c.max_uses}
                  </td>
                  <td className="small">{c.expires_at.slice(0, 16).replace("T", " ")}</td>
                  <td className="small">{c.granted_by}</td>
                  <td>{c.status === "active" && <button className="danger" onClick={() => revoke(c.id)}>Revoke</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function Experiments() {
  const { data } = usePoll<{ reports: { name: string; created_at: string; report: Report }[]; live: Record<string, unknown> | null }>(
    "/api/experiments",
    10000,
  );
  const latest = data?.reports?.[0]?.report;
  return (
    <div className="stack">
      <h1>Experiments</h1>
      <p className="muted" style={{ margin: 0 }}>
        Diagnoser variants replayed on the same captured incidents (paired). A change ships only if it is non-inferior on
        correct fixes and on unsafe actions.
      </p>
      {!latest ? (
        <div className="card empty">
          No report stored yet. Run <code>oncallify eval run --store $ONCALLIFY_STORE</code>.
        </div>
      ) : (
        <>
          <div className="small muted">{latest.corpus_note}</div>
          <section className="card table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Variant</th>
                  <th>Cause correct</th>
                  <th>Correct fix</th>
                  <th>Unsafe</th>
                  <th>Missed</th>
                  <th>p50 latency</th>
                </tr>
              </thead>
              <tbody>
                {latest.summaries.map((s) => (
                  <tr key={s.variant}>
                    <td className="mono">{s.variant}</td>
                    <td>{pct(s.cause_correct)}</td>
                    <td>{pct(s.action_correct)}</td>
                    <td>{pct(s.unsafe)}</td>
                    <td>{pct(s.missed)}</td>
                    <td className="small">{s.latency_ms_p50} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          {latest.comparisons.map((c) => (
            <section key={c.candidate} className="card stack table-wrap">
              <h2>
                <code>{c.candidate}</code> vs <code>{c.baseline}</code>{" "}
                <span className="faint small">paired, {c.n_scenarios} scenarios</span>
              </h2>
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Diff</th>
                    <th>95% CI</th>
                    <th />
                    <th>p (Holm)</th>
                    <th>Test</th>
                  </tr>
                </thead>
                <tbody>
                  {c.metrics.map((m) => (
                    <tr key={m.metric}>
                      <td className="mono">{m.metric}</td>
                      <td>{(m.diff * 100).toFixed(1)} pts</td>
                      <td className="small">
                        [{(m.ci_low * 100).toFixed(1)}, {(m.ci_high * 100).toFixed(1)}]
                      </td>
                      <td>
                        <CiBar low={m.ci_low} high={m.ci_high} est={m.diff} />
                      </td>
                      <td className={m.p_holm < 0.05 ? "" : "faint"}>{m.p_holm.toFixed(4)}</td>
                      <td className="small faint">{m.test}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="small muted">{c.power_note.text}</div>
            </section>
          ))}
          {latest.validation && (
            <section className="card stack">
              <h2>Are the tests honest? (A/A simulation)</h2>
              <dl className="kv small">
                <dt>McNemar false positives</dt>
                <dd>{pct(latest.validation.mcnemar_fpr)} (nominal 5%)</dd>
                <dt>Sign-flip false positives</dt>
                <dd>{pct(latest.validation.signflip_fpr)} (nominal 5%)</dd>
                <dt>Bootstrap 95% coverage</dt>
                <dd>{pct(latest.validation.bootstrap_coverage)}</dd>
                <dt>Peeking, naive z-test</dt>
                <dd>{pct(latest.validation.peeking_fpr_naive)} false positives</dd>
                <dt>Peeking, mSPRT</dt>
                <dd>{pct(latest.validation.peeking_fpr_msprt)} false positives</dd>
              </dl>
            </section>
          )}
        </>
      )}
      {data?.live && (
        <section className="card">
          <h2>Live experiment</h2>
          <pre className="md">{JSON.stringify(data.live, null, 2)}</pre>
        </section>
      )}
    </div>
  );
}

export function Safety() {
  const { data, reload } = usePoll<SafetyT>("/api/safety", 5000);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return <div className="empty">Loading...</div>;
  const toggle = async () => {
    try {
      await api.post("/api/killswitch", { engaged: !data.kill_switch });
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Safety</h1>
        <div className="row">
          <span className={`chip ${data.kill_switch ? "red" : "green"}`}>
            kill switch {data.kill_switch ? "ENGAGED: nothing executes" : "off"}
          </span>
          <button className={data.kill_switch ? "" : "danger"} onClick={toggle} disabled={!getToken()}>
            {data.kill_switch ? "Release" : "Engage kill switch"}
          </button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}
      <section className="card stack">
        <h3>Rules enforced in code</h3>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {data.rules.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </section>
      <section className="card table-wrap">
        <h3 style={{ marginBottom: 8 }}>Allowlist</h3>
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>What it does</th>
              <th>Params</th>
              <th>Contract?</th>
            </tr>
          </thead>
          <tbody>
            {data.allowlist.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.id}</td>
                <td className="small">
                  {a.description} {a.disruptive && <span className="chip amber">disruptive</span>}
                </td>
                <td className="mono small">{a.params.join(", ")}</td>
                <td>{a.contractable ? <span className="chip violet">allowed</span> : <span className="chip">never</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card table-wrap">
        <h3 style={{ marginBottom: 8 }}>Audit log</h3>
        <table>
          <tbody>
            {data.audit.map((a, i) => (
              <tr key={i}>
                <td className="mono small faint">{a.ts.slice(0, 19).replace("T", " ")}</td>
                <td className="small">{a.actor}</td>
                <td className="mono small">{a.kind}</td>
                <td className="mono small faint">{a.incident_id ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

export function Settings() {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);
  return (
    <div className="stack" style={{ maxWidth: 560 }}>
      <h1>Settings</h1>
      <section className="card stack">
        <label htmlFor="tok">Approver token</label>
        <input id="tok" type="password" value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" />
        <div className="small muted">
          Create one with <code>oncallify approver --name you</code>. Stored only in this browser; the server keeps a hash.
        </div>
        <div className="row">
          <button
            className="primary"
            onClick={() => {
              setToken(value.trim());
              setSaved(true);
            }}
          >
            Save
          </button>
          {saved && <span className="small muted">Saved.</span>}
        </div>
      </section>
    </div>
  );
}
