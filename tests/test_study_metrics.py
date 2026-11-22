"""Known-answer tests for the study statistics.

Each test here uses a tiny table whose statistic can be computed by hand on paper,
so a regression in the implementation shows up as a wrong number rather than as a
plausible-looking one. Undefined cases must return ``None``, never a fabricated
value.
"""

from __future__ import annotations

import math

import pytest

from medeval_study.analysis import (
    JudgedItem,
    analyse_study,
    auroc,
    brier_score,
    build_truth,
    calibration_error,
    consistency_metrics,
    cross_judge_metrics,
    judge_metrics,
    phi_coefficient,
    possible_key_errors,
)
from medeval_study.judging import FAILURE_TYPES, JudgeSpec, Verdict, classify_failure
from medeval_study.models import CandidateResponse, McqItem

OPTIONS = {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"}


def item(item_id: str, key: str = "A") -> McqItem:
    return McqItem(
        item_id=item_id,
        question="A synthetic question long enough to pass the loader filter, with four options.",
        options=dict(OPTIONS),
        key_letter=key,
        key_text=OPTIONS[key],
        dataset="synthetic",
        split="dev",
        row_index=0,
    )


def response(item_id: str, letter: str) -> CandidateResponse:
    return CandidateResponse(
        item_id=item_id,
        chosen_letter=letter,
        chosen_text=OPTIONS[letter],
        text=f"Answer: {letter}",
        condition="correct" if letter == "A" else "incorrect",
        template_id="t1",
    )


def rows(spec: list[tuple[bool, str, int | None]]) -> list[JudgedItem]:
    """Build JudgedItem rows from (candidate_was_correct, verdict, confidence)."""
    out = []
    for index, (correct, verdict, confidence) in enumerate(spec):
        error = None if verdict else "parse_error: empty_output"
        out.append(
            JudgedItem(
                item_id=f"i{index}",
                is_correct=correct,
                verdict=verdict if verdict else None,
                confidence=confidence,
                parse_ok=verdict is not None,
                error=error,
                failure_type=classify_failure(error),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Proportions
# --------------------------------------------------------------------------- #
def test_perfect_judge_on_balanced_items() -> None:
    data = rows([(True, "CORRECT", 90)] * 5 + [(False, "INCORRECT", 90)] * 5)
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=50)
    assert metrics["accuracy"]["estimate"] == 1.0
    assert metrics["accuracy"]["numerator"] == 10
    assert metrics["false_accept_rate"]["estimate"] == 0.0
    assert metrics["false_reject_rate"]["estimate"] == 0.0
    assert metrics["cohen_kappa_vs_key"] == pytest.approx(1.0)
    assert metrics["n_parse_failures"] == 0


def test_constant_judge_gets_chance_level_kappa() -> None:
    """Saying CORRECT to everything agrees 50% of the time, which is chance here."""
    data = rows([(True, "CORRECT", 70)] * 5 + [(False, "CORRECT", 70)] * 5)
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=50)
    assert metrics["accuracy"]["estimate"] == 0.5
    assert metrics["false_accept_rate"]["estimate"] == 1.0
    assert metrics["false_reject_rate"]["estimate"] == 0.0
    assert metrics["cohen_kappa_vs_key"] == pytest.approx(0.0)


def test_classify_failure_maps_every_recorded_form() -> None:
    assert classify_failure(None) == "none"
    assert classify_failure("api_error: HTTP 401") == "api_error"
    assert classify_failure("parse_error: empty_output") == "empty_reply"
    assert classify_failure("parse_error: no_json_object") == "parse_failure"
    assert classify_failure("spend guard: call cap reached") == "refused"
    assert classify_failure("parse_error: invalid_verdict:MAYBE") == "parse_failure"


def test_failure_type_constants_are_closed() -> None:
    assert set(FAILURE_TYPES) == {"none", "parse_failure", "empty_reply", "api_error", "refused"}


def test_kappa_is_none_when_both_raters_are_constant() -> None:
    data = rows([(True, "CORRECT", 90)] * 4)
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=20)
    assert metrics["cohen_kappa_vs_key"] is None, "expected agreement of 1 makes kappa undefined"


def test_parse_failures_are_counted_not_dropped_silently() -> None:
    data = rows([(True, "CORRECT", 90), (True, "", None), (False, "INCORRECT", 80)])
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=50)
    assert metrics["n_items"] == 3
    assert metrics["n_scored"] == 2
    assert metrics["n_parse_failures"] == 1
    assert metrics["accuracy"]["denominator"] == 2


def test_wilson_interval_for_zero_successes() -> None:
    metrics = judge_metrics(rows([(True, "INCORRECT", 90)] * 10), judge_id="j", repeat=0, n_resamples=50)
    low, high = metrics["accuracy"]["ci95_wilson"]
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-3)


