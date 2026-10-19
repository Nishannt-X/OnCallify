"""PostgreSQL store (psycopg 3). Atomicity comes from single conditional UPDATE
statements, never read-modify-write in Python:

    UPDATE approvals SET used_at = now WHERE id = $1 AND used_at IS NULL AND expires_at > now RETURNING ...

so two executors racing on one approval cannot both get a row back.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from oncallify.model import Approval, Contract, Incident, Proposal, iso, to_dict

_CLOSED = ("resolved", "self_resolved", "escalated", "rejected")


def _ts(value: Any) -> str | None:
    return iso(value) if value is not None else None


class PostgresStore:
    def __init__(self, url: str):
        self.url = url
        self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.migrate()

    def migrate(self) -> None:
        sql = resources.files("oncallify.store").joinpath("migrations/001_init.sql").read_text(encoding="utf-8")
        with self._conn.transaction():
            self._conn.execute("SELECT pg_advisory_xact_lock(4242)")  # one migrator at a time
            self._conn.execute(sql)
            self._conn.execute("INSERT INTO schema_migrations (version) VALUES ('001') ON CONFLICT DO NOTHING")

    def close(self) -> None:
        self._conn.close()

    # incidents ----------------------------------------------------------------------------
    def create_incident(self, inc: Incident) -> None:
        self._conn.execute(
            "INSERT INTO incidents (id, service, status, title, opened_at, updated_at, closed_at, primary_rule, "
            "variant, proposal_id, body) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (inc.id, inc.service, inc.status, inc.title, inc.opened_at, inc.updated_at, inc.closed_at,
             inc.primary_rule, inc.variant, inc.proposal_id, Jsonb(to_dict(inc))),
        )

    def save_incident(self, inc: Incident) -> None:
        self._conn.execute(
            "UPDATE incidents SET status=%s, title=%s, updated_at=%s, closed_at=%s, variant=%s, proposal_id=%s, "
            "body=%s WHERE id=%s",
            (inc.status, inc.title, inc.updated_at, inc.closed_at, inc.variant, inc.proposal_id,
             Jsonb(to_dict(inc)), inc.id),
        )

    @staticmethod
    def _incident(row: dict[str, Any] | None) -> Incident | None:
        return Incident(**row["body"]) if row else None

    def get_incident(self, incident_id: str) -> Incident | None:
        row = self._conn.execute("SELECT body FROM incidents WHERE id=%s", (incident_id,)).fetchone()
        return self._incident(row)

    def list_incidents(self, limit: int = 100) -> list[Incident]:
        rows = self._conn.execute("SELECT body FROM incidents ORDER BY opened_at DESC LIMIT %s", (limit,)).fetchall()
        return [Incident(**r["body"]) for r in rows]

    def find_open_incident(self, service: str) -> Incident | None:
        row = self._conn.execute(
            "SELECT body FROM incidents WHERE service=%s AND status <> ALL(%s) ORDER BY opened_at DESC LIMIT 1",
            (service, list(_CLOSED)),
        ).fetchone()
        return self._incident(row)

    # proposals ----------------------------------------------------------------------------
    def create_proposal(self, p: Proposal) -> None:
        self._conn.execute(
            "INSERT INTO proposals (id, incident_id, action, params, params_hash, status, created_at, expires_at, "
            "body) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (p.id, p.incident_id, p.action, Jsonb(p.params), p.params_hash, p.status, p.created_at, p.expires_at,
             Jsonb(to_dict(p))),
        )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        row = self._conn.execute("SELECT body, status FROM proposals WHERE id=%s", (proposal_id,)).fetchone()
        if not row:
            return None
        body = dict(row["body"])
        body["status"] = row["status"]  # the column is authoritative; body is a snapshot
        return Proposal(**body)

    def transition_proposal(self, proposal_id: str, from_status: str, to_status: str) -> bool:
        cur = self._conn.execute(
            "UPDATE proposals SET status=%s WHERE id=%s AND status=%s", (to_status, proposal_id, from_status)
        )
        return cur.rowcount == 1

    # approvals ----------------------------------------------------------------------------
    _APPROVAL_COLS = ("id, proposal_id, incident_id, action, params, params_hash, approver, source, confirm_text, "
                      "evidence_digest, contract_id, granted_at, expires_at, used_at, executed_at, execute_result")

    @staticmethod
    def _approval(row: dict[str, Any]) -> Approval:
        return Approval(
            id=row["id"], proposal_id=row["proposal_id"], incident_id=row["incident_id"], action=row["action"],
            params=dict(row["params"]), params_hash=row["params_hash"], approver=row["approver"],
            source=row["source"], confirm_text=row["confirm_text"], evidence_digest=row["evidence_digest"],
            contract_id=row["contract_id"], granted_at=_ts(row["granted_at"]) or "",
            expires_at=_ts(row["expires_at"]) or "", used_at=_ts(row["used_at"]),
            executed_at=_ts(row["executed_at"]), execute_result=row["execute_result"],
        )

    def create_approval(self, a: Approval) -> None:
        self._conn.execute(
            f"INSERT INTO approvals ({self._APPROVAL_COLS}) VALUES ({','.join(['%s'] * 16)})",
            (a.id, a.proposal_id, a.incident_id, a.action, Jsonb(a.params), a.params_hash, a.approver, a.source,
             a.confirm_text, a.evidence_digest, a.contract_id, a.granted_at, a.expires_at, a.used_at,
             a.executed_at, Jsonb(a.execute_result) if a.execute_result is not None else None),
        )

    def consume_approval(self, approval_id: str, now_iso: str) -> Approval | None:
        try:
            row = self._conn.execute(
                f"UPDATE approvals SET used_at=%s WHERE id=%s AND used_at IS NULL AND expires_at > %s "
                f"RETURNING {self._APPROVAL_COLS}",
                (now_iso, approval_id, now_iso),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            return None  # another approval for the same proposal was already consumed
        return self._approval(row) if row else None

    def record_execution(self, approval_id: str, result: dict[str, Any], executed_at: str) -> None:
        self._conn.execute(
            "UPDATE approvals SET execute_result=%s, executed_at=%s WHERE id=%s",
            (Jsonb(result), executed_at, approval_id),
        )

    def list_approvals(self, incident_id: str) -> list[Approval]:
        rows = self._conn.execute(
            f"SELECT {self._APPROVAL_COLS} FROM approvals WHERE incident_id=%s ORDER BY granted_at", (incident_id,)
        ).fetchall()
        return [self._approval(r) for r in rows]

    # contracts ----------------------------------------------------------------------------
    _CONTRACT_COLS = ("id, rule, cause, action, params, params_hash, max_uses, uses, min_confidence, granted_by, "
                      "granted_at, expires_at, status, source_incident")

    def create_contract(self, c: Contract) -> None:
        self._conn.execute(
            f"INSERT INTO contracts ({self._CONTRACT_COLS}) VALUES ({','.join(['%s'] * 14)})",
            (c.id, c.rule, c.cause, c.action, Jsonb(c.params), c.params_hash, c.max_uses, c.uses, c.min_confidence,
             c.granted_by, c.granted_at, c.expires_at, c.status, c.source_incident),
        )

    def list_contracts(self) -> list[Contract]:
        rows = self._conn.execute(f"SELECT {self._CONTRACT_COLS} FROM contracts ORDER BY granted_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = dict(d["params"])
            d["granted_at"] = _ts(d["granted_at"])
            d["expires_at"] = _ts(d["expires_at"])
            out.append(Contract(**d))
        return out

    def use_contract(self, contract_id: str, now_iso: str) -> bool:
        cur = self._conn.execute(
            "UPDATE contracts SET uses = uses + 1 WHERE id=%s AND status='active' AND expires_at > %s "
            "AND uses < max_uses",
            (contract_id, now_iso),
        )
        return cur.rowcount == 1

    def revoke_contract(self, contract_id: str) -> bool:
        cur = self._conn.execute("UPDATE contracts SET status='revoked' WHERE id=%s AND status='active'",
                                 (contract_id,))
        return cur.rowcount == 1

    # audit / reports ----------------------------------------------------------------------
    def audit(self, actor: str, kind: str, incident_id: str | None, detail: dict[str, Any]) -> None:
        self._conn.execute("INSERT INTO audit (actor, kind, incident_id, detail) VALUES (%s,%s,%s,%s)",
                           (actor, kind, incident_id, Jsonb(json.loads(json.dumps(detail, default=str)))))

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, actor, kind, incident_id, detail FROM audit ORDER BY id DESC LIMIT %s", (limit,)
        ).fetchall()
        return [{**r, "ts": _ts(r["ts"])} for r in rows]

    def save_report(self, name: str, report: dict[str, Any]) -> None:
        self._conn.execute("INSERT INTO experiment_reports (name, report) VALUES (%s, %s)", (name, Jsonb(report)))

    def list_reports(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, created_at, report FROM experiment_reports ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return [{**r, "created_at": _ts(r["created_at"])} for r in rows]
