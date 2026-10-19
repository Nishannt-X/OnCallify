from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from oncallify.model import Alert, Change, Evidence, LogSummary, LogTemplate

RUNBOOKS = Path(__file__).resolve().parents[2] / "runbooks"
T0 = "2026-10-20T00:00:00.000000Z"
T_END = "2026-10-20T00:01:00.000000Z"


def series(values: list[float], step: float = 1.0) -> list[list[float]]:
    return [[round(i * step, 2), float(v)] for i, v in enumerate(values)]


def alert(rule: str = "LedgerlineInfraErrors", fp: str = "fp-1", severity: str = "page",
          status: str = "firing") -> Alert:
    return Alert(fingerprint=fp, rule=rule, service="ledgerline", severity=severity, summary=rule, status=status,
                 starts_at="2026-10-20T00:00:50.000000Z")


def make_evidence(*, kinds: dict[str, int] | None = None, recent: dict[str, int] | None = None,
                  exemplars: list[str] | None = None, metrics: dict[str, list[list[float]]] | None = None,
                  changes: list[Change] | None = None, alerts: list[Alert] | None = None) -> Evidence:
    kinds = kinds or {}
    templates = [LogTemplate(template=e, count=1, level="ERROR", error_kind=None, first_ts=T0, last_ts=T_END,
                             exemplar=e) for e in (exemplars or [])]
    return Evidence(
        service="ledgerline", window_start=T0, window_end=T_END,
        alerts=alerts if alerts is not None else [alert()],
        logs=LogSummary(total_lines=sum(kinds.values()), by_level={"ERROR": sum(kinds.values())},
                        by_error_kind=dict(kinds), templates=templates,
                        recent_error_kinds=dict(recent if recent is not None else kinds)),
        metrics=metrics or {},
        changes=changes or [],
    )


def config_change(cid: str, keys: dict[str, list[str | None]], author: str = "dev-asha") -> Change:
    return Change(id=cid, ts="2026-10-20T00:00:40.000000Z", source="config", author=author, summary="change",
                  keys=keys, parent="0" * 12)


def vault_change(secret: str) -> Change:
    return Change(id="vault-1", ts="2026-10-20T00:00:41.000000Z", source="vault", author="vault-rotation-job",
                  summary=f"rotated secret {secret}", keys={"secret": [None, secret]})


# canonical evidence for each cause, shaped like the real captures ---------------------------

def ev_leak() -> Evidence:
    return make_evidence(kinds={"pool_exhausted": 200}, metrics={
        "ledgerline_pool_active": series([10] * 20), "ledgerline_pool_max": series([10] * 20),
        "tomcat_threads_busy": series([11] * 20), "transfer_latency_mean_s": series([0.5] * 20)},
        exemplars=["request failed: PoolExhaustedException: Timeout: Pool empty"])


def ev_undersized() -> Evidence:
    return make_evidence(kinds={"pool_exhausted": 150}, metrics={
        "ledgerline_pool_active": series([2] * 20), "ledgerline_pool_max": series([10] * 5 + [2] * 15),
        "tomcat_threads_busy": series([9] * 20)},
        changes=[config_change("abc123def456", {"pool.max": ["10", "2"], "pool.max.wait.ms": ["500", "100"]})])


def ev_bad_config(herring: bool = False) -> Evidence:
    changes = [config_change("bad000000001", {"fx.url": ["http://ok/fx", "http://127.0.0.1:8089/fx"]})]
    if herring:
        changes.append(config_change("4e771c9a0b12", {"fx.timeout.ms": ["1000", "1100"]}, author="dev-rohan"))
    return make_evidence(kinds={"fx_unavailable": 900}, changes=changes,
                         exemplars=["request failed: FxException: fx connection refused at 127.0.0.1:8089"])


def ev_fx_slow() -> Evidence:
    return make_evidence(kinds={"fx_timeout": 300, "pool_exhausted": 100}, metrics={
        "transfer_latency_mean_s": series([0.04] * 5 + [1.0] * 15)},
        exemplars=["request failed: FxException: fx request timed out after 1000 ms"])


def ev_credential(herring_only: bool = False) -> Evidence:
    return make_evidence(kinds={"db_auth": 400},
                         changes=[vault_change("reporting-api-key" if herring_only else "ledgerline-db")],
                         exemplars=["request failed: PSQLException: FATAL: password authentication failed"])


def ev_memory() -> Evidence:
    heap = [60e6 + i * 15e6 for i in range(20)]
    return make_evidence(kinds={}, metrics={"jvm_heap_used_bytes": series(heap),
                                            "jvm_heap_max_bytes": series([384e6] * 20)},
                         alerts=[alert("LedgerlineHeapPressure")])


def ev_transient() -> Evidence:
    return make_evidence(kinds={"fx_unavailable": 40}, recent={},
                         exemplars=["request failed: FxException: fx provider returned 503"])


CANONICAL = {
    "connection_leak": ev_leak, "pool_undersized": ev_undersized, "bad_config": ev_bad_config,
    "fx_degraded": ev_fx_slow, "credential_drift": ev_credential, "memory_leak": ev_memory,
    "transient": ev_transient,
}


@pytest.fixture
def config_repo(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """A real git config repo + vault + app secret, like the local stack's."""
    conf = tmp_path / "conf"
    vault = tmp_path / "vault"
    secrets = tmp_path / "secrets"
    for d in (conf, vault, secrets):
        d.mkdir()
    (conf / "ledgerline.properties").write_text(
        "pool.max=10\npool.max.wait.ms=500\nfx.url=http://127.0.0.1:8080/ledgerline/fx/rate\n"
        "fx.timeout.ms=1000\nfx.fallback.enabled=false\n", encoding="utf-8")

    def git(*args: str, author: str = "dev") -> str:
        return subprocess.run(["git", "-C", str(conf), "-c", f"user.name={author}", "-c", "user.email=d@x", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", "ledgerline.properties")
    git("commit", "-qm", "initial")
    (secrets / "ledgerline-db.password").write_text("old", encoding="utf-8")
    (vault / "ledgerline-db").write_text("new", encoding="utf-8")
    env = {"LEDGERLINE_CONF_DIR": str(conf), "ONCALLIFY_VAULT": str(vault),
           "LEDGERLINE_SECRET_FILE": str(secrets / "ledgerline-db.password"), "TOMCAT_URL": "http://127.0.0.1:1"}
    yield {"conf": conf, "vault": vault, "secret": secrets / "ledgerline-db.password", "git": git, "env": env,
           "root": tmp_path}


def pg_url() -> str | None:
    return os.environ.get("ONCALLIFY_TEST_PG_URL")
