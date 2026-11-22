"""Grading study machinery for Experiment 002.

A *study* is a reusable pipeline: sample MCQ items from a public benchmark, build
candidate answers from fixed templates, ask one or more judges for a verdict and
a confidence, and report agreement, calibration and error correlation with
interval estimates.

Experiment 002 in ``experiments/002_judge_families`` is one configuration of this
machinery. Nothing in this package knows about a specific model: judges arrive as
:class:`~medeval_study.judging.JudgeSpec` objects.
"""

from __future__ import annotations

from .analysis import (
    JudgedItem,
    analyse_study,
    auroc,
    brier_score,
    calibration_error,
    phi_coefficient,
    possible_key_errors,
)
from .datasets import DATASET_LICENCES, DatasetBundle, DatasetLicence, load_mcq_items, register_dataset
from .judging import JudgeRunner, JudgeSpec, MockJudgeTransport, ParsedVerdict, Verdict, parse_verdict
from .models import CandidateResponse, JudgeView, McqItem
from .report import write_reports
from .responses import TEMPLATES, build_candidate_response
from .runner import StudyConfig, StudyRun
from .sampling import SamplingPlan, select_items

__all__ = [
    "DATASET_LICENCES",
    "TEMPLATES",
    "CandidateResponse",
    "DatasetBundle",
    "DatasetLicence",
    "JudgeRunner",
    "JudgeSpec",
    "JudgeView",
    "JudgedItem",
    "McqItem",
    "MockJudgeTransport",
    "ParsedVerdict",
    "SamplingPlan",
    "StudyConfig",
    "StudyRun",
    "Verdict",
    "analyse_study",
    "auroc",
    "brier_score",
    "build_candidate_response",
    "calibration_error",
    "load_mcq_items",
    "parse_verdict",
    "phi_coefficient",
    "possible_key_errors",
    "register_dataset",
    "select_items",
    "write_reports",
]
