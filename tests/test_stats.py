"""Paper-grade statistics: formulas, seeds and undefined cases."""

from __future__ import annotations

import math

import pytest

from medeval_core import stats


def test_bootstrap_interval_is_seeded_and_brackets_the_estimate() -> None:
    values = [0, 1, 1, 1, 0, 1, 1, 0, 1, 1]
    first = stats.bootstrap_ci(values, n_resamples=400, seed=7)
    second = stats.bootstrap_ci(values, n_resamples=400, seed=7)
    assert first == second
    assert first.estimate == pytest.approx(0.7)
    assert first.low <= first.estimate <= first.high
    assert first.method == "percentile"
    assert first.n == 10


def test_stratified_bootstrap_uses_every_stratum() -> None:
    values = [0, 0, 1, 1, 1, 1, 1, 1]
    strata = ["a", "a", "b", "b", "c", "c", "c", "c"]
    interval = stats.bootstrap_ci(values, strata=strata, n_resamples=200, seed=11)
    assert interval.method == "stratified-percentile"
    assert interval.low <= interval.estimate <= interval.high


def test_bootstrap_rejects_empty_and_bad_alpha() -> None:
    with pytest.raises(ValueError):
        stats.bootstrap_ci([])
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 2], alpha=1.5)


def test_paired_bootstrap_reports_the_mean_difference() -> None:
    a = [5, 4, 3, 5, 4, 5]
    b = [3, 3, 2, 4, 4, 3]
    interval = stats.paired_bootstrap_diff(a, b, n_resamples=300, seed=3)
    assert interval.estimate == pytest.approx(1.1667, abs=1e-3)
    assert interval.method.startswith("paired-")
    with pytest.raises(ValueError):
        stats.paired_bootstrap_diff([1], [1, 2])


def test_mcnemar_exact_for_small_discordant_counts() -> None:
    # 8 items where left is wrong/right is right, 1 the other way.
    left = [False] * 8 + [True] + [True] * 11
    right = [True] * 8 + [False] + [True] * 11
    result = stats.mcnemar_test(left, right)
    assert result["b01"] == 8 and result["b10"] == 1
    assert result["method"] == "exact binomial"
    assert result["p_value"] < 0.05
    assert result["significant"] is True


def test_mcnemar_with_no_discordant_pairs_is_not_significant() -> None:
    result = stats.mcnemar_test([True, False], [True, False])
    assert result["p_value"] == 1.0
    assert result["method"] == "no discordant pairs"
    assert result["significant"] is False


def test_cohen_kappa_known_example_and_undefined_case() -> None:
    # 2x2 example: 20 agreements out of 20 but one rater uses one category.
    assert math.isnan(stats.cohen_kappa(["a"] * 10, ["a"] * 10))
    a = ["a", "a", "b", "b", "a", "b", "a", "b"]
    b = ["a", "b", "b", "b", "a", "b", "a", "a"]
    assert stats.cohen_kappa(a, b) == pytest.approx(0.5)
    assert stats.raw_agreement(a, b) == pytest.approx(0.75)


def test_fleiss_kappa_for_three_raters() -> None:
    # Three raters, all agree on 5 items and all disagree on 1.
    rows = [
        ["yes", "yes", "yes"],
        ["yes", "yes", "yes"],
        ["no", "no", "no"],
        ["no", "no", "no"],
        ["yes", "no", "yes"],
    ]
    value = stats.fleiss_kappa(rows)
    assert -1.0 <= value <= 1.0
    assert math.isnan(stats.fleiss_kappa([["yes", "yes"], ["yes", "yes"]]))


def test_krippendorff_alpha_with_missing_values() -> None:
    rows = [
        ["a", "a", None, "a"],
        ["b", "b", "b", "b"],
        ["a", "a", "a", "a"],
    ]
    value = stats.krippendorff_alpha(rows)
    assert -1.0 <= value <= 1.0
    assert math.isnan(stats.krippendorff_alpha([["a"], ["b"]]))


def test_disagreement_matrix_and_cross_family_correlation() -> None:
    ratings = {
        "j1": ["a", "b", "a", "b"],
        "j2": ["a", "b", "b", "b"],
        "j3": ["b", "b", "a", "a"],
    }
    matrix = stats.disagreement_matrix(ratings)
    assert matrix["n_items"] == 4
    assert matrix["unanimous_items"] == 1
    pair = next(row for row in matrix["pairs"] if row["left"] == "j1" and row["right"] == "j2")
    assert pair["disagreements"] == 1
    correlation = stats.cross_family_error_correlation([True, False, True, False], [True, False, False, True])
    assert correlation["both_wrong"] == 1
    assert -1.0 <= correlation["phi"] <= 1.0


def test_behavioural_summaries() -> None:
    assert stats.abstention_rate([True, False, False, True])["rate"] == pytest.approx(0.5)
    assert stats.format_compliance_rate([True, True, False])["rate"] == pytest.approx(2 / 3)
    latency = stats.latency_summary([10, 20, 30, 40, 50])
    assert latency["p50_ms"] == pytest.approx(30)
    assert latency["p95_ms"] == pytest.approx(48)
    assert stats.cost_per_correct_item(2.0, 4) == pytest.approx(0.5)
    assert stats.cost_per_correct_item(None, 4) is None
    assert stats.cost_per_correct_item(2.0, 0) is None


def test_wilson_interval_is_bounded() -> None:
    low, high = stats.wilson_interval(7, 10)
    assert 0 <= low <= 0.7 <= high <= 1
    assert math.isnan(stats.wilson_interval(0, 0)[0])


def test_macro_classification_metrics_and_bootstrap() -> None:
    gold = ["hallucinated", "not-hallucinated", "not-sure", "hallucinated"]
    predicted = ["hallucinated", "not-hallucinated", "hallucinated", "__parse_error__"]
    report = stats.classification_report(predicted, gold, labels=["hallucinated", "not-hallucinated", "not-sure"])
    assert report["n"] == 4
    assert report["per_class"]["hallucinated"]["support"] == 2
    assert 0 <= report["macro_f1"] <= 1
    assert 0 <= report["macro_precision"] <= 1
    assert stats.macro_f1(predicted, gold) == pytest.approx(report["macro_f1"])
    interval = stats.bootstrap_macro_metric(
        predicted,
        gold,
        labels=["hallucinated", "not-hallucinated", "not-sure"],
        n_resamples=120,
        seed=5,
    )
    assert interval.low <= interval.estimate <= interval.high
    assert interval.method == "item-bootstrap-macro"
    with pytest.raises(ValueError):
        stats.macro_f1(["a"], ["a", "b"])
