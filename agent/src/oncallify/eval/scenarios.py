"""Captures the replay corpus by really breaking the running stack.

For every scenario: reset to a clean baseline, drive closed-loop traffic, optionally
plant a red herring (an unrelated change), inject the fault, wait for a REAL alert from
the gateway's rule engine, then build Evidence with the same code the live agent uses
and save it with the ground truth. Nothing in a bundle is synthetic except the label,
and the label is known because we caused the failure.

Requires the local stack: Tomcat + Ledgerline, the gateway, PostgreSQL, the collector.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from oncallify.changes import ChangeLedger, append_jsonl, parse_properties
from oncallify.clients import GatewayClient, ServiceClient
from oncallify.collector import Collector
from oncallify.eval.corpus import save
from oncallify.evidence import EvidenceBuilder
from oncallify.loadgen import LoadGenerator
from oncallify.model import Alert, iso, parse_iso, to_dict, utcnow
from oncallify.settings import Settings

logger = logging.getLogger(__name__)

RESET_AUTHOR = "scenario-reset"
FAMILIES = ("connection_leak", "pool_undersized", "bad_config", "fx_degraded", "credential_drift", "memory_leak",
            "transient")
HERRINGS = ("none", "config", "vault", "none", "config", "none", "vault", "config")
DEVS = ("dev-asha", "dev-rohan", "dev-meera", "dev-kabir")
UNRELATED_EDITS = (("fx.timeout.ms", "1100"), ("feature.statements.v2", "true"), ("fx.timeout.ms", "1200"),
                   ("feature.audit.verbose", "true"))


@dataclass(slots=True)
class ScenarioSpec:
    family: str
    group: int
    workers: int
    herring: str
    knobs: dict[str, Any] = field(default_factory=dict)

    @property
    def scenario_id(self) -> str:
        return f"{self.family}-g{self.group}"


def default_specs(groups: int = 8) -> list[ScenarioSpec]:
    specs: list[ScenarioSpec] = []
    for g in range(groups):
        workers = (8, 10, 12, 14)[g % 4]
        herring = HERRINGS[g % len(HERRINGS)]
        for fam in FAMILIES:
            knobs: dict[str, Any] = {}
            if fam == "pool_undersized":
                knobs = {"pool.max": ("2", "1")[g % 2], "pool.max.wait.ms": ("100", "50")[(g // 2) % 2]}
            elif fam == "bad_config":
                knobs = {"fx.url": (
                    "http://127.0.0.1:8089/ledgerline/fx/rate",
                    "http://127.0.0.1:9091/fx/rate",
                    "http://127.0.0.1:8080/ledgerline/fx/rates-v2",  # wrong path: 404, not refused
                )[g % 3]}
            elif fam == "fx_degraded":
                knobs = {"fx_delay_ms": (1500, 3000, 5000)[g % 3]}
            elif fam == "transient":
                knobs = {"blip_s": (8, 10, 12)[g % 3]}
            specs.append(ScenarioSpec(fam, g, workers, herring, knobs))
    return specs


class StackControl:
    """Everything the runner does to the live system."""

    def __init__(self, s: Settings, admin_token: str, manager_password: str):
        self.s = s
        self.admin_token = admin_token
        self.manager_password = manager_password
        self.http = httpx.Client(timeout=10.0)
        self.config = s.conf_dir / "ledgerline.properties"
        self.app_secret = s.conf_dir.parent / "secrets" / "ledgerline-db.password"
        self.vault_db = s.vault_dir / "ledgerline-db"
        self.vault_audit = s.ledger_dir / "vault-audit.jsonl"
        self.baseline = self.config.read_text(encoding="utf-8")

    # faults inside the app -----------------------------------------------------------------
    def fault(self, name: str, enabled: bool, **extra: Any) -> None:
        r = self.http.post(f"{self.s.ledgerline_url}/admin/faults", headers={"X-Admin-Token": self.admin_token},
                           json={"fault": name, "enabled": enabled, **extra})
        r.raise_for_status()

    def all_faults_off(self) -> None:
        for f in ("connection_leak", "memory_leak", "fx_slow", "fx_errors"):
            # the app may be mid-reload; a reload clears faults anyway
            with contextlib.suppress(httpx.HTTPError):
                self.fault(f, False)

    # config changes (git commits) ------------------------------------------------------------
    def _git(self, *args: str, author: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.s.conf_dir), "-c", f"user.name={author}", "-c", f"user.email={author}@example.dev",
             *args], check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()

    def _write_atomic(self, path: Path, text: str) -> None:
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)

    def commit_edits(self, edits: dict[str, str], message: str, author: str) -> str:
        lines = self.config.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        out = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if key in edits:
                out.append(f"{key}={edits[key]}")
                seen.add(key)
            else:
                out.append(line)
        out += [f"{k}={v}" for k, v in edits.items() if k not in seen]
        self._write_atomic(self.config, "\n".join(out) + "\n")
        self._git("add", "ledgerline.properties", author=author)
        self._git("commit", "-q", "-m", message, author=author)
        return self._git("rev-parse", "--short=12", "HEAD", author=author)

    def restore_config(self) -> None:
        if parse_properties(self.config.read_text(encoding="utf-8")) != parse_properties(self.baseline):
            self._write_atomic(self.config, self.baseline)
            self._git("add", "ledgerline.properties", author=RESET_AUTHOR)
            self._git("commit", "-q", "-m", "scenario reset to baseline", author=RESET_AUTHOR)

    # secrets ------------------------------------------------------------------------------
    def rotate_db_password(self) -> None:
        new = secrets.token_hex(12)
        subprocess.run(["psql", self.s_pg_admin(), "-v", "ON_ERROR_STOP=1", "-qc",
                        f"ALTER ROLE ledgerline PASSWORD '{new}'"], check=True, capture_output=True, timeout=15)
        self._write_atomic(self.vault_db, new)
        append_jsonl(self.vault_audit, {"ts": iso(utcnow()), "id": f"vault-{secrets.token_hex(4)}",
                                        "author": "vault-rotation-job", "summary": "rotated secret ledgerline-db",
                                        "keys": {"secret": [None, "ledgerline-db"]}})

    def unrelated_rotation(self) -> None:
        append_jsonl(self.vault_audit, {"ts": iso(utcnow()), "id": f"vault-{secrets.token_hex(4)}",
                                        "author": "vault-rotation-job", "summary": "rotated secret reporting-api-key",
                                        "keys": {"secret": [None, "reporting-api-key"]}})

    def s_pg_admin(self) -> str:
        return os.environ.get("PG_ADMIN_URL", "postgresql://postgres@127.0.0.1:5432/postgres")

    def sync_secret(self) -> None:
        if self.vault_db.read_bytes() != self.app_secret.read_bytes():
            self._write_atomic(self.app_secret, self.vault_db.read_text(encoding="utf-8"))

    # whole-app ----------------------------------------------------------------------------
    def reload_app(self) -> None:
        try:
            r = self.http.get(f"{self.s.tomcat_url}/manager/text/reload", params={"path": "/ledgerline"},
                              auth=("oncallify-runbook", self.manager_password), timeout=60)
            if r.text.startswith("OK"):
                return
            logger.warning("reload failed (%s); restarting Tomcat", r.text.strip())
        except httpx.HTTPError as e:
            logger.warning("manager unreachable (%s); restarting Tomcat", e)
        self.hard_restart()

    def hard_restart(self) -> None:
        """Last resort after an out-of-memory: restart the whole Tomcat instance."""
        script = self.s.root / "deploy" / "local" / "tomcat.sh"
        subprocess.run(["bash", str(script), "stop"], capture_output=True, timeout=60)
        subprocess.run(["bash", str(script), "start"], check=True, capture_output=True, timeout=120)

    def wait_healthy(self, timeout_s: float = 90) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if self.http.get(f"{self.s.ledgerline_url}/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise RuntimeError("ledgerline did not become healthy")


class CaptureRunner:
    def __init__(self, s: Settings, control: StackControl, gateway: GatewayClient, ledger: ChangeLedger,
                 out_dir: Path, *, warm_s: float = 50, settle_s: float = 4, log: Callable[[str], None] = print):
        self.s = s
        self.c = control
        self.gateway = gateway
        self.builder = EvidenceBuilder(gateway, ledger, lookback_s=s.evidence_lookback_s)
        self.out_dir = out_dir
        self.warm_s = warm_s
        self.settle_s = settle_s
        self.log = log

    def _wait_no_firing(self, timeout_s: float = 180) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if not self.gateway.alerts(status="firing"):
                return
            time.sleep(2)
        raise RuntimeError("alerts did not clear after reset")

    def reset(self) -> None:
        self.c.all_faults_off()
        self.c.restore_config()
        self.c.sync_secret()
        self.c.reload_app()
        self.c.wait_healthy()
        self._wait_no_firing()

    def _await_alert(self, onset: str, timeout_s: float) -> list[Alert]:
        """Wait for a PAGE alert, exactly like the live pipeline (tickets never open an
        incident); return every alert firing since onset, tickets included as context."""
        deadline = time.time() + timeout_s
        t_onset = parse_iso(onset) - timedelta(seconds=1)
        while time.time() < deadline:
            firing = [a for a in self.gateway.alerts(status="firing") if parse_iso(a.starts_at) >= t_onset]
            if any(a.severity == "page" for a in firing):
                return firing
            time.sleep(1)
        return []

    def _inject(self, spec: ScenarioSpec, author: str) -> str | None:
        """Cause the failure. Returns the commit to revert for config faults."""
        fam, k = spec.family, spec.knobs
        if fam == "connection_leak":
            self.c.fault("connection_leak", True)
        elif fam == "memory_leak":
            self.c.fault("memory_leak", True)
        elif fam == "fx_degraded":
            self.c.fault("fx_slow", True, fx_delay_ms=k["fx_delay_ms"])
        elif fam == "transient":
            self.c.fault("fx_errors", True)
            threading.Timer(float(k["blip_s"]), lambda: self.c.fault("fx_errors", False)).start()
        elif fam == "pool_undersized":
            return self.c.commit_edits(dict(k), "tune connection pool for the load-test profile", author)
        elif fam == "bad_config":
            return self.c.commit_edits(dict(k), "point fx client at the new rates endpoint", author)
        elif fam == "credential_drift":
            self.c.rotate_db_password()
        return None

    def capture(self, spec: ScenarioSpec) -> Path | None:
        """Clean-window rule: from the end of reset to the injection there are at least
        ``warm_s`` (>= lookback - 10 s) seconds of healthy traffic, so a bundle's evidence
        window never contains the previous scenario's errors."""
        sid = spec.scenario_id
        self.log(f"[{sid}] reset")
        self.reset()
        author = DEVS[spec.group % len(DEVS)]
        load = LoadGenerator(self.s.ledgerline_url, workers=spec.workers, seed=spec.group).start()
        try:
            time.sleep(self.warm_s)
            if spec.herring == "config":
                key, value = UNRELATED_EDITS[spec.group % len(UNRELATED_EDITS)]
                self.c.commit_edits({key: value}, f"set {key}={value}", DEVS[(spec.group + 1) % len(DEVS)])
                time.sleep(12)
            elif spec.herring == "vault":
                self.c.unrelated_rotation()
                time.sleep(12)
            onset = iso(utcnow())
            self.log(f"[{sid}] inject {spec.family} {spec.knobs} (herring={spec.herring}, workers={spec.workers})")
            suspect = self._inject(spec, author)
            alerts = self._await_alert(onset, timeout_s=150)
            if not alerts:
                self.log(f"[{sid}] NO ALERT within 150s: scenario skipped")
                return None
            time.sleep(self.settle_s)
            alerts = self._await_alert(onset, timeout_s=5) or alerts
            ev = self.builder.build(self.s.service, alerts, until=iso(utcnow()))
        finally:
            load.stop()
            if spec.family in ("fx_degraded", "connection_leak", "memory_leak"):
                self.c.all_faults_off()
        bundle = {
            "scenario_id": sid,
            "family": spec.family,
            "group": f"g{spec.group}",
            "knobs": {"workers": spec.workers, "herring": spec.herring, **spec.knobs},
            "captured_at": iso(utcnow()),
            "load": {str(k): v for k, v in load.stats.statuses.items()},
            "evidence": to_dict(ev),
            "truth": {"cause": spec.family, "suspect_change": suspect},
        }
        path = save(bundle, self.out_dir)
        self.log(f"[{sid}] saved: alerts={[a.rule for a in alerts]} errors={ev.logs.by_error_kind}")
        return path


