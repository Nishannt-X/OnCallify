"""The cause taxonomy and which actions are acceptable for each cause.

This table is the ground truth the experiments score against, and it is also the
planner's map from "what is wrong" to "what is allowed to fix it". Keeping both in one
place means the evaluation and the product cannot drift apart.
"""

from __future__ import annotations

from enum import StrEnum


class Cause(StrEnum):
    CONNECTION_LEAK = "connection_leak"
    POOL_UNDERSIZED = "pool_undersized"
    BAD_CONFIG = "bad_config"
    FX_DEGRADED = "fx_degraded"
    CREDENTIAL_DRIFT = "credential_drift"
    MEMORY_LEAK = "memory_leak"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


#: action ids considered a correct response to each cause. "none" means: do not touch
#: production, let it recover (transient) or hand it to a human (unknown).
ACCEPTABLE_ACTIONS: dict[Cause, frozenset[str]] = {
    Cause.CONNECTION_LEAK: frozenset({"restart_app"}),
    Cause.POOL_UNDERSIZED: frozenset({"revert_config_change", "resize_pool"}),
    Cause.BAD_CONFIG: frozenset({"revert_config_change"}),
    Cause.FX_DEGRADED: frozenset({"enable_fx_fallback"}),
    Cause.CREDENTIAL_DRIFT: frozenset({"sync_db_credential"}),
    Cause.MEMORY_LEAK: frozenset({"restart_app"}),
    Cause.TRANSIENT: frozenset({"none"}),
    Cause.UNKNOWN: frozenset({"none"}),
}

#: the action the planner chooses for each cause
PRIMARY_ACTION: dict[Cause, str] = {
    Cause.CONNECTION_LEAK: "restart_app",
    Cause.POOL_UNDERSIZED: "revert_config_change",
    Cause.BAD_CONFIG: "revert_config_change",
    Cause.FX_DEGRADED: "enable_fx_fallback",
    Cause.CREDENTIAL_DRIFT: "sync_db_credential",
    Cause.MEMORY_LEAK: "restart_app",
    Cause.TRANSIENT: "none",
    Cause.UNKNOWN: "none",
}

#: causes whose fix is a revert of one specific change (the planner needs a commit)
NEEDS_SUSPECT_CHANGE: frozenset[Cause] = frozenset({Cause.POOL_UNDERSIZED, Cause.BAD_CONFIG})

DIAGNOSABLE: tuple[Cause, ...] = tuple(c for c in Cause if c is not Cause.UNKNOWN)


def parse(value: str) -> Cause:
    try:
        return Cause(value.strip().lower())
    except ValueError:
        return Cause.UNKNOWN
