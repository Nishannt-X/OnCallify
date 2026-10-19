"""Builds the Evidence a diagnoser sees for one incident.

Live: logs and metric scrapes come from the gateway's event store, changes from the
change ledger. The same Evidence shape is saved into replay bundles, so a diagnoser
cannot tell (and must not be able to tell) a replay from a live incident.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from oncallify import logreduce
from oncallify.changes import ChangeLedger
from oncallify.clients import GatewayClient
from oncallify.model import Alert, Evidence, iso, parse_iso

#: which series the diagnoser gets, and how. Counters become per-scrape increases.
GAUGES = (
    "ledgerline_pool_active",
    "ledgerline_pool_max",
    "ledgerline_pool_waiting",
    "ledgerline_pool_idle",
    "ledgerline_config_version",
    "ledgerline_fx_fallback_enabled",
    "tomcat_threads_busy",
    "jvm_heap_used_bytes",
    "jvm_heap_max_bytes",
    "scrape_failed",
)
COUNTERS = ("ledgerline_errors_total", "ledgerline_http_requests_total", "ledgerline_fx_fallback_total")
_MAX_POINTS = 90


def _series_key(sample: dict[str, Any]) -> str | None:
    name = str(sample["name"])
    labels = sample.get("labels") or {}
    if name in GAUGES:
        return name
    if name == "ledgerline_errors_total":
        return f"errors:{labels.get('kind', '')}"
    if name == "ledgerline_http_requests_total":
        status = str(labels.get("status", ""))
        return f"requests:{status[0]}xx" if status else None
    if name == "ledgerline_fx_fallback_total":
        return "fx_fallback_used"
    if name == "ledgerline_http_request_duration_seconds_sum" and labels.get("route") == "POST /transfers":
        return "transfer_latency_sum"
    if name == "ledgerline_http_request_duration_seconds_count" and labels.get("route") == "POST /transfers":
        return "transfer_latency_count"
    return None


def metric_series(scrapes: list[dict[str, Any]], window_start: str) -> dict[str, list[list[float]]]:
    """Turn raw scrape events into [[t_seconds, value], ...] per series.

    Gauges are reported as-is. Counters are converted to increases between consecutive
    scrapes (a decrease means the process restarted; the new value is the increase).
    Latency becomes a mean per scrape interval from the histogram sum/count.
    """
    t0 = parse_iso(window_start)
    raw: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ev in sorted(scrapes, key=lambda e: str(e["ts"])):
        t = (parse_iso(str(ev["ts"])) - t0).total_seconds()
        totals: dict[str, float] = defaultdict(float)
        for s in (ev.get("body") or {}).get("samples") or []:
            key = _series_key(s)
            if key is not None:
                totals[key] += float(s["value"])
        for key, value in totals.items():
            raw[key].append((t, value))

    out: dict[str, list[list[float]]] = {}
    counter_keys = {k for k in raw if k.startswith(("errors:", "requests:")) or k == "fx_fallback_used"}
    for key, points in raw.items():
        if key in counter_keys:
            inc: list[list[float]] = []
            for (_, prev), (t, cur) in zip(points, points[1:], strict=False):
                inc.append([round(t, 2), cur - prev if cur >= prev else cur])
            out[key] = inc
        elif key not in ("transfer_latency_sum", "transfer_latency_count"):
            out[key] = [[round(t, 2), v] for t, v in points]

    sums, counts = raw.get("transfer_latency_sum", []), raw.get("transfer_latency_count", [])
    if sums and counts and len(sums) == len(counts):
        lat: list[list[float]] = []
        for i in range(1, len(sums)):
            dc = counts[i][1] - counts[i - 1][1]
            ds = sums[i][1] - sums[i - 1][1]
            if dc > 0 and ds >= 0:
                lat.append([round(sums[i][0], 2), round(ds / dc, 4)])
        out["transfer_latency_mean_s"] = lat

    return {k: _downsample(v) for k, v in out.items()}


def _downsample(points: list[list[float]]) -> list[list[float]]:
    if len(points) <= _MAX_POINTS:
        return points
    step = len(points) / _MAX_POINTS
    return [points[int(i * step)] for i in range(_MAX_POINTS)]


class EvidenceBuilder:
    def __init__(self, gateway: GatewayClient, ledger: ChangeLedger, *, lookback_s: int = 120):
        self.gateway = gateway
        self.ledger = ledger
        self.lookback_s = lookback_s

    def build(self, service: str, alerts: list[Alert], *, until: str) -> Evidence:
        first = min(parse_iso(a.starts_at) for a in alerts)
        start = first - timedelta(seconds=self.lookback_s)
        end = parse_iso(until)
        since_s, until_s = iso(start), iso(end)
        logs = [e["body"] for e in self.gateway.events(service, "log", since_s, until_s)]
        scrapes = self.gateway.events(service, "metrics", since_s, until_s)
        recent = iso(end - timedelta(seconds=15))
        return Evidence(
            service=service,
            window_start=since_s,
            window_end=until_s,
            alerts=alerts,
            logs=logreduce.summarize(logs, recent_since=recent),
            metrics=metric_series(scrapes, since_s),
            changes=self.ledger.changes(start, end),
        )
