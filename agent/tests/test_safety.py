"""The safety properties. If any of these fail, nothing else matters."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest

from oncallify import actions, approvals
from oncallify.approvals import ApprovalError, Approvers
from oncallify.model import Incident, iso, new_id, params_hash, utcnow
from oncallify.runbooks import KillSwitchEngaged, RunbookExecutor
from oncallify.store import MemoryStore
from oncallify.store.base import Store

from .conftest import RUNBOOKS, pg_url


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    if request.param == "memory":
        yield MemoryStore()
        return
    url = pg_url()
    if not url:
        pytest.skip("set ONCALLIFY_TEST_PG_URL to run store tests against PostgreSQL")
    from oncallify.store.postgres import PostgresStore

    s = PostgresStore(url)
    s._conn.execute("TRUNCATE approvals, proposals, contracts, incidents, audit, experiment_reports CASCADE")
    yield s
    s.close()


def _incident(store: Store) -> Incident:
    now = iso(utcnow())
    inc = Incident(id=new_id("inc"), service="ledgerline", status="awaiting_approval", title="t", opened_at=now,
                   updated_at=now, primary_rule="LedgerlineInfraErrors")
    store.create_incident(inc)
    return inc


def _proposal(store: Store, action: str = "restart_app", params: dict | None = None, ttl: int = 600):  # type: ignore[no-untyped-def,type-arg]
    inc = _incident(store)
    p = approvals.make_proposal(inc.id, action, params or {"app": "ledgerline"}, rationale="r", dry_run={},
                                ttl_s=ttl)
    store.create_proposal(p)
    return p


# ---- allowlist ------------------------------------------------------------------------------

def test_only_allowlisted_actions_with_exact_params_validate() -> None:
    assert actions.validate("resize_pool", {"app": "ledgerline", "size": 20}) == {"app": "ledgerline", "size": 20}
    for bad in [("rm_rf", {}), ("restart_app", {"app": "payroll"}), ("restart_app", {}),
                ("restart_app", {"app": "ledgerline", "force": True}), ("resize_pool", {"app": "ledgerline",
                                                                                          "size": 500}),
                ("resize_pool", {"app": "ledgerline", "size": True}),
                ("revert_config_change", {"app": "ledgerline", "commit": "HEAD; rm -rf /"})]:
        with pytest.raises(actions.ParamError):
            actions.validate(*bad)


def test_planner_escalates_instead_of_crashing_when_the_allowlist_refuses() -> None:
    from oncallify import planner
    from oncallify.model import Diagnosis

    from .conftest import config_change, make_evidence

    ev = make_evidence(kinds={"fx_unavailable": 10}, changes=[config_change("not-a-sha!", {"fx.url": ["a", "b"]})])
    plan = planner.plan(Diagnosis("bad_config", 0.9, "", suspect_change="not-a-sha!"), ev)
    assert plan.action == "none" and plan.escalate and "allowlist" in plan.reason


def test_reverts_can_never_be_covered_by_a_contract() -> None:
    assert not actions.get("revert_config_change").contractable
    with pytest.raises(ApprovalError, match="cannot be covered"):
        approvals.grant_contract(MemoryStore(), rule="r", cause="bad_config", action="revert_config_change",
                                 params={"app": "ledgerline", "commit": "abcdef1"}, granted_by="x", days=1,
                                 max_uses=1, min_confidence=0.8, source_incident=None)


# ---- approvals --------------------------------------------------------------------------------

def test_approval_requires_the_exact_fingerprint(store: Store) -> None:
    p = _proposal(store)
    with pytest.raises(ApprovalError, match="fingerprint"):
        approvals.approve(store, p.id, approver="n", confirm_text="yes do it", evidence_digest="d", ttl_s=60)
    g = approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint.upper(), evidence_digest="d",
                          ttl_s=60)
    assert g.approval.params_hash == p.params_hash == params_hash(p.action, p.params)


def test_a_proposal_can_be_approved_only_once(store: Store) -> None:
    p = _proposal(store)
    approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=60)
    with pytest.raises(ApprovalError, match="not open"):
        approvals.approve(store, p.id, approver="m", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=60)


def test_expired_proposal_cannot_be_approved(store: Store) -> None:
    p = _proposal(store, ttl=-1)
    with pytest.raises(ApprovalError, match="expired"):
        approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=60)


def test_an_approval_is_consumed_exactly_once_even_under_a_race(store: Store) -> None:
    p = _proposal(store)
    g = approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=60)
    racers: list[Store]
    if isinstance(store, MemoryStore):
        racers = [store] * 8
    else:  # eight independent database connections, like eight executor processes
        from oncallify.store.postgres import PostgresStore

        racers = [PostgresStore(store.url) for _ in range(8)]  # type: ignore[attr-defined]
    wins: list[str] = []
    errors: list[str] = []
    barrier = threading.Barrier(len(racers))

    def go(s: Store) -> None:
        barrier.wait()
        try:
            approvals.consume_for_execution(s, g.approval.id)
            wins.append("x")
        except ApprovalError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=go, args=(s,)) for s in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    if not isinstance(store, MemoryStore):
        for s in racers:
            s.close()
    assert len(wins) == 1
    assert len(errors) == 7 and all("already used" in e for e in errors)


def test_expired_approval_cannot_execute(store: Store) -> None:
    p = _proposal(store)
    g = approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=-1)
    with pytest.raises(ApprovalError):
        approvals.consume_for_execution(store, g.approval.id)


def test_tampered_proposal_params_are_caught_at_execution(store: Store) -> None:
    p = _proposal(store, "resize_pool", {"app": "ledgerline", "size": 10})
    g = approvals.approve(store, p.id, approver="n", confirm_text=p.fingerprint, evidence_digest="d", ttl_s=60)
    if isinstance(store, MemoryStore):
        store._proposals[p.id].params = {"app": "ledgerline", "size": 50}  # someone edits the row
    else:
        store._conn.execute(  # type: ignore[attr-defined]
            "UPDATE proposals SET body = jsonb_set(body, '{params,size}', '50') WHERE id=%s", (p.id,))
    with pytest.raises(ApprovalError, match="does not match"):
        approvals.consume_for_execution(store, g.approval.id)


# ---- contracts --------------------------------------------------------------------------------

def _contract(store: Store, **kw):  # type: ignore[no-untyped-def]
    args = dict(rule="LedgerlineInfraErrors", cause="connection_leak", action="restart_app",
                params={"app": "ledgerline"}, granted_by="n", days=7, max_uses=2, min_confidence=0.8,
                source_incident=None)
    args.update(kw)
    return approvals.grant_contract(store, **args)  # type: ignore[arg-type]


def test_contract_matches_only_same_rule_cause_action_params_and_confidence(store: Store) -> None:
    c = _contract(store)
    p = _proposal(store)
    rules = {"LedgerlinePoolSaturated", "LedgerlineInfraErrors"}
    assert approvals.matching_contract(store, rules=rules, cause="connection_leak", proposal=p,
                                       confidence=0.9).id == c.id  # type: ignore[union-attr]
    assert approvals.matching_contract(store, rules={"Other"}, cause="connection_leak", proposal=p,
                                       confidence=0.9) is None
    assert approvals.matching_contract(store, rules=rules, cause="memory_leak", proposal=p, confidence=0.9) is None
    assert approvals.matching_contract(store, rules=rules, cause="connection_leak", proposal=p,
                                       confidence=0.5) is None


def test_contract_uses_are_capped_and_revocation_is_final(store: Store) -> None:
    c = _contract(store, max_uses=2)
    now = iso(utcnow())
    assert store.use_contract(c.id, now) and store.use_contract(c.id, now)
    assert not store.use_contract(c.id, now)
    c2 = _contract(store)
    assert store.revoke_contract(c2.id)
    assert not store.use_contract(c2.id, now)
    assert not store.revoke_contract(c2.id)


def test_expired_contract_cannot_be_used(store: Store) -> None:
    c = _contract(store)
    assert not store.use_contract(c.id, iso(utcnow() + timedelta(days=8)))


def test_contract_limits_are_enforced() -> None:
    with pytest.raises(ApprovalError):
        _contract(MemoryStore(), days=365)
    with pytest.raises(ApprovalError):
        _contract(MemoryStore(), min_confidence=0.1)


def test_approver_tokens_are_stored_hashed_and_reload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from oncallify.wiring import write_approver

    f = tmp_path / "approvers.json"
    write_approver(f, "nishant", "s3cret-token")
    assert "s3cret-token" not in f.read_text()
    a = Approvers.from_file(f)
    assert a.who("s3cret-token") == "nishant" and a.who("guess") is None and a.who(None) is None
    write_approver(f, "asha", "other-token")
    assert a.who("other-token") == "asha"  # picked up without a restart


# ---- runbook executor -----------------------------------------------------------------------

def _executor(repo, tmp_path) -> RunbookExecutor:  # type: ignore[no-untyped-def]
    return RunbookExecutor(RUNBOOKS, repo["env"], killswitch_file=tmp_path / "KILL", windows=False)


def test_executor_runs_the_real_runbook_dry_then_real(config_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    ex = _executor(config_repo, tmp_path)
    dry = ex.run("enable_fx_fallback", {"app": "ledgerline"}, dry_run=True)
    assert dry.ok and not dry.changed and "would set" in dry.detail
    real = ex.run("enable_fx_fallback", {"app": "ledgerline"}, dry_run=False)
    assert real.ok and real.changed
    assert "fx.fallback.enabled=true" in (config_repo["conf"] / "ledgerline.properties").read_text()


def test_executor_reports_precondition_failures(config_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = _executor(config_repo, tmp_path).run("revert_config_change", {"app": "ledgerline", "commit": "abcdef1"},
                                             dry_run=True)
    assert not r.ok and r.code == "precondition_failed"


def test_kill_switch_blocks_execution_but_not_dry_runs(config_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    ex = _executor(config_repo, tmp_path)
    (tmp_path / "KILL").write_text("engaged")
    assert ex.run("enable_fx_fallback", {"app": "ledgerline"}, dry_run=True).ok
    with pytest.raises(KillSwitchEngaged):
        ex.run("enable_fx_fallback", {"app": "ledgerline"}, dry_run=False)


def test_executor_refuses_non_allowlisted_actions_before_running_anything(config_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(actions.ParamError):
        _executor(config_repo, tmp_path).run("drop_database", {"app": "ledgerline"}, dry_run=True)


def test_windows_command_line_matches_the_powershell_contract(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ex = RunbookExecutor(RUNBOOKS, {}, killswitch_file=tmp_path / "K", windows=True)
    cmd = ex.command("resize_pool", {"app": "ledgerline", "size": 20}, dry_run=True)
    assert cmd[-6:] == [str(RUNBOOKS / "windows" / "resize-pool.ps1"), "-App", "ledgerline", "-Size", "20",
                        "-DryRun"]
    assert all((RUNBOOKS / "windows" / f"{a.replace('_', '-')}.ps1").exists() for a in actions.REGISTRY)
    assert all((RUNBOOKS / "linux" / f"{a.replace('_', '-')}.sh").exists() for a in actions.REGISTRY)
