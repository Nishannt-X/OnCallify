from __future__ import annotations

import copy
import threading
from typing import Any

from oncallify.model import Approval, Contract, Incident, IncidentStatus, Proposal, iso, utcnow


class MemoryStore:
    """Thread-safe in-memory store. One lock makes the compare-and-set operations atomic."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._incidents: dict[str, Incident] = {}
        self._proposals: dict[str, Proposal] = {}
        self._approvals: dict[str, Approval] = {}
        self._contracts: dict[str, Contract] = {}
        self._audit: list[dict[str, Any]] = []
        self._reports: list[dict[str, Any]] = []

    # incidents ----------------------------------------------------------------------------
    def create_incident(self, inc: Incident) -> None:
        with self._lock:
            if inc.id in self._incidents:
                raise KeyError(f"incident {inc.id} exists")
            self._incidents[inc.id] = copy.deepcopy(inc)

    def get_incident(self, incident_id: str) -> Incident | None:
        with self._lock:
            inc = self._incidents.get(incident_id)
            return copy.deepcopy(inc) if inc else None

    def save_incident(self, inc: Incident) -> None:
        with self._lock:
            self._incidents[inc.id] = copy.deepcopy(inc)

    def list_incidents(self, limit: int = 100) -> list[Incident]:
        with self._lock:
            rows = sorted(self._incidents.values(), key=lambda i: i.opened_at, reverse=True)[:limit]
            return [copy.deepcopy(i) for i in rows]

    def find_open_incident(self, service: str) -> Incident | None:
        with self._lock:
            for inc in sorted(self._incidents.values(), key=lambda i: i.opened_at, reverse=True):
                if inc.service == service and not IncidentStatus(inc.status).closed:
                    return copy.deepcopy(inc)
            return None

    # proposals ----------------------------------------------------------------------------
    def create_proposal(self, p: Proposal) -> None:
        with self._lock:
            self._proposals[p.id] = copy.deepcopy(p)

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            p = self._proposals.get(proposal_id)
            return copy.deepcopy(p) if p else None

    def transition_proposal(self, proposal_id: str, from_status: str, to_status: str) -> bool:
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None or p.status != from_status:
                return False
            p.status = to_status
            return True

    # approvals ----------------------------------------------------------------------------
    def create_approval(self, a: Approval) -> None:
        with self._lock:
            self._approvals[a.id] = copy.deepcopy(a)

    def consume_approval(self, approval_id: str, now_iso: str) -> Approval | None:
        with self._lock:
            a = self._approvals.get(approval_id)
            if a is None or a.used_at is not None or a.expires_at <= now_iso:
                return None
            a.used_at = now_iso
            return copy.deepcopy(a)

    def record_execution(self, approval_id: str, result: dict[str, Any], executed_at: str) -> None:
        with self._lock:
            a = self._approvals[approval_id]
            a.execute_result = copy.deepcopy(result)
            a.executed_at = executed_at

    def list_approvals(self, incident_id: str) -> list[Approval]:
        with self._lock:
            rows = [a for a in self._approvals.values() if a.incident_id == incident_id]
            return [copy.deepcopy(a) for a in sorted(rows, key=lambda a: a.granted_at)]

    # contracts ----------------------------------------------------------------------------
    def create_contract(self, c: Contract) -> None:
        with self._lock:
            self._contracts[c.id] = copy.deepcopy(c)

    def list_contracts(self) -> list[Contract]:
        with self._lock:
            return [copy.deepcopy(c) for c in sorted(self._contracts.values(), key=lambda c: c.granted_at)]

    def use_contract(self, contract_id: str, now_iso: str) -> bool:
        with self._lock:
            c = self._contracts.get(contract_id)
            if c is None or c.status != "active" or c.expires_at <= now_iso or c.uses >= c.max_uses:
                return False
            c.uses += 1
            return True

    def revoke_contract(self, contract_id: str) -> bool:
        with self._lock:
            c = self._contracts.get(contract_id)
            if c is None or c.status != "active":
                return False
            c.status = "revoked"
            return True

    # audit / reports ----------------------------------------------------------------------
    def audit(self, actor: str, kind: str, incident_id: str | None, detail: dict[str, Any]) -> None:
        with self._lock:
            self._audit.append({"ts": iso(utcnow()), "actor": actor, "kind": kind, "incident_id": incident_id,
                                "detail": copy.deepcopy(detail)})

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._audit[-limit:][::-1])

    def save_report(self, name: str, report: dict[str, Any]) -> None:
        with self._lock:
            self._reports.append({"name": name, "created_at": iso(utcnow()), "report": copy.deepcopy(report)})

    def list_reports(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._reports[::-1])

    def close(self) -> None:
        pass
