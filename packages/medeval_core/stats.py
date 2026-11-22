"""Paper-grade statistics for the v0.6 evaluation harness.

This module is deliberately self-contained: numpy and scipy only, no machine
learning stack and no import of the study layer.  Every interval and test is
seeded, so a number in a generated report can be reproduced from the seed that
the run manifest records.

What is provided:

* percentile / stratified bootstrap confidence intervals for a statistic;
* paired bootstrap differences and McNemar's test for paired binary outcomes;
* inter-rater reliability (raw agreement, Cohen's kappa, Fleiss' kappa,
  Krippendorff's alpha) for two or more judges;
* cross-family error correlation and a disagreement matrix for judge panels;
* safety-relevant behavioural summaries: abstention, format compliance,
  latency percentiles and cost per correct item.

Missing or mathematically undefined statistics are returned as ``nan`` (or
``None`` for counts), never as a fabricated number.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "BootstrapInterval",
    "abstention_rate",
    "bootstrap_ci",
    "bootstrap_macro_metric",
    "classification_report",
    "cohen_kappa",
    "cost_per_correct_item",
    "cross_family_error_correlation",
    "disagreement_matrix",
    "fleiss_kappa",
    "format_compliance_rate",
    "krippendorff_alpha",
    "latency_summary",
    "macro_f1",
    "macro_precision",
    "mcnemar_test",
    "paired_bootstrap_diff",
    "raw_agreement",
    "wilson_interval",
]


@dataclass(frozen=True)
class BootstrapInterval:
    """A resampled confidence interval with full provenance."""

    estimate: float
    low: float
    high: float
    n: int
    n_resamples: int
    method: str
    seed: int
    alpha: float = 0.05

    @property
    def width(self) -> float:
        return self.high - self.low

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "low": self.low,
            "high": self.high,
            "width": self.width,
            "n": self.n,
            "n_resamples": self.n_resamples,
            "method": self.method,
            "seed": self.seed,
            "alpha": self.alpha,
        }


def _clean(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    return array[~np.isnan(array)]


def _statistic_fn(statistic: str | Callable[[np.ndarray], float]) -> Callable[[np.ndarray], float]:
    if callable(statistic):
        return statistic
    if statistic == "mean":
        return lambda values: float(np.mean(values)) if values.size else float("nan")
    if statistic == "sum":
        return lambda values: float(np.sum(values))
    if statistic == "proportion":
        return lambda values: float(np.mean(values)) if values.size else float("nan")
    raise ValueError(f"unknown statistic {statistic!r}; use 'mean', 'sum', 'proportion' or a callable")


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic: str | Callable[[np.ndarray], float] = "mean",
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260101,
    method: str = "percentile",
    strata: Sequence[Hashable] | None = None,
) -> BootstrapInterval:
    """Bootstrap confidence interval for a statistic of one sample.

    ``strata`` gives a cluster/stratum label per observation.  With strata the
    interval is **stratified**: each stratum is resampled at its observed size and
    the statistic is recomputed on the pooled resample.  That is the right choice
    when items come from several datasets or difficulty bands, because a plain
    resample can under-represent a small stratum.
    """
    if n_resamples < 1:
        raise ValueError("n_resamples must be at least 1")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")
    if method not in {"percentile"}:
        raise ValueError("v0.6 supports the percentile bootstrap; method must be 'percentile'")
    array = _clean(values)
    if array.size == 0:
        raise ValueError("bootstrap_ci needs at least one non-missing value")
    stat = _statistic_fn(statistic)
    if array.size == 1:
        only = float(stat(array))
        return BootstrapInterval(only, only, only, 1, 0, "single-observation", seed, alpha)

    rng = np.random.default_rng(seed)
    estimate = float(stat(array))
    draws = np.empty(n_resamples, dtype=float)

    if strata is not None:
        labels = list(strata)
        if len(labels) != len(values):  # compare with the *raw* input, not cleaned
            raise ValueError("strata must have one label per observation")
        # Pair observations with labels and drop missing values together.
        kept = [
            (float(value), str(label))
            for value, label in zip(values, labels, strict=True)
            if not (isinstance(value, float) and math.isnan(value))
        ]
        groups: dict[str, list[float]] = {}
        for value, label in kept:
            groups.setdefault(label, []).append(value)
        names = sorted(groups)
        pools = [np.asarray(groups[name], dtype=float) for name in names]
        for index in range(n_resamples):
            sample = np.concatenate([pool[rng.integers(0, pool.size, size=pool.size)] for pool in pools])
            draws[index] = float(stat(sample))
        method_label = "stratified-percentile"
    else:
        n = array.size
        for index in range(n_resamples):
            draws[index] = float(stat(array[rng.integers(0, n, size=n)]))
        method_label = "percentile"

    low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return BootstrapInterval(
        estimate=estimate,
        low=float(low),
        high=float(high),
        n=int(array.size),
        n_resamples=n_resamples,
        method=method_label,
        seed=seed,
        alpha=alpha,
    )


def paired_bootstrap_diff(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260101,
) -> BootstrapInterval:
    """Paired bootstrap of ``mean(a) - mean(b)`` for repeated measures."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    differences = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    interval = bootstrap_ci(differences, n_resamples=n_resamples, alpha=alpha, seed=seed)
    return BootstrapInterval(
        estimate=interval.estimate,
        low=interval.low,
        high=interval.high,
        n=interval.n,
        n_resamples=interval.n_resamples,
        method="paired-" + interval.method,
        seed=seed,
        alpha=alpha,
    )


