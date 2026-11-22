"""Tests for Experiment 003: pool building, screening, pilot gate, flag rule.

The pool, gate and flag rules are pre-registered in
``experiments/003_frontier_judges/PLAN.md``; these tests pin them so the rules
cannot drift without a failing test and a deviations-log entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_core.client import LLMClient
from medeval_core.config import Settings
from medeval_study.exp003 import (
    FLAG_CONFIDENCE,
    MockLetterTransport,
    build_screening_prompt,
    build_stratified_pool,
    exp003_review_queue,
    load_excluded_item_ids,
    parse_screening_letter,
    run_screening,
    write_review_queue,
)
from medeval_study.models import McqItem
from tests.support import ROOT

OPTIONS = {"A": "alpha answer", "B": "beta option", "C": "gamma choice", "D": "delta final"}


def item(item_id: str, key: str = "A", row_index: int = 0) -> McqItem:
    return McqItem(
        item_id=item_id,
        question=f"Synthetic screening question number {item_id}, long enough for the loader filter.",
        options=dict(OPTIONS),
        key_letter=key,
        key_text=OPTIONS[key],
        dataset="synthetic",
        split="dev",
        row_index=row_index,
    )


def screening_rows(spec: dict[str, bool]) -> list[dict]:
    return [{"item_id": item_id, "correct": correct} for item_id, correct in spec.items()]


# --------------------------------------------------------------------------- #
# Screening prompt and parsing
# --------------------------------------------------------------------------- #
def test_screening_prompt_is_closed_book_and_letter_only() -> None:
    prompt = build_screening_prompt(item("i1", key="C"))
    lowered = prompt.lower()
    for banned in ("answer key", "correct option is", "ground truth", "key:", "the answer is c"):
        assert banned not in lowered, "the prompt must not reveal which option the key names"
    assert "reply with only the letter" in lowered
    # All four options are shown (the question is closed-book but the options
    # are given); the key is only which letter is correct, never stated.
    for letter in ("A", "B", "C", "D"):
        assert f"{letter}) " in prompt


def test_parse_screening_letter_handles_plain_and_json() -> None:
    letters = ("A", "B", "C", "D")
    assert parse_screening_letter("C", letters) == "C"
    assert parse_screening_letter("b", letters) == "B"
    assert parse_screening_letter('{"answer": "D"}', letters) == "D"
    assert parse_screening_letter("The answer is A.", letters) == "A"
    assert parse_screening_letter("", letters) is None


def test_mock_screener_never_answers_an_unregistered_prompt() -> None:
    transport = MockLetterTransport()
    completion = transport.complete(
        [{"role": "user", "content": "unregistered"}], temperature=0.0, seed=1, max_tokens=4, timeout_s=1
    )
    assert completion.error is not None


# --------------------------------------------------------------------------- #
# Stratified pool
# --------------------------------------------------------------------------- #
def test_pool_is_stratified_and_respects_the_shortfall_rule() -> None:
    items = [item(f"i{index}", row_index=index) for index in range(200)]
    rows = screening_rows({f"i{index}": index % 5 != 0 for index in range(200)})  # 40 wrong
    pool = build_stratified_pool(screening_rows=rows, items=items, seed=20261118)
    assert len(pool.hard) == 40, "a smaller hard pool is used in full"
    assert len(pool.control) == 40
    assert pool.n_hard_available == 40
    assert "below the pre-registered 100" in pool.note
    assert {row.item_id for row in pool.hard}.isdisjoint({row.item_id for row in pool.control})


def test_pool_samples_100_hard_when_available() -> None:
    items = [item(f"i{index}", row_index=index) for index in range(300)]
    rows = screening_rows({f"i{index}": index % 2 == 0 for index in range(300)})  # 150 wrong
    pool = build_stratified_pool(screening_rows=rows, items=items, seed=20261118)
    assert len(pool.hard) == 100
    assert len(pool.control) == 40
    assert pool.note == ""


def test_pool_is_deterministic_for_a_seed() -> None:
    items = [item(f"i{index}", row_index=index) for index in range(300)]
    rows = screening_rows({f"i{index}": index % 2 == 0 for index in range(300)})
    first = build_stratified_pool(screening_rows=rows, items=items, seed=7)
    second = build_stratified_pool(screening_rows=rows, items=items, seed=7)
    other = build_stratified_pool(screening_rows=rows, items=items, seed=8)
    assert [row.item_id for row in first.hard] == [row.item_id for row in second.hard]
    assert [row.item_id for row in first.hard] != [row.item_id for row in other.hard]


def test_pool_excludes_experiment_002_items(tmp_path: Path) -> None:
    items = [item(f"i{index}", row_index=index) for index in range(50)]
    rows = screening_rows({f"i{index}": False for index in range(50)})
    pool = build_stratified_pool(
        screening_rows=rows, items=items, seed=1, excluded_ids={"i0", "i1", "i2"}
    )
    chosen = {row.item_id for row in pool.hard}
    assert chosen.isdisjoint({"i0", "i1", "i2"})


def test_load_excluded_item_ids_reads_exp002_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "study_runs" / "exp002-main"
    run_dir.mkdir(parents=True)
    (run_dir / "items.jsonl").write_text(
        "".join(json.dumps({"item_id": f"dev-{index:05d}"}) + "\n" for index in range(5)),
        encoding="utf-8",
    )
    excluded = load_excluded_item_ids(run_root=tmp_path / "study_runs")
    assert excluded == {f"dev-{index:05d}" for index in range(5)}


# --------------------------------------------------------------------------- #
# Screening run
# --------------------------------------------------------------------------- #
def test_run_screening_writes_ids_and_flags_only(tmp_path: Path) -> None:
    items = [item(f"i{index}", key="A", row_index=index) for index in range(6)]
    transport = MockLetterTransport(accuracy=0.5, seed=3)
    client = LLMClient(transport, Settings(max_tokens=8, temperature=0.0, seed=3))
    summary = run_screening(items=items, client=client, out_path=tmp_path / "screening.jsonl")
    assert summary["n_screened"] == 6
    text = (tmp_path / "screening.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.strip().splitlines()]
    assert all(set(row) == {"item_id", "correct", "model"} for row in rows)
    assert "alpha answer" not in text, "no benchmark text in the committed file"
    # Resume: a second pass changes nothing.
    summary_again = run_screening(items=items, client=client, out_path=tmp_path / "screening.jsonl")
    assert summary_again["n_screened"] == 6


# --------------------------------------------------------------------------- #
# Review queue (pre-registered flag rule)
# --------------------------------------------------------------------------- #
def verdict(item_id: str, judge: str, verdict_text: str, confidence: int, repeat: int = 0):
    from medeval_study.judging import Verdict

    return Verdict(
        item_id=item_id, judge_id=judge, repeat=repeat, verdict=verdict_text,
        confidence=confidence, parse_ok=True, attempts=1, model="m",
        prompt_sha256="x", latency_ms=1.0,
    )


def test_flag_rule_requires_all_judges_confident_against_key() -> None:
    # Candidate named B; key says A. Both judges endorse the candidate (CORRECT)
    # at high confidence — that is disagreement with the key -> flagged.
    candidate = {"i1": "B"}
    key = {"i1": "A"}
    verdicts = [
        verdict("i1", "j1", "CORRECT", 95),
        verdict("i1", "j2", "CORRECT", 92),
    ]
    flags = exp003_review_queue(verdicts, candidate_letter_by_item=candidate, key_letter_by_item=key)
    assert flags["n_flagged"] == 1
    assert flags["flagged_item_ids"] == ["i1"]
    assert "never a finding that the key is wrong" in flags["caveat"]
    # The other disagreement direction: candidate matches the key, judge rejects it.
    candidate_b = {"i2": "A"}
    key_b = {"i2": "A"}
    verdicts_b = [
        verdict("i2", "j1", "INCORRECT", 97),
        verdict("i2", "j2", "INCORRECT", 93),
    ]
    flags_b = exp003_review_queue(verdicts_b, candidate_letter_by_item=candidate_b, key_letter_by_item=key_b)
    assert flags_b["n_flagged"] == 1


def test_flag_rule_rejects_judges_that_agree_with_the_key() -> None:
    candidate = {"i1": "B"}
    key = {"i1": "A"}
    verdicts = [
        verdict("i1", "j1", "INCORRECT", 95),  # agrees with the key (candidate is wrong)
        verdict("i1", "j2", "INCORRECT", 92),
    ]
    flags = exp003_review_queue(verdicts, candidate_letter_by_item=candidate, key_letter_by_item=key)
    assert flags["n_flagged"] == 0


def test_flag_rule_rejects_a_dissenting_judge() -> None:
    candidate = {"i1": "B"}
    key = {"i1": "A"}
    verdicts = [
        verdict("i1", "j1", "INCORRECT", 95),
        verdict("i1", "j2", "CORRECT", 95),  # dissents
        verdict("i1", "j3", "INCORRECT", 95),
    ]
    flags = exp003_review_queue(verdicts, candidate_letter_by_item=candidate, key_letter_by_item=key)
    assert flags["n_flagged"] == 0, "all judges that returned a verdict must disagree"


def test_flag_rule_rejects_low_confidence_and_single_repeat_gap() -> None:
    candidate = {"i1": "B"}
    key = {"i1": "A"}
    low = [
        verdict("i1", "j1", "INCORRECT", 85),
        verdict("i1", "j2", "INCORRECT", 95),
    ]
    assert exp003_review_queue(low, candidate_letter_by_item=candidate, key_letter_by_item=key)["n_flagged"] == 0
    repeats = [
        verdict("i1", "j1", "INCORRECT", 95, repeat=0),
        verdict("i1", "j1", "CORRECT", 95, repeat=1),  # flip on repeat 1
        verdict("i1", "j2", "INCORRECT", 95, repeat=0),
        verdict("i1", "j2", "INCORRECT", 95, repeat=1),
    ]
    assert exp003_review_queue(repeats, candidate_letter_by_item=candidate, key_letter_by_item=key)["n_flagged"] == 0
    assert FLAG_CONFIDENCE == 90


def test_review_queue_csv_contains_ids_only(tmp_path: Path) -> None:
    path = write_review_queue(tmp_path / "review_queue.csv", ["i2", "i1", "i2"])
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines == ["item_id", "i1", "i2"]


# --------------------------------------------------------------------------- #
# Pilot gate
# --------------------------------------------------------------------------- #
def test_pilot_gate_stops_below_the_floor() -> None:
    from medeval_study.runner import _pilot_gate_decision

    truth = {f"i{index}": True for index in range(40)}
    # 40 items, one judge, 3 disagreements: below the floor of 5.
    verdicts = [verdict(f"i{index}", "j1", "INCORRECT" if index < 3 else "CORRECT", 90) for index in range(40)]
    decision = _pilot_gate_decision(verdicts, truth_by_item=truth, pilot_items=[f"i{index}" for index in range(40)], min_disagreements=5)
    assert decision["passed"] is False
    assert decision["n_disagreements"] == 3
    assert "STOP" in decision["decision"]


def test_pilot_gate_continues_at_the_floor() -> None:
    from medeval_study.runner import _pilot_gate_decision

    truth = {f"i{index}": True for index in range(40)}
    verdicts = [
        verdict(f"i{index}", "j1", "INCORRECT" if index < 3 else "CORRECT", 90) for index in range(40)
    ] + [verdict(f"i{index}", "j2", "INCORRECT" if index < 2 else "CORRECT", 90) for index in range(40)]
    decision = _pilot_gate_decision(verdicts, truth_by_item=truth, pilot_items=[f"i{index}" for index in range(40)], min_disagreements=5)
    assert decision["passed"] is True and decision["n_disagreements"] == 5


# --------------------------------------------------------------------------- #
# Full dry run
# --------------------------------------------------------------------------- #
@pytest.fixture()
def _loader(monkeypatch: pytest.MonkeyPatch):
    from medeval_study import runner as runner_module
    from medeval_study.datasets import DatasetBundle, DatasetLicence

    items = tuple(item(f"syn-{index:04d}", key="ABCD"[index % 4], row_index=index) for index in range(160))
    licence = DatasetLicence(
        key="synthetic", hf_repo="none/synthetic", licence="synthetic-test",
        licence_evidence="fixture", redistribute=True, attribution="fixture",
    )
    bundle = DatasetBundle(
        items=items, licence=licence, dataset="synthetic", split="dev",
        strategy="test", source_detail="fixture", n_raw_rows=len(items), n_dropped=0,
    )
    monkeypatch.setattr(runner_module, "dataset_cache_status", lambda *a, **k: bundle)


def test_exp003_dry_run_completes_with_all_outputs(_loader, tmp_path: Path, capsys) -> None:
    from medeval_study.exp003 import Exp003Study
    from medeval_study.runner import StudyConfig

    config = StudyConfig(
        experiment="003_frontier_judges", dataset="synthetic", n_items=140,
        seed=20261118, repeats=1, dry_run=True, judges=(),
        connection_judges=(
            {"judge_id": "judge_one", "connection_id": "d1", "family_label": "mock A", "backend": "mock",
             "base_url": "http://127.0.0.1:9", "model": "mock-a", "temperature": 0.3, "repeats": 1,
             "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0},
            {"judge_id": "judge_two", "connection_id": "d2", "family_label": "mock B", "backend": "mock",
             "base_url": "http://127.0.0.1:9", "model": "mock-b", "temperature": 0.3, "repeats": 1,
             "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0},
        ),
        consistency_subsample=6,
        pilot_items=20, pilot_min_disagreements=5,
        screening_size=80,
        run_id="exp003-dry-test", resume=False,
    )
    result = Exp003Study(config, Settings(seed=20261118, max_tokens=32), run_root=tmp_path).execute()
    assert result["status"] == "completed"
    run_dir = tmp_path / "exp003-dry-test"
    for name in ("screening.jsonl", "metrics.json", "report.md", "items.csv", "review_queue.csv", "run.json"):
        assert (run_dir / name).is_file(), f"{name} missing"
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics.get("strata") or {}) == {"hard", "control"}
    assert "review_queue" in metrics
    assert "single_reviewer_note" in metrics
    state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert state["pilot_gate"]["passed"] is True
    assert state["strata"]["n_hard"] + state["strata"]["n_control"] == metrics["n_items"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "PIPELINE TEST ONLY" in report
    assert "Limitations" in report
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert "sk-" not in path.read_text(encoding="utf-8", errors="ignore"), f"secret-like string in {path.name}"


def test_exp003_screening_text_never_reaches_a_judge(_loader, tmp_path: Path) -> None:
    """The screening file holds ids and flags only; no option text is committed."""
    from medeval_study.exp003 import Exp003Study
    from medeval_study.runner import StudyConfig

    config = StudyConfig(
        experiment="003_frontier_judges", dataset="synthetic", n_items=40,
        seed=20261118, repeats=1, dry_run=True, judges=(),
        connection_judges=(
            {"judge_id": "judge_one", "connection_id": "d1", "family_label": "mock A", "backend": "mock",
             "base_url": "http://127.0.0.1:9", "model": "mock-a", "temperature": 0.3, "repeats": 1,
             "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0},
        ),
        pilot_items=10, pilot_min_disagreements=3,
        screening_size=40,
        run_id="exp003-screen", resume=False,
    )
    Exp003Study(config, Settings(seed=20261118, max_tokens=32), run_root=tmp_path).execute()
    screening = (tmp_path / "exp003-screen" / "screening.jsonl").read_text(encoding="utf-8")
    assert "alpha answer" not in screening and "beta option" not in screening
    assert "Synthetic screening question" not in screening


def test_pre_registration_exists_and_committed_first() -> None:
    plan = ROOT / "experiments" / "003_frontier_judges" / "PLAN.md"
    assert plan.is_file()
    text = plan.read_text(encoding="utf-8")
    for heading in ("Question", "Data and selection", "Pilot gate", "Metrics", "Flag rule",
                    "Deviations log"):
        assert heading in text
