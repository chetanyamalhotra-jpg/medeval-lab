"""Metrics for the grading study, with interval estimates and no point-estimate-only claims.

Every proportion gets a Wilson interval; every agreement, correlation, calibration
and discrimination statistic gets a 1000-resample bootstrap interval over
*items* (pairs are resampled together, never independently). Nothing here uses a
normal approximation on a 60-item sample.

Two definitions are spelled out because they are easy to misread:

* **confidence** is the judge's stated probability that its own verdict is right.
  So ``ECE``/``Brier`` compare ``confidence/100`` with the observed frequency of
  "the judge agreed with the key", and ``AUROC`` asks whether confidence
  separates the occasions when the judge was right from those when it was wrong.
* **an error** is a disagreement with the key: saying CORRECT about a candidate
  that named a wrong option, or INCORRECT about a candidate that named the key
  option. The key is an imperfect reference, so an "error" may be the key's fault;
  that is what the possible-key-error flag and the with/without comparison are
  for, and it cannot be resolved without an expert.

Undefined statistics return ``None`` with a reason string rather than a number.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from medeval_reliability import bootstrap_ci, cohen_kappa, gwet_ac1, mcnemar_test, percent_agreement, proportion_ci
from medeval_study.judging import (
    FAILURE_TYPES,
    VERDICT_CORRECT,
    JudgeSpec,
    Verdict,
)
from medeval_study.models import CandidateResponse, McqItem

__all__ = [
    "JudgedItem",
    "analyse_study",
    "auroc",
    "brier_score",
    "calibration_error",
    "phi_coefficient",
    "possible_key_errors",
]

CONFIDENCE_HIGH = 80
CONFIDENCE_VERY_HIGH = 90
N_RESAMPLES = 1000


# --------------------------------------------------------------------------- #
# Per-item alignment
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class JudgedItem:
    """One item as one judge saw it, aligned with the key-derived ground truth."""

    item_id: str
    is_correct: bool
    verdict: str | None
    confidence: int | None
    parse_ok: bool
    error: str | None = None
    failure_type: str = "none"

    @property
    def says_correct(self) -> bool | None:
        if self.verdict is None:
            return None
        return self.verdict == VERDICT_CORRECT

    @property
    def agrees_with_key(self) -> bool | None:
        said = self.says_correct
        if said is None:
            return None
        return said == self.is_correct

    @property
    def is_error(self) -> int | None:
        agrees = self.agrees_with_key
        return None if agrees is None else int(not agrees)


def build_truth(items: Sequence[McqItem], responses: Mapping[str, CandidateResponse]) -> dict[str, bool]:
    """Ground truth per item: did the candidate name the key option?"""
    truth: dict[str, bool] = {}
    for item in items:
        response = responses.get(item.item_id)
        if response is None:
            continue
        truth[item.item_id] = response.chosen_letter == item.key_letter
    return truth


def align(verdicts: Iterable[Verdict], truth: Mapping[str, bool], judge_id: str, repeat: int) -> list[JudgedItem]:
    """Load one judge's verdicts for one repeat into an item-aligned table."""
    out: list[JudgedItem] = []
    for verdict in verdicts:
        if verdict.judge_id != judge_id or verdict.repeat != repeat:
            continue
        if verdict.item_id not in truth:
            continue
        out.append(
            JudgedItem(
                item_id=verdict.item_id,
                is_correct=bool(truth[verdict.item_id]),
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                parse_ok=verdict.parse_ok,
                error=verdict.error,
                failure_type=verdict.failure_type,
            )
        )
    return sorted(out, key=lambda row: row.item_id)


# --------------------------------------------------------------------------- #
# Small statistics
# --------------------------------------------------------------------------- #
def auroc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """Rank-based AUROC with ties averaged (Mann-Whitney U)."""
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have equal length")
    pairs = [(float(s), bool(y)) for s, y in zip(scores, labels, strict=True)]
    positives = [s for s, y in pairs if y]
    negatives = [s for s, y in pairs if not y]
    if not positives or not negatives:
        return None
    rank_sum = 0.0
    ordered = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    ranks = [0.0] * len(pairs)
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and pairs[ordered[end + 1]][0] == pairs[ordered[index]][0]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[ordered[position]] = average_rank
        index = end + 1
    for position, (_, label) in enumerate(pairs):
        if label:
            rank_sum += ranks[position]
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    """Mean squared error of a probability against a binary outcome."""
    if not probabilities:
        return None
    total = sum((float(p) - (1.0 if y else 0.0)) ** 2 for p, y in zip(probabilities, outcomes, strict=True))
    return total / len(probabilities)