def mcnemar_test(
    a: Sequence[bool],
    b: Sequence[bool],
    *,
    exact_max_discordant: int = 25,
) -> dict[str, Any]:
    """McNemar's test for two paired binary outcomes.

    Exact binomial p-value when the discordant count is small (the default
    threshold is 25), continuity-corrected chi-square otherwise.  ``b01`` counts
    items where ``a`` is wrong and ``b`` is right, ``b10`` the reverse.
    """
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    left = [bool(x) for x in a]
    right = [bool(x) for x in b]
    b01 = sum(1 for x, y in zip(left, right, strict=True) if not x and y)
    b10 = sum(1 for x, y in zip(left, right, strict=True) if x and not y)
    discordant = b01 + b10
    if discordant == 0:
        return {
            "b01": 0,
            "b10": 0,
            "discordant": 0,
            "statistic": 0.0,
            "p_value": 1.0,
            "method": "no discordant pairs",
            "significant": False,
            "alpha": None,
        }
    if discordant <= exact_max_discordant:
        from scipy import stats as scipy_stats

        result = scipy_stats.binomtest(min(b01, b10), discordant, 0.5, alternative="two-sided")
        p_value = float(result.pvalue)
        method = "exact binomial"
        statistic = float(min(b01, b10))
    else:
        from scipy import stats as scipy_stats

        statistic = (abs(b01 - b10) - 1) ** 2 / discordant
        p_value = float(scipy_stats.chi2.sf(statistic, df=1))
        method = "chi-square continuity-corrected"
    return {
        "b01": b01,
        "b10": b10,
        "discordant": discordant,
        "statistic": statistic,
        "p_value": p_value,
        "method": method,
        "significant": p_value < 0.05,
        "alpha": 0.05,
    }


