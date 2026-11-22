"""Evaluation metrics for the medeval-lab toolkit.

Harness-independent metric functions and their definitions live in
:mod:`medeval_metrics.metrics`. Harness plugins live in
:mod:`medeval_metrics.plugins` (importing that module requires ``lm_eval``).
"""

from __future__ import annotations

from .metrics import (
    METRICS,
    ECEResult,
    MetricDefinition,
    RubricScore,
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
