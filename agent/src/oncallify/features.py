"""Named, interpretable features computed from Evidence.

Used by the rule diagnosers (as readable conditions) and by the ML diagnoser (as its
input vector). Every feature is something a human on call would look at, which keeps
the ML model explainable: its top features read like a runbook.
"""

from __future__ import annotations

from dataclasses import dataclass

from oncallify.model import Change, Evidence, parse_iso

ERROR_KINDS = (
    "pool_exhausted",
    "db_auth",
    "db_unavailable",
    "fx_timeout",
    "fx_unavailable",
    "fx_bad_response",
    "out_of_memory",
    "internal",
)


def _values(ev: Evidence, key: str) -> list[float]:
    return [p[1] for p in ev.metrics.get(key, [])]


def _tail(ev: Evidence, key: str, frac: float = 0.25) -> list[float]:
    vals = _values(ev, key)
    if not vals:
        return []
    n = max(1, int(len(vals) * frac))
    return vals[-n:]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@dataclass(slots=True)
class SuspectChanges:
    pool_shrink: Change | None
    fx_url: Change | None
    vault_rotation: Change | None
    other_config: list[Change]


def suspect_changes(ev: Evidence) -> SuspectChanges:
    """Classify the window's changes by what they touched (facts, not verdicts)."""
    pool_shrink = fx_url = vault = None
    other: list[Change] = []
    for c in ev.changes:
        if c.source == "vault":
            if "ledgerline-db" in c.summary or "ledgerline-db" in c.keys.get("secret", [None, ""])[1:]:
                vault = c
            else:
                other.append(c)
            continue
        if c.source != "config":
            continue
        touched = False
        if "pool.max" in c.keys:
            old, new = c.keys["pool.max"]
            try:
                if old is not None and new is not None and int(new) < int(old):
                    pool_shrink = c
                    touched = True
            except ValueError:
                pass
        if "pool.max.wait.ms" in c.keys and pool_shrink is None:
            old, new = c.keys["pool.max.wait.ms"]
            try:
                if old is not None and new is not None and int(new) < int(old):
                    pool_shrink = c
                    touched = True
            except ValueError:
                pass
        if "fx.url" in c.keys:
            fx_url = c
            touched = True
        if not touched:
            other.append(c)
    return SuspectChanges(pool_shrink, fx_url, vault, other)


def extract(ev: Evidence) -> dict[str, float]:
    f: dict[str, float] = {}
    kinds = ev.logs.by_error_kind
    infra_total = sum(v for k, v in kinds.items() if k != "business")
    f["infra_errors"] = float(infra_total)
    for k in ERROR_KINDS:
        f[f"share_{k}"] = kinds.get(k, 0) / infra_total if infra_total else 0.0
    recent_total = sum(ev.logs.recent_error_kinds.values())
    f["errors_recent"] = float(recent_total)
    # are errors still happening at the end of the window, relative to the window average?
    span = max(1.0, (parse_iso(ev.window_end) - parse_iso(ev.window_start)).total_seconds())
    f["errors_still_active"] = (recent_total / 15.0) / max(1e-9, infra_total / span) if infra_total else 0.0

    # message-level hints that a human reads in the exemplar
    exemplars = " ".join(t.exemplar.lower() for t in ev.logs.templates)
    f["msg_connection_refused"] = 1.0 if "connection refused" in exemplars else 0.0
    f["msg_provider_503"] = 1.0 if "returned 503" in exemplars else 0.0
    f["msg_password_failed"] = 1.0 if "password authentication failed" in exemplars else 0.0
    f["msg_timed_out"] = 1.0 if "timed out" in exemplars else 0.0

    heap_used, heap_max = _values(ev, "jvm_heap_used_bytes"), _values(ev, "jvm_heap_max_bytes")
    if heap_used and heap_max and heap_max[-1] > 0:
        ratios = [u / heap_max[-1] for u in heap_used]
        f["heap_ratio_max"] = max(ratios)
        n = len(ratios)
        first, last = _mean(ratios[: max(1, n // 4)]), _mean(ratios[-max(1, n // 4):])
        f["heap_ratio_rise"] = last - first
    else:
        f["heap_ratio_max"] = 0.0
        f["heap_ratio_rise"] = 0.0

    pool_active, pool_max = _tail(ev, "ledgerline_pool_active"), _tail(ev, "ledgerline_pool_max")
    threads = _tail(ev, "tomcat_threads_busy")
    pmax = pool_max[-1] if pool_max else 0.0
    f["pool_saturation"] = (_mean(pool_active) / pmax) if pmax else 0.0
    # leak signature: connections are held while few request threads are doing work
    f["pool_held_per_busy_thread"] = _mean(pool_active) / max(1.0, _mean(threads)) if pool_active else 0.0
    f["pool_waiting"] = _mean(_tail(ev, "ledgerline_pool_waiting"))
    f["pool_max_now"] = pmax
    pmax_all = _values(ev, "ledgerline_pool_max")
    f["pool_max_dropped"] = 1.0 if pmax_all and pmax_all[-1] < max(pmax_all) else 0.0

    lat = _values(ev, "transfer_latency_mean_s")
    f["latency_mean_tail"] = _mean(lat[-max(1, len(lat) // 4):]) if lat else 0.0
    f["latency_mean_head"] = _mean(lat[: max(1, len(lat) // 4)]) if lat else 0.0

    ok = sum(_values(ev, "requests:2xx"))
    bad = sum(_values(ev, "requests:5xx"))
    f["error_ratio_5xx"] = bad / (ok + bad) if (ok + bad) else 0.0

    sc = suspect_changes(ev)
    f["chg_pool_shrink"] = 1.0 if sc.pool_shrink else 0.0
    f["chg_fx_url"] = 1.0 if sc.fx_url else 0.0
    f["chg_vault_rotation"] = 1.0 if sc.vault_rotation else 0.0
    f["chg_other"] = float(len(sc.other_config))

    rules = {a.rule for a in ev.alerts}
    f["alert_heap"] = 1.0 if "LedgerlineHeapPressure" in rules else 0.0
    f["alert_pool"] = 1.0 if "LedgerlinePoolSaturated" in rules else 0.0
    f["alert_errors"] = 1.0 if "LedgerlineInfraErrors" in rules else 0.0
    f["alert_down"] = 1.0 if "LedgerlineDown" in rules else 0.0
    f["scrape_failed_share"] = _mean(_values(ev, "scrape_failed"))
    return f


FEATURE_NAMES: tuple[str, ...] = tuple(
    extract(
        Evidence.from_dict({
            "service": "x",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-01T00:02:00Z",
            "alerts": [],
            "logs": {"total_lines": 0, "by_level": {}, "by_error_kind": {}, "templates": []},
            "metrics": {},
            "changes": [],
        })
    ).keys()
)
