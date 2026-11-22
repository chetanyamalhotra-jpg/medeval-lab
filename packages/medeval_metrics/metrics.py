"""Our own evaluation metrics, each with a documented formula and limits.

Design rules:

* every metric is a plain function over arrays, with no hidden state;
* every metric has a :class:`MetricDefinition` carrying formula, unit,
  interpretation, assumptions, when *not* to use it, and literature IDs;
* no composite score is produced. ``rubric_weighted_score`` returns the
  per-criterion breakdown and a weighted total that is explicitly labelled
  exploratory, because its interpretation has not been validated.

The harness can only average per-document contributions, so ``ece`` is computed
here and *not* registered as a harness plugin (see ``plugins.py``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = [
    "METRICS",
    "ECEResult",
    "MetricDefinition",
    "RubricScore",
    "accuracy",
    "brier_score",
    "critical_error_rate",
    "expected_calibration_error",
    "mean_confidence",
    "nll",
    "omission_rate",
    "perturbation_consistency",
    "rubric_weighted_score",
    "subgroup_performance",
    "unsafe_recommendation_rate",
]


@dataclass(frozen=True)
class MetricDefinition:
    """Documentation that makes a metric reviewable and traceable."""

    metric_id: str
    version: str
    name: str
    formula: str
    unit: str
    interpretation: str
    assumptions: tuple[str, ...]
    when_not_to_use: str
    literature_ids: tuple[str, ...]
    higher_is_better: bool | None = None
    safety_related: bool = False
    decomposable_per_item: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "version": self.version,
            "name": self.name,
            "formula": self.formula,
            "unit": self.unit,
            "interpretation": self.interpretation,
            "assumptions": list(self.assumptions),
            "when_not_to_use": self.when_not_to_use,
            "literature_ids": list(self.literature_ids),
            "higher_is_better": self.higher_is_better,
            "safety_related": self.safety_related,
            "decomposable_per_item": self.decomposable_per_item,
        }


def _clean(values: Sequence[float | bool | None] | np.ndarray) -> np.ndarray:
    """Drop missing values; coerce booleans to 0/1 floats."""
    array = np.asarray(
        [np.nan if v is None else float(v) for v in values], dtype=float
    )
    return array[~np.isnan(array)]


def _rate(flags: Sequence[bool | None]) -> tuple[float, int]:
    clean = _clean(flags)
    if clean.size == 0:
        return float("nan"), 0
    return float(clean.sum() / clean.size), int(clean.size)


def accuracy(correct: Sequence[bool | None]) -> float:
    """Proportion correct over rated items. ``nan`` when nothing was rated."""
    return _rate(correct)[0]


def critical_error_rate(flags: Sequence[bool | None]) -> float:
    """Share of rated responses containing at least one critical error.

    Numerator: responses flagged with any critical error (wrong diagnosis that
    would change management, dangerous omission, unsafe action). Denominator:
    responses actually rated. Unrated responses are excluded and must be
    reported separately -- silently treating them as "no error" would bias the
    rate downwards.
    """
    return _rate(flags)[0]


def unsafe_recommendation_rate(flags: Sequence[bool | None]) -> float:
    """Share of rated responses recommending a harmful action (drug, dose, delay)."""
    return _rate(flags)[0]


def omission_rate(flags: Sequence[bool | None]) -> float:
    """Share of rated responses missing a pre-specified essential element."""
    return _rate(flags)[0]


@dataclass(frozen=True)
class RubricScore:
    """Per-criterion rubric result.

    ``weighted_total`` is reported for completeness only; the v0.1 report never
    ranks systems by it because no validation study supports that use.
    """

    per_criterion: dict[str, float]
    weights: dict[str, float]
    weighted_total: float
    n_criteria: int
    max_possible: float
    interpretation: str = "exploratory; per-criterion values are the reported unit"

    def as_dict(self) -> dict[str, Any]:
        return {
            "per_criterion": self.per_criterion,
            "weights": self.weights,
            "weighted_total": self.weighted_total,
            "n_criteria": self.n_criteria,
            "max_possible": self.max_possible,
            "interpretation": self.interpretation,
        }


def rubric_weighted_score(
    scores: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    max_scale: float = 2.0,
) -> RubricScore:
    """Weighted mean of criterion scores with a per-criterion breakdown.

    ``sum(w_c * s_c) / sum(w_c)`` over the criteria present in both mappings.
    Raises if a scored criterion has no weight, because silently renormalising
    over the remaining criteria changes what the number means.
    """
    if not scores:
        raise ValueError("rubric_weighted_score needs at least one criterion score")
    missing = [c for c in scores if c not in weights]
    if missing:
        raise ValueError(f"no weight defined for criteria: {missing}")
    for criterion, value in scores.items():
        if not np.isfinite(value):
            raise ValueError(f"criterion {criterion!r} has a non-finite score")
    weight_sum = float(sum(weights[c] for c in scores))
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive value")
    total = float(sum(weights[c] * scores[c] for c in scores) / weight_sum)
    return RubricScore(
        per_criterion={c: float(v) for c, v in scores.items()},
        weights={c: float(weights[c]) for c in scores},
        weighted_total=total,
        n_criteria=len(scores),
        max_possible=max_scale,
    )


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int | bool]) -> float:
    """Brier score for binary correctness: ``mean((p - y)^2)``.

    ``p`` is the probability the model assigns to its answer being correct and
    ``y`` is 1 for correct, 0 for incorrect. Lower is better; 0 is perfect.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    p = _clean(probabilities)
    y = np.asarray([float(o) for o in outcomes], dtype=float)
    mask = ~np.isnan(np.asarray(probabilities, dtype=float))
    y = y[mask]
    if p.size == 0:
        return float("nan")
    if np.any((p < 0) | (p > 1)):
        raise ValueError("probabilities must lie in [0, 1]")
    return float(np.mean((p - y) ** 2))


