"""Runtime settings, all from environment variables (12-factor). Defaults match
``deploy/local/env.sh`` so the local stack works with no configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _root() -> Path:
    return Path(os.environ.get("ONCALLIFY_ROOT", Path(__file__).resolve().parents[3]))


def _run() -> Path:
    return Path(os.environ.get("ONCALLIFY_RUN", _root() / ".run"))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(slots=True)
class Settings:
    root: Path = field(default_factory=_root)
    run_dir: Path = field(default_factory=_run)

    service: str = field(default_factory=lambda: _env("ONCALLIFY_SERVICE", "ledgerline"))
    ledgerline_url: str = field(default_factory=lambda: _env("LEDGERLINE_URL", "http://127.0.0.1:8080/ledgerline"))
    tomcat_url: str = field(
        default_factory=lambda: _env("TOMCAT_URL", f"http://127.0.0.1:{_env('TOMCAT_PORT', '8080')}")
    )
    gateway_url: str = field(
        default_factory=lambda: _env("GATEWAY_URL", f"http://127.0.0.1:{_env('GATEWAY_PORT', '7070')}")
    )
    agent_port: int = field(default_factory=lambda: int(_env("AGENT_PORT", "7080")))
    ingest_token: str = field(default_factory=lambda: _env("ONCALLIFY_INGEST_TOKEN", ""))

    #: storage: "memory" or a postgresql:// URL
    store_url: str = field(default_factory=lambda: _env("ONCALLIFY_STORE", "memory"))
    #: transcripts: "memory", a mongodb:// URL, or cloudant+https://<account>.cloudant.com/<db>
    transcripts_url: str = field(default_factory=lambda: _env("ONCALLIFY_TRANSCRIPTS", "memory"))

    #: diagnoser variant used when no live experiment is configured
    default_variant: str = field(default_factory=lambda: _env("ONCALLIFY_VARIANT", "rules-v2"))
    #: name of a gateway experiment that splits live incidents between variants (optional)
    live_experiment: str = field(default_factory=lambda: _env("ONCALLIFY_LIVE_EXPERIMENT", ""))

    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    llm_model: str = field(default_factory=lambda: _env("ONCALLIFY_LLM_MODEL", "granite4:micro"))
    embed_model: str = field(default_factory=lambda: _env("ONCALLIFY_EMBED_MODEL", "granite-embedding:30m"))
    guardian_model: str = field(default_factory=lambda: _env("ONCALLIFY_GUARDIAN_MODEL", ""))

    #: seconds of history before the alert that the evidence covers (alerts fire ~10 s after
    #: onset, so 60 s gives a clean baseline plus the change that preceded the fault)
    evidence_lookback_s: int = field(default_factory=lambda: int(_env("ONCALLIFY_LOOKBACK_S", "60")))
    #: wait this long after the first alert before diagnosing, so evidence settles
    settle_s: float = field(default_factory=lambda: float(_env("ONCALLIFY_SETTLE_S", "4")))
    proposal_ttl_s: int = 600
    approval_ttl_s: int = 900
    verify_poll_s: float = field(default_factory=lambda: float(_env("ONCALLIFY_VERIFY_POLL_S", "5")))
    #: authors whose config commits are invisible to the change ledger (the test harness)
    ledger_ignore_authors: tuple[str, ...] = field(
        default_factory=lambda: tuple(a for a in _env("ONCALLIFY_LEDGER_IGNORE_AUTHORS", "").split(",") if a)
    )
    #: JSON file of {"sha256-of-token": "approver name"}
    approvers_file: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("ONCALLIFY_APPROVERS")) else None
    )
    web_dist: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("ONCALLIFY_WEB_DIST")) else None
    )

    @property
    def conf_dir(self) -> Path:
        return Path(_env("LEDGERLINE_CONF_DIR", str(self.run_dir / "ledgerline" / "conf")))

    @property
    def vault_dir(self) -> Path:
        return Path(_env("ONCALLIFY_VAULT", str(self.run_dir / "vault")))

    @property
    def ledger_dir(self) -> Path:
        """Append-only JSONL change feeds (vault audit, deploys)."""
        return self.run_dir / "ledger"

    @property
    def runbook_dir(self) -> Path:
        return Path(_env("ONCALLIFY_RUNBOOKS", str(self.root / "runbooks")))

    @property
    def killswitch_file(self) -> Path:
        return self.run_dir / "KILL_SWITCH"
