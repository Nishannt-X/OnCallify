import { useState } from "react";
import type { Proposal } from "../types";

interface Props {
  proposal: Proposal;
  hasToken: boolean;
  onApprove: (confirm: string) => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  alertResolvedMeanwhile?: boolean;
}

/**
 * The single gateway where a human authorizes mutating production.
 * Approve stays strictly disabled until the exact fingerprint (first 8 chars of SHA-256 hash)
 * is entered: you authorize THIS action with THESE exact parameters, not a vague "yes".
 */
export function ApprovalPanel({ proposal, hasToken, onApprove, onReject, alertResolvedMeanwhile }: Props) {
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const matches = confirm.trim().toLowerCase() === proposal.fingerprint.toLowerCase();
  const expired = Date.parse(proposal.expires_at) < Date.now();

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="proposal stack" aria-label="proposed fix">
      <div className="row-between">
        <div className="row" style={{ gap: 12 }}>
          <span className="chip amber" style={{ fontSize: 13, padding: "4px 12px" }}>
            <span className="pulse-dot" style={{ width: 7, height: 7 }} />
            Needs your approval
          </span>
          <h2 style={{ fontSize: 20 }}>
            Proposed Fix: <code>{proposal.action}</code>
          </h2>
        </div>
        <span className="mono faint small">proposal {proposal.id}</span>
      </div>

      <dl className="kv" style={{ background: "var(--surface)", padding: 16, borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
        <dt>Parameters</dt>
        <dd>
          <code>{JSON.stringify(proposal.params)}</code>
        </dd>
        <dt>Blast Radius</dt>
        <dd style={{ fontWeight: 550 }}>{proposal.blast_radius}</dd>
        <dt>Dry-Run Probe</dt>
        <dd>
          <span className={`chip ${proposal.dry_run.ok ? "green" : "red"}`} style={{ marginRight: 8 }}>
            {proposal.dry_run.ok ? "PASSED" : "FAILED"}: {proposal.dry_run.code}
          </span>
          <span className="muted">{proposal.dry_run.detail}</span>
        </dd>
        <dt>Rationale</dt>
        <dd className="muted">{proposal.rationale}</dd>
      </dl>

      {alertResolvedMeanwhile && (
        <div className="banner red">
          ⚠️ The alert resolved on its own while waiting. Re-check before approving.
        </div>
      )}
      {expired && <div className="banner red">⚠️ This proposal has expired. Nothing can be approved.</div>}
      {!hasToken && <div className="banner amber">Set your approver token in Settings to approve or reject.</div>}

      <div className="card" style={{ background: "var(--surface)", border: "1px solid var(--line)" }}>
        <div className="stack">
          <div className="row-between">
            <label htmlFor="fp" className="small" style={{ fontWeight: 600 }}>
              Type <code>{proposal.fingerprint}</code> to approve
            </label>
            <span className="small faint">Guarantees parameter tamper prevention</span>
          </div>
          <div className="row">
            <input
              id="fp"
              className="fingerprint"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={proposal.fingerprint}
              autoComplete="off"
              spellCheck={false}
              style={{ fontSize: 16, padding: "8px 14px", width: "16ch" }}
            />
            <button
              className="primary"
              disabled={!matches || !hasToken || busy || expired}
              onClick={() => run(() => onApprove(confirm.trim()))}
              style={{ padding: "9px 20px" }}
            >
              {busy ? "Authorizing..." : "Approve and run"}
            </button>
            {matches && <span className="chip green">Fingerprint verified</span>}
          </div>

          <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14, marginTop: 4 }}>
            <div className="row">
              <input
                aria-label="reason to reject"
                style={{ flex: 1, minWidth: 240 }}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason to reject (required if rejecting)"
              />
              <button
                className="danger"
                disabled={!reason.trim() || !hasToken || busy}
                onClick={() => run(() => onReject(reason.trim()))}
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
    </section>
  );
}