def calibration_error(probabilities: Sequence[float], outcomes: Sequence[bool], *, bins: int = 5) -> float | None:
    """Expected calibration error over equal-width bins of the predicted probability."""
    if not probabilities:
        return None
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    used = 0
    for index in range(bins):
        low, high = edges[index], edges[index + 1]
        members = [
            (float(p), bool(y))
            for p, y in zip(probabilities, outcomes, strict=True)
            if (low <= p < high) or (index == bins - 1 and p == high)
        ]
        if not members:
            continue
        mean_probability = sum(p for p, _ in members) / len(members)
        observed = sum(1 for _, y in members if y) / len(members)
        total += abs(observed - mean_probability) * len(members)
        used += len(members)
    if used == 0:
        return None
    return total / used


def phi_coefficient(left: Sequence[bool], right: Sequence[bool]) -> float | None:
    """Pearson correlation of two binary vectors (the 2x2 phi coefficient)."""
    if len(left) != len(right):
        raise ValueError("vectors must have equal length")
    n = len(left)
    if n == 0:
        return None
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _wilson(successes: int, n: int) -> dict[str, Any]:
    low, high = proportion_ci(successes, n)
    return {
        "numerator": successes,
        "denominator": n,
        "estimate": (successes / n) if n else None,
        "ci95_wilson": [low, high] if n else None,
        "method": "wilson",
    }


def _bootstrap_index_ci(
    n_items: int,
    statistic: Any,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 20260101,
) -> dict[str, Any] | None:
    """Bootstrap over items for a statistic of paired per-item arrays.

    ``statistic`` receives an integer index array (with repeats) and returns a
    float, so paired statistics such as kappa resample items jointly.
    """
    if n_items < 2:
        return None
    indices = np.arange(n_items, dtype=float)

    def _wrapped(sample: Any) -> float:
        value = statistic(sample.astype(int))
        if value is None:
            return float("nan")
        return float(value)

    try:
        ci = bootstrap_ci(indices, statistic=_wrapped, n_resamples=n_resamples, method="percentile", seed=seed)
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    if ci is None or (isinstance(ci.estimate, float) and math.isnan(ci.estimate)):
        return None
    if ci.low != ci.low or ci.high != ci.high:  # NaN interval: statistic undefined in resamples
        return None
    payload = ci.as_dict()
    payload["method"] = "percentile bootstrap over items"
    return payload


# --------------------------------------------------------------------------- #
# Per-judge metrics
# --------------------------------------------------------------------------- #
def _confidence_probabilities(rows: Sequence[JudgedItem]) -> list[float]:
    out: list[float] = []
    for row in rows:
        if row.confidence is None:
            continue
        out.append(row.confidence / 100.0)
    return out


