"""From a diagnosis to at most ONE allowlisted action with exact parameters.

The planner is deliberately boring and shared by every diagnoser variant, so an
experiment compares diagnosis quality only. Its rules:

* low confidence or unknown cause -> no action, hand to a human;
* a revert needs a suspect change that exists in the evidence and touched the
  relevant keys; otherwise fall back (pool) or hand to a human (config);
* the result is validated against the allowlist before it is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oncallify import actions
from oncallify.causes import PRIMARY_ACTION, Cause, parse
from oncallify.model import Diagnosis, Evidence

MIN_CONFIDENCE = 0.5


@dataclass(slots=True)
class Plan:
    action: str  # an allowlisted action id, or "none"
    params: dict[str, Any]
    reason: str
    escalate: bool = False


def plan(diagnosis: Diagnosis, evidence: Evidence, app: str = "ledgerline") -> Plan:
    cause = parse(diagnosis.cause)
    if cause is Cause.UNKNOWN:
        return Plan("none", {}, "cause unknown: a human should look", escalate=True)
    if diagnosis.confidence < MIN_CONFIDENCE:
        return Plan("none", {}, f"confidence {diagnosis.confidence:.2f} is below {MIN_CONFIDENCE}: a human "
                                "should look", escalate=True)
    action = PRIMARY_ACTION[cause]
    if action == "none":
        return Plan("none", {}, "transient: no change to production; watch for recurrence")

    params: dict[str, Any] = {"app": app}
    if action == "revert_config_change":
        change = next((c for c in evidence.changes if c.id == diagnosis.suspect_change and c.source == "config"),
                      None)
        if change is None:
            if cause is Cause.POOL_UNDERSIZED:
                return _resize_fallback(evidence, app)
            return Plan("none", {}, "no config change in the evidence to revert: a human should look",
                        escalate=True)
        params["commit"] = change.id
    try:
        actions.validate(action, params)
    except actions.ParamError as e:
        # the allowlist said no: that is a reason to ask a human, never to crash
        return Plan("none", {}, f"planned {action} was refused by the allowlist ({e}): a human should look",
                    escalate=True)
    return Plan(action, params, f"{cause.value} -> {action}")


def _resize_fallback(evidence: Evidence, app: str) -> Plan:
    pool_max = [v for _, v in evidence.metrics.get("ledgerline_pool_max", [])]
    target = int(max(pool_max)) if pool_max else 10
    target = min(50, max(5, target))
    params = {"app": app, "size": target}
    actions.validate("resize_pool", params)
    return Plan("resize_pool", params, f"pool undersized but no revertable change: restore size {target}")