def wilson_interval(successes: int, n: int, *, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a proportion (small-n safe)."""
    if n <= 0:
        return (float("nan"), float("nan"))
    from scipy import stats as scipy_stats

    z = float(scipy_stats.norm.ppf(1 - alpha / 2))
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def raw_agreement(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Proportion of identical ratings between two raters."""
    if len(a) != len(b):
        raise ValueError("raters must rate the same number of units")
    if not a:
        return float("nan")
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters (nominal), ``nan`` when undefined."""
    if len(a) != len(b):
        raise ValueError("raters must rate the same number of units")
    n = len(a)
    if n == 0:
        return float("nan")
    observed = raw_agreement(a, b)
    counts_a: dict[Hashable, int] = {}
    counts_b: dict[Hashable, int] = {}
    for value in a:
        counts_a[value] = counts_a.get(value, 0) + 1
    for value in b:
        counts_b[value] = counts_b.get(value, 0) + 1
    expected = sum(
        (counts_a.get(key, 0) / n) * (counts_b.get(key, 0) / n) for key in set(counts_a) | set(counts_b)
    )
    if math.isclose(expected, 1.0):
        return float("nan")
    return (observed - expected) / (1.0 - expected)


def fleiss_kappa(ratings: Sequence[Sequence[Hashable]]) -> float:
    """Fleiss' kappa for two or more raters; ``ratings`` is items x raters.

    Items may be skipped when a rater did not rate them (``None``), but every
    retained item must have the same number of ratings.  Rows with fewer than two
    ratings are dropped.
    """
    cleaned = [
        [value for value in row if value is not None]
        for row in ratings
    ]
    cleaned = [row for row in cleaned if len(row) >= 2]
    if not cleaned:
        return float("nan")
    m = len(cleaned[0])
    if any(len(row) != m for row in cleaned):
        raise ValueError("Fleiss' kappa requires the same number of raters per item")
    categories = sorted({value for row in cleaned for value in row}, key=str)
    index = {value: position for position, value in enumerate(categories)}
    table = np.zeros((len(cleaned), len(categories)), dtype=float)
    for row_index, row in enumerate(cleaned):
        for value in row:
            table[row_index, index[value]] += 1
    per_item = (np.sum(table**2, axis=1) - m) / (m * (m - 1.0))
    p_bar = float(np.mean(per_item))
    p_j = table.sum(axis=0) / table.sum()
    p_e = float(np.sum(p_j**2))
    if math.isclose(p_e, 1.0):
        return float("nan")
    return (p_bar - p_e) / (1.0 - p_e)


def krippendorff_alpha(ratings: Sequence[Sequence[Hashable]]) -> float:
    """Krippendorff's alpha (nominal) for any number of raters and missing data."""
    cleaned = [list(row) for row in ratings]
    observations = [
        [value for value in row if value is not None]
        for row in cleaned
    ]
    observations = [row for row in observations if len(row) >= 2]
    if not observations:
        return float("nan")
    categories = sorted({value for row in observations for value in row}, key=str)
    if len(categories) < 2:
        return float("nan")
    index = {value: position for position, value in enumerate(categories)}
    q = len(categories)
    coincidence = np.zeros((q, q), dtype=float)
    for row in observations:
        m_u = len(row)
        counts: dict[Hashable, int] = {}
        for value in row:
            counts[value] = counts.get(value, 0) + 1
        for value_c, n_c in counts.items():
            for value_k, n_k in counts.items():
                if value_c == value_k:
                    coincidence[index[value_c], index[value_k]] += n_c * (n_c - 1) / (m_u - 1)
                else:
                    coincidence[index[value_c], index[value_k]] += n_c * n_k / (m_u - 1)
    n_c = coincidence.sum(axis=1)
    total = float(n_c.sum())
    if total <= 0:
        return float("nan")
    delta = np.ones((q, q), dtype=float) - np.eye(q)
    d_o = float(np.sum(coincidence * delta)) / total
    d_e = float(n_c @ delta @ n_c) / (total * (total - 1.0))
    if math.isclose(d_e, 0.0):
        return float("nan")
    return 1.0 - d_o / d_e


def disagreement_matrix(ratings: Mapping[str, Sequence[Hashable]]) -> dict[str, Any]:
    """Pairwise disagreement rates for a judge panel.

    ``ratings`` maps a judge id to one label per item (``None`` for a missing
    rating).  The result includes every pair's disagreement rate and the items on
    which the panel is not unanimous.
    """
    judges = list(ratings)
    n = 0
    for values in ratings.values():
        n = max(n, len(values))
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(judges):
        for right in judges[i + 1 :]:
            left_values = ratings[left]
            right_values = ratings[right]
            shared = [
                (x, y)
                for x, y in zip(left_values, right_values, strict=False)
                if x is not None and y is not None
            ]
            disagreements = sum(1 for x, y in shared if x != y)
            pairs.append({
                "left": left,
                "right": right,
                "n": len(shared),
                "disagreements": disagreements,
                "disagreement_rate": disagreements / len(shared) if shared else float("nan"),
            })
    unanimous = 0
    split_items: list[int] = []
    for index in range(n):
        row = [values[index] for values in ratings.values() if index < len(values) and values[index] is not None]
        if len(set(row)) <= 1:
            unanimous += 1
        else:
            split_items.append(index)
    return {
        "judges": judges,
        "n_items": n,
        "pairs": pairs,
        "unanimous_items": unanimous,
        "unanimous_rate": unanimous / n if n else float("nan"),
        "split_item_indices": split_items,
    }


def cross_family_error_correlation(
    errors_a: Sequence[bool],
    errors_b: Sequence[bool],
) -> dict[str, Any]:
    """Phi coefficient between two judges' error indicators.

    ``errors_*`` are booleans where ``True`` means the judge was wrong on that
    item.  Positive phi means the two judges fail on the same items (shared blind
    spots); a value near zero means their errors are unrelated.  The function also
    reports the both-wrong rate and the excess over the independence expectation,
    which is easier to read in a report than a correlation alone.
    """
    if len(errors_a) != len(errors_b):
        raise ValueError("error vectors must have equal length")
    n = len(errors_a)
    if n == 0:
        return {"phi": float("nan"), "n": 0}
    a = [bool(x) for x in errors_a]
    b = [bool(x) for x in errors_b]
    both = sum(1 for x, y in zip(a, b, strict=True) if x and y)
    only_a = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(a, b, strict=True) if not x and y)
    neither = n - both - only_a - only_b
    denominator = math.sqrt((both + only_a) * (both + only_b) * (neither + only_a) * (neither + only_b))
    phi = (both * neither - only_a * only_b) / denominator if denominator else float("nan")
    rate_a = (both + only_a) / n
    rate_b = (both + only_b) / n
    return {
        "phi": phi,
        "n": n,
        "both_wrong": both,
        "only_a": only_a,
        "only_b": only_b,
        "neither": neither,
        "both_wrong_rate": both / n,
        "expected_both_wrong_rate": rate_a * rate_b,
        "excess_both_wrong_rate": both / n - rate_a * rate_b,
    }


