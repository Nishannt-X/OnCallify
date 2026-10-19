"""Statistics for paired A/B comparisons of diagnoser variants.

Design of the comparison:

* PAIRED: every scenario is replayed through both variants, so each scenario is its
  own control. That removes scenario difficulty from the comparison and makes a small
  corpus far more sensitive than two independent samples would be.
* CLUSTERED: a nondeterministic variant (an LLM) is run k times per scenario. The k
  runs of one scenario are correlated, so the scenario, not the run, is the unit of
  resampling (cluster bootstrap, cluster sign-flip test). Treating k*n runs as
  independent would shrink the intervals and inflate false positives.

Every function here is checked by ``validate_tests``: under a true null, each test must
reject at about its nominal rate (A/A validation).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

# ---- paired binary: exact McNemar ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class McNemar:
    b: int  # A right, B wrong
    c: int  # A wrong, B right
    p_value: float


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> McNemar:
    """Two-sided exact McNemar test for paired binary outcomes.

    Only discordant pairs carry information; under H0 each discordant pair is equally
    likely to favour A or B, so the count favouring B is Binomial(b + c, 1/2).
    """
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    n10 = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    n01 = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = n10 + n01
    if n == 0:
        return McNemar(n10, n01, 1.0)
    p = 2.0 * sps.binom.cdf(min(n10, n01), n, 0.5)
    return McNemar(n10, n01, min(1.0, float(p)))


# ---- clustered paired differences -----------------------------------------------------------


def cluster_means(values: Sequence[float], clusters: Sequence[str]) -> np.ndarray:
    """Mean of *values* within each cluster, in first-seen cluster order."""
    order: dict[str, list[float]] = {}
    for v, c in zip(values, clusters, strict=True):
        order.setdefault(c, []).append(float(v))
    return np.array([np.mean(vs) for vs in order.values()])


@dataclass(frozen=True, slots=True)
class Interval:
    estimate: float
    low: float
    high: float
    level: float


def cluster_bootstrap_ci(diffs: Sequence[float], clusters: Sequence[str], *, n_boot: int = 10_000,
                         level: float = 0.95, seed: int = 0) -> Interval:
    """Percentile bootstrap CI for the mean paired difference, resampling clusters.

    Each cluster contributes its mean difference; we resample clusters with replacement.
    (With one run per scenario this is the ordinary paired bootstrap.)
    """
    per = cluster_means(diffs, clusters)
    if len(per) == 0:
        return Interval(0.0, 0.0, 0.0, level)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(per), size=(n_boot, len(per)))
    boots = per[idx].mean(axis=1)
    alpha = 1 - level
    return Interval(float(per.mean()), float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2)),
                    level)


def cluster_signflip_pvalue(diffs: Sequence[float], clusters: Sequence[str], *, n_perm: int = 20_000,
                            seed: int = 0) -> float:
    """Two-sided permutation p-value for H0: mean paired difference = 0.

    Under H0 the labels A/B are exchangeable within a scenario, so each scenario's mean
    difference is equally likely to have either sign. Exact enumeration when the number
    of non-zero clusters is small, Monte Carlo otherwise.
    """
    per = cluster_means(diffs, clusters)
    per = per[per != 0]
    if len(per) == 0:
        return 1.0
    observed = abs(per.sum())
    if len(per) <= 16:
        signs = np.array(np.meshgrid(*[[-1, 1]] * len(per))).reshape(len(per), -1).T
        sums = np.abs(signs @ per)
        return float(np.mean(sums >= observed - 1e-12))
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(per)))
    sums = np.abs(signs @ per)
    # +1 correction so a Monte Carlo p-value is never exactly zero
    return float((np.sum(sums >= observed - 1e-12) + 1) / (n_perm + 1))


# ---- multiple comparisons -------------------------------------------------------------------


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (controls family-wise error, uniformly more
    powerful than Bonferroni)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted


# ---- power ------------------------------------------------------------------------------------


def mcnemar_power(n: int, p10: float, p01: float, alpha: float = 0.05) -> float:
    """Approximate power of McNemar's test (Connor 1987) for n pairs where a pair favours
    A with prob p10 and favours B with prob p01."""
    psi = p10 + p01
    delta = abs(p01 - p10)
    if n <= 0 or psi <= 0 or delta == 0:
        return alpha if delta == 0 else 0.0
    za = sps.norm.ppf(1 - alpha / 2)
    denom = math.sqrt(max(psi - delta**2, 1e-12))
    z = (delta * math.sqrt(n) - za * math.sqrt(psi)) / denom
    return float(sps.norm.cdf(z))


def mcnemar_mde(n: int, discordance: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest accuracy difference detectable with *power* given n pairs and the
    expected share of discordant pairs. This is the number to quote instead of
    overclaiming: 'with 56 scenarios we can only detect differences of X points or more'."""
    if n <= 0 or discordance <= 0:
        return float("nan")
    lo, hi = 0.0, discordance
    for _ in range(60):
        mid = (lo + hi) / 2
        p01 = (discordance + mid) / 2
        p10 = (discordance - mid) / 2
        if mcnemar_power(n, p10, p01, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi if mcnemar_power(n, (discordance - hi) / 2, (discordance + hi) / 2, alpha) >= power else float("nan")


# ---- sample ratio mismatch ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SRM:
    observed: dict[str, int]
    expected: dict[str, float]
    p_value: float
    mismatch: bool


def srm_check(observed: dict[str, int], weights: dict[str, float], threshold: float = 0.001) -> SRM:
    """Chi-square goodness-of-fit of arm counts against the configured split.

    A tiny p-value means assignment or logging is broken (a bug, not a result), and the
    experiment's outcome metrics must not be trusted. The threshold is strict (0.001)
    because this check runs continuously and false alarms are costly.
    """
    arms = sorted(weights)
    total = sum(observed.get(a, 0) for a in arms)
    wsum = sum(weights[a] for a in arms)
    expected = {a: total * weights[a] / wsum for a in arms}
    if total == 0:
        return SRM({a: 0 for a in arms}, expected, 1.0, False)
    obs = [observed.get(a, 0) for a in arms]
    exp = [expected[a] for a in arms]
    p = float(sps.chisquare(obs, exp).pvalue)
    return SRM({a: observed.get(a, 0) for a in arms}, expected, p, p < threshold)


# ---- non-inferiority -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NonInferiority:
    margin: float
    lower_bound: float
    passed: bool


def noninferiority(diffs: Sequence[float], clusters: Sequence[str], *, margin: float, level: float = 0.95,
                   seed: int = 0, higher_is_better: bool = True, n_boot: int = 10_000) -> NonInferiority:
    """One-sided test that candidate is not worse than baseline by more than *margin*.

    diffs are candidate - baseline. For a metric where higher is better, pass if the
    one-sided lower confidence bound of the mean difference is above -margin. For a
    metric where lower is better (unsafe rate), the sign is flipped first.
    """
    d = [x if higher_is_better else -x for x in diffs]
    # one-sided bound at `level` = two-sided interval at 2*level - 1
    ci = cluster_bootstrap_ci(d, clusters, n_boot=n_boot, level=2 * level - 1, seed=seed)
    return NonInferiority(margin, ci.low, ci.low > -margin)


# ---- sequential testing (always-valid p-values) ---------------------------------------------


def msprt_pvalues(diffs: Sequence[float], *, tau2: float = 0.05, sigma2: float | None = None) -> list[float]:
    """Always-valid p-values for H0: mean difference = 0 (mixture SPRT, Johari et al. 2017).

    Classic tests are only valid if you look once. Peeking at a running experiment and
    stopping on p < 0.05 inflates false positives badly. These p-values can be checked
    after every observation and remain valid at any stopping time.
    """
    x = np.asarray(diffs, dtype=float)
    out: list[float] = []
    p = 1.0
    s2 = float(np.var(x, ddof=1)) if sigma2 is None and len(x) > 1 else (sigma2 or 0.25)
    s2 = max(s2, 1e-6)
    total = 0.0
    for n, v in enumerate(x, start=1):
        total += v
        mean = total / n
        lam = math.sqrt(s2 / (s2 + n * tau2)) * math.exp(n**2 * tau2 * mean**2 / (2 * s2 * (s2 + n * tau2)))
        p = min(p, 1.0 / lam)
        out.append(p)
    return out


# ---- A/A validation of the tests themselves -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Validation:
    n_sims: int
    n_pairs: int
    mcnemar_fpr: float
    signflip_fpr: float
    bootstrap_coverage: float
    peeking_looks: int
    peeking_fpr_naive: float
    peeking_fpr_msprt: float


def _paired_null(rng: np.random.Generator, n: int, p: float, rho: float) -> tuple[np.ndarray, np.ndarray]:
    """Correlated paired binaries with equal marginals (a true null)."""
    a = rng.random(n) < p
    copy = rng.random(n) < rho
    b = np.where(copy, a, rng.random(n) < p)
    return a, b


def validate_tests(*, n_sims: int = 2000, n_pairs: int = 56, p: float = 0.7, rho: float = 0.5, alpha: float = 0.05,
                   peek_n: int = 500, peek_every: int = 10, seed: int = 0,
                   progress: Callable[[int], None] | None = None) -> Validation:
    """Simulate many A/A experiments and measure how often each test cries wolf.

    Part 1, the tests used on the corpus (paired binary outcomes, n_pairs scenarios):
    McNemar and the sign-flip test should reject at most ~alpha (exact tests on discrete
    data are conservative), and the bootstrap 95% CI should cover the true zero ~95%.

    Part 2, why the live experiment uses always-valid p-values: a continuous paired
    metric (think time-to-diagnosis) is monitored every ``peek_every`` incidents up to
    ``peek_n``. Stopping at the first z-test with p < alpha rejects far more than alpha
    of the time; the mSPRT p-value, checked at the same looks, stays below alpha.
    """
    rng = np.random.default_rng(seed)
    mc = sf = cover = peek_naive = peek_msprt = 0
    clusters = [str(i) for i in range(n_pairs)]
    looks = list(range(peek_every, peek_n + 1, peek_every))
    z_crit = sps.norm.ppf(1 - alpha / 2)
    for i in range(n_sims):
        a, b = _paired_null(rng, n_pairs, p, rho)
        if mcnemar_exact(list(a), list(b)).p_value < alpha:
            mc += 1
        diffs = (b.astype(float) - a.astype(float)).tolist()
        if cluster_signflip_pvalue(diffs, clusters, n_perm=2000, seed=i) < alpha:
            sf += 1
        ci = cluster_bootstrap_ci(diffs, clusters, n_boot=1000, seed=i)
        if ci.low <= 0.0 <= ci.high:
            cover += 1

        stream = rng.normal(0.0, 1.0, size=peek_n)  # null: mean paired difference is 0
        csum = np.cumsum(stream)
        if any(abs(csum[k - 1] / k) / (1.0 / math.sqrt(k)) > z_crit for k in looks):
            peek_naive += 1
        pv = msprt_pvalues(stream.tolist(), tau2=0.1, sigma2=1.0)
        if any(pv[k - 1] < alpha for k in looks):
            peek_msprt += 1
        if progress and i % 200 == 0:
            progress(i)
    return Validation(n_sims, n_pairs, mc / n_sims, sf / n_sims, cover / n_sims, len(looks), peek_naive / n_sims,
                      peek_msprt / n_sims)