def judge_metrics(
    rows: Sequence[JudgedItem],
    *,
    judge_id: str,
    repeat: int,
    seed: int = 20260101,
    n_resamples: int = N_RESAMPLES,
) -> dict[str, Any]:
    """All single-judge metrics for one repeat."""
    scored = [row for row in rows if row.parse_ok and row.verdict is not None]
    n = len(rows)
    n_scored = len(scored)
    n_parse_failures = sum(1 for row in rows if row.error and not row.error.startswith("api_error"))
    n_api_errors = sum(1 for row in rows if row.error and row.error.startswith("api_error"))

    correct_rows = [row for row in scored if row.is_correct]
    wrong_rows = [row for row in scored if not row.is_correct]
    n_agree = sum(1 for row in scored if row.agrees_with_key)

    accuracy = _wilson(n_agree, n_scored)
    false_accept = _wilson(
        sum(1 for row in wrong_rows if row.says_correct),
        len(wrong_rows),
    )
    false_reject = _wilson(
        sum(1 for row in correct_rows if row.says_correct is False),
        len(correct_rows),
    )

    verdicts = [bool(row.says_correct) for row in scored]
    truth = [bool(row.is_correct) for row in scored]
    kappa_key = cohen_kappa(verdicts, truth) if n_scored >= 2 else float("nan")
    kappa_ci = _bootstrap_index_ci(
        n_scored,
        lambda idx: _safe_kappa(
            [verdicts[i] for i in idx],
            [truth[i] for i in idx],
        ),
        n_resamples=n_resamples,
        seed=seed,
    )

    confidences = [float(row.confidence) for row in scored if row.confidence is not None]
    outcomes = [bool(row.agrees_with_key) for row in scored if row.confidence is not None]
    probabilities = [value / 100.0 for value in confidences]

    ece = calibration_error(probabilities, outcomes, bins=5) if probabilities else None
    brier = brier_score(probabilities, outcomes) if probabilities else None
    discriminative_auroc = auroc(confidences, outcomes) if confidences else None
    raw_auroc = (
        auroc([1.0 if row.says_correct else 0.0 for row in scored], truth)
        if n_scored > 1 and len(set(truth)) > 1
        else None
    )

    ece_ci = _bootstrap_index_ci(
        len(probabilities),
        lambda idx: _safe_stat(
            lambda: calibration_error([probabilities[i] for i in idx], [outcomes[i] for i in idx], bins=5)
        ),
        n_resamples=n_resamples,
        seed=seed,
    )
    brier_ci = _bootstrap_index_ci(
        len(probabilities),
        lambda idx: _safe_stat(lambda: brier_score([probabilities[i] for i in idx], [outcomes[i] for i in idx])),
        n_resamples=n_resamples,
        seed=seed,
    )
    auroc_ci = _bootstrap_index_ci(
        len(confidences),
        lambda idx: _safe_stat(lambda: auroc([confidences[i] for i in idx], [outcomes[i] for i in idx])),
        n_resamples=n_resamples,
        seed=seed,
    )

    confidence_values = [row.confidence for row in scored if row.confidence is not None]

    # Accuracy two ways (v0.3): the first excludes failed items, the second
    # counts every failed item as a disagreement with the key. Both are always
    # reported so neither can be quoted without the other next to it.
    n_failures = n - n_scored
    failures_as_wrong = _wilson(n_agree, n) if n else {
        "numerator": 0, "denominator": 0, "estimate": None, "ci95_wilson": None, "method": "wilson",
    }

    counts_by_type = dict.fromkeys(FAILURE_TYPES, 0)
    for row in rows:
        counts_by_type[row.failure_type] = counts_by_type.get(row.failure_type, 0) + 1

    return {
        "judge_id": judge_id,
        "repeat": repeat,
        "n_items": n,
        "n_scored": n_scored,
        "n_parse_failures": n_parse_failures,
        "n_api_errors": n_api_errors,
        "unscored_item_ids": [row.item_id for row in rows if not row.parse_ok],
        "accuracy": {**accuracy, "label": "failed items excluded from the denominator"},
        "accuracy_excluding_failures": {
            **accuracy,
            "label": "failed items excluded from the denominator",
        },
        "accuracy_counting_failures_as_wrong": {
            **failures_as_wrong,
            "label": "every failed item counted as a disagreement with the key",
        },
        "n_failures": n_failures,
        "failure_counts": counts_by_type,
        "false_accept_rate": false_accept,
        "false_reject_rate": false_reject,
        "cohen_kappa_vs_key": None if math.isnan(kappa_key) else float(kappa_key),
        "cohen_kappa_vs_key_ci95": kappa_ci,
        "gwet_ac1_vs_key": _safe_stat(lambda: gwet_ac1(verdicts, truth)),
        "percent_agreement_vs_key": _safe_stat(lambda: percent_agreement(verdicts, truth)),
        "calibration": {
            "ece_5_bins": ece,
            "ece_5_bins_ci95": ece_ci,
            "brier": brier,
            "brier_ci95": brier_ci,
            "auroc_confidence_predicts_own_correctness": discriminative_auroc,
            "auroc_confidence_predicts_own_correctness_ci95": auroc_ci,
            "auroc_raw_verdict_predicts_truth": raw_auroc,
            "mean_confidence": (sum(confidences) / len(confidences)) if confidences else None,
            "confidence_min": min(confidence_values) if confidence_values else None,
            "confidence_max": max(confidence_values) if confidence_values else None,
            "confidence_histogram_5_bins": _histogram(confidence_values, bins=5, low=0.0, high=100.0),
            "reliability_bins": _reliability_bins(probabilities, outcomes, bins=5),
        },
        "verdict_distribution": {
            "correct": sum(1 for row in scored if row.says_correct),
            "incorrect": sum(1 for row in scored if row.says_correct is False),
        },
        "error_examples": [
            {
                "item_id": row.item_id,
                "candidate_was_correct": row.is_correct,
                "judge_said": row.verdict,
                "confidence": row.confidence,
            }
            for row in scored
            if not row.agrees_with_key
        ][:10],
    }


