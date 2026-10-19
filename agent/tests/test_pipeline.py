"""The incident pipeline and the HTTP API, with fakes for the outside world."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from oncallify.api import create_app
from oncallify.approvals import ApprovalError, Approvers
from oncallify.diagnose.rules import RulesV2
from oncallify.model import Alert, Evidence
from oncallify.pipeline import Deps, Pipeline
from oncallify.runbooks import KillSwitchEngaged, RunbookResult
from oncallify.settings import Settings
from oncallify.store import MemoryStore
from oncallify.store.transcripts import MemoryTranscripts
from oncallify.verify import Check, VerifyResult

from .conftest import alert, ev_leak, ev_transient, ev_undersized

TOKEN = "approver-token"


class FakeEvidence:
    def __init__(self, ev: Evidence):
        self.ev = ev

    def build(self, service: str, alerts: list[Alert], *, until: str) -> Evidence:
        self.ev.alerts = alerts
        return self.ev


@dataclass
class FakeExecutor:
    dry_ok: bool = True
    real_ok: bool = True
    killed: bool = False
    calls: list[tuple[str, bool]] = field(default_factory=list)
    killswitch_file: Any = None

    def killswitch_engaged(self) -> bool:
        return self.killed

    def run(self, action: str, params: dict[str, Any], *, dry_run: bool) -> RunbookResult:
        if not dry_run and self.killed:
            raise KillSwitchEngaged("kill switch")
        self.calls.append((action, dry_run))
        ok = self.dry_ok if dry_run else self.real_ok
        return RunbookResult(ok, "ok" if ok else "precondition_failed", not dry_run and ok, "detail", dry_run)


@dataclass
class FakeVerifier:
    ok: bool = True
    self_resolves: bool = True
    seen: list[list[str]] = field(default_factory=list)

    def verify(self, *, fingerprints: list[str], **_: Any) -> VerifyResult:
        self.seen.append(fingerprints)
        return VerifyResult(self.ok, 1, [Check("alert_resolved_after_fix", self.ok)])

    def wait_self_resolve(self, fingerprints: list[str], **_: Any) -> VerifyResult:
        return VerifyResult(self.self_resolves, 1, [Check("alert_resolved", self.self_resolves)])


def make(ev: Evidence, **over: Any) -> tuple[Pipeline, FakeExecutor, FakeVerifier]:
    settings = Settings()
    settings.settle_s = 0
    settings.ingest_token = "ingest"
    executor = over.pop("executor", FakeExecutor())
    verifier = over.pop("verifier", FakeVerifier())
    deps = Deps(settings=settings, store=MemoryStore(), transcripts=MemoryTranscripts(), gateway=None,  # type: ignore[arg-type]
                evidence=FakeEvidence(ev), executor=executor, verifier=verifier,  # type: ignore[arg-type]
                diagnoser_for=lambda v: RulesV2(), approvers=Approvers({Approvers.hash_token(TOKEN): "nishant"}),
                sleep=lambda s: None)
    return Pipeline(deps, background=False), executor, verifier


def test_leak_goes_to_approval_then_executes_and_resolves() -> None:
    pl, ex, ver = make(ev_leak())
    inc_id = pl.on_alert(alert())
    inc = pl.d.store.get_incident(inc_id)
    assert inc and inc.status == "awaiting_approval"
    p = pl.d.store.get_proposal(inc.proposal_id)  # type: ignore[arg-type]
    assert p and p.action == "restart_app"
    assert ex.calls == [("restart_app", True)]  # only the dry run so far
    pl.approve(inc_id, p.id, token=TOKEN, confirm=p.fingerprint)
    inc = pl.d.store.get_incident(inc_id)
    assert inc and inc.status == "resolved" and inc.postmortem and "restart_app" in inc.postmortem
    assert ex.calls[-1] == ("restart_app", False)
    assert pl.d.store.get_proposal(p.id).status == "executed"  # type: ignore[union-attr]


def test_unknown_token_and_wrong_fingerprint_are_refused() -> None:
    pl, ex, _ = make(ev_leak())
    inc_id = pl.on_alert(alert())
    inc = pl.d.store.get_incident(inc_id)
    with pytest.raises(ApprovalError, match="unknown approver"):
        pl.approve(inc_id, inc.proposal_id, token="nope", confirm="x")  # type: ignore[arg-type,union-attr]
    with pytest.raises(ApprovalError, match="fingerprint"):
        pl.approve(inc_id, inc.proposal_id, token=TOKEN, confirm="00000000")  # type: ignore[arg-type,union-attr]
    assert all(dry for _, dry in ex.calls)


def test_second_alert_attaches_to_the_open_incident_and_tickets_never_open_one() -> None:
    pl, _, ver = make(ev_leak())
    assert pl.on_alert(alert("LedgerlinePoolSaturated", "fp-t", severity="ticket")) == ""
    first = pl.on_alert(alert("LedgerlineInfraErrors", "fp-1"))
    again = pl.on_alert(alert("LedgerlinePoolSaturated", "fp-2", severity="ticket"))
    assert first == again
    inc = pl.d.store.get_incident(first)
    assert inc and inc.alert_fingerprints == ["fp-1", "fp-2"]
    pl.approve(first, inc.proposal_id, token=TOKEN, confirm=pl.d.store.get_proposal(inc.proposal_id).fingerprint)  # type: ignore[arg-type,union-attr]
    assert ver.seen[-1] == ["fp-1"]  # recovery is judged on the paging alert, not the ticket


def test_contract_auto_approves_the_repeat_and_nobody_is_asked() -> None:
    pl, ex, _ = make(ev_leak())
    first = pl.on_alert(alert(fp="a1"))
    inc = pl.d.store.get_incident(first)
    pl.approve(first, inc.proposal_id, token=TOKEN, confirm=pl.d.store.get_proposal(inc.proposal_id).fingerprint)  # type: ignore[arg-type,union-attr]
    pl.grant_contract(first, token=TOKEN, days=7, max_uses=1, min_confidence=0.7)
    second = pl.on_alert(alert(fp="a2"))
    inc2 = pl.d.store.get_incident(second)
    assert inc2 and inc2.status == "resolved"
    assert [a.source for a in pl.d.store.list_approvals(second)] == ["contract"]
    third = pl.on_alert(alert(fp="a3"))  # contract used up: back to a human
    assert pl.d.store.get_incident(third).status == "awaiting_approval"  # type: ignore[union-attr]


def test_contract_cannot_be_granted_from_an_unverified_incident() -> None:
    pl, _, _ = make(ev_leak(), verifier=FakeVerifier(ok=False))
    inc_id = pl.on_alert(alert())
    inc = pl.d.store.get_incident(inc_id)
    pl.approve(inc_id, inc.proposal_id, token=TOKEN, confirm=pl.d.store.get_proposal(inc.proposal_id).fingerprint)  # type: ignore[arg-type,union-attr]
    assert pl.d.store.get_incident(inc_id).status == "escalated"  # type: ignore[union-attr]
    with pytest.raises(ApprovalError, match="verified, resolved"):
        pl.grant_contract(inc_id, token=TOKEN, days=1, max_uses=1)


def test_transient_is_left_alone_and_closes_when_it_self_resolves() -> None:
    pl, ex, _ = make(ev_transient())
    inc = pl.d.store.get_incident(pl.on_alert(alert()))
    assert inc and inc.status == "self_resolved" and ex.calls == []


def test_transient_that_does_not_stop_is_escalated() -> None:
    pl, _, _ = make(ev_transient(), verifier=FakeVerifier(self_resolves=False))
    assert pl.d.store.get_incident(pl.on_alert(alert())).status == "escalated"  # type: ignore[union-attr]


def test_refused_dry_run_escalates_without_proposing() -> None:
    pl, ex, _ = make(ev_undersized(), executor=FakeExecutor(dry_ok=False))
    inc = pl.d.store.get_incident(pl.on_alert(alert()))
    assert inc and inc.status == "escalated" and inc.proposal_id is None
    assert ex.calls == [("revert_config_change", True)]


def test_kill_switch_stops_an_already_approved_action() -> None:
    ex = FakeExecutor()
    pl, _, _ = make(ev_leak(), executor=ex)
    inc_id = pl.on_alert(alert())
    inc = pl.d.store.get_incident(inc_id)
    ex.killed = True
    pl.approve(inc_id, inc.proposal_id, token=TOKEN, confirm=pl.d.store.get_proposal(inc.proposal_id).fingerprint)  # type: ignore[arg-type,union-attr]
    inc = pl.d.store.get_incident(inc_id)
    assert inc and inc.status == "escalated" and "kill switch" in inc.timeline[-1]["detail"]
    assert all(dry for _, dry in ex.calls)


def test_reject_closes_the_incident_and_the_proposal() -> None:
    pl, _, _ = make(ev_leak())
    inc_id = pl.on_alert(alert())
    inc = pl.d.store.get_incident(inc_id)
    pl.reject(inc_id, inc.proposal_id, token=TOKEN, reason="wrong diagnosis")  # type: ignore[arg-type,union-attr]
    assert pl.d.store.get_incident(inc_id).status == "rejected"  # type: ignore[union-attr]
    with pytest.raises(ApprovalError):
        pl.approve(inc_id, inc.proposal_id, token=TOKEN, confirm="x")  # type: ignore[arg-type,union-attr]


# ---- API ----------------------------------------------------------------------------------------

def test_api_requires_credentials_for_every_write() -> None:
    pl, _, _ = make(ev_leak())
    c = TestClient(create_app(pl))
    body = {"fingerprint": "f1", "rule": "LedgerlineInfraErrors", "service": "ledgerline", "severity": "page",
            "summary": "s", "status": "firing", "starts_at": "2026-10-20T00:00:50.000000Z"}
    assert c.post("/alerts", json=body).status_code == 401
    r = c.post("/alerts", json=body, headers={"x-ingest-token": "ingest"})
    assert r.status_code == 202
    inc_id = r.json()["incident_id"]
    detail = c.get(f"/api/incidents/{inc_id}").json()
    assert detail["status"] == "awaiting_approval" and detail["proposal"]["fingerprint"]
    pid, fp = detail["proposal"]["id"], detail["proposal"]["fingerprint"]
    assert c.post(f"/api/incidents/{inc_id}/approve", json={"proposal_id": pid, "confirm": fp}).status_code == 401
    bad = c.post(f"/api/incidents/{inc_id}/approve", json={"proposal_id": pid, "confirm": "nope"},
                 headers={"x-approver-token": TOKEN})
    assert bad.status_code == 409
    ok = c.post(f"/api/incidents/{inc_id}/approve", json={"proposal_id": pid, "confirm": fp},
                headers={"x-approver-token": TOKEN})
    assert ok.status_code == 200
    assert c.get(f"/api/incidents/{inc_id}").json()["status"] == "resolved"
    assert c.post("/api/killswitch", json={"engaged": True}).status_code == 401


def test_api_safety_page_lists_allowlist_and_rules() -> None:
    pl, _, _ = make(ev_leak())
    body = TestClient(create_app(pl)).get("/api/safety").json()
    ids = {a["id"] for a in body["allowlist"]}
    assert ids == {"restart_app", "revert_config_change", "resize_pool", "enable_fx_fallback", "sync_db_credential"}
    assert any("Contracts" in r for r in body["rules"])
