"""Tests for the metric definitions, formulas and harness plugins."""

from __future__ import annotations

import math

import pytest

from medeval_metrics import (
    METRICS,
    accuracy,
    brier_score,
    critical_error_rate,
    expected_calibration_error,
    mean_confidence,
    nll,
    omission_rate,
    perturbation_consistency,
    rubric_weighted_score,
    subgroup_performance,
    unsafe_recommendation_rate,
)
from medeval_metrics.lm_eval_tasks import process_results_mc


def test_accuracy_hand_value() -> None:
    assert accuracy([True, False, True, None]) == pytest.approx(2 / 3)


def test_accuracy_all_missing_is_nan() -> None:
    assert math.isnan(accuracy([None, None]))


def test_critical_error_rate_hand_value() -> None:
    assert critical_error_rate([True, False, None, False]) == pytest.approx(1 / 3)


def test_error_rates_share_semantics() -> None:
    flags = [True, True, False, False]
    assert unsafe_recommendation_rate(flags) == pytest.approx(0.5)
    assert omission_rate(flags) == pytest.approx(0.5)


def test_brier_score_hand_value() -> None:
    # ((0.9-1)^2 + (0.1-0)^2)/2 = (0.01+0.01)/2
    assert brier_score([0.9, 0.1], [1, 0]) == pytest.approx(0.01)


def test_brier_score_rejects_out_of_range_probability() -> None:
    with pytest.raises(ValueError):
        brier_score([1.2], [1])


def test_nll_hand_value() -> None:
    # -(ln .5 + ln .75)/2 = (0.693147 + 0.287682)/2
    assert nll([0.5, 0.25], [1, 0]) == pytest.approx((math.log(2) + math.log(4 / 3)) / 2)


def test_mean_confidence() -> None:
    assert mean_confidence([0.2, 0.8, None]) == pytest.approx(0.5)


def test_ece_hand_value_two_bins() -> None:
    # bin [0,.5): p=.15,.05 -> conf .1, acc 0     contribution .5*|0-.1| = .05
    # bin [.5,1]: p=.95,.85 -> conf .9, acc .5    contribution .5*|.5-.9| = .2
    probabilities = [0.95, 0.85, 0.15, 0.05]
    outcomes = [1, 0, 0, 0]
    result = expected_calibration_error(probabilities, outcomes, n_bins=2)
    assert result.ece == pytest.approx(0.25)
    assert result.mce == pytest.approx(0.4)
    assert result.n == 4
    assert len(result.bins) == 2


def test_ece_perfect_calibration_is_zero() -> None:
    # Four items at confidence .5, two correct -> bin gap 0
    result = expected_calibration_error([0.5] * 4, [1, 0, 1, 0], n_bins=2)
    assert result.ece == pytest.approx(0.0)


def test_ece_quantile_strategy_runs() -> None:
    result = expected_calibration_error(
        [0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], n_bins=2, strategy="quantile"
    )
    assert 0.0 <= result.ece <= 1.0


def test_ece_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [1], strategy="kmeans")


def test_rubric_weighted_score_hand_value() -> None:
    result = rubric_weighted_score({"diagnostic_accuracy": 2.0, "management": 1.0}, {"diagnostic_accuracy": 2.0, "management": 1.0})
    assert result.weighted_total == pytest.approx((2 * 2 + 1) / 3)
    assert result.per_criterion["diagnostic_accuracy"] == 2.0
    assert result.n_criteria == 2


def test_rubric_weighted_score_requires_weight() -> None:
    with pytest.raises(ValueError):
        rubric_weighted_score({"diagnostic_accuracy": 2.0}, {"management": 1.0})


def test_rubric_weighted_score_requires_positive_weights() -> None:
    with pytest.raises(ValueError):
        rubric_weighted_score({"a": 1.0}, {"a": 0.0})


def test_perturbation_consistency_hand_value() -> None:
    assert perturbation_consistency([1, 2, 3], [1, 2, 4]) == pytest.approx(2 / 3)


def test_perturbation_consistency_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        perturbation_consistency([1, 2], [1])


