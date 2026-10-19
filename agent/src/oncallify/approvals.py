"""Approval policy: consent is a record, not a prompt.

A proposal is what OnCallify offers (one action, exact params, dry-run result, blast
radius); it expires. An approval is a human's (or a contract's) consent to exactly that
proposal: it carries the params hash, the fingerprint the human typed, a digest of the
evidence they were shown, and an expiry; it can be consumed once.

Execution re-checks everything at the last moment: the approval is consumed atomically,
its params hash must equal the proposal's, and the proposal must still be approved.
(Design credit: Beacon's approval ledger; see docs/ORIGIN.md.)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from oncallify import actions
from oncallify.model import Approval, Contract, Proposal, iso, new_id, params_hash, utcnow
from oncallify.store.base import Store


class ApprovalError(RuntimeError):
    pass


def make_proposal(incident_id: str, action: str, params: dict[str, Any], *, rationale: str,
                  dry_run: dict[str, Any], ttl_s: int) -> Proposal:
    clean = actions.validate(action, params)
    now = utcnow()
    return Proposal(
        id=new_id("prop"),
        incident_id=incident_id,
        action=action,
        params=clean,
        params_hash=params_hash(action, clean),
        blast_radius=actions.get(action).blast_radius(clean),
        rationale=rationale,
        dry_run=dry_run,
        created_at=iso(now),
        expires_at=iso(now + timedelta(seconds=ttl_s)),
    )


class Approvers:
    """Maps API tokens to approver names. Only SHA-256 hashes of tokens are stored.

    When backed by a file, the file is re-read whenever it changes, so adding or
    removing an approver needs no restart.
    """

    def __init__(self, hashed: dict[str, str], path: Path | None = None):
        self._hashed = dict(hashed)
        self._path = path
        self._mtime = path.stat().st_mtime if path and path.exists() else None

    @classmethod
    def from_file(cls, path: Path | None) -> Approvers:
        if path is None:
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}, path)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _refresh(self) -> None:
        if self._path is None:
            return
        mtime = self._path.stat().st_mtime if self._path.exists() else None
        if mtime != self._mtime:
            self._hashed = json.loads(self._path.read_text(encoding="utf-8")) if mtime is not None else {}
            self._mtime = mtime

    def who(self, token: str | None) -> str | None:
        if not token:
            return None
        self._refresh()
        digest = self.hash_token(token)
        for stored, name in self._hashed.items():
            if hmac.compare_digest(stored, digest):
                return name
        return None


@dataclass(slots=True)
class Grant:
    approval: Approval
    proposal: Proposal


def approve(store: Store, proposal_id: str, *, approver: str, confirm_text: str, evidence_digest: str,
            ttl_s: int) -> Grant:
    """A human approves. They must type the proposal's fingerprint (first 8 of the params
    hash), which proves they are approving THIS action with THESE params."""
    p = store.get_proposal(proposal_id)
    if p is None:
        raise ApprovalError("no such proposal")
    now = iso(utcnow())
    if p.expires_at <= now:
        store.transition_proposal(p.id, "open", "expired")
        raise ApprovalError("proposal expired; ask OnCallify to propose again")
    if confirm_text.strip().lower() != p.fingerprint:
        raise ApprovalError(f"confirmation must be the proposal fingerprint {p.fingerprint!r}")
    if not store.transition_proposal(p.id, "open", "approved"):
        current = store.get_proposal(p.id)
        raise ApprovalError(f"proposal is {current.status if current else 'gone'}, not open")
    a = Approval(
        id=new_id("appr"), proposal_id=p.id, incident_id=p.incident_id, action=p.action, params=p.params,
        params_hash=p.params_hash, approver=approver, source="human", confirm_text=confirm_text.strip(),
        evidence_digest=evidence_digest, granted_at=now,
        expires_at=iso(utcnow() + timedelta(seconds=ttl_s)),
    )
    store.create_approval(a)
    store.audit(approver, "approval.granted", p.incident_id, {"proposal": p.id, "action": p.action,
                                                              "params_hash": p.params_hash})
    return Grant(a, p)


def approve_by_contract(store: Store, proposal: Proposal, contract: Contract, *, evidence_digest: str,
                        ttl_s: int) -> Grant | None:
    """A standing contract approves. Consumes one use atomically; None if it cannot."""
    if contract.params_hash != proposal.params_hash or contract.action != proposal.action:
        return None
    now = iso(utcnow())
    if not store.use_contract(contract.id, now):
        return None
    if not store.transition_proposal(proposal.id, "open", "approved"):
        return None
    a = Approval(
        id=new_id("appr"), proposal_id=proposal.id, incident_id=proposal.incident_id, action=proposal.action,
        params=proposal.params, params_hash=proposal.params_hash, approver=f"contract:{contract.granted_by}",
        source="contract", confirm_text=f"contract {contract.id}", evidence_digest=evidence_digest,
        granted_at=now, expires_at=iso(utcnow() + timedelta(seconds=ttl_s)), contract_id=contract.id,
    )
    store.create_approval(a)
    store.audit("oncallify", "approval.contract", proposal.incident_id,
                {"proposal": proposal.id, "contract": contract.id, "action": proposal.action})
    return Grant(a, proposal)


def consume_for_execution(store: Store, approval_id: str) -> Grant:
    """Last check before execution. Consumes the approval (once, atomically)."""
    a = store.consume_approval(approval_id, iso(utcnow()))
    if a is None:
        raise ApprovalError("approval missing, expired, or already used")
    p = store.get_proposal(a.proposal_id)
    if p is None or p.status != "approved":
        raise ApprovalError("proposal is no longer approved")
    if a.params_hash != p.params_hash or a.params_hash != params_hash(p.action, p.params):
        raise ApprovalError("approval does not match the proposal's action and params")
    actions.validate(p.action, p.params)  # the allowlist may have tightened since
    return Grant(a, p)


def grant_contract(store: Store, *, rule: str, cause: str, action: str, params: dict[str, Any], granted_by: str,
                   days: int, max_uses: int, min_confidence: float, source_incident: str | None) -> Contract:
    spec = actions.get(action)
    if not spec.contractable:
        raise ApprovalError(f"{action} cannot be covered by a contract (its params change every time)")
    if not 1 <= days <= 30 or not 1 <= max_uses <= 20:
        raise ApprovalError("contracts last 1-30 days and allow 1-20 uses")
    if not 0.5 <= min_confidence <= 1.0:
        raise ApprovalError("min_confidence must be between 0.5 and 1.0")
    clean = actions.validate(action, params)
    now = utcnow()
    c = Contract(
        id=new_id("ctr"), rule=rule, cause=cause, action=action, params=clean,
        params_hash=params_hash(action, clean), max_uses=max_uses, uses=0, min_confidence=min_confidence,
        granted_by=granted_by, granted_at=iso(now), expires_at=iso(now + timedelta(days=days)),
        source_incident=source_incident,
    )
    store.create_contract(c)
    store.audit(granted_by, "contract.granted", source_incident, {"contract": c.id, "rule": rule, "action": action})
    return c


def matching_contract(store: Store, *, rules: set[str], cause: str, proposal: Proposal,
                      confidence: float) -> Contract | None:
    """A live contract whose alert rule is among this incident's alerts (an incident groups
    several alerts and which one fires first is a race), with the same cause, action and
    exact params, and whose confidence floor the diagnosis meets."""
    now = iso(utcnow())
    for c in store.list_contracts():
        if (c.status == "active" and c.expires_at > now and c.uses < c.max_uses and c.rule in rules
                and c.cause == cause and c.action == proposal.action and c.params_hash == proposal.params_hash
                and confidence >= c.min_confidence):
            return c
    return None
