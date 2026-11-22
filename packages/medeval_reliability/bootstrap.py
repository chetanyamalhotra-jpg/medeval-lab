"""Bootstrap confidence intervals and paired comparisons.

All resampling is seeded, so every interval in a report can be reproduced from
its recorded seed. Defaults: 10 000 resamples, 95% percentile interval; BCa is
available and is the pre-registered choice for Experiment 001.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats

__all__ = [
    "BootstrapCI",
    "bootstrap_ci",
    "cluster_bootstrap_ci",
    "mcnemar_test",
    "paired_bootstrap_diff",
]


@dataclass(frozen=True)
class BootstrapCI:
    """Result of a bootstrap interval."""

    estimate: float
    low: float
    high: float
    n_resamples: int
    method: str
    seed: int
    alpha: float = 0.05
    n: int = 0

    @property
    def width(self) -> float:
        return self.high - self.low

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "low": self.low,
            "high": self.high,
            "width": self.width,
            "n_resamples": self.n_resamples,
            "method": self.method,
            "seed": self.seed,
            "alpha": self.alpha,
            "n": self.n,
        }


def _as_float_statistic(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    return float(values.mean())


def bootstrap_ci(
    values: Sequence[float] | np.ndarray,
    statistic: Callable[[np.ndarray], float] | None = None,
    *,
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    method: str = "percentile",
    seed: int = 20260101,
) -> BootstrapCI:
    """Bootstrap confidence interval for a statistic of a 1-D sample.

    ``method='percentile'`` takes empirical quantiles of the resampled
    statistic. ``method='bca'`` applies the bias-corrected and accelerated
    correction (Efron 1987), which needs the jackknife acceleration and is the
    documented choice for Experiment 001.

    Assumption: observations are exchangeable. For items that share a stem or a
    perturbation group, use :func:`cluster_bootstrap_ci`, because resampling
    correlated units understates the interval width.
    """
    array = np.asarray(values, dtype=float)
    array = array[~np.isnan(array)]
    if array.size == 0:
        raise ValueError("bootstrap_ci needs at least one non-missing value")
    if array.size == 1:
        only = float(array[0])
        return BootstrapCI(only, only, only, 0, method, seed, alpha, 1)
    stat = statistic or _as_float_statistic
    rng = np.random.default_rng(seed)
    n = array.size
    estimate = float(stat(array))
    draws = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        sample = array[rng.integers(0, n, size=n)]
        draws[i] = float(stat(sample))

    if method == "percentile":
        low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    elif method == "bca":
        low, high = _bca_interval(array, stat, estimate, draws, alpha)
    else:
        raise ValueError("method must be 'percentile' or 'bca'")
    return BootstrapCI(float(estimate), float(low), float(high), n_resamples, method, seed, alpha, n)


def _bca_interval(
    array: np.ndarray,
    stat: Callable[[np.ndarray], float],
    estimate: float,
    draws: np.ndarray,
    alpha: float,
) -> tuple[float, float]:
    """BCa correction: z0 from the bootstrap distribution, acceleration from jackknife."""
    n = array.size
    prop_less = float(np.mean(draws < estimate))
    prop_less = min(max(prop_less, 1.0 / (draws.size + 1)), 1.0 - 1.0 / (draws.size + 1))
    z0 = float(stats.norm.ppf(prop_less))

    jack = np.empty(n, dtype=float)
    for i in range(n):
        jack[i] = float(stat(np.delete(array, i)))
    jack_mean = jack.mean()
    diffs = jack_mean - jack
    denom = 6.0 * float(np.sum(diffs**2)) ** 1.5
    accel = float(np.sum(diffs**3)) / denom if denom != 0 else 0.0

    def adjusted(percentile: float) -> float:
        z = float(stats.norm.ppf(percentile))
        value = z0 + (z0 + z) / (1.0 - accel * (z0 + z))
        return float(stats.norm.cdf(value))

    low_q = adjusted(alpha / 2)
    high_q = adjusted(1 - alpha / 2)
    low, high = np.quantile(draws, [low_q, high_q])
    return float(low), float(high)


def cluster_bootstrap_ci(
    values: Sequence[float],
    clusters: Sequence[Any],
    *,
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260101,
) -> BootstrapCI:
    """Bootstrap that resamples whole clusters (e.g. all items of one stem)."""
    if len(values) != len(clusters):
        raise ValueError("values and clusters must have the same length")
    array = np.asarray(values, dtype=float)
    cluster_array = np.asarray([str(c) for c in clusters])
    unique = sorted(set(cluster_array))
    if len(unique) < 2:
        raise ValueError("cluster bootstrap needs at least two clusters")
    grouped = {name: array[cluster_array == name] for name in unique}
    rng = np.random.default_rng(seed)
    estimate = float(array.mean())
    draws = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        chosen = rng.integers(0, len(unique), size=len(unique))
        pooled = np.concatenate([grouped[unique[j]] for j in chosen])
        draws[i] = float(pooled.mean())
    low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return BootstrapCI(estimate, float(low), float(high), n_resamples, "cluster-percentile", seed, alpha, array.size)


def paired_bootstrap_diff(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260101,
    method: str = "percentile",
) -> BootstrapCI:
    """Paired bootstrap of ``mean(a) - mean(b)``.

    Pairing preserves the within-item correlation; an unpaired interval would be
    too wide for repeated measures on the same items.
    """
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    diff = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    return bootstrap_ci(diff, n_resamples=n_resamples, alpha=alpha, method=method, seed=seed)


def mcnemar_test(a: Sequence[bool], b: Sequence[bool]) -> dict[str, float]:
    """McNemar's test for two paired binary raters/classifiers.

    Uses the exact binomial test on discordant pairs when ``n_discordant <= 25``
    (conservative default) and the continuity-corrected chi-square otherwise.
    Returns ``{"b01", "b10", "p_value", "method"}``.
    """
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    b01 = sum(1 for x, y in zip(a, b, strict=True) if (not x) and y)
    b10 = sum(1 for x, y in zip(a, b, strict=True) if x and (not y))
    n_disc = b01 + b10
    if n_disc == 0:
        return {"b01": 0.0, "b10": 0.0, "p_value": 1.0, "method": "no discordant pairs"}
    if n_disc <= 25:
        result = stats.binomtest(min(b01, b10), n_disc, 0.5, alternative="two-sided")
        return {"b01": float(b01), "b10": float(b10), "p_value": float(result.pvalue), "method": "exact binomial"}
    stat = (abs(b01 - b10) - 1) ** 2 / n_disc
    p = float(stats.chi2.sf(stat, df=1))
    return {"b01": float(b01), "b10": float(b10), "p_value": p, "method": "chi-square continuity-corrected"}


def proportion_ci(successes: int, n: int, *, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a proportion (better small-n behaviour than Wald)."""
    if n <= 0:
        return (float("nan"), float("nan"))
    z = float(stats.norm.ppf(1 - alpha / 2))
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
