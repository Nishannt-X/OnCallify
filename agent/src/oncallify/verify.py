"""Verification: "recovered" is a claim to prove, never something the model says.

All three checks must pass (design credit: Beacon's verify loop):

1. **alert resolved after the fix**: the alert that paged is resolved, and it resolved
   AFTER the action executed (a stale or earlier resolve does not count);
2. **errors quiet now**: across two scrapes a few seconds apart, infrastructure errors
   did not increase;
3. **post-condition**: the action's own effect is observable (the app restarted after
   the fix, the flag is on, the pool has the new size, the reverted keys are back, the
   credentials match).
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oncallify.changes import parse_properties
from oncallify.clients import GatewayClient, ServiceClient
from oncallify.model import parse_iso, to_dict
from oncallify.promtext import Sample


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    attempts: int
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(to_dict(self))


def infra_error_total(samples: list[Sample]) -> float:
    return float(sum(s.value for s in samples
                     if s.name == "ledgerline_errors_total" and s.labels.get("kind") != "business"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


class Verifier:
    def __init__(self, gateway: GatewayClient, service: ServiceClient, *, conf_dir: Path, vault_dir: Path,
                 app_secret: Path, sleep: Callable[[float], None] = time.sleep, quiet_gap_s: float = 4.0):
        self.gateway = gateway
        self.service = service
        self.conf_dir = conf_dir
        self.vault_dir = vault_dir
        self.app_secret = app_secret
        self.sleep = sleep
        self.quiet_gap_s = quiet_gap_s

    # individual checks --------------------------------------------------------------------
    def alerts_resolved_after(self, fingerprints: list[str], executed_at: str) -> Check:
        """Every paging alert of the incident resolved, each one AFTER the fix."""
        done = []
        for fp in fingerprints:
            a = self.gateway.alert(fp)
            if a is None:
                return Check("alert_resolved_after_fix", False, f"alert {fp} not found")
            if a.status != "resolved" or not a.ends_at:
                return Check("alert_resolved_after_fix", False, f"{a.rule} is still {a.status}")
            if parse_iso(a.ends_at) <= parse_iso(executed_at):
                return Check("alert_resolved_after_fix", False,
                             f"{a.rule} resolved at {a.ends_at}, before the fix at {executed_at}")
            done.append(f"{a.rule} at {a.ends_at}")
        return Check("alert_resolved_after_fix", True, "resolved: " + ", ".join(done))

    def errors_quiet(self) -> Check:
        try:
            first = infra_error_total(self.service.metrics())
            self.sleep(self.quiet_gap_s)
            second = infra_error_total(self.service.metrics())
        except Exception as e:  # noqa: BLE001 - any scrape failure means "not proven"
            return Check("errors_quiet", False, f"scrape failed: {e}")
        # a counter that went down means the app restarted; the new value is the increase
        increase = second - first if second >= first else second
        return Check("errors_quiet", increase <= 0, f"{increase:.0f} new infrastructure errors in "
                                                    f"{self.quiet_gap_s:.0f}s")

    def postcondition(self, action: str, params: dict[str, Any], executed_at: str) -> Check:
        name = f"postcondition:{action}"
        try:
            if action == "restart_app":
                h = self.service.health()
                started = h.get("started_at")
                ok = bool(started) and parse_iso(str(started)) > parse_iso(executed_at)
                return Check(name, ok, f"app started at {started}")
            if action in ("enable_fx_fallback", "resize_pool"):
                samples = {s.name: s.value for s in self.service.metrics()}
                if action == "enable_fx_fallback":
                    v = samples.get("ledgerline_fx_fallback_enabled")
                    return Check(name, v == 1.0, f"fx fallback flag = {v}")
                v = samples.get("ledgerline_pool_max")
                return Check(name, v == float(params["size"]), f"pool max = {v}")
            if action == "sync_db_credential":
                same = _sha(self.app_secret) == _sha(self.vault_dir / f"{params['app']}-db") != ""
                return Check(name, same, "app credential matches vault" if same else "credentials differ")
            if action == "revert_config_change":
                return self._reverted(params["commit"], name)
        except Exception as e:  # noqa: BLE001
            return Check(name, False, f"could not check: {e}")
        return Check(name, False, f"no post-condition defined for {action}")

    def _reverted(self, commit: str, name: str) -> Check:
        def show(rev: str) -> dict[str, str]:
            out = subprocess.run(["git", "-C", str(self.conf_dir), "show", f"{rev}:ledgerline.properties"],
                                 capture_output=True, text=True, timeout=10, check=True).stdout
            return parse_properties(out)

        before, after = show(f"{commit}^"), show(commit)
        touched = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        current = parse_properties((self.conf_dir / "ledgerline.properties").read_text(encoding="utf-8"))
        wrong = [k for k in sorted(touched) if current.get(k) != before.get(k)]
        if wrong:
            return Check(name, False, f"keys not back to their earlier values: {', '.join(wrong)}")
        return Check(name, True, f"keys restored: {', '.join(sorted(touched))}")

    # the loop -----------------------------------------------------------------------------
    def verify(self, *, fingerprints: list[str], action: str, params: dict[str, Any], executed_at: str,
               wait_s: float, attempts: int, poll_s: float) -> VerifyResult:
        self.sleep(wait_s)
        checks: list[Check] = []
        for attempt in range(1, attempts + 1):
            post = self.postcondition(action, params, executed_at)
            alert = self.alerts_resolved_after(fingerprints, executed_at)
            quiet = self.errors_quiet() if post.ok and alert.ok else Check("errors_quiet", False, "not checked yet")
            checks = [alert, quiet, post]
            if all(c.ok for c in checks):
                return VerifyResult(True, attempt, checks)
            if attempt < attempts:
                self.sleep(poll_s)
        return VerifyResult(False, attempts, checks)

    def wait_self_resolve(self, fingerprints: list[str], *, attempts: int, poll_s: float) -> VerifyResult:
        """For 'no action' decisions: prove the problem went away on its own."""
        checks: list[Check] = []
        for attempt in range(1, attempts + 1):
            states = [self.gateway.alert(fp) for fp in fingerprints]
            resolved = all(a is not None and a.status == "resolved" for a in states)
            checks = [Check("alert_resolved", resolved,
                            ", ".join(f"{a.rule} {a.status}" if a else "missing" for a in states))]
            if resolved:
                quiet = self.errors_quiet()
                checks.append(quiet)
                if quiet.ok:
                    return VerifyResult(True, attempt, checks)
            if attempt < attempts:
                self.sleep(poll_s)
        return VerifyResult(False, attempts, checks)
