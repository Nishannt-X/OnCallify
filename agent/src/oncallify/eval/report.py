"""Turns replay outcomes into a comparison report (JSON + Markdown) and a CI gate."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from oncallify.eval import stats
from oncallify.eval.replay import Outcome, confusion

METRICS = ("cause_correct", "action_correct", "unsafe", "missed")
LOWER_IS_BETTER = {"unsafe", "missed"}


def _rate(outs: list[Outcome], metric: str) -> float:
    return float(np.mean([getattr(o, metric) for o in outs])) if outs else float("nan")


def per_family(outs: list[Outcome], metric: str) -> dict[str, float]:
    by: dict[str, list[Outcome]] = defaultdict(list)
    for o in outs:
        by[o.family].append(o)
    return {f: round(_rate(v, metric), 3) for f, v in sorted(by.items())}


def summarize(outs: list[Outcome]) -> dict[str, Any]:
    return {
        "variant": outs[0].variant if outs else "",
        "n_runs": len(outs),
        "n_scenarios": len({o.scenario_id for o in outs}),
        **{m: round(_rate(outs, m), 3) for m in METRICS},
        "latency_ms_p50": round(float(np.median([o.latency_ms for o in outs])), 2) if outs else None,
        "tokens_mean": round(float(np.mean([o.tokens for o in outs])), 1) if outs else None,
        "cause_correct_by_family": per_family(outs, "cause_correct"),
        "confusion": confusion(outs),
    }


def _pairs(a: list[Outcome], b: list[Outcome]) -> list[tuple[Outcome, Outcome]]:
    """Pair runs by scenario. If one side is deterministic (1 run) and the other has k
    repeats, the single run is reused against each repeat."""
    by_b: dict[str, list[Outcome]] = defaultdict(list)
    for o in b:
        by_b[o.scenario_id].append(o)
    by_a: dict[str, list[Outcome]] = defaultdict(list)
    for o in a:
        by_a[o.scenario_id].append(o)
    pairs = []
    for sid in sorted(set(by_a) & set(by_b)):
        ra, rb = sorted(by_a[sid], key=lambda o: o.repeat), sorted(by_b[sid], key=lambda o: o.repeat)
        k = max(len(ra), len(rb))
        for i in range(k):
            pairs.append((ra[i % len(ra)], rb[i % len(rb)]))
    return pairs


@dataclass(slots=True)
class MetricComparison:
    metric: str
    baseline: float
    candidate: float
    diff: float
    ci_low: float
    ci_high: float
    p_value: float
    test: str
    p_holm: float = float("nan")


def compare(baseline: list[Outcome], candidate: list[Outcome], *, seed: int = 0) -> dict[str, Any]:
    pairs = _pairs(baseline, candidate)
    if not pairs:
        raise ValueError("no scenarios in common")
    clusters = [pa.scenario_id for pa, _ in pairs]
    repeated = len(pairs) > len(set(clusters))
    rows: list[MetricComparison] = []
    for m in METRICS:
        xa = [bool(getattr(pa, m)) for pa, _ in pairs]
        xb = [bool(getattr(pb, m)) for _, pb in pairs]
        diffs = [float(y) - float(x) for x, y in zip(xa, xb, strict=True)]
        ci = stats.cluster_bootstrap_ci(diffs, clusters, seed=seed)
        if repeated:
            p, test = stats.cluster_signflip_pvalue(diffs, clusters, seed=seed), "cluster sign-flip"
        else:
            p, test = stats.mcnemar_exact(xa, xb).p_value, "exact McNemar"
        rows.append(MetricComparison(m, round(float(np.mean(xa)), 3), round(float(np.mean(xb)), 3),
                                     round(ci.estimate, 3), round(ci.low, 3), round(ci.high, 3), round(p, 4), test))
    adj = stats.holm({r.metric: r.p_value for r in rows})
    for r in rows:
        r.p_holm = round(adj[r.metric], 4)

    xa = [pa.cause_correct for pa, _ in pairs]
    xb = [pb.cause_correct for _, pb in pairs]
    discordant = float(np.mean([x != y for x, y in zip(xa, xb, strict=True)]))
    n = len(set(clusters))
    return {
        "baseline": baseline[0].variant,
        "candidate": candidate[0].variant,
        "n_scenarios": n,
        "n_pairs": len(pairs),
        "metrics": [asdict(r) for r in rows],
        "power_note": {
            "discordant_share": round(discordant, 3),
            "mde_cause_correct_80pct_power": round(stats.mcnemar_mde(n, max(discordant, 0.05)), 3),
            "text": (f"With {n} paired scenarios and {discordant:.0%} discordant pairs, a difference in cause "
                     f"accuracy smaller than about {stats.mcnemar_mde(n, max(discordant, 0.05)):.0%} would usually "
                     "go undetected (80% power, alpha 0.05)."),
        },
    }


def gate(baseline: list[Outcome], candidate: list[Outcome], *, accuracy_margin: float = 0.05,
         unsafe_margin: float = 0.02, seed: int = 0) -> dict[str, Any]:
    """Merge gate for a prompt/model/diagnoser change: the candidate must be non-inferior
    to the baseline on action correctness AND on the unsafe-action rate."""
    pairs = _pairs(baseline, candidate)
    clusters = [pa.scenario_id for pa, _ in pairs]
    acc = [float(pb.action_correct) - float(pa.action_correct) for pa, pb in pairs]
    uns = [float(pb.unsafe) - float(pa.unsafe) for pa, pb in pairs]
    ni_acc = stats.noninferiority(acc, clusters, margin=accuracy_margin, seed=seed)
    ni_uns = stats.noninferiority(uns, clusters, margin=unsafe_margin, higher_is_better=False, seed=seed)
    return {
        "baseline": baseline[0].variant,
        "candidate": candidate[0].variant,
        "action_correct": {"margin": accuracy_margin, "lower_bound": round(ni_acc.lower_bound, 3),
                           "passed": ni_acc.passed},
        "unsafe": {"margin": unsafe_margin, "lower_bound_of_improvement": round(ni_uns.lower_bound, 3),
                   "passed": ni_uns.passed},
        "passed": ni_acc.passed and ni_uns.passed,
    }


def markdown(summaries: list[dict[str, Any]], comparisons: list[dict[str, Any]],
             validation: dict[str, Any] | None, corpus_note: str) -> str:
    out = ["# OnCallify experiment report", "", corpus_note, "", "## Variants", "",
           "| variant | scenarios | runs | cause correct | action correct | unsafe | missed | p50 latency ms |",
           "|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        out.append(f"| {s['variant']} | {s['n_scenarios']} | {s['n_runs']} | {s['cause_correct']:.1%} | "
                   f"{s['action_correct']:.1%} | {s['unsafe']:.1%} | {s['missed']:.1%} | {s['latency_ms_p50']} |")
    for c in comparisons:
        out += ["", f"## {c['candidate']} vs {c['baseline']} (paired, {c['n_scenarios']} scenarios)", "",
                "| metric | baseline | candidate | diff | 95% CI | p | p (Holm) | test |",
                "|---|---|---|---|---|---|---|---|"]
        for m in c["metrics"]:
            out.append(f"| {m['metric']} | {m['baseline']:.1%} | {m['candidate']:.1%} | {m['diff']:+.1%} | "
                       f"[{m['ci_low']:+.1%}, {m['ci_high']:+.1%}] | {m['p_value']:.4f} | {m['p_holm']:.4f} | "
                       f"{m['test']} |")
        out += ["", f"_{c['power_note']['text']}_"]
    for s in summaries:
        out += ["", f"### Where {s['variant']} goes wrong (true cause -> predicted)", ""]
        for true, row in s["confusion"].items():
            wrong = {p: n for p, n in row.items() if p != true}
            if wrong:
                out.append(f"- {true}: " + ", ".join(f"{p} x{n}" for p, n in wrong.items()))
    if validation:
        out += ["", "## Are the tests themselves trustworthy? (A/A simulation)", "",
                f"- {validation['n_sims']} simulated experiments with no real difference, {validation['n_pairs']} "
                "paired scenarios each:",
                f"  - exact McNemar false-positive rate: {validation['mcnemar_fpr']:.1%} (nominal 5%)",
                f"  - cluster sign-flip false-positive rate: {validation['signflip_fpr']:.1%} (nominal 5%)",
                f"  - bootstrap 95% CI coverage: {validation['bootstrap_coverage']:.1%} (nominal 95%)",
                f"- Peeking at a running experiment {validation['peeking_looks']} times:",
                f"  - naive z-test, stop at first p<0.05: {validation['peeking_fpr_naive']:.1%} false positives",
                f"  - always-valid mSPRT p-values: {validation['peeking_fpr_msprt']:.1%} false positives"]
    return "\n".join(out) + "\n"
