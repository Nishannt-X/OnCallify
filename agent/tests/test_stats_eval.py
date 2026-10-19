"""Experiment statistics and the replay/report/gate machinery."""

from __future__ import annotations

import math

import pytest
from scipy import stats as sps

from oncallify.eval import stats
from oncallify.eval.corpus import Bundle, Truth
from oncallify.eval.replay import run_variant, score
from oncallify.eval.report import compare, gate, markdown, summarize
from oncallify.model import Diagnosis

from .conftest import CANONICAL, ev_bad_config


def test_mcnemar_matches_the_binomial_definition() -> None:
    a = [True] * 10 + [False] * 2 + [True] * 30
    b = [False] * 10 + [True] * 2 + [True] * 30
    r = stats.mcnemar_exact(a, b)
    assert (r.b, r.c) == (10, 2)
    assert r.p_value == pytest.approx(2 * sps.binom.cdf(2, 12, 0.5))
    assert stats.mcnemar_exact([True, False], [True, False]).p_value == 1.0


def test_holm_matches_a_hand_computed_example() -> None:
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": pytest.approx(0.03), "c": pytest.approx(0.06), "b": pytest.approx(0.06)}


def test_cluster_bootstrap_resamples_scenarios_not_runs() -> None:
    # 3 repeats of 10 scenarios: the interval must be as wide as for 10 independent values
    diffs = [1.0, 0.0] * 5
    clustered = [d for d in diffs for _ in range(3)]
    clusters = [f"s{i}" for i in range(10) for _ in range(3)]
    ci_c = stats.cluster_bootstrap_ci(clustered, clusters, seed=1)
    ci_naive = stats.cluster_bootstrap_ci(clustered, [str(i) for i in range(30)], seed=1)
    assert (ci_c.high - ci_c.low) > (ci_naive.high - ci_naive.low) * 1.4


def test_signflip_is_exact_for_small_samples() -> None:
    # 6 scenarios all favouring B: p = 2 / 2**6 for a two-sided exact test
    assert stats.cluster_signflip_pvalue([1.0] * 6, list("abcdef")) == pytest.approx(2 / 64)
    assert stats.cluster_signflip_pvalue([0.0] * 6, list("abcdef")) == 1.0


def test_srm_flags_a_broken_split() -> None:
    assert not stats.srm_check({"A": 510, "B": 490}, {"A": 1, "B": 1}).mismatch
    assert stats.srm_check({"A": 600, "B": 400}, {"A": 1, "B": 1}).mismatch
    assert not stats.srm_check({"A": 905, "B": 95}, {"A": 9, "B": 1}).mismatch


def test_power_and_mde_behave() -> None:
    assert stats.mcnemar_power(200, 0.05, 0.20) > stats.mcnemar_power(50, 0.05, 0.20)
    mde_small, mde_big = stats.mcnemar_mde(56, 0.25), stats.mcnemar_mde(500, 0.25)
    assert 0 < mde_big < mde_small < 0.25
    assert math.isnan(stats.mcnemar_mde(0, 0.25))


def test_msprt_pvalues_are_monotone_and_detect_a_real_effect() -> None:
    p = stats.msprt_pvalues([1.0] * 40, sigma2=1.0)
    assert all(x >= y for x, y in zip(p, p[1:], strict=False))
    assert p[-1] < 0.01


def test_noninferiority_passes_equal_and_fails_clearly_worse() -> None:
    clusters = [str(i) for i in range(40)]
    assert stats.noninferiority([0.0] * 40, clusters, margin=0.05).passed
    assert not stats.noninferiority([-1.0] * 10 + [0.0] * 30, clusters, margin=0.05).passed


def test_a_a_validation_rejects_at_about_the_nominal_rate() -> None:
    v = stats.validate_tests(n_sims=300, n_pairs=40, peek_n=200, seed=3)
    assert v.mcnemar_fpr <= 0.08 and v.signflip_fpr <= 0.08
    assert 0.90 <= v.bootstrap_coverage <= 0.99
    assert v.peeking_fpr_naive > 0.10  # peeking really does inflate false positives
    assert v.peeking_fpr_msprt <= 0.08


# ---- replay -------------------------------------------------------------------------------------

def _corpus() -> list[Bundle]:
    out = []
    for g in range(3):
        for cause, make in CANONICAL.items():
            ev = make()
            suspect = next((c.id for c in ev.changes if c.source == "config" and
                            ("pool.max" in c.keys or "fx.url" in c.keys)), None)
            out.append(Bundle(f"{cause}-g{g}", cause, f"g{g}", {}, "t", ev, Truth(cause, suspect)))
    return out


def test_scoring_requires_the_right_commit_for_a_revert() -> None:
    b = Bundle("x", "bad_config", "g0", {}, "t", ev_bad_config(herring=True), Truth("bad_config", "bad000000001"))
    right = score("v", b, Diagnosis("bad_config", 0.9, "", suspect_change="bad000000001"), 0)
    wrong = score("v", b, Diagnosis("bad_config", 0.9, "", suspect_change="4e771c9a0b12"), 0)
    assert right.action_correct and not right.unsafe
    assert wrong.cause_correct and not wrong.action_correct and wrong.unsafe  # reverting an innocent commit


def test_missed_and_unsafe_are_scored() -> None:
    b = Bundle("x", "connection_leak", "g0", {}, "t", CANONICAL["connection_leak"](), Truth("connection_leak"))
    assert score("v", b, Diagnosis("transient", 0.9, ""), 0).missed
    assert score("v", b, Diagnosis("fx_degraded", 0.9, ""), 0).unsafe
    assert not score("v", b, Diagnosis("unknown", 0.9, ""), 0).unsafe  # handing to a human is not unsafe


def test_replay_compare_and_gate_end_to_end() -> None:
    corpus = _corpus()
    v1 = run_variant("rules-v1", corpus)
    v2 = run_variant("rules-v2", corpus)
    ml = run_variant("ml-logreg", corpus)  # leave-one-group-out inside
    assert len(v1) == len(v2) == len(ml) == len(corpus)
    s1, s2 = summarize(v1), summarize(v2)
    assert s2["cause_correct"] > s1["cause_correct"]
    c = compare(v1, v2)
    assert c["n_scenarios"] == len(corpus) and {m["metric"] for m in c["metrics"]} == {
        "cause_correct", "action_correct", "unsafe", "missed"}
    assert gate(v1, v2)["passed"]
    assert not gate(v2, v1)["passed"]  # a regression is blocked
    md = markdown([s1, s2], [c], None, "note")
    assert "rules-v2 vs rules-v1" in md