def _safe_stat(compute: Any) -> float | None:
    try:
        value = compute()
    except (ValueError, ZeroDivisionError):
        return None
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _safe_kappa(left: Sequence[Any], right: Sequence[Any]) -> float:
    value = cohen_kappa(left, right)
    return float("nan") if value is None else float(value)


def _reliability_bins(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
    *,
    bins: int = 5,
) -> list[dict[str, Any]]:
    """Per-bin mean confidence and observed accuracy, for a reliability diagram."""
    if not probabilities:
        return []
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows: list[dict[str, Any]] = []
    for index in range(bins):
        low, high = float(edges[index]), float(edges[index + 1])
        members = [
            (float(p), bool(y))
            for p, y in zip(probabilities, outcomes, strict=True)
            if (low <= p < high) or (index == bins - 1 and p == high)
        ]
        if not members:
            rows.append({"low": low, "high": high, "n": 0, "mean_confidence": None, "observed_accuracy": None})
            continue
        rows.append(
            {
                "low": low,
                "high": high,
                "n": len(members),
                "mean_confidence": sum(p for p, _ in members) / len(members),
                "observed_accuracy": sum(1 for _, y in members if y) / len(members),
            }
        )
    return rows


def _histogram(values: Sequence[float], *, bins: int, low: float, high: float) -> list[dict[str, Any]]:
    if not values:
        return []
    edges = np.linspace(low, high, bins + 1)
    counts = [0] * bins
    for value in values:
        index = min(int((value - low) / (high - low) * bins), bins - 1)
        counts[max(0, index)] += 1
    return [
        {"low": float(edges[i]), "high": float(edges[i + 1]), "count": counts[i]}
        for i in range(bins)
    ]


# --------------------------------------------------------------------------- #
# Consistency across repeats
# --------------------------------------------------------------------------- #
def consistency_metrics(verdicts: Sequence[Verdict], judge_id: str, repeats: int) -> dict[str, Any]:
    """Do the three repeats of the same judge give the same verdict?"""
    by_item: dict[str, list[Verdict]] = {}
    for verdict in verdicts:
        if verdict.judge_id != judge_id:
            continue
        by_item.setdefault(verdict.item_id, []).append(verdict)
    complete = [rows for rows in by_item.values() if len(rows) >= repeats and all(row.parse_ok for row in rows)]
    if not complete:
        return {"judge_id": judge_id, "n_items_all_repeats": 0, "consistent_fraction": None}
    consistent = 0
    pairwise: list[float] = []
    for rows in complete:
        ordered = sorted(rows, key=lambda row: row.repeat)
        labels = [row.verdict for row in ordered]
        if len(set(labels)) == 1:
            consistent += 1
        agreements = []
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                agreements.append(1.0 if labels[i] == labels[j] else 0.0)
        pairwise.extend(agreements)
    n = len(complete)
    successes = consistent
    low, high = proportion_ci(successes, n)
    return {
        "judge_id": judge_id,
        "n_items_all_repeats": n,
        "consistent_count": successes,
        "consistent_fraction": successes / n,
        "consistent_fraction_ci95_wilson": [low, high],
        "mean_pairwise_agreement_across_repeats": (sum(pairwise) / len(pairwise)) if pairwise else None,
    }


