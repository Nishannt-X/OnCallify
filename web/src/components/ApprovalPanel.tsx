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
 * The one place a human changes production. Approve stays disabled until the exact
 * fingerprint (first 8 chars of the params hash) is typed: you approve THIS action
 * with THESE parameters, not "yes, do it".
 */
export function ApprovalPanel({ proposal, hasToken, onApprove, onReject, alertResolvedMeanwhile }: Props) {
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const matches = confirm.trim().toLowerCase() === proposal.fingerprint;
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
    <section className="card proposal stack" aria-label="proposed fix">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>
          Proposed fix: <code>{proposal.action}</code>
        </h2>
        <span className="chip amber">needs your approval</span>
      </div>
      <dl className="kv">
        <dt>Params</dt>
        <dd>
          <code>{JSON.stringify(proposal.params)}</code>
        </dd>
        <dt>Blast radius</dt>
        <dd>{proposal.blast_radius}</dd>
        <dt>Dry run</dt>
        <dd>
          <span className={`chip ${proposal.dry_run.ok ? "green" : "red"}`}>{proposal.dry_run.code}</span>{" "}
          {proposal.dry_run.detail}
        </dd>
        <dt>Why</dt>
        <dd>{proposal.rationale}</dd>
      </dl>
      {alertResolvedMeanwhile && (
        <div className="banner red">The alert resolved on its own while waiting. Re-check before approving.</div>
      )}
      {expired && <div className="banner red">This proposal has expired. Nothing can be approved.</div>}
      {!hasToken && <div className="small error">Set your approver token in Settings to approve or reject.</div>}
      <div className="row">
        <label htmlFor="fp" className="small muted">
          Type <code>{proposal.fingerprint}</code> to approve
        </label>
        <input
          id="fp"
          className="fingerprint"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={proposal.fingerprint}
          autoComplete="off"
          spellCheck={false}
        />
        <button
          className="primary"
          disabled={!matches || !hasToken || busy || expired}
          onClick={() => run(() => onApprove(confirm.trim()))}
        >
          Approve and run
        </button>
      </div>
      <div className="row">
        <input
          aria-label="reason to reject"
          style={{ flex: 1, minWidth: 200 }}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason to reject (required)"
        />
        <button className="danger" disabled={!reason.trim() || !hasToken || busy} onClick={() => run(() => onReject(reason.trim()))}>
          Reject
        </button>
      </div>
      {error && <div className="error small">{error}</div>}
    </section>
  );
}
