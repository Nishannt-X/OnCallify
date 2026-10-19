"""The allowlist. Nothing outside ``REGISTRY`` can be dry-run, approved or executed.

Each action has an exact parameter schema (unknown or missing keys are rejected, types
and ranges are enforced), a human-readable blast radius, whether a standing contract may
cover it, and how long verification may take. The actual work is done by a runbook
(bash on Linux/macOS, PowerShell on Windows) with the same name.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

APPS = frozenset({"ledgerline"})
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


class ParamError(ValueError):
    """Action id or parameters are not allowlisted."""


def _app(v: Any) -> str:
    if not isinstance(v, str) or v not in APPS:
        raise ParamError(f"app must be one of {sorted(APPS)}")
    return v


def _commit(v: Any) -> str:
    if not isinstance(v, str) or not _COMMIT.match(v):
        raise ParamError("commit must be 7-40 lowercase hex characters")
    return v


def _pool_size(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 5 <= v <= 50:
        raise ParamError("size must be an integer between 5 and 50")
    return v


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    description: str
    params: dict[str, Callable[[Any], Any]]
    blast_radius: Callable[[dict[str, Any]], str]
    #: may a standing contract pre-approve it? Only if its params are stable across incidents.
    contractable: bool
    verify_wait_s: float
    verify_attempts: int
    #: what the action does to the service while it runs
    disruptive: bool


REGISTRY: dict[str, ActionSpec] = {
    "restart_app": ActionSpec(
        id="restart_app",
        description="Reload the application context in Tomcat (frees leaked connections and memory)",
        params={"app": _app},
        blast_radius=lambda p: f"{p['app']} is unavailable for a few seconds while Tomcat reloads it; "
                               "in-flight requests fail; in-memory state is lost",
        contractable=True,
        verify_wait_s=10,
        verify_attempts=12,
        disruptive=True,
    ),
    "revert_config_change": ActionSpec(
        id="revert_config_change",
        description="Revert one commit in the service's config repository (git revert)",
        params={"app": _app, "commit": _commit},
        blast_radius=lambda p: f"{p['app']} config returns to its values before commit {p['commit']}; "
                               "later unrelated changes are kept; the app hot-reloads config",
        # the commit differs every time: a standing approval would be a blank cheque
        contractable=False,
        verify_wait_s=10,
        verify_attempts=12,
        disruptive=False,
    ),
    "resize_pool": ActionSpec(
        id="resize_pool",
        description="Set the database connection pool size (committed to the config repository)",
        params={"app": _app, "size": _pool_size},
        blast_radius=lambda p: f"{p['app']} rebuilds its connection pool with {p['size']} connections; "
                               "up to that many database sessions",
        contractable=True,
        verify_wait_s=10,
        verify_attempts=12,
        disruptive=False,
    ),
    "enable_fx_fallback": ActionSpec(
        id="enable_fx_fallback",
        description="Serve last known FX rates when the provider fails (feature flag, committed)",
        params={"app": _app},
        blast_radius=lambda p: f"{p['app']} converts currencies at cached rates while the provider is down; "
                               "transfers are flagged fx_fallback=true for later reconciliation",
        contractable=True,
        verify_wait_s=10,
        verify_attempts=12,
        disruptive=False,
    ),
    "sync_db_credential": ActionSpec(
        id="sync_db_credential",
        description="Copy the current database password from the vault into the app's secret file",
        params={"app": _app},
        blast_radius=lambda p: f"{p['app']} reconnects to the database with the vault's current password; "
                               "the connection pool is rebuilt",
        contractable=True,
        verify_wait_s=10,
        verify_attempts=12,
        disruptive=False,
    ),
}


def get(action_id: str) -> ActionSpec:
    spec = REGISTRY.get(action_id)
    if spec is None:
        raise ParamError(f"action {action_id!r} is not allowlisted")
    return spec


def validate(action_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return a clean copy of *params* if they exactly match the schema, else raise."""
    spec = get(action_id)
    unexpected = set(params) - set(spec.params)
    if unexpected:
        raise ParamError(f"unexpected params: {sorted(unexpected)}")
    missing = set(spec.params) - set(params)
    if missing:
        raise ParamError(f"missing params: {sorted(missing)}")
    return {k: check(params[k]) for k, check in spec.params.items()}