# --------------------------------------------------------------------------- #
# Cross-judge error correlation
# --------------------------------------------------------------------------- #
def _permutation_both_wrong(
    left: Sequence[bool],
    right: Sequence[bool],
    *,
    n_permutations: int = 5000,
    seed: int = 20260101,
) -> dict[str, Any] | None:
    """Permutation test of both-wrong co-occurrence, preserving both marginals."""
    n = len(left)
    if n < 3 or len(right) != n:
        return None
    observed = sum(1 for a, b in zip(left, right, strict=True) if a and b)
    rng = np.random.default_rng(seed)
    right_arr = np.asarray(right, dtype=bool)
    draws = np.empty(n_permutations, dtype=int)
    for i in range(n_permutations):
        shuff = rng.permutation(right_arr)
        draws[i] = int(np.sum(np.asarray(left, dtype=bool) & shuff))
    expected = float(draws.mean())
    deviation = abs(observed - expected)
    extreme = float(np.mean(np.abs(draws - expected) >= deviation - 1e-12))
    return {
        "observed_both_wrong": observed,
        "expected_both_wrong_under_independence": expected,
        "p_value_two_sided": extreme,
        "n_permutations": n_permutations,
        "method": "permutation of one judge's error indicators",
    }


def _binomial_both_wrong(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any] | None:
    n = len(left)
    if n == 0:
        return None
    p1 = sum(left) / n
    p2 = sum(right) / n
    expected = n * p1 * p2
    observed = sum(1 for a, b in zip(left, right, strict=True) if a and b)
    p_value = None
    if 0 < p1 * p2 < 1 and n > 0:
        from scipy import stats as scipy_stats

        p_value = float(scipy_stats.binomtest(observed, n, p1 * p2, alternative="two-sided").pvalue)
    return {
        "observed": observed,
        "expected": expected,
        "excess_ratio": (observed / expected) if expected > 0 else None,
        "p_value_binomial": p_value,
        "method": "binomial with p = error_rate_1 * error_rate_2 (anti-conservative: p estimated from the same data)",
    }


def cross_judge_metrics(
    first: Sequence[JudgedItem],
    second: Sequence[JudgedItem],
    *,
    judge_a: str,
    judge_b: str,
    seed: int = 20260101,
    n_resamples: int = N_RESAMPLES,
) -> dict[str, Any]:
    """Agreement and error correlation between two judges on the same items."""
    by_a = {row.item_id: row for row in first if row.verdict is not None}
    by_b = {row.item_id: row for row in second if row.verdict is not None}
    common = sorted(set(by_a) & set(by_b))
    if len(common) < 2:
        return {"judge_a": judge_a, "judge_b": judge_b, "n_common_items": len(common), "note": "too few common items"}
    a_rows = [by_a[item] for item in common]
    b_rows = [by_b[item] for item in common]
    a_verdicts = [bool(row.says_correct) for row in a_rows]
    b_verdicts = [bool(row.says_correct) for row in b_rows]
    a_errors = [not row.agrees_with_key for row in a_rows]
    b_errors = [not row.agrees_with_key for row in b_rows]

    kappa = cohen_kappa(a_verdicts, b_verdicts)
    kappa_ci = _bootstrap_index_ci(
        len(common),
        lambda idx: _safe_kappa([a_verdicts[i] for i in idx], [b_verdicts[i] for i in idx]),
        n_resamples=n_resamples,
        seed=seed,
    )
    phi = phi_coefficient(a_errors, b_errors)
    phi_ci = _bootstrap_index_ci(
        len(common),
        lambda idx: _safe_stat(
            lambda: phi_coefficient([a_errors[i] for i in idx], [b_errors[i] for i in idx])
        ),
        n_resamples=n_resamples,
        seed=seed,
    )
    both_wrong = sum(1 for x, y in zip(a_errors, b_errors, strict=True) if x and y)
    either_wrong = sum(1 for x, y in zip(a_errors, b_errors, strict=True) if x or y)
    mcnemar = mcnemar_test(a_errors, b_errors)
    return {
        "judge_a": judge_a,
        "judge_b": judge_b,
        "n_common_items": len(common),
        "verdict_agreement": {
            "percent_agreement": percent_agreement(a_verdicts, b_verdicts),
            "cohen_kappa": None if math.isnan(kappa) else float(kappa),
            "cohen_kappa_ci95": kappa_ci,
            "gwet_ac1": _safe_stat(lambda: gwet_ac1(a_verdicts, b_verdicts)),
        },
        "error_correlation": {
            "phi_coefficient": phi,
            "phi_coefficient_ci95": phi_ci,
            "error_rate_a": sum(a_errors) / len(a_errors),
            "error_rate_b": sum(b_errors) / len(b_errors),
            "both_wrong_count": both_wrong,
            "both_wrong_rate": both_wrong / len(common),
            "either_wrong_count": either_wrong,
            "jaccard_of_error_sets": (both_wrong / either_wrong) if either_wrong else None,
            "both_wrong_vs_independence_binomial": _binomial_both_wrong(a_errors, b_errors),
            "both_wrong_vs_independence_permutation": _permutation_both_wrong(a_errors, b_errors, seed=seed),
            "mcnemar_errors": mcnemar,
        },
    }