def abstention_rate(outputs: Sequence[Mapping[str, Any]] | Sequence[bool]) -> dict[str, Any]:
    """Fraction of items where the model abstained (``"ABSTAIN"``/unknown)."""
    flags: list[bool] = []
    for output in outputs:
        if isinstance(output, bool):
            flags.append(output)
        else:
            flags.append(bool(output.get("abstained")))
    n = len(flags)
    count = sum(1 for flag in flags if flag)
    return {"n": n, "abstained": count, "rate": count / n if n else float("nan")}


def format_compliance_rate(flags: Sequence[bool]) -> dict[str, Any]:
    """Fraction of outputs that satisfied the pack's declared output format."""
    n = len(flags)
    compliant = sum(1 for flag in flags if flag)
    return {"n": n, "compliant": compliant, "rate": compliant / n if n else float("nan")}


def latency_summary(latencies_ms: Sequence[float]) -> dict[str, Any]:
    """Median, p95 and mean latency in milliseconds (``nan`` when empty)."""
    values = [value for value in latencies_ms if value is not None and value >= 0]
    if not values:
        return {"n": 0, "p50_ms": float("nan"), "p95_ms": float("nan"), "mean_ms": float("nan")}
    array = np.asarray(values, dtype=float)
    return {
        "n": int(array.size),
        "p50_ms": float(np.percentile(array, 50)),
        "p95_ms": float(np.percentile(array, 95)),
        "mean_ms": float(np.mean(array)),
        "total_ms": float(np.sum(array)),
    }