def nll(probabilities: Sequence[float], outcomes: Sequence[int | bool], *, eps: float = 1e-12) -> float:
    """Mean negative log-likelihood of the observed correctness outcome."""
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    p = np.clip(np.asarray(probabilities, dtype=float), eps, 1 - eps)
    y = np.asarray([float(o) for o in outcomes], dtype=float)
    if p.size == 0:
        return float("nan")
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def mean_confidence(probabilities: Sequence[float]) -> float:
    """Average stated probability of correctness."""
    p = _clean(probabilities)
    return float(np.mean(p)) if p.size else float("nan")


@dataclass(frozen=True)
class ECEResult:
    """Expected and maximum calibration error with bin detail."""

    ece: float
    mce: float
    n: int
    n_bins: int
    bins: list[dict[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ece": self.ece,
            "mce": self.mce,
            "n": self.n,
            "n_bins": self.n_bins,
            "bins": self.bins,
        }


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int | bool],
    *,
    n_bins: int = 10,
    strategy: str = "uniform",
) -> ECEResult:
    """Expected Calibration Error and Maximum Calibration Error.

    ``ECE = sum_b (n_b/n) * |acc_b - conf_b|`` over equal-width (``uniform``) or
    equal-count (``quantile``) bins of the stated probability of correctness.
    ``MCE = max_b |acc_b - conf_b|``.

    ECE is *not* decomposable into per-item contributions, so it cannot be a
    harness metric (the harness averages per-document values). It also depends on
    binning; the bin count and strategy are always reported with the number.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray([float(o) for o in outcomes], dtype=float)
    if p.size == 0:
        return ECEResult(float("nan"), float("nan"), 0, n_bins, [])
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    if strategy == "uniform":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        indices = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    elif strategy == "quantile":
        edges = np.quantile(p, np.linspace(0, 1, n_bins + 1))
        edges[0], edges[-1] = 0.0, 1.0
        indices = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    else:
        raise ValueError("strategy must be 'uniform' or 'quantile'")

    total = p.size
    ece = 0.0
    mce = 0.0
    bins: list[dict[str, float]] = []
    for b in range(n_bins):
        mask = indices == b
        count = int(mask.sum())
        if count == 0:
            continue
        confidence = float(p[mask].mean())
        accuracy_b = float(y[mask].mean())
        gap = abs(accuracy_b - confidence)
        ece += (count / total) * gap
        mce = max(mce, gap)
        bins.append(
            {
                "bin": float(b),
                "n": float(count),
                "mean_confidence": confidence,
                "mean_accuracy": accuracy_b,
                "gap": gap,
            }
        )
    return ECEResult(ece=float(ece), mce=float(mce), n=int(total), n_bins=n_bins, bins=bins)


def perturbation_consistency(
    base: Sequence[Any], perturbed: Sequence[Any]
) -> float:
    """Share of cases where the answer is unchanged after a benign perturbation.

    ``mean(1[base_i == perturbed_i])``. Perturbations must be label-preserving
    (option reordering, paraphrase, added distractors); a change means the
    answer depended on nuisance features. This measures stability, not accuracy.
    """
    if len(base) != len(perturbed):
        raise ValueError("base and perturbed must have equal length")
    if not base:
        return float("nan")
    same = sum(1 for a, b in zip(base, perturbed, strict=True) if a == b)
    return same / len(base)


def subgroup_performance(
    groups: Sequence[str],
    scores: Sequence[float | bool | None],
    *,
    min_n: int = 5,
) -> dict[str, dict[str, float]]:
    """Mean score per subgroup with counts.

    Subgroups with fewer than ``min_n`` observations are still reported (nothing
    is silently dropped) but carry ``"small_n": 1`` so downstream code can avoid
    reading them as evidence.
    """
    if len(groups) != len(scores):
        raise ValueError("groups and scores must have equal length")
    buckets: dict[str, list[float]] = {}
    for group, score in zip(groups, scores, strict=True):
        if score is None:
            continue
        buckets.setdefault(str(group), []).append(float(score))
    out: dict[str, dict[str, float]] = {}
    for group in sorted(buckets):
        values = np.asarray(buckets[group], dtype=float)
        out[group] = {
            "n": float(values.size),
            "mean": float(values.mean()),
            "small_n": float(values.size < min_n),
        }
    return out


METRICS: dict[str, MetricDefinition] = {
    "accuracy": MetricDefinition(
        metric_id="accuracy",
        version="v0.1",
        name="Accuracy",
        formula="(#correct) / (#rated)",
        unit="proportion of rated items",
        interpretation="Reference-label agreement for items with a deterministic key.",
        assumptions=(
            "the key is correct (see the audit report for quantified key errors)",
            "unrated items are missing at random with respect to correctness",
        ),
        when_not_to_use="Do not use as a clinical quality measure; it scores key agreement only.",
        literature_ids=("lit_error_bars", "lit_medqa"),
        higher_is_better=True,
    ),
    "critical_error_rate": MetricDefinition(
        metric_id="critical_error_rate",
        version="v0.1",
        name="Critical error rate",
        formula="(#responses with >=1 critical error) / (#rated responses)",
        unit="proportion of rated responses",
        interpretation="How often a response contains an error that would change management or cause harm.",
        assumptions=(
            "critical-error definition is stated before scoring",
            "raters are blinded to the system that produced the response",
        ),
        when_not_to_use="Not a clinical safety measure; criteria are literature-derived, not clinician-validated.",
        literature_ids=("lit_healthbench", "lit_medhelm"),
        higher_is_better=False,
        safety_related=True,
    ),
    "unsafe_recommendation_rate": MetricDefinition(
        metric_id="unsafe_recommendation_rate",
        version="v0.1",
        name="Unsafe recommendation rate",
        formula="(#responses recommending a harmful action) / (#rated responses)",
        unit="proportion of rated responses",
        interpretation="Frequency of actionable harm: contraindicated drug, dangerous dose, or harmful delay.",
        assumptions=("a rule list defines what counts as harmful and is itself reviewed",),
        when_not_to_use="A zero rate does not mean safe: the rule list is finite and incomplete.",
        literature_ids=("lit_healthbench", "lit_medhalt"),
        higher_is_better=False,
        safety_related=True,
    ),
    "omission_rate": MetricDefinition(
        metric_id="omission_rate",
        version="v0.1",
        name="Omission rate",
        formula="(#responses missing a required element) / (#rated responses)",
        unit="proportion of rated responses",
        interpretation="How often an essential element (e.g. urgent referral) is absent.",
        assumptions=("the required-element list is fixed before scoring",),
        when_not_to_use="Sensitive to how elements are phrased; compare only within one rubric version.",
        literature_ids=("lit_healthbench", "lit_rubric_grading"),
        higher_is_better=False,
        safety_related=True,
    ),
    "rubric_weighted_score": MetricDefinition(
        metric_id="rubric_weighted_score",
        version="v0.1",
        name="Rubric-weighted score",
        formula="sum_c(w_c * s_c) / sum_c(w_c)",
        unit="rubric scale points (per-criterion values reported alongside)",
        interpretation="Weighted mean of anchored criterion scores; the per-criterion values are the reported unit.",
        assumptions=("weights encode importance, not empirical validity",),
        when_not_to_use="Do not rank systems by the weighted total in v0.1; its interpretation is unvalidated.",
        literature_ids=("lit_healthbench", "lit_rubric_grading"),
        higher_is_better=True,
    ),
    "brier_score": MetricDefinition(
        metric_id="brier_score",
        version="v0.1",
        name="Brier score",
        formula="mean((p - y)^2) over items, p = stated probability correct, y in {0,1}",
        unit="squared probability (0 best, 0.25 = uninformative at p=0.5)",
        interpretation="Calibration plus discrimination for binary correctness.",
        assumptions=("probabilities are comparable across items", "y is correct"),
        when_not_to_use="Do not average across items with different difficulty without also reporting base rates.",
        literature_ids=("lit_brier",),
        higher_is_better=False,
    ),
    "ece": MetricDefinition(
        metric_id="ece",
        version="v0.1",
        name="Expected calibration error",
        formula="sum_b (n_b/n) * |acc_b - conf_b|",
        unit="probability difference",
        interpretation="Average gap between stated confidence and observed accuracy across bins.",
        assumptions=("bins contain enough items to estimate acc_b",),
        when_not_to_use="Not decomposable per item, binning-dependent, and unstable with small n; never report without bin count and n.",
        literature_ids=("lit_ece",),
        higher_is_better=False,
    ),
    "perturbation_consistency": MetricDefinition(
        metric_id="perturbation_consistency",
        version="v0.1",
        name="Perturbation consistency",
        formula="mean(1[answer_before == answer_after_benign_perturbation])",
        unit="proportion of cases",
        interpretation="Stability of the answer under label-preserving changes.",
        assumptions=("perturbations preserve the correct answer",),
        when_not_to_use="High consistency is not accuracy: a system can be consistently wrong.",
        literature_ids=("lit_benchmark_quality", "lit_contamination"),
        higher_is_better=True,
    ),
    "subgroup_performance": MetricDefinition(
        metric_id="subgroup_performance",
        version="v0.1",
        name="Subgroup performance",
        formula="per-group mean score with n",
        unit="proportion or rubric points per subgroup",
        interpretation="Whether performance is uneven across item groups (topic, difficulty, source).",
        assumptions=("group labels are pre-specified and not chosen after seeing results",),
        when_not_to_use="Do not interpret small subgroups; multiplicity is not corrected by default.",
        literature_ids=("lit_error_bars", "lit_benchmark_quality"),
        higher_is_better=True,
    ),
}
