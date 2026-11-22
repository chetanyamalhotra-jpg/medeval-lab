"""Experiment 003 amendment 1: adaptive screening and the staged controller.

These tests pin the pre-registered rules from the 2026-11-19 deviations-log
entry: batches of 50, stop on target_met / exhausted / cap_reached /
stopped_by_user with the reason recorded, and one confirmed stage at a time
(screening -> pool -> pilot -> gate -> remaining) where the pilot gate can end
the run before any remaining item is judged.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from medeval_core.client import LLMClient
from medeval_core.config import Settings
from medeval_study import runner as runner_module
from medeval_study.datasets import DatasetBundle, DatasetLicence
from medeval_study.exp003 import (
    N_CONTROL,
    N_HARD,
    SCREENING_BATCH,
    SCREENING_CAP,
    Exp003Study,
    MockLetterTransport,
    run_screening_adaptive,
)
from medeval_study.models import McqItem
from medeval_study.runner import StudyConfig

OPTIONS = {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"}

JUDGES = (
    {
        "judge_id": "judge_one", "connection_id": "conn-1", "family_label": "mock family A",
        "backend": "mock", "base_url": "http://127.0.0.1:9", "model": "mock-a",
        "temperature": 0.3, "repeats": 1, "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0,
    },
    {
        "judge_id": "judge_two", "connection_id": "conn-2", "family_label": "mock family B",
        "backend": "mock", "base_url": "http://127.0.0.1:9", "model": "mock-b",
        "temperature": 0.3, "repeats": 1, "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0,
    },
)

SCREENING = {
    "connection_id": "conn-screen", "family_label": "mock screener family",
    "base_url": "http://127.0.0.1:9", "model": "mock-screen", "max_tokens": 8,
    "call_cap": 500, "usd_cap": 5.0,
}


def make_items(count: int) -> tuple[McqItem, ...]:
    return tuple(
        McqItem(
            item_id=f"syn-{index:04d}",
            question=f"Synthetic staged question {index} with enough length for the loader filter.",
            options=dict(OPTIONS),
            key_letter="ABCD"[index % 4],
            key_text=OPTIONS["ABCD"[index % 4]],
            dataset="synthetic",
            split="dev",
            row_index=index,
        )
        for index in range(count)
    )


@pytest.fixture()
def bundle(monkeypatch: pytest.MonkeyPatch):
    items = make_items(160)
    licence = DatasetLicence(
        key="synthetic", hf_repo="none/synthetic", licence="synthetic-test",
        licence_evidence="fixture", redistribute=True, attribution="fixture",
    )
    payload = DatasetBundle(
        items=items, licence=licence, dataset="synthetic", split="dev",
        strategy="test", source_detail="fixture", n_raw_rows=len(items), n_dropped=0,
    )
    monkeypatch.setattr(runner_module, "dataset_cache_status", lambda *a, **k: payload)
    return payload


def screen(transport: MockLetterTransport, out_path: Path, items, **kwargs) -> dict:
    client = LLMClient(transport, Settings(max_tokens=8, temperature=0.0, seed=3))
    return run_screening_adaptive(
        items=items, client=client, out_path=out_path, seed=20261118, **kwargs
    )


# --------------------------------------------------------------------------- #
# Adaptive screening stop rules
# --------------------------------------------------------------------------- #
def test_screening_stops_when_the_hard_target_is_met(tmp_path: Path) -> None:
    items = make_items(160)
    summary = screen(MockLetterTransport(accuracy=0.0, seed=1), tmp_path / "s.jsonl", items)
    assert summary["stop_reason"] == "target_met"
    assert summary["n_wrong"] >= N_HARD
    assert summary["n_screened"] == 100, "two batches of 50 reach the target exactly"
    assert summary["batches"] == 2
    assert summary["batch_size"] == SCREENING_BATCH == 50


def test_screening_stops_when_items_are_exhausted(tmp_path: Path) -> None:
    items = make_items(60)
    summary = screen(MockLetterTransport(accuracy=1.0, seed=1), tmp_path / "s.jsonl", items)
    assert summary["stop_reason"] == "exhausted"
    assert summary["n_screened"] == 60
    assert summary["n_wrong"] == 0


def test_screening_stops_at_the_cap(tmp_path: Path) -> None:
    items = make_items(160)
    summary = screen(
        MockLetterTransport(accuracy=1.0, seed=1), tmp_path / "s.jsonl", items, cap=20
    )
    assert summary["stop_reason"] == "cap_reached"
    assert summary["n_screened"] == 20
    assert summary["cap"] == 20


def test_screening_records_stop_on_request(tmp_path: Path) -> None:
    class _Flag:
        def is_set(self) -> bool:
            return True

    items = make_items(160)
    summary = screen(
        MockLetterTransport(accuracy=0.5, seed=1), tmp_path / "s.jsonl", items, stop_flag=_Flag()
    )
    assert summary["stop_reason"] == "stopped_by_user"
    assert summary["n_screened"] == 0


def test_screening_is_resumable_and_writes_ids_and_flags_only(tmp_path: Path) -> None:
    items = make_items(160)
    path = tmp_path / "s.jsonl"
    first = screen(MockLetterTransport(accuracy=0.0, seed=1), path, items, cap=50)
    assert first["stop_reason"] == "cap_reached" and first["n_screened"] == 50
    text = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.strip().splitlines()]
    assert set(rows[0]) == {"item_id", "correct", "model"}
    second = screen(MockLetterTransport(accuracy=0.0, seed=1), path, items, target_hard=100)
    assert second["n_screened"] >= 100
    assert {row["item_id"] for row in rows} <= {
        json.loads(line)["item_id"] for line in path.read_text(encoding="utf-8").strip().splitlines()
    }


def test_screening_cap_constant_is_the_registered_one() -> None:
    assert SCREENING_CAP == 1000 and N_HARD == 100 and N_CONTROL == 40


# --------------------------------------------------------------------------- #
# Staged controller
# --------------------------------------------------------------------------- #
def _config(stage: str, *, pilot_min: int = 5, dry_run: bool = True) -> StudyConfig:
    return StudyConfig(
        experiment="003_frontier_judges", dataset="synthetic", split="dev", n_items=140,
        seed=20261118, repeats=1, dry_run=dry_run, judges=(),
        connection_judges=JUDGES, screening_binding=None if dry_run else dict(SCREENING),
        pilot_items=40, pilot_min_disagreements=pilot_min, screening_size=600,
        run_id="staged-test", resume=True, stage=stage,
    )


def _study(config: StudyConfig, root: Path) -> Exp003Study:
    return Exp003Study(config, Settings(seed=20261118, max_tokens=32), run_root=root)


def _state(root: Path) -> dict:
    return json.loads((root / "staged-test" / "run.json").read_text(encoding="utf-8"))


def test_staged_run_advances_one_confirmed_stage_at_a_time(bundle, tmp_path: Path) -> None:
    screening = _study(_config("screening"), tmp_path).execute()
    assert screening["status"] == "awaiting_confirmation"
    assert screening["next_stage"] == "pool"
    state = _state(tmp_path)
    assert state["stages_completed"] == ["screening"]
    assert state["screening"]["stop_reason"] in {"target_met", "exhausted", "cap_reached"}
    assert state["screening"]["mock"] is True
    assert not (tmp_path / "staged-test" / "metrics.json").exists(), "screening alone produces no metrics"

    pool = _study(_config("pool"), tmp_path).execute()
    assert pool["status"] == "awaiting_confirmation" and pool["next_stage"] == "pilot"
    state = _state(tmp_path)
    assert state["stages_completed"] == ["screening", "pool"]
    plan = state["pool_plan"]
    assert plan["n_hard"] == min(N_HARD, state["screening"]["n_wrong"])
    assert plan["n_control"] == N_CONTROL

    pilot = _study(_config("pilot"), tmp_path).execute()
    state = _state(tmp_path)
    assert pilot["stage_plan"], "the pilot returns the stage plan for the UI"
    assert "pilot" in state["stages_completed"]
    gate = state["pilot_gate"]
    assert gate["n_disagreements"] >= 5
    if gate["passed"]:
        assert pilot["status"] == "awaiting_confirmation"
        remaining = _study(_config("remaining"), tmp_path).execute()
        assert remaining["status"] == "completed"
        state = _state(tmp_path)
        assert state["stages_completed"] == ["screening", "pool", "pilot", "remaining"]
        assert (tmp_path / "staged-test" / "review_queue.csv").is_file()
    else:
        assert pilot["status"] == "pilot_stopped"
        assert _study(_config("remaining"), tmp_path).execute()["status"] == "pilot_stopped"


def test_gate_failure_blocks_the_remaining_stage(bundle, tmp_path: Path) -> None:
    # The floor is a design field, so it is the same for every stage; an
    # unreachable floor proves the stop path without touching the lock.
    _study(_config("screening", pilot_min=10_000), tmp_path).execute()
    _study(_config("pool", pilot_min=10_000), tmp_path).execute()
    pilot = _study(_config("pilot", pilot_min=10_000), tmp_path).execute()
    assert pilot["status"] == "pilot_stopped"
    state = _state(tmp_path)
    assert state["pilot_gate"]["passed"] is False
    assert "STOP" in state["pilot_gate"]["decision"]
    assert (tmp_path / "staged-test" / "report.md").is_file(), "the partial report is still written"
    blocked = next(row for row in pilot["stage_plan"] if row["name"] == "remaining")
    assert blocked["status"] == "blocked"
    assert blocked["needs_confirmation"] is False


def test_pilot_does_not_run_the_remaining_items(bundle, tmp_path: Path) -> None:
    _study(_config("screening"), tmp_path).execute()
    _study(_config("pool"), tmp_path).execute()
    _study(_config("pilot"), tmp_path).execute()
    rows = [
        json.loads(line)
        for line in (tmp_path / "staged-test" / "judgements.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len({row["item_id"] for row in rows}) == 40, "only the pilot items were judged"
    assert _state(tmp_path)["pilot_gate"]["pilot_size"] == 40


def test_stage_plan_costs_match_the_registered_design(bundle, tmp_path: Path) -> None:
    _study(_config("screening"), tmp_path).execute()
    stages = _study(_config("pool"), tmp_path).execute()["stage_plan"]
    by_name = {row["name"]: row for row in stages}
    assert by_name["pilot"]["worst_case_calls"] == 40 * len(JUDGES)
    sample_items = N_HARD + N_CONTROL
    assert by_name["remaining"]["worst_case_calls"] <= (sample_items - 40) * len(JUDGES)
    assert by_name["screening"]["worst_case_calls"] > 0
    assert all(row["worst_case_usd"] is None for row in stages), "prices were not set"


def test_stage_plan_uses_prices_when_connections_carry_them(bundle, tmp_path: Path) -> None:
    judges = tuple({**binding, "price_in_per_mtok": 0.5, "price_out_per_mtok": 2.0} for binding in JUDGES)
    config = StudyConfig(
        experiment="003_frontier_judges", dataset="synthetic", split="dev", n_items=140,
        seed=20261118, repeats=1, dry_run=True, judges=(), connection_judges=judges,
        pilot_items=40, pilot_min_disagreements=5, screening_size=600,
        run_id="priced", resume=False, stage="screening",
    )
    study = _study(config, tmp_path)
    plan = study.stage_plan()
    priced = [row for row in plan if row["worst_case_usd"] is not None]
    assert priced, "prices on the connections must flow into the plan"
    assert all(row["worst_case_usd"] >= 0 for row in priced)


def test_screening_path_is_local_in_dry_run_and_committed_for_real_runs(tmp_path: Path) -> None:
    dry = _study(_config("screening"), tmp_path)
    assert dry.screening_path() == dry.run_dir / "screening.jsonl"
    real = _study(_config("screening", dry_run=False), tmp_path)
    assert real.screening_path().name == "screening.jsonl"
    assert "experiment_003" in str(real.screening_path())
    assert real.screening_path() != real.run_dir / "screening.jsonl"


# --------------------------------------------------------------------------- #
# Role validation (blocking, not warning)
# --------------------------------------------------------------------------- #
def test_screening_model_cannot_also_be_a_judge(bundle, tmp_path: Path) -> None:
    config = replace(
        _config("screening", dry_run=False),
        screening_binding={**dict(SCREENING), "connection_id": "conn-1"},  # judge one's connection
    )
    result = _study(config, tmp_path).execute()
    assert result["status"] == "refused"
    assert "screening model is also a judge" in result["refused_reason"]


def test_screening_family_must_differ_from_every_judge(bundle, tmp_path: Path) -> None:
    config = replace(
        _config("screening", dry_run=False),
        screening_binding={**dict(SCREENING), "family_label": "Mock Family A"},  # judge one's family
    )
    result = _study(config, tmp_path).execute()
    assert result["status"] == "refused"
    assert "different family" in result["refused_reason"]


def test_exp003_requires_judge_connections(bundle, tmp_path: Path) -> None:
    config = replace(_config("screening", dry_run=False), connection_judges=())
    result = _study(config, tmp_path).execute()
    assert result["status"] == "refused"
    assert "judge connections" in result["refused_reason"]


def test_stage_is_not_part_of_the_config_lock(bundle, tmp_path: Path) -> None:
    """Advancing a stage must not trip the lock that protects the design."""
    _study(_config("screening"), tmp_path).execute()
    result = _study(_config("pool"), tmp_path).execute()
    assert result["status"] == "awaiting_confirmation", result.get("refused_reason")
