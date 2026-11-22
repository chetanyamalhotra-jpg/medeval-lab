"""Tests for the criterion catalogue, rubric and literature traceability."""

from __future__ import annotations

from pathlib import Path

import pytest

from medeval_criteria import (
    CriterionError,
    check_traceability,
    load_catalogue,
    load_literature,
    load_rubric,
)
from medeval_metrics import METRICS

BAD_CATALOGUE = """
criteria_version: v0.1
criteria:
  - id: broken
    name: Broken
    definition: something
    scoring_scale:
      type: ordinal
      anchors:
        - {score: 0, label: a, description: b}
        - {score: 2, label: c, description: d}
    failure_examples: [x]
    applicable_task_types: [management]
    literature_ids: [lit_not_real]
    limitations: nope
    reference_standard: reference-label
"""


def test_catalogue_loads_with_expected_constructs() -> None:
    catalogue = load_catalogue()
    assert catalogue.version == "v0.1"
    expected = {
        "diagnostic_accuracy",
        "management_appropriateness",
        "critical_omission",
        "unsafe_recommendation",
        "guideline_evidence_concordance",
        "reasoning_soundness",
        "completeness",
        "calibration_appropriate_hedging",
    }
    assert {criterion.id for criterion in catalogue.criteria} == expected


def test_anchors_are_contiguous_and_described() -> None:
    for criterion in load_catalogue().criteria:
        scores = [anchor.score for anchor in criterion.anchors]
        assert scores == list(range(len(scores)))
        assert len(scores) >= 2
        for anchor in criterion.anchors:
            assert anchor.label and anchor.description


def test_safety_criteria_carry_the_required_caveat() -> None:
    safety = load_catalogue().safety_criteria()
    assert {criterion.id for criterion in safety} == {
        "critical_omission",
        "unsafe_recommendation",
    }
    for criterion in safety:
        assert "literature-derived, not clinician-validated" in criterion.limitations.lower()


def test_every_criterion_has_literature_and_task_types() -> None:
    for criterion in load_catalogue().criteria:
        assert criterion.literature_ids
        assert criterion.applicable_task_types
        assert criterion.reference_standard in {"reference-label", "judge-reliability"}


def test_rubric_matches_catalogue() -> None:
    rubric = load_rubric()
    catalogue = load_catalogue()
    assert set(rubric.weights) == set(catalogue.by_id())
    assert all(weight > 0 for weight in rubric.weights.values())
    assert rubric.criteria_version == catalogue.version


def test_non_contiguous_anchors_rejected(tmp_path: Path) -> None:
    path = tmp_path / "criteria.yaml"
    path.write_text(BAD_CATALOGUE, encoding="utf-8")
    with pytest.raises(CriterionError, match="contiguous"):
        load_catalogue(path)


def test_safety_criterion_without_caveat_rejected(tmp_path: Path) -> None:
    path = tmp_path / "criteria.yaml"
    text = BAD_CATALOGUE.replace("score: 2", "score: 1").replace(
        "reference_standard: reference-label", "safety_related: true\n    reference_standard: reference-label"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CriterionError, match="clinician-validated"):
        load_catalogue(path)


def test_unknown_reference_standard_rejected(tmp_path: Path) -> None:
    path = tmp_path / "criteria.yaml"
    text = BAD_CATALOGUE.replace("score: 2", "score: 1").replace(
        "reference_standard: reference-label", "reference_standard: clinical-validation"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CriterionError, match="reference_standard"):
        load_catalogue(path)


def test_literature_registry_loads() -> None:
    rows = load_literature()
    assert len(rows) >= 20
    for row in rows.values():
        assert row.title
        assert row.verified.lower() in {"yes", "no"}
        assert row.limitations


def test_real_traceability_has_no_errors() -> None:
    issues = check_traceability(load_catalogue(), METRICS, load_literature())
    errors = [issue for issue in issues if issue.severity == "error"]
    assert not errors, [issue.as_dict() for issue in errors]


def test_missing_literature_row_is_an_error(tmp_path: Path) -> None:
    catalogue = load_catalogue()
    rows = load_literature()
    rows.pop("lit_healthbench")
    issues = check_traceability(catalogue, METRICS, rows)
    errors = [issue for issue in issues if issue.severity == "error"]
    assert errors
    assert any("lit_healthbench" in issue.message for issue in errors)


def test_unverified_rows_are_warnings_only() -> None:
    issues = check_traceability(load_catalogue(), METRICS, load_literature())
    unverified = [issue for issue in issues if "not verified" in issue.message]
    assert unverified
    assert all(issue.severity == "warning" for issue in unverified)


def test_metric_without_literature_is_an_error() -> None:
    class FakeMetric:
        literature_ids: tuple[str, ...] = ()

    issues = check_traceability(load_catalogue(), {"fake": FakeMetric()}, load_literature())
    assert any(issue.severity == "error" and issue.subject == "fake" for issue in issues)
