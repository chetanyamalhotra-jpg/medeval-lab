"""Checks on the Experiment 001 data wiring (not on its statistical results)."""

from __future__ import annotations

import importlib.util
import json
import sys

import pytest

from tests.support import ROOT

RUN_PATH = ROOT / "experiments" / "001_judge_reliability" / "run.py"


@pytest.fixture(scope="module")
def experiment_module():
    spec = importlib.util.spec_from_file_location("exp001_run", RUN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses needs the module registered
    spec.loader.exec_module(module)
    return module


def test_cases_load_and_match_items(experiment_module) -> None:
    cases = experiment_module.load_cases()
    assert len(cases) == 50
    items = {item["id"] for item in json.loads((ROOT / "data" / "items" / "seed_v0.1.json").read_text(encoding="utf-8"))}
    # The ten former hidden rows are sealed outside version control in v0.2.
    # Historical v0.1 results remain immutable; a fresh v0.1 rerun must not be
    # treated as a replacement experiment without those private rows.
    assert len(items) == 40
    assert sum(case.case.item_id in items for case in cases) == 40


def test_target_side_is_balanced_and_deterministic(experiment_module) -> None:
    first = experiment_module.load_cases()
    second = experiment_module.load_cases()
    targets = [case.case.target for case in first]
    assert targets == [case.case.target for case in second], "assignment must be deterministic"
    assert set(targets) == {"A", "B"}, "the planted error must appear on both sides"
    # Neither side should carry more than 70% of the targets, otherwise the
    # position-bias probe loses its control.
    share_a = targets.count("A") / len(targets)
    assert 0.3 <= share_a <= 0.7


def test_target_points_at_the_planted_error(experiment_module) -> None:
    specs = json.loads(
        (ROOT / "experiments" / "001_judge_reliability" / "data" / "case_specs_v0.1.json").read_text(encoding="utf-8")
    )
    by_id = {spec["case_id"]: spec for spec in specs}
    for case in experiment_module.load_cases():
        spec = by_id[case.case.case_id]
        chosen = case.case.response(case.case.target or "B")
        assert chosen == spec["bad_response"]
        other = case.case.response("B" if case.case.target == "A" else "A")
        assert other == spec["good_response"]


def test_error_types_match_specs(experiment_module) -> None:
    specs = json.loads(
        (ROOT / "experiments" / "001_judge_reliability" / "data" / "case_specs_v0.1.json").read_text(encoding="utf-8")
    )
    expected = {spec["case_id"]: spec["error_type"] for spec in specs}
    for case in experiment_module.load_cases():
        assert case.error_type == expected[case.case.case_id]


def test_plan_exists_and_is_pre_registered() -> None:
    plan = ROOT / "experiments" / "001_judge_reliability" / "PLAN.md"
    text = plan.read_text(encoding="utf-8")
    for required in ("Hypotheses", "Stopping rules", "What the results can and cannot support"):
        assert required in text