# --------------------------------------------------------------------------- #
# Possible key errors
# --------------------------------------------------------------------------- #
def possible_key_errors(
    judges: Sequence[str],
    aligned: Mapping[str, Sequence[JudgedItem]],
    *,
    confidence_floor: int = CONFIDENCE_HIGH,
    single_judge_floor: int = CONFIDENCE_VERY_HIGH,
) -> dict[str, Any]:
    """Items that deserve a human look because confident judges disagree with the key.

    With two judges the rule is the pre-specified one: both say the candidate is
    wrong when the key says it is right (or both say it is right when the key says
    it is wrong), both at or above ``confidence_floor``. With one judge that rule
    cannot be applied, so a separate, weaker, clearly-labelled flag is produced
    and no item is promoted to "possible key error".
    """
    by_judge = {judge: {row.item_id: row for row in rows} for judge, rows in aligned.items()}
    common_ids = sorted(set.intersection(*[set(rows) for rows in by_judge.values()])) if by_judge else []
    confident_both: list[dict[str, Any]] = []
    # The two-judge rule needs two judges. With one judge there is nothing to
    # corroborate the disagreement, so the weaker fallback below is used instead.
    for item_id in common_ids if len(judges) >= 2 else []:
        rows = [by_judge[judge][item_id] for judge in judges if judge in by_judge]
        if len(rows) < len(judges):
            continue
        if any(row.verdict is None or row.confidence is None for row in rows):
            continue
        if all(row.confidence >= confidence_floor for row in rows) and len({not row.agrees_with_key for row in rows}) == 1:
            agrees = bool(rows[0].agrees_with_key)
            if not agrees:
                confident_both.append(
                    {
                        "item_id": item_id,
                        "candidate_was_correct": rows[0].is_correct,
                        "judges": {row_judge: {"verdict": row.verdict, "confidence": row.confidence} for row_judge, row in zip(judges, rows, strict=True)},
                        "reason": "both judges confidently disagree with the key",
                    }
                )

    single: list[dict[str, Any]] = []
    if len(judges) < 2:
        for judge in judges:
            for item_id, row in by_judge.get(judge, {}).items():
                if row.verdict is None or row.confidence is None:
                    continue
                if row.confidence >= single_judge_floor and not row.agrees_with_key:
                    single.append(
                        {
                            "item_id": item_id,
                            "candidate_was_correct": row.is_correct,
                            "judge": judge,
                            "verdict": row.verdict,
                            "confidence": row.confidence,
                            "reason": "single judge very confident and disagrees with the key; not distinguishable from a judge error",
                        }
                    )
    return {
        "rule": {
            "two_judge": f"both judges disagree with the key at confidence >= {confidence_floor}",
            "single_judge_fallback": (
                f"one judge disagrees at confidence >= {single_judge_floor}; reported for manual review only, "
                "never as a key error"
            ),
            "applied": "two_judge" if len(judges) >= 2 else "single_judge_fallback",
        },
        "n_flagged_possible_key_errors": len(confident_both),
        "possible_key_errors": confident_both,
        "n_flagged_single_judge": len(single),
        "single_judge_flags": single,
        "flagged_item_ids": sorted({row["item_id"] for row in confident_both} | {row["item_id"] for row in single}),
    }


