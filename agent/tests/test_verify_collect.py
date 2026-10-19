"""Verification against fake HTTP, the collector's idempotent ids, transcript stores."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import mongomock

from oncallify.clients import GatewayClient, ServiceClient
from oncallify.collector import Collector, LogTailer
from oncallify.model import iso, utcnow
from oncallify.store.transcripts import CloudantTranscripts, MemoryTranscripts, MongoTranscripts
from oncallify.verify import Verifier


def _verifier(alert_state: dict, metrics_text: list[str], health: dict, tmp: Path, conf: Path | None = None,
              vault: Path | None = None, secret: Path | None = None) -> Verifier:  # type: ignore[type-arg]
    scrapes = iter(metrics_text)

    def gw(req: httpx.Request) -> httpx.Response:
        fp = req.url.path.rsplit("/", 1)[-1]
        a = alert_state.get(fp)
        return httpx.Response(200, json=a) if a else httpx.Response(404, json={})

    def svc(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/metrics":
            return httpx.Response(200, text=next(scrapes))
        return httpx.Response(200, json=health)

    return Verifier(GatewayClient("http://gw", "t", transport=httpx.MockTransport(gw)),
                    ServiceClient("http://svc", transport=httpx.MockTransport(svc)),
                    conf_dir=conf or tmp, vault_dir=vault or tmp, app_secret=secret or tmp / "s",
                    sleep=lambda s: None, quiet_gap_s=0)


def _alert(fp: str, status: str, ends_at: str | None) -> dict:  # type: ignore[type-arg]
    return {"fingerprint": fp, "rule": "LedgerlineInfraErrors", "service": "ledgerline", "severity": "page",
            "summary": "", "status": status, "starts_at": iso(utcnow() - timedelta(minutes=2)), "ends_at": ends_at}


ERR = 'ledgerline_errors_total{{kind="pool_exhausted"}} {n}\nledgerline_errors_total{{kind="business"}} {b}\n'


def test_recovery_needs_all_three_checks(tmp_path: Path) -> None:
    executed = iso(utcnow() - timedelta(seconds=60))
    later = iso(utcnow() - timedelta(seconds=5))
    v = _verifier({"fp": _alert("fp", "resolved", later)}, [ERR.format(n=5, b=0), ERR.format(n=5, b=9)],
                  {"started_at": iso(utcnow() - timedelta(seconds=30))}, tmp_path)
    r = v.verify(fingerprints=["fp"], action="restart_app", params={"app": "ledgerline"}, executed_at=executed,
                 wait_s=0, attempts=1, poll_s=0)
    assert r.ok, r.checks  # business errors rising do not count


def test_an_alert_that_resolved_before_the_fix_does_not_count(tmp_path: Path) -> None:
    executed = iso(utcnow())
    earlier = iso(utcnow() - timedelta(seconds=30))
    v = _verifier({"fp": _alert("fp", "resolved", earlier)}, [ERR.format(n=1, b=0)] * 4,
                  {"started_at": iso(utcnow() + timedelta(seconds=1))}, tmp_path)
    r = v.verify(fingerprints=["fp"], action="restart_app", params={"app": "ledgerline"}, executed_at=executed,
                 wait_s=0, attempts=2, poll_s=0)
    assert not r.ok and "before the fix" in r.checks[0].detail


def test_new_infrastructure_errors_fail_verification(tmp_path: Path) -> None:
    executed = iso(utcnow() - timedelta(seconds=60))
    v = _verifier({"fp": _alert("fp", "resolved", iso(utcnow()))}, [ERR.format(n=5, b=0), ERR.format(n=9, b=0)],
                  {"started_at": iso(utcnow())}, tmp_path)
    r = v.verify(fingerprints=["fp"], action="restart_app", params={"app": "ledgerline"}, executed_at=executed,
                 wait_s=0, attempts=1, poll_s=0)
    assert not r.ok and "4 new" in r.checks[1].detail


def test_revert_postcondition_reads_git(config_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    conf, git = config_repo["conf"], config_repo["git"]
    f = conf / "ledgerline.properties"
    f.write_text(f.read_text().replace("pool.max=10", "pool.max=2"))
    git("commit", "-qam", "shrink")
    sha = git("rev-parse", "--short=12", "HEAD")
    v = _verifier({}, [], {}, tmp_path, conf=conf)
    assert not v.postcondition("revert_config_change", {"commit": sha}, iso(utcnow())).ok
    git("revert", "--no-edit", sha)
    assert v.postcondition("revert_config_change", {"commit": sha}, iso(utcnow())).ok


def test_credential_postcondition_compares_fingerprints(config_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    v = _verifier({}, [], {}, tmp_path, vault=config_repo["vault"], secret=config_repo["secret"])
    assert not v.postcondition("sync_db_credential", {"app": "ledgerline"}, iso(utcnow())).ok
    config_repo["secret"].write_text("new")
    assert v.postcondition("sync_db_credential", {"app": "ledgerline"}, iso(utcnow())).ok


# ---- collector ------------------------------------------------------------------------------

def test_log_tailer_ids_are_stable_and_partial_lines_wait(tmp_path: Path) -> None:
    log = tmp_path / "app.jsonl"
    log.write_text('{"ts":"2026-10-20T00:00:01Z","msg":"a"}\n{"ts":"2026-10-20T00:00:02Z","msg":"b"}\n{"ts":"20')
    t = LogTailer(log, "ledgerline")
    first = t.read_new()
    assert [e["body"]["msg"] for e in first] == ["a", "b"]
    with log.open("a") as fh:
        fh.write('26-10-20T00:00:03Z","msg":"c"}\n')
    second = t.read_new()
    assert [e["body"]["msg"] for e in second] == ["c"]
    again = LogTailer(log, "ledgerline").read_new()  # a restarted collector re-reads the file...
    assert [e["event_id"] for e in again] == [e["event_id"] for e in first + second]  # ...with the same ids


def test_collector_keeps_events_pending_when_the_gateway_is_down(tmp_path: Path) -> None:
    log = tmp_path / "app.jsonl"
    log.write_text('{"ts":"2026-10-20T00:00:01Z","msg":"a"}\n')
    up = {"ok": False}
    posted: list[list[dict]] = []  # type: ignore[type-arg]

    def gw(req: httpx.Request) -> httpx.Response:
        if not up["ok"]:
            return httpx.Response(503)
        posted.append(json.loads(req.content)["events"])
        return httpx.Response(202, json={"inserted": len(posted[-1])})

    def svc(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="g 1\n")

    c = Collector(GatewayClient("http://gw", "t", transport=httpx.MockTransport(gw)),
                  ServiceClient("http://svc", transport=httpx.MockTransport(svc)), log)
    assert c.tick()["sent"] == 0
    up["ok"] = True
    assert c.tick()["sent"] == 3  # 1 log line + 2 scrapes, nothing lost
    assert {e["type"] for e in posted[0]} == {"log", "metrics"}


# ---- transcripts ----------------------------------------------------------------------------

def test_memory_and_mongo_transcripts_filter_by_incident() -> None:
    for store in (MemoryTranscripts(), MongoTranscripts("mongodb://x", client=mongomock.MongoClient())):
        store.put({"incident_id": "i1", "ts": "1", "x": 1})
        store.put({"incident_id": "i2", "ts": "2"})
        assert [d["x"] for d in store.for_incident("i1")] == [1]


def test_cloudant_exchanges_api_key_for_iam_token_and_reuses_it() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.host}{req.url.path}")
        if req.url.host == "iam.cloud.ibm.com":
            assert b"apikey=k3y" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expiration": 9_999_999_999})
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.path.endswith("/_find"):
            return httpx.Response(200, json={"docs": [{"incident_id": "i1"}]})
        return httpx.Response(201, json={"ok": True, "id": "doc1"})

    c = CloudantTranscripts("https://acct.cloudant.com", "transcripts", "k3y", transport=httpx.MockTransport(handler))
    assert c.put({"incident_id": "i1"}) == "doc1"
    assert c.for_incident("i1") == [{"incident_id": "i1"}]
    assert sum(1 for x in calls if "iam" in x) == 1
