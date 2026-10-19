"""Builds a real Pipeline from Settings (the composition root)."""

from __future__ import annotations

import json
import logging
import os
from functools import cache
from pathlib import Path

import httpx

from oncallify.approvals import Approvers
from oncallify.changes import ChangeLedger, GitConfigSource, JsonlSource
from oncallify.clients import GatewayClient, ServiceClient
from oncallify.diagnose import build
from oncallify.diagnose.base import Diagnoser
from oncallify.evidence import EvidenceBuilder
from oncallify.guardian import Guardian
from oncallify.model import Incident
from oncallify.pipeline import Deps, Pipeline
from oncallify.runbooks import RunbookExecutor
from oncallify.settings import Settings
from oncallify.store import open_store
from oncallify.store.transcripts import open_transcripts
from oncallify.verify import Verifier

logger = logging.getLogger("oncallify")


def change_ledger(s: Settings) -> ChangeLedger:
    return ChangeLedger([
        GitConfigSource(s.conf_dir, ignore_authors=s.ledger_ignore_authors),
        JsonlSource(s.ledger_dir / "vault-audit.jsonl", "vault"),
        JsonlSource(s.ledger_dir / "deploys.jsonl", "deploy"),
    ])


def runbook_env(s: Settings) -> dict[str, str]:
    return {
        "LEDGERLINE_CONF_DIR": str(s.conf_dir),
        "LEDGERLINE_SECRET_FILE": str(s.conf_dir.parent / "secrets" / "ledgerline-db.password"),
        "ONCALLIFY_VAULT": str(s.vault_dir),
        "TOMCAT_URL": s.tomcat_url,
        "RUNBOOK_ACTOR": "oncallify-runbook",
    }


def _notifier(webhook: str | None):  # type: ignore[no-untyped-def]
    def notify(message: str, inc: Incident) -> None:
        logger.warning("[%s] %s: %s", inc.status, inc.id, message)
        if webhook:
            try:  # Slack-compatible incoming webhook payload
                httpx.post(webhook, json={"text": f"OnCallify {inc.id} ({inc.title}): {message}"}, timeout=5)
            except httpx.HTTPError as e:
                logger.warning("notify webhook failed: %s", e)

    return notify


def build_pipeline(s: Settings, *, background: bool = True) -> Pipeline:
    gateway = GatewayClient(s.gateway_url, s.ingest_token)
    service = ServiceClient(s.ledgerline_url)
    env = runbook_env(s)

    @cache
    def diagnoser_for(variant: str) -> Diagnoser:
        return build(variant, ollama_url=s.ollama_url, llm_model=s.llm_model)

    deps = Deps(
        settings=s,
        store=open_store(s.store_url),
        transcripts=open_transcripts(s.transcripts_url),
        gateway=gateway,
        evidence=EvidenceBuilder(gateway, change_ledger(s), lookback_s=s.evidence_lookback_s),
        executor=RunbookExecutor(s.runbook_dir, env, killswitch_file=s.killswitch_file),
        verifier=Verifier(gateway, service, conf_dir=s.conf_dir, vault_dir=s.vault_dir,
                          app_secret=Path(env["LEDGERLINE_SECRET_FILE"])),
        diagnoser_for=diagnoser_for,
        approvers=Approvers.from_file(s.approvers_file),
        guardian=Guardian(s.ollama_url, s.guardian_model) if s.guardian_model else None,
        notify=_notifier(os.environ.get("ONCALLIFY_NOTIFY_WEBHOOK")),
    )
    return Pipeline(deps, background=background)


def write_approver(path: Path, name: str, token: str) -> None:
    """Add an approver token (stored hashed) to the approvers file."""
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[Approvers.hash_token(token)] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