# --------------------------------------------------------------------------- #
# Whole-study analysis
# --------------------------------------------------------------------------- #
def analyse_study(
    *,
    items: Sequence[McqItem],
    responses: Mapping[str, CandidateResponse],
    verdicts: Sequence[Verdict],
    judges: Sequence[JudgeSpec],
    primary_repeat: int = 0,
    repeats: int = 3,
    seed: int = 20260101,
    n_resamples: int = N_RESAMPLES,
    pipeline_test_only: bool = False,
) -> dict[str, Any]:
    """Compute every metric the specification asks for, in one JSON-ready dict."""
    truth = build_truth(items, responses)
    available = [judge for judge in judges if judge.available]
    aligned: dict[str, list[JudgedItem]] = {
        judge.judge_id: align(verdicts, truth, judge.judge_id, primary_repeat) for judge in available
    }

    per_judge: dict[str, Any] = {}
    for judge in available:
        rows = aligned[judge.judge_id]
        per_judge[judge.judge_id] = {
            "spec": judge.as_dict(),
            "primary_repeat": judge_metrics(rows, judge_id=judge.judge_id, repeat=primary_repeat, seed=seed, n_resamples=n_resamples),
            "across_repeats": consistency_metrics(verdicts, judge.judge_id, repeats),
        }

    pairwise: dict[str, Any] = {}
    ids = [judge.judge_id for judge in available]
    for i, first in enumerate(ids):
        for second in ids[i + 1:]:
            pairwise[f"{first}|{second}"] = cross_judge_metrics(
                aligned[first],
                aligned[second],
                judge_a=first,
                judge_b=second,
                seed=seed,
                n_resamples=n_resamples,
            )

    flags = possible_key_errors(ids, aligned, confidence_floor=CONFIDENCE_HIGH)
    flagged = set(flags["flagged_item_ids"])

    without_flagged: dict[str, Any] = {}
    for judge in available:
        kept = [row for row in aligned[judge.judge_id] if row.item_id not in flagged]
        if kept and len(kept) != len(aligned[judge.judge_id]):
            without_flagged[judge.judge_id] = judge_metrics(
                kept, judge_id=judge.judge_id, repeat=primary_repeat, seed=seed, n_resamples=n_resamples
            )

    # Benchmark health, defined explicitly in the report.
    n_items = len(truth)
    n_key_correct_candidates = sum(1 for value in truth.values() if value)
    ceiling_per_judge: dict[str, Any] = {}
    for judge in available:
        rows_all_repeats: dict[str, list[JudgedItem]] = {}
        for verdict in verdicts:
            if verdict.judge_id != judge.judge_id or not verdict.parse_ok or verdict.item_id not in truth:
                continue
            rows_all_repeats.setdefault(verdict.item_id, []).append(
                JudgedItem(verdict.item_id, bool(truth[verdict.item_id]), verdict.verdict, verdict.confidence, True)
            )
        complete = [rows for rows in rows_all_repeats.values() if len(rows) == repeats]
        if complete:
            n_all_right = sum(1 for rows in complete if all(row.agrees_with_key for row in rows))
            low, high = proportion_ci(n_all_right, len(complete))
            ceiling_per_judge[judge.judge_id] = {
                "n_items_with_all_repeats": len(complete),
                "n_all_repeats_correct": n_all_right,
                "ceiling_rate": n_all_right / len(complete),
                "ceiling_rate_ci95_wilson": [low, high],
                "definition": "fraction of items the judge gets right on every repeat",
            }

    return {
        "pipeline_test_only": pipeline_test_only,
        "status": "mock" if pipeline_test_only else "real",
        "n_items": n_items,
        "n_candidates_correct": n_key_correct_candidates,
        "n_candidates_incorrect": n_items - n_key_correct_candidates,
        "candidate_condition_balance": {
            "correct": n_key_correct_candidates,
            "incorrect": n_items - n_key_correct_candidates,
        },
        "judges": per_judge,
        "cross_judge": pairwise,
        "possible_key_errors": flags,
        "with_and_without_flagged_items": {
            "excluding_flagged": without_flagged,
            "n_flagged": len(flagged),
            "note": "accuracy recomputed after dropping flagged items, to show how much the flag could matter",
        },
        "benchmark_health": {
            "n_items": n_items,
            "ceiling_per_judge": ceiling_per_judge,
            "key_letter_distribution": _key_distribution(items),
            "definition_ceiling": "fraction of items answered correctly on every repeat by that judge",
        },
        "methods": {
            "proportion_intervals": "Wilson score, 95%",
            "other_intervals": f"percentile bootstrap over items, {n_resamples} resamples",
            "consistency": f"identical verdict across all {repeats} repeats",
            "confidence_scale": "0-100, treated as P(own verdict is right)",
            "n_resamples": n_resamples,
        },
    }


def _key_distribution(items: Sequence[McqItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.key_letter] = counts.get(item.key_letter, 0) + 1
    return dict(sorted(counts.items()))