def test_subgroup_performance_hand_values() -> None:
    result = subgroup_performance(["cardio", "cardio", "cardio", "renal"], [1, 1, 0, 0], min_n=3)
    assert result["cardio"]["n"] == 3
    assert result["cardio"]["mean"] == pytest.approx(2 / 3)
    assert result["renal"]["small_n"] == 1


def test_all_metrics_are_documented() -> None:
    assert METRICS, "metric registry must not be empty"
    for metric_id, definition in METRICS.items():
        assert definition.metric_id == metric_id
        assert definition.version == "v0.1"
        assert definition.formula.strip()
        assert definition.interpretation.strip()
        assert definition.assumptions, metric_id
        assert definition.when_not_to_use.strip(), metric_id
        assert definition.literature_ids, metric_id
        assert definition.unit.strip()
        for literature_id in definition.literature_ids:
            assert literature_id.startswith("lit_"), literature_id


def test_safety_metrics_are_marked() -> None:
    for metric_id in ("critical_error_rate", "unsafe_recommendation_rate", "omission_rate"):
        assert METRICS[metric_id].safety_related is True


def test_process_results_mc_uniform_logprobs() -> None:
    # All choices equal -> p=0.25 each; argmax picks index 0, so gold index 1 is wrong.
    result = process_results_mc([0.0, 0.0, 0.0, 0.0], gold_index=1, n_choices=4)
    assert result["acc"] == 0
    assert result["medeval_brier"] == pytest.approx((0.25 - 0) ** 2)
    assert result["medeval_nll"] == pytest.approx(-math.log(0.25))


def test_process_results_mc_confident_and_correct() -> None:
    # logprobs 0, -1.9, -1.9, -1.9 -> softmax p0 = 0.6900
    result = process_results_mc([0.0, -1.9, -1.9, -1.9], gold_index=0, n_choices=4)
    p0 = 1.0 / (1.0 + 3 * math.exp(-1.9))
    assert result["acc"] == 1
    assert result["medeval_brier"] == pytest.approx((p0 - 1) ** 2)
    assert result["medeval_mean_confidence"] == pytest.approx(p0)


def test_process_results_mc_accepts_tuple_results() -> None:
    result = process_results_mc([(0.0, True), (-5.0, False), (-5.0, False), (-5.0, False)], gold_index=0, n_choices=4)
    assert result["acc"] == 1


def test_process_results_mc_rejects_wrong_choice_count() -> None:
    with pytest.raises(ValueError):
        process_results_mc([0.0, 1.0], gold_index=0, n_choices=4)


def test_process_results_mc_rejects_out_of_range_gold() -> None:
    with pytest.raises(ValueError):
        process_results_mc([0.0, 1.0, 2.0, 3.0], gold_index=4, n_choices=4)


def test_plugin_aggregations_are_registered() -> None:
    pytest.importorskip("lm_eval")
    from lm_eval.api.registry import get_aggregation

    from medeval_metrics import plugins  # noqa: F401  (import registers)

    worst = get_aggregation("medeval_worst_decile")
    trimmed = get_aggregation("medeval_trimmed_mean")
    assert worst is not None and trimmed is not None
    # p10 of 0..9 is 0.9; a 10% trimmed mean of 0..9 is 4.5
    assert worst(list(range(10))) == pytest.approx(0.9)
    assert trimmed(list(range(10))) == pytest.approx(4.5)


def test_plugin_metric_handles_nested_items() -> None:
    pytest.importorskip("lm_eval")
    from medeval_metrics.plugins import medeval_acc_metric, medeval_brier_metric

    assert medeval_acc_metric([[1, 0], [1], 0]) == pytest.approx(0.5)
    assert medeval_brier_metric([0.25, 0.25]) == pytest.approx(0.25)


def test_plugin_is_discoverable_as_entry_point() -> None:
    pytest.importorskip("lm_eval")
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="lm_eval.metrics")}
    assert {"medeval_brier", "medeval_nll", "medeval_mean_confidence"} <= names
    agg_names = {ep.name for ep in entry_points(group="lm_eval.aggregations")}
    assert {"medeval_worst_decile", "medeval_trimmed_mean"} <= agg_names
