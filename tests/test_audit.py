"""Tests for the benchmark audit: duplicates, answer keys, contamination, report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_audit import (
    AuditRecord,
    answer_in_stem,
    audit_split,
    check_answer_key,
    detect_index_base,
    find_duplicates,
    jaccard,
    materialize_jsonl,
    normalise_text,
    shingles,
    shortcut_baselines,
)
from medeval_audit.loaders import DATASETS, LoadedSplit
from tests.support import make_item as _unused_make_item  # noqa: F401  (keeps import style consistent)


def record(
    item_id: str,
    question: str,
    options: tuple[str, ...] = ("alpha", "beta", "gamma", "delta"),
    gold_index: int | None = 0,
    raw: dict | None = None,
) -> AuditRecord:
    gold_text = options[gold_index] if gold_index is not None and 0 <= gold_index < len(options) else None
    return AuditRecord(
        dataset="test",
        row_index=0,
        item_id=item_id,
        question=question,
        options=options,
        option_labels=("A", "B", "C", "D"),
        gold_index=gold_index,
        gold_text=gold_text,
        raw=raw or {},
    )


def test_normalise_text_strips_punctuation_and_case() -> None:
    assert normalise_text("  What IS the Dose?  ") == "what is the dose"


def test_shingles_are_deterministic_and_drop_stopwords() -> None:
    first = shingles("Which of the following is the best initial management")
    second = shingles("Which of the following is the best initial management")
    assert first == second
    assert all("the" not in s.split() for s in first)


def test_jaccard_identical_sets_is_one() -> None:
    assert jaccard(shingles("severe chest pain radiating to the arm"), shingles("severe chest pain radiating to the arm")) == 1.0


def test_jaccard_empty_is_zero() -> None:
    assert jaccard(frozenset(), shingles("anything at all here")) == 0.0


def test_exact_duplicates_grouped() -> None:
    records = [
        record("a", "A patient with severe chest pain and ST elevation on ECG needs what?"),
        record("b", "A patient with severe chest pain and ST elevation on ECG needs what?"),
        record("c", "A completely different question about renal dosing of antibiotics?"),
    ]
    report = find_duplicates(records)
    assert report.n_exact_groups == 1
    assert report.n_near_groups == 0
    assert report.flagged_item_ids == ["a", "b"]


def test_near_duplicates_detected() -> None:
    base = "A 60 year old man presents with severe chest pain and ST elevation in leads two three and four"
    records = [
        record("a", base),
        record("b", "A 60 year old man presents with severe chest pain and ST elevation in leads two three and four today"),
        record("c", "A young woman presents with a rash and joint pain after a sore throat last week"),
    ]
    report = find_duplicates(records, threshold=0.8)
    assert report.n_near_groups == 1
    assert set(report.flagged_item_ids) == {"a", "b"}


def test_option_set_duplicates_flagged() -> None:
    records = [
        record("a", "Question one about the first topic here", ("x1", "x2", "x3", "x4")),
        record("b", "Question two about a different topic entirely", ("x1", "x2", "x3", "x4")),
    ]
    report = find_duplicates(records)
    assert report.n_option_set_groups == 1
    kinds = {group.kind for group in report.groups}
    assert "option_set" in kinds


def test_duplicate_report_does_not_mutate_input() -> None:
    records = [record("a", "the same question text repeated twice"), record("b", "the same question text repeated twice")]
    before = [(r.item_id, r.question) for r in records]
    find_duplicates(records)
    assert [(r.item_id, r.question) for r in records] == before


def test_detect_index_base_zero_based() -> None:
    base, evidence = detect_index_base([0, 1, 2, 3, 0], 4)
    assert base == "zero_based"
    assert "0 present" in evidence


def test_detect_index_base_one_based() -> None:
    base, _ = detect_index_base([1, 2, 3, 4], 4)
    assert base == "one_based"


def test_detect_index_base_invalid() -> None:
    base, _ = detect_index_base([0, 5], 4)
    assert base == "invalid"


def test_answer_key_counts_missing_and_out_of_range() -> None:
    records = [
        record("ok", "a valid question about aspirin and chest pain", gold_index=1),
        record("missing", "a question with no key recorded at all", gold_index=None),
        record("oor", "a question with an impossible key index", gold_index=7),
    ]
    report = check_answer_key(records)
    assert report.n_missing_gold == 1
    assert report.n_out_of_range == 1
    assert "missing" in report.flagged_item_ids
    assert "oor" in report.flagged_item_ids


def test_answer_key_detects_duplicate_options() -> None:
    records = [record("dup", "a question whose options repeat themselves", ("same", "same", "other", "third"))]
    report = check_answer_key(records)
    assert report.n_duplicate_options == 1


def test_answer_key_detects_conflicting_key_columns() -> None:
    records = [
        record(
            "conflict",
            "a question where two key columns disagree badly",
            gold_index=0,
            raw={"cop": 0, "cop_idx": 2},
        )
    ]
    report = check_answer_key(records)
    assert report.conflicting_key_ids == ["conflict"]


def test_shortcut_baselines_hand_values() -> None:
    # Both records have gold at index 0 and the longest option at index 0.
    records = [
        record("a", "question one text here", ("a very long correct option", "b", "c", "d"), gold_index=0),
        record("b", "question two text here", ("another long correct option", "b", "c", "d"), gold_index=0),
    ]
    first, longest, gold_len, other_len = shortcut_baselines(records)
    assert first == pytest.approx(1.0)
    assert longest == pytest.approx(1.0)
    assert gold_len > other_len


def test_key_distribution_and_uniformity() -> None:
    options = ("o0", "o1", "o2", "o3")
    records = [record(f"i{i}", f"question number {i} about something medical", options, gold_index=i % 4) for i in range(40)]
    report = check_answer_key(records)
    assert report.gold_distribution == {"0": 10, "1": 10, "2": 10, "3": 10}
    assert report.uniformity_p_value is not None
    assert report.uniformity_p_value > 0.99


def test_answer_in_stem_detects_echo() -> None:
    records = [
        record(
            "echo",
            "A patient has acute myocardial infarction. What is the diagnosis?",
            ("acute myocardial infarction", "pericarditis", "pneumonia", "gastritis"),
            gold_index=0,
        ),
        record(
            "clean",
            "A patient has crushing chest pain. What is the most likely diagnosis?",
            ("acute myocardial infarction", "pericarditis", "pneumonia", "gastritis"),
            gold_index=0,
        ),
    ]
    rate, hits, _ = answer_in_stem(records)
    assert hits == ["echo"]
    assert rate == pytest.approx(0.5)


def test_audit_split_builds_report_without_mutation() -> None:
    loaded = LoadedSplit(
        spec=DATASETS["medmcqa"],
        split="validation",
        records=tuple(record(f"i{i}", f"distinct clinical question number {i} text") for i in range(3)),
        strategy="test",
        source_detail="unit-test",
        n_raw_rows=3,
    )
    audit = audit_split(loaded)
    payload = audit.as_dict()
    assert payload["n_items"] == 3
    assert payload["licence"] == "apache-2.0"
    assert len(payload["fingerprint"]) == 64
    assert "duplicates" in payload and "answer_key" in payload and "contamination" in payload
    assert loaded.records[0].question == "distinct clinical question number 0 text"


def test_materialize_jsonl_round_trip(tmp_path: Path) -> None:
    loaded = LoadedSplit(
        spec=DATASETS["medmcqa"],
        split="validation",
        records=(record("i0", "question text here", raw={"id": "i0", "question": "question text here"}),),
        strategy="test",
        source_detail="unit-test",
        n_raw_rows=1,
    )
    out = materialize_jsonl(loaded, tmp_path / "rows.jsonl")
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"id": "i0", "question": "question text here"}]