def cost_per_correct_item(total_usd: float | None, n_correct: int) -> float | None:
    """USD spent per correct item, or ``None`` when no verified price exists."""
    if total_usd is None or n_correct <= 0:
        return None
    return total_usd / n_correct


def classification_report(
    predicted: Sequence[str],
    gold: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Per-class precision/recall/F1 plus macro and micro summaries.

    Used for the three-class hallucination preset.  Labels absent from both
    ``gold`` and ``predicted`` are skipped, so an unused label does not drag the
    macro average to zero.
    """
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must have equal length")
    names = list(labels) if labels is not None else sorted(set(gold) | set(predicted))
    n = len(gold)
    per_class: dict[str, Any] = {}
    true_positives = 0
    for name in names:
        tp = sum(1 for p, g in zip(predicted, gold, strict=True) if p == name and g == name)
        fp = sum(1 for p, g in zip(predicted, gold, strict=True) if p == name and g != name)
        fn = sum(1 for p, g in zip(predicted, gold, strict=True) if p != name and g == name)
        support = sum(1 for g in gold if g == name)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
        }
        true_positives += tp
    present = [name for name in names if per_class[name]["support"] or per_class[name]["true_positive"]]
    macro_f1_value = sum(per_class[name]["f1"] for name in present) / len(present) if present else float("nan")
    macro_precision_value = (
        sum(per_class[name]["precision"] for name in present) / len(present) if present else float("nan")
    )
    accuracy = sum(1 for p, g in zip(predicted, gold, strict=True) if p == g) / n if n else float("nan")
    return {
        "n": n,
        "labels": names,
        "per_class": per_class,
        "macro_f1": macro_f1_value,
        "macro_precision": macro_precision_value,
        "micro_precision": true_positives / n if n else float("nan"),
        "accuracy": accuracy,
    }


def macro_f1(predicted: Sequence[str], gold: Sequence[str], *, labels: Sequence[str] | None = None) -> float:
    return float(classification_report(predicted, gold, labels=labels)["macro_f1"])


def macro_precision(predicted: Sequence[str], gold: Sequence[str], *, labels: Sequence[str] | None = None) -> float:
    return float(classification_report(predicted, gold, labels=labels)["macro_precision"])


def bootstrap_macro_metric(
    predicted: Sequence[str],
    gold: Sequence[str],
    *,
    metric: str = "f1",
    labels: Sequence[str] | None = None,
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260101,
) -> BootstrapInterval:
    """Bootstrap a macro classification metric by resampling items."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must have equal length")
    if not predicted:
        raise ValueError("bootstrap_macro_metric needs at least one item")
    if metric not in {"f1", "precision"}:
        raise ValueError("metric must be 'f1' or 'precision'")
    key = "macro_f1" if metric == "f1" else "macro_precision"
    estimate = float(classification_report(predicted, gold, labels=labels)[key])
    rng = np.random.default_rng(seed)
    n = len(gold)
    draws = np.empty(n_resamples, dtype=float)
    for index in range(n_resamples):
        chosen = rng.integers(0, n, size=n)
        sample_pred = [predicted[i] for i in chosen]
        sample_gold = [gold[i] for i in chosen]
        draws[index] = float(classification_report(sample_pred, sample_gold, labels=labels)[key])
    low, high = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return BootstrapInterval(
        estimate=estimate,
        low=float(low),
        high=float(high),
        n=n,
        n_resamples=n_resamples,
        method="item-bootstrap-macro",
        seed=seed,
        alpha=alpha,
    )
