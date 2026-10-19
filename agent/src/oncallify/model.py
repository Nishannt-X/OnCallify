"""Plain data records. Everything is JSON-serialisable through ``to_dict``."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime) -> str:
    """Fixed-width UTC timestamp. Always with microseconds, so string order == time order
    (isoformat() drops ".000000" when zero, which would break string comparison)."""
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses / enums / datetimes into JSON-friendly values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [to_dict(v) for v in obj]
    return obj


def params_hash(action: str, params: dict[str, Any]) -> str:
    """Stable identity of (action, params). Approvals and contracts bind to this."""
    canonical = json.dumps({"action": action, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IncidentStatus(StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    SELF_RESOLVED = "self_resolved"
    ESCALATED = "escalated"
    REJECTED = "rejected"

    @property
    def closed(self) -> bool:
        return self in {
            IncidentStatus.RESOLVED,
            IncidentStatus.SELF_RESOLVED,
            IncidentStatus.ESCALATED,
            IncidentStatus.REJECTED,
        }


@dataclass(slots=True)
class Alert:
    fingerprint: str
    rule: str
    service: str
    severity: str
    summary: str
    status: str
    starts_at: str
    ends_at: str | None = None
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Alert:
        return cls(
            fingerprint=str(d["fingerprint"]),
            rule=str(d["rule"]),
            service=str(d["service"]),
            severity=str(d.get("severity", "page")),
            summary=str(d.get("summary", "")),
            status=str(d.get("status", "firing")),
            starts_at=str(d["starts_at"]),
            ends_at=d.get("ends_at"),
            value=float(d.get("value", 0.0)),
            labels={str(k): str(v) for k, v in (d.get("labels") or {}).items()},
        )


@dataclass(slots=True)
class Change:
    """One change to the running system: a config commit, a secret rotation, a deploy."""

    id: str
    ts: str
    source: str  # config | vault | deploy
    author: str
    summary: str
    #: for config commits: key -> [old, new]; for vault: {"secret": [None, name]}
    keys: dict[str, list[str | None]] = field(default_factory=dict)
    parent: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Change:
        return cls(
            id=str(d["id"]),
            ts=str(d["ts"]),
            source=str(d["source"]),
            author=str(d.get("author", "")),
            summary=str(d.get("summary", "")),
            keys={str(k): list(v) for k, v in (d.get("keys") or {}).items()},
            parent=d.get("parent"),
        )


@dataclass(slots=True)
class LogTemplate:
    template: str
    count: int
    level: str
    error_kind: str | None
    first_ts: str
    last_ts: str
    exemplar: str


@dataclass(slots=True)
class LogSummary:
    total_lines: int
    by_level: dict[str, int]
    by_error_kind: dict[str, int]
    templates: list[LogTemplate]
    #: errors per kind in the last 15 s of the window (is it still happening?)
    recent_error_kinds: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LogSummary:
        return cls(
            total_lines=int(d["total_lines"]),
            by_level=dict(d.get("by_level") or {}),
            by_error_kind=dict(d.get("by_error_kind") or {}),
            templates=[LogTemplate(**t) for t in d.get("templates") or []],
            recent_error_kinds=dict(d.get("recent_error_kinds") or {}),
        )


@dataclass(slots=True)
class Evidence:
    """What the diagnoser is allowed to see. Built live or loaded from a replay bundle."""

    service: str
    window_start: str
    window_end: str
    alerts: list[Alert]
    logs: LogSummary
    #: metric name -> list of [seconds since window_start, value]
    metrics: dict[str, list[list[float]]]
    changes: list[Change]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(
            service=str(d["service"]),
            window_start=str(d["window_start"]),
            window_end=str(d["window_end"]),
            alerts=[Alert.from_dict(a) for a in d.get("alerts") or []],
            logs=LogSummary.from_dict(d["logs"]),
            metrics={str(k): [[float(x), float(y)] for x, y in v] for k, v in (d.get("metrics") or {}).items()},
            changes=[Change.from_dict(c) for c in d.get("changes") or []],
        )

    def digest(self) -> str:
        """Fingerprint of exactly what a human was shown when they approved."""
        blob = json.dumps(to_dict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass(slots=True)
class Diagnosis:
    cause: str
    confidence: float
    rationale: str
    evidence_refs: list[str] = field(default_factory=list)
    suspect_change: str | None = None
    variant: str = ""
    latency_ms: float = 0.0
    tokens: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Proposal:
    id: str
    incident_id: str
    action: str
    params: dict[str, Any]
    params_hash: str
    blast_radius: str
    rationale: str
    dry_run: dict[str, Any]
    created_at: str
    expires_at: str
    status: str = "open"  # open | approved | rejected | withdrawn | expired | executed

    @property
    def fingerprint(self) -> str:
        """What a human types to approve: the first 8 characters of the params hash."""
        return self.params_hash[:8]


@dataclass(slots=True)
class Approval:
    id: str
    proposal_id: str
    incident_id: str
    action: str
    params: dict[str, Any]
    params_hash: str
    approver: str
    source: str  # human | contract
    confirm_text: str
    evidence_digest: str
    granted_at: str
    expires_at: str
    contract_id: str | None = None
    used_at: str | None = None
    executed_at: str | None = None
    execute_result: dict[str, Any] | None = None


@dataclass(slots=True)
class Contract:
    """A standing approval: one rule + cause + action + exact params, N uses, until expiry."""

    id: str
    rule: str
    cause: str
    action: str
    params: dict[str, Any]
    params_hash: str
    max_uses: int
    uses: int
    min_confidence: float
    granted_by: str
    granted_at: str
    expires_at: str
    status: str = "active"  # active | revoked
    source_incident: str | None = None


@dataclass(slots=True)
class Incident:
    id: str
    service: str
    status: str
    title: str
    opened_at: str
    updated_at: str
    primary_rule: str
    alert_fingerprints: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None
    proposal_id: str | None = None
    variant: str | None = None
    verify: dict[str, Any] | None = None
    postmortem: str | None = None
    closed_at: str | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