# --------------------------------------------------------------------------- #
# Calibration and discrimination
# --------------------------------------------------------------------------- #
def test_auroc_known_values() -> None:
    assert auroc([1, 2, 3, 4], [False, False, True, True]) == pytest.approx(1.0)
    assert auroc([1, 2, 3, 4], [True, True, False, False]) == pytest.approx(0.0)
    assert auroc([1, 1, 2, 2], [False, True, False, True]) == pytest.approx(0.5)
    assert auroc([1, 1], [True, True]) is None, "one class only is undefined"


def test_brier_known_values() -> None:
    assert brier_score([1.0, 1.0], [True, True]) == pytest.approx(0.0)
    assert brier_score([0.5, 0.5], [True, False]) == pytest.approx(0.25)
    assert brier_score([0.0, 0.0], [True, True]) == pytest.approx(1.0)


def test_ece_known_values() -> None:
    assert calibration_error([1.0] * 4, [True] * 4) == pytest.approx(0.0)
    assert calibration_error([0.5] * 4, [True] * 4) == pytest.approx(0.5)
    assert calibration_error([1.0] * 4, [True, True, True, False]) == pytest.approx(0.25)


def test_ece_is_none_without_data() -> None:
    assert calibration_error([], []) is None


def test_phi_coefficient_known_values() -> None:
    assert phi_coefficient([True, False, True, False], [True, False, True, False]) == pytest.approx(1.0)
    assert phi_coefficient([True, False, True, False], [False, True, False, True]) == pytest.approx(-1.0)
    assert phi_coefficient([True, True, True, True], [True, False, True, False]) is None


# --------------------------------------------------------------------------- #
# Consistency and cross-judge
# --------------------------------------------------------------------------- #
def judge_spec(judge_id: str) -> JudgeSpec:
    return JudgeSpec(judge_id=judge_id, family="test", backend="mock", model="mock", repeats=3)


def verdicts_for(judge_id: str, pattern: dict[str, list[str]]) -> list[Verdict]:
    out = []
    for item_id, labels in pattern.items():
        for repeat, label in enumerate(labels):
            out.append(
                Verdict(
                    item_id=item_id,
                    judge_id=judge_id,
                    repeat=repeat,
                    verdict=label,
                    confidence=90,
                    parse_ok=True,
                    attempts=1,
                    model="mock",
                    prompt_sha256="x" * 8,
                    latency_ms=1.0,
                )
            )
    return out


def test_consistency_counts_identical_verdicts() -> None:
    verdicts = verdicts_for("j1", {"i1": ["CORRECT"] * 3, "i2": ["CORRECT", "INCORRECT", "CORRECT"], "i3": ["INCORRECT"] * 3})
    metrics = consistency_metrics(verdicts, "j1", repeats=3)
    assert metrics["n_items_all_repeats"] == 3
    assert metrics["consistent_count"] == 2
    assert metrics["consistent_fraction"] == pytest.approx(2 / 3)
    assert metrics["mean_pairwise_agreement_across_repeats"] == pytest.approx(7 / 9, abs=1e-6)


def test_cross_judge_both_wrong_rate_and_phi() -> None:
    # Four items: both right, both wrong, only A wrong, only B wrong.
    truth = {"i1": True, "i2": True, "i3": False, "i4": False}
    a = [
        JudgedItem("i1", True, "CORRECT", 90, True),
        JudgedItem("i2", True, "INCORRECT", 90, True),
        JudgedItem("i3", False, "CORRECT", 90, True),
        JudgedItem("i4", False, "INCORRECT", 90, True),
    ]
    b = [
        JudgedItem("i1", True, "CORRECT", 90, True),
        JudgedItem("i2", True, "INCORRECT", 90, True),
        JudgedItem("i3", False, "INCORRECT", 90, True),
        JudgedItem("i4", False, "CORRECT", 90, True),
    ]
    assert all(row.item_id in truth for row in a)
    metrics = cross_judge_metrics(a, b, judge_a="a", judge_b="b", n_resamples=50)
    assert metrics["n_common_items"] == 4
    assert metrics["error_correlation"]["both_wrong_count"] == 1
    assert metrics["error_correlation"]["both_wrong_rate"] == pytest.approx(0.25)
    # Errors: a = [F, T, T, F], b = [F, T, F, T] -> uncorrelated, so phi is 0.
    assert metrics["error_correlation"]["phi_coefficient"] == pytest.approx(0.0, abs=1e-9)
    assert metrics["error_correlation"]["both_wrong_vs_independence_binomial"]["expected"] == pytest.approx(
        4 * (2 / 4) * (2 / 4)
    )