def run_capture(s: Settings, out_dir: Path, specs: list[ScenarioSpec], *, skip_existing: bool = True,
                log: Callable[[str], None] = print) -> dict[str, Any]:
    from oncallify.wiring import change_ledger

    admin = (s.vault_dir / "ledgerline-admin-token").read_text(encoding="utf-8").strip()
    manager = (s.vault_dir / "tomcat-manager").read_text(encoding="utf-8").strip()
    control = StackControl(s, admin, manager)
    gateway = GatewayClient(s.gateway_url, s.ingest_token)
    log_path = s.run_dir / "ledgerline" / "logs" / "ledgerline.jsonl"
    collector = Collector(gateway, ServiceClient(s.ledgerline_url), log_path, interval_s=1.0)
    # start at the end of the existing log: history from earlier runs is not evidence
    try:
        collector.tailer.cursor.inode = os.stat(log_path).st_ino
        collector.tailer.cursor.offset = os.stat(log_path).st_size
    except FileNotFoundError:
        pass
    thread = threading.Thread(target=collector.run, daemon=True)
    thread.start()
    runner = CaptureRunner(s, control, gateway, change_ledger(s), out_dir, log=log)
    done: list[str] = []
    skipped: list[str] = []
    try:
        for spec in specs:
            if skip_existing and (out_dir / f"{spec.scenario_id}.json").exists():
                continue
            try:
                p = runner.capture(spec)
            except Exception as e:  # noqa: BLE001 - keep capturing the rest; report at the end
                log(f"[{spec.scenario_id}] FAILED: {e}")
                p = None
            (done if p else skipped).append(spec.scenario_id)
    finally:
        try:  # reset while the collector still runs, so alerts can resolve
            runner.reset()
        except Exception as e:  # noqa: BLE001
            log(f"final reset failed: {e}")
        collector.stop()
    summary = {"captured": done, "skipped": skipped, "specs": [asdict(s) for s in specs]}
    (out_dir / "_capture_log.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
