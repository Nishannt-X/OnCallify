"""Rule-based diagnosers: transparent baselines every learned variant has to beat."""

from __future__ import annotations

import time
from typing import Any

from oncallify import features
from oncallify.causes import Cause
from oncallify.diagnose.base import register
from oncallify.model import Diagnosis, Evidence

_INFRA = ("pool_exhausted", "db_auth", "db_unavailable", "fx_timeout", "fx_unavailable", "fx_bad_response",
          "out_of_memory", "internal")


def _dominant_kind(ev: Evidence) -> tuple[str | None, float]:
    kinds = {k: v for k, v in ev.logs.by_error_kind.items() if k in _INFRA}
    total = sum(kinds.values())
    if not total:
        return None, 0.0
    kind = max(kinds, key=lambda k: kinds[k])
    return kind, kinds[kind] / total


class RulesV1:
    """What a tired human does first: look at the most common error and pattern-match.

    Uses log error kinds only. No change history, no metric shape. Known blind spots
    (by design, they are what the experiments measure): it cannot tell a leak from an
    undersized pool or a slow provider (all show pool_exhausted), and it cannot tell a
    bad URL from a provider blip (both show fx_unavailable).
    """

    name = "rules-v1"

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        t0 = time.perf_counter()
        kind, share = _dominant_kind(evidence)
        heap_alert = any(a.rule == "LedgerlineHeapPressure" for a in evidence.alerts)
        mapping = {
            "pool_exhausted": Cause.CONNECTION_LEAK,
            "db_auth": Cause.CREDENTIAL_DRIFT,
            "fx_timeout": Cause.FX_DEGRADED,
            "fx_unavailable": Cause.BAD_CONFIG,
            "out_of_memory": Cause.MEMORY_LEAK,
        }
        if heap_alert and kind in (None, "out_of_memory", "internal"):
            cause, conf, why = Cause.MEMORY_LEAK, 0.7, "heap pressure alert"
        elif kind is None:
            cause, conf, why = Cause.TRANSIENT, 0.4, "no infrastructure errors in the window"
        else:
            cause = mapping.get(kind, Cause.UNKNOWN)
            conf = round(0.5 + 0.4 * share, 2)
            why = f"dominant error kind {kind} ({share:.0%} of infrastructure errors)"
        suspect = None
        if cause is Cause.BAD_CONFIG:
            # v1 blames the most recent config change, whatever it touched
            configs = [c for c in evidence.changes if c.source == "config"]
            suspect = configs[-1].id if configs else None
        return Diagnosis(
            cause=cause.value, confidence=conf, rationale=why, suspect_change=suspect,
            evidence_refs=[f"logs.by_error_kind.{kind}"] if kind else [], variant=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )


class RulesV2:
    """Adds the three things experienced on-call engineers check next:

    1. what changed (the change ledger), and does the change explain the symptom;
    2. metric shape (is the pool busy while threads are idle? is heap climbing?);
    3. is it still happening (errors in the last 15 s vs the window average).
    """

    name = "rules-v2"

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        t0 = time.perf_counter()
        f = features.extract(evidence)
        sc = features.suspect_changes(evidence)
        kind, share = _dominant_kind(evidence)
        refs: list[str] = []
        suspect: str | None = None

        def done(cause: Cause, conf: float, why: str) -> Diagnosis:
            return Diagnosis(cause=cause.value, confidence=round(conf, 2), rationale=why, evidence_refs=refs,
                             suspect_change=suspect, variant=self.name,
                             latency_ms=(time.perf_counter() - t0) * 1000)

        if f["alert_down"] and (f["heap_ratio_rise"] > 0.25 or f["heap_ratio_max"] >= 0.8):
            refs += ["alerts.LedgerlineDown", "metrics.jvm_heap_used_bytes"]
            return done(Cause.MEMORY_LEAK, 0.8, "the service stopped answering after its heap climbed to "
                                                f"{f['heap_ratio_max']:.0%}")

        if f["infra_errors"] == 0 and f["heap_ratio_max"] < 0.85:
            return done(Cause.TRANSIENT, 0.5, "no infrastructure errors and no resource pressure")

        if f["heap_ratio_max"] >= 0.85 and (f["heap_ratio_rise"] > 0.25 or f["share_out_of_memory"] > 0):
            refs += ["metrics.jvm_heap_used_bytes"]
            return done(Cause.MEMORY_LEAK, 0.85, f"heap peaked at {f['heap_ratio_max']:.0%} and rose "
                                                f"{f['heap_ratio_rise']:.0%} over the window")

        if kind == "db_auth":
            refs += ["logs.by_error_kind.db_auth"]
            if sc.vault_rotation:
                refs += [f"changes.{sc.vault_rotation.id}"]
                return done(Cause.CREDENTIAL_DRIFT, 0.92, "database rejects the password and the secret was "
                                                          f"rotated at {sc.vault_rotation.ts}")
            return done(Cause.CREDENTIAL_DRIFT, 0.7, "database rejects the password")

        if kind == "fx_unavailable":
            refs += ["logs.by_error_kind.fx_unavailable"]
            if sc.fx_url is not None and f["msg_connection_refused"]:
                suspect = sc.fx_url.id
                refs += [f"changes.{sc.fx_url.id}"]
                return done(Cause.BAD_CONFIG, 0.9, f"fx.url changed in {sc.fx_url.id} and the provider now "
                                                   "refuses connections")
            if f["errors_still_active"] < 0.3:
                return done(Cause.TRANSIENT, 0.75, "provider errors have stopped on their own")
            if sc.fx_url is not None:
                suspect = sc.fx_url.id
                return done(Cause.BAD_CONFIG, 0.6, f"fx.url changed in {sc.fx_url.id}")
            return done(Cause.FX_DEGRADED, 0.55, "provider is failing and nothing changed on our side")

        if kind in ("pool_exhausted", "fx_timeout"):
            if sc.pool_shrink is not None:
                suspect = sc.pool_shrink.id
                refs += [f"changes.{sc.pool_shrink.id}", "metrics.ledgerline_pool_max"]
                return done(Cause.POOL_UNDERSIZED, 0.88, f"pool limits were reduced in {sc.pool_shrink.id} "
                                                         "and the pool is now exhausted")
            # High latency alone proves nothing here: a leak or a small pool also makes every
            # request wait the pool's maxWait. FX trouble must show up as FX timeouts.
            if f["share_fx_timeout"] > 0.15:
                refs += ["logs.by_error_kind.fx_timeout", "metrics.transfer_latency_mean_s"]
                return done(Cause.FX_DEGRADED, 0.8, "provider calls time out while holding connections")
            refs += ["metrics.ledgerline_pool_active", "logs.by_error_kind.pool_exhausted"]
            if f["pool_held_per_busy_thread"] >= 1.5:
                return done(Cause.CONNECTION_LEAK, 0.85, "connections stay borrowed while few request threads "
                                                         "are busy: they are not being returned")
            # by elimination: pool size unchanged, provider healthy, pool still exhausted
            return done(Cause.CONNECTION_LEAK, 0.75, "pool exhausted with no pool change and no provider errors: "
                                                     "connections are not being returned")

        if kind == "out_of_memory":
            return done(Cause.MEMORY_LEAK, 0.8, "out-of-memory errors")

        return done(Cause.UNKNOWN, 0.3, f"no rule explains dominant error kind {kind} ({share:.0%})")


@register("rules-v1")
def _v1(**_: Any) -> RulesV1:
    return RulesV1()


@register("rules-v2")
def _v2(**_: Any) -> RulesV2:
    return RulesV2()