def test_possible_key_error_requires_both_judges_to_be_confident() -> None:
    items = [item("i1", key="A"), item("i2", key="A")]
    responses = {"i1": response("i1", "A"), "i2": response("i2", "A")}
    truth = build_truth(items, responses)
    # Both judges say INCORRECT about a candidate that matches the key.
    a = [JudgedItem("i1", True, "INCORRECT", 95, True), JudgedItem("i2", True, "CORRECT", 60, True)]
    b = [JudgedItem("i1", True, "INCORRECT", 90, True), JudgedItem("i2", True, "CORRECT", 90, True)]
    flags = possible_key_errors(["a", "b"], {"a": a, "b": b})
    assert truth == {"i1": True, "i2": True}
    assert flags["n_flagged_possible_key_errors"] == 1
    assert flags["possible_key_errors"][0]["item_id"] == "i1"
    assert flags["rule"]["applied"] == "two_judge"


def test_single_judge_fallback_never_claims_a_key_error() -> None:
    a = [JudgedItem("i1", True, "INCORRECT", 99, True)]
    flags = possible_key_errors(["a"], {"a": a})
    assert flags["n_flagged_possible_key_errors"] == 0
    assert flags["n_flagged_single_judge"] == 1
    assert flags["rule"]["applied"] == "single_judge_fallback"


# --------------------------------------------------------------------------- #
# Whole-study aggregation
# --------------------------------------------------------------------------- #
def test_analyse_study_reports_counts_and_flags() -> None:
    items = [item(f"i{index}", key="A") for index in range(4)]
    responses = {"i0": response("i0", "A"), "i1": response("i1", "B"), "i2": response("i2", "A"), "i3": response("i3", "B")}
    judges = [judge_spec("j1"), judge_spec("j2")]
    verdicts = [
        *verdicts_for("j1", {"i0": ["CORRECT"] * 3, "i1": ["INCORRECT"] * 3, "i2": ["CORRECT"] * 3, "i3": ["CORRECT"] * 3}),
        *verdicts_for("j2", {"i0": ["INCORRECT"] * 3, "i1": ["INCORRECT"] * 3, "i2": ["INCORRECT"] * 3, "i3": ["CORRECT"] * 3}),
    ]
    metrics = analyse_study(
        items=items,
        responses=responses,
        verdicts=verdicts,
        judges=judges,
        repeats=3,
        n_resamples=50,
    )
    assert metrics["n_items"] == 4
    assert metrics["n_candidates_correct"] == 2
    assert metrics["n_candidates_incorrect"] == 2
    assert metrics["judges"]["j1"]["primary_repeat"]["accuracy"]["estimate"] == pytest.approx(0.75)
    assert metrics["judges"]["j2"]["primary_repeat"]["accuracy"]["estimate"] == pytest.approx(0.25)
    assert "j1|j2" in metrics["cross_judge"]
    assert metrics["pipeline_test_only"] is False
    assert metrics["benchmark_health"]["ceiling_per_judge"]["j1"]["ceiling_rate"] == pytest.approx(0.75)
    assert "possible_key_errors" in metrics


def test_analyse_study_marks_mock_runs() -> None:
    metrics = analyse_study(
        items=[item("i0")],
        responses={"i0": response("i0", "A")},
        verdicts=verdicts_for("j1", {"i0": ["CORRECT"] * 3}),
        judges=[judge_spec("j1")],
        repeats=3,
        n_resamples=20,
        pipeline_test_only=True,
    )
    assert metrics["pipeline_test_only"] is True
    assert metrics["status"] == "mock"


def test_accuracy_interval_is_present_and_ordered() -> None:
    data = rows([(True, "CORRECT", 80)] * 7 + [(False, "CORRECT", 80)] * 3)
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=50)
    low, high = metrics["accuracy"]["ci95_wilson"]
    assert low <= metrics["accuracy"]["estimate"] <= high
    assert not math.isnan(low)


def test_accuracy_two_ways_labels_both_variants() -> None:
    # 6 scored items of which 5 agree; 2 failed items.
    data = rows(
        [(True, "CORRECT", 90)] * 4
        + [(False, "INCORRECT", 90), (False, "CORRECT", 90)]
        + [(True, "", None)] * 2
    )
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=20)
    strict = metrics["accuracy"]
    counted = metrics["accuracy_counting_failures_as_wrong"]
    assert strict["denominator"] == 6 and strict["numerator"] == 5
    assert counted["denominator"] == 8 and counted["numerator"] == 5
    assert "failed items excluded" in strict["label"]
    assert "counted as a disagreement" in counted["label"]
    assert counted["estimate"] < strict["estimate"]


def test_failure_counts_per_type() -> None:
    data = rows(
        [(True, "CORRECT", 90)]
        + [(True, "", None)] * 2  # parse_error: empty_output by rows() helper
    )
    # rows() builds errors as "parse_error: empty_output" for failed verdicts.
    metrics = judge_metrics(data, judge_id="j", repeat=0, n_resamples=20)
    counts = metrics["failure_counts"]
    assert counts["none"] == 1
    assert counts["empty_reply"] == 2
    assert sum(counts.values()) == len(data)
    assert metrics["n_failures"] == 2
