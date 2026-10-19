"""The incident pipeline. One alert in; at most one approved, verified change out.

    alert -> incident -> (settle) -> evidence -> diagnosis -> plan
          -> none:   transient? wait for self-resolve : escalate to a human
          -> action: dry run -> proposal -> [guardian] -> contract? auto-approve : wait for a human
                     -> approval consumed once -> execute -> verify (3 checks) -> resolved | escalated
                     -> postmortem draft

Every step is written to the incident timeline and the audit log, so the question
"who told it to do that?" always has an answer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

from oncallify import approvals, planner, postmortem
from oncallify.actions import get as get_action
from oncallify.approvals import ApprovalError, Approvers
from oncallify.clients import GatewayClient
from oncallify.diagnose.base import Diagnoser
from oncallify.evidence import EvidenceBuilder
from oncallify.guardian import Guardian
from oncallify.model import Alert, Evidence, Incident, IncidentStatus, iso, new_id, to_dict, utcnow
from oncallify.runbooks import KillSwitchEngaged, RunbookExecutor
from oncallify.settings import Settings
from oncallify.store.base import Store
from oncallify.store.transcripts import TranscriptStore
from oncallify.verify import Verifier

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Deps:
    settings: Settings
    store: Store
    transcripts: TranscriptStore
    gateway: GatewayClient
    evidence: EvidenceBuilder
    executor: RunbookExecutor
    verifier: Verifier
    diagnoser_for: Callable[[str], Diagnoser]
    approvers: Approvers
    guardian: Guardian | None = None
    notify: Callable[[str, Incident], None] = field(default=lambda msg, inc: logger.info("%s: %s", inc.id, msg))
    sleep: Callable[[float], None] = time.sleep


class Pipeline:
    def __init__(self, deps: Deps, *, background: bool = True, workers: int = 4):
        self.d = deps
        self.background = background
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="incident") if background else None
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # helpers ------------------------------------------------------------------------------
    def _lock(self, incident_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(incident_id, threading.Lock())

    def _spawn(self, fn: Callable[..., Any], *args: Any) -> None:
        if self._pool is None:
            fn(*args)
        else:
            self._pool.submit(self._guarded, fn, *args)

    @staticmethod
    def _guarded(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a crashed worker must never take the server down
            logger.exception("incident worker failed")

    def _event(self, inc: Incident, kind: str, detail: str, **extra: Any) -> None:
        inc.timeline.append({"ts": iso(utcnow()), "kind": kind, "detail": detail, **extra})
        inc.updated_at = iso(utcnow())

    def _set(self, inc: Incident, status: IncidentStatus, detail: str) -> None:
        inc.status = status.value
        if status.closed:
            inc.closed_at = iso(utcnow())
        self._event(inc, f"status.{status.value}", detail)
        self.d.store.save_incident(inc)
        self.d.store.audit("oncallify", f"incident.{status.value}", inc.id, {"detail": detail})

    def _close_with_postmortem(self, inc: Incident, status: IncidentStatus, detail: str) -> None:
        self._set(inc, status, detail)
        appr = [to_dict(a) for a in self.d.store.list_approvals(inc.id)]
        inc.postmortem = postmortem.render(inc, appr)
        self.d.store.save_incident(inc)

    @staticmethod
    def _paging(inc: Incident) -> list[str]:
        """Fingerprints of the incident's paging alerts: recovery means all of them resolve."""
        fps = [str(a["fingerprint"]) for a in inc.alerts if a.get("severity", "page") == "page"]
        return fps or list(inc.alert_fingerprints)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)

    # 1. alerts ----------------------------------------------------------------------------
    def on_alert(self, alert: Alert) -> str:
        """Called by the API for every alert transition. Returns the incident id."""
        if alert.status == "resolved":
            return self._on_resolved(alert)
        with self._locks_guard:
            inc = self.d.store.find_open_incident(alert.service)
            if inc is None and alert.severity != "page":
                # ticket-severity alerts are context, not pages: they never open an incident
                return ""
            if inc is not None:
                if alert.fingerprint not in inc.alert_fingerprints:
                    inc.alert_fingerprints.append(alert.fingerprint)
                    inc.alerts.append(to_dict(alert))
                    self._event(inc, "alert.attached", f"{alert.rule} also firing")
                    self.d.store.save_incident(inc)
                return inc.id
            now = iso(utcnow())
            inc = Incident(id=new_id("inc"), service=alert.service, status=IncidentStatus.OPEN.value,
                           title=f"{alert.rule} on {alert.service}", opened_at=now, updated_at=now,
                           primary_rule=alert.rule, alert_fingerprints=[alert.fingerprint],
                           alerts=[to_dict(alert)])
            self._event(inc, "alert.firing", alert.summary)
            self.d.store.create_incident(inc)
        self._spawn(self.process, inc.id)
        return inc.id

    def _on_resolved(self, alert: Alert) -> str:
        for inc in self.d.store.list_incidents(limit=50):
            if alert.fingerprint in inc.alert_fingerprints:
                for a in inc.alerts:
                    if a.get("fingerprint") == alert.fingerprint:
                        a.update(status="resolved", ends_at=alert.ends_at)
                note = "alert resolved"
                if inc.status == IncidentStatus.AWAITING_APPROVAL.value:
                    note = "alert resolved on its own while waiting for approval: re-check before approving"
                self._event(inc, "alert.resolved", note)
                self.d.store.save_incident(inc)
                return inc.id
        return ""

    # 2. diagnose and propose ---------------------------------------------------------------
    def _variant(self, inc: Incident) -> str:
        exp = self.d.settings.live_experiment
        if not exp:
            return self.d.settings.default_variant
        try:
            return self.d.gateway.assign(exp, inc.id)
        except httpx.HTTPError as e:
            logger.warning("assignment failed, using default variant: %s", e)
            return self.d.settings.default_variant

    def process(self, incident_id: str) -> None:
        with self._lock(incident_id):
            inc = self.d.store.get_incident(incident_id)
            if inc is None or inc.status != IncidentStatus.OPEN.value:
                return
            self.d.sleep(self.d.settings.settle_s)
            inc = self.d.store.get_incident(incident_id) or inc  # pick up alerts attached meanwhile
            self._set(inc, IncidentStatus.DIAGNOSING, "collecting evidence")
            alerts = [Alert.from_dict(a) for a in inc.alerts]
            try:
                ev = self.d.evidence.build(inc.service, alerts, until=iso(utcnow()))
            except httpx.HTTPError as e:
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, f"could not collect evidence: {e}")
                self.d.notify("escalated: evidence unavailable", inc)
                return
            self.decide(inc, ev)

    def decide(self, inc: Incident, ev: Evidence) -> None:
        variant = self._variant(inc)
        inc.variant = variant
        diagnosis = self.d.diagnoser_for(variant).diagnose(ev)
        inc.evidence = to_dict(ev)
        inc.diagnosis = to_dict(diagnosis)
        self.d.transcripts.put({"incident_id": inc.id, "ts": iso(utcnow()), "variant": variant,
                                "evidence_digest": ev.digest(), "diagnosis": to_dict(diagnosis)})
        if self.d.settings.live_experiment:
            try:
                self.d.gateway.expose(self.d.settings.live_experiment, inc.id, variant)
            except httpx.HTTPError as e:
                self._event(inc, "experiment.exposure_failed", str(e))
        self._event(inc, "diagnosis", f"{diagnosis.cause} ({diagnosis.confidence:.2f}) by {variant}: "
                                      f"{diagnosis.rationale}")
        plan = planner.plan(diagnosis, ev, app=inc.service)

        if plan.action == "none":
            if plan.escalate:
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, plan.reason)
                self.d.notify(f"needs a human: {plan.reason}", inc)
                return
            self._set(inc, IncidentStatus.VERIFYING, plan.reason)
            result = self.d.verifier.wait_self_resolve(self._paging(inc), attempts=12,
                                                       poll_s=self.d.settings.verify_poll_s)
            inc.verify = result.to_dict()
            if result.ok:
                self._close_with_postmortem(inc, IncidentStatus.SELF_RESOLVED, "recovered without any change")
            else:
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, "expected a transient, it did not stop")
                self.d.notify("escalated: transient did not clear", inc)
            return

        dry = self.d.executor.run(plan.action, plan.params, dry_run=True)
        self._event(inc, "dry_run", f"{plan.action}: {dry.code}: {dry.detail}")
        if not dry.ok:
            self._close_with_postmortem(inc, IncidentStatus.ESCALATED, f"dry run refused: {dry.detail}")
            self.d.notify("escalated: dry run refused", inc)
            return
        proposal = approvals.make_proposal(inc.id, plan.action, plan.params, rationale=diagnosis.rationale,
                                           dry_run=to_dict(dry), ttl_s=self.d.settings.proposal_ttl_s)
        self.d.store.create_proposal(proposal)
        inc.proposal_id = proposal.id
        self._event(inc, "proposal", f"{proposal.action} {proposal.params} (fingerprint {proposal.fingerprint})")

        flagged = False
        if self.d.guardian is not None:
            verdict = self.d.guardian.review(f"{diagnosis.cause}: {diagnosis.rationale}",
                                             f"{proposal.action} {proposal.params}: {proposal.blast_radius}")
            flagged = verdict.flagged
            self._event(inc, "guardian", f"{'FLAGGED' if verdict.flagged else 'ok'}: {verdict.detail}"
                        if verdict.checked else verdict.detail)

        contract = None if flagged else approvals.matching_contract(
            self.d.store, rules={str(a["rule"]) for a in inc.alerts}, cause=diagnosis.cause, proposal=proposal,
            confidence=diagnosis.confidence)
        if contract is not None:
            grant = approvals.approve_by_contract(self.d.store, proposal, contract, evidence_digest=ev.digest(),
                                                  ttl_s=self.d.settings.approval_ttl_s)
            if grant is not None:
                self._event(inc, "approval.contract", f"contract {contract.id} covers this; nobody was paged")
                self.d.store.save_incident(inc)
                self.execute(inc.id, grant.approval.id)
                return
        self._set(inc, IncidentStatus.AWAITING_APPROVAL,
                  f"type {proposal.fingerprint} to approve {proposal.action}")
        self.d.notify(f"approval needed: {proposal.action} {proposal.params} (fingerprint {proposal.fingerprint})",
                      inc)

    # 3. human decisions -------------------------------------------------------------------
    def approve(self, incident_id: str, proposal_id: str, *, token: str | None, confirm: str) -> str:
        who = self.d.approvers.who(token)
        if who is None:
            raise ApprovalError("unknown approver token")
        inc = self.d.store.get_incident(incident_id)
        if inc is None or inc.proposal_id != proposal_id:
            raise ApprovalError("proposal does not belong to this incident")
        if inc.status != IncidentStatus.AWAITING_APPROVAL.value:
            raise ApprovalError(f"incident is {inc.status}, not awaiting approval")
        digest = Evidence.from_dict(inc.evidence).digest() if inc.evidence else ""
        grant = approvals.approve(self.d.store, proposal_id, approver=who, confirm_text=confirm,
                                  evidence_digest=digest, ttl_s=self.d.settings.approval_ttl_s)
        self._event(inc, "approval.human", f"approved by {who}")
        self.d.store.save_incident(inc)
        self._spawn(self.execute, incident_id, grant.approval.id)
        return grant.approval.id

    def reject(self, incident_id: str, proposal_id: str, *, token: str | None, reason: str) -> None:
        who = self.d.approvers.who(token)
        if who is None:
            raise ApprovalError("unknown approver token")
        inc = self.d.store.get_incident(incident_id)
        if inc is None or inc.proposal_id != proposal_id:
            raise ApprovalError("proposal does not belong to this incident")
        if not self.d.store.transition_proposal(proposal_id, "open", "rejected"):
            raise ApprovalError("proposal is not open")
        self.d.store.audit(who, "approval.rejected", incident_id, {"proposal": proposal_id, "reason": reason})
        self._close_with_postmortem(inc, IncidentStatus.REJECTED, f"rejected by {who}: {reason[:200]}")

    # 4. execute and verify ----------------------------------------------------------------
    def execute(self, incident_id: str, approval_id: str) -> None:
        with self._lock(f"exec:{incident_id}"):
            inc = self.d.store.get_incident(incident_id)
            if inc is None:
                return
            try:
                grant = approvals.consume_for_execution(self.d.store, approval_id)
            except ApprovalError as e:
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, f"execution refused: {e}")
                return
            p = grant.proposal
            self._set(inc, IncidentStatus.EXECUTING, f"{p.action} {p.params}")
            started = iso(utcnow())
            try:
                result = self.d.executor.run(p.action, p.params, dry_run=False)
            except KillSwitchEngaged as e:
                self.d.store.record_execution(approval_id, {"ok": False, "detail": str(e)}, started)
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, str(e))
                self.d.notify("kill switch blocked an approved action", inc)
                return
            self.d.store.record_execution(approval_id, to_dict(result), started)
            self.d.store.transition_proposal(p.id, "approved", "executed")
            self._event(inc, "executed", f"{result.code}: {result.detail}")
            if not result.ok:
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, f"runbook failed: {result.detail}")
                self.d.notify("escalated: runbook failed", inc)
                return
            spec = get_action(p.action)
            self._set(inc, IncidentStatus.VERIFYING, "proving recovery")
            vr = self.d.verifier.verify(fingerprints=self._paging(inc), action=p.action, params=p.params,
                                        executed_at=started, wait_s=spec.verify_wait_s,
                                        attempts=spec.verify_attempts, poll_s=self.d.settings.verify_poll_s)
            inc = self.d.store.get_incident(incident_id) or inc
            inc.verify = vr.to_dict()
            if vr.ok:
                self._close_with_postmortem(inc, IncidentStatus.RESOLVED,
                                            f"verified after {vr.attempts} check(s)")
            else:
                failed = ", ".join(c.name for c in vr.checks if not c.ok)
                self._close_with_postmortem(inc, IncidentStatus.ESCALATED, f"not verified: {failed}")
                self.d.notify("escalated: fix did not verify", inc)

    # 5. contracts -------------------------------------------------------------------------
    def grant_contract(self, incident_id: str, *, token: str | None, days: int, max_uses: int,
                       min_confidence: float = 0.8) -> str:
        who = self.d.approvers.who(token)
        if who is None:
            raise ApprovalError("unknown approver token")
        inc = self.d.store.get_incident(incident_id)
        if inc is None or inc.status != IncidentStatus.RESOLVED.value or not inc.proposal_id:
            raise ApprovalError("a contract can only be granted from a verified, resolved incident")
        p = self.d.store.get_proposal(inc.proposal_id)
        if p is None:
            raise ApprovalError("proposal not found")
        cause = str((inc.diagnosis or {}).get("cause", "unknown"))
        c = approvals.grant_contract(self.d.store, rule=inc.primary_rule, cause=cause, action=p.action,
                                     params=p.params, granted_by=who, days=days, max_uses=max_uses,
                                     min_confidence=min_confidence, source_incident=inc.id)
        return c.id
