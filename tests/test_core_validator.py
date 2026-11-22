"""Tests for the item JSON Schema and semantic validator."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from medeval_core.validator import (
    ItemValidationError,
    load_items,
    load_schema,
    validate_item,
    validate_items,
)
from tests.support import make_item


def _codes(item: dict) -> set[str]:
    return {issue.code for issue in validate_item(item, today=date(2026, 2, 1))}


def test_schema_is_valid_metaschema() -> None:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)


def test_valid_item_passes() -> None:
    report = validate_items([make_item()], today=date(2026, 2, 1))
    assert report.ok, [i.as_dict() for i in report.errors]
    assert report.n_items == 1


def test_missing_provenance_is_rejected() -> None:
    item = make_item()
    del item["provenance"]
    report = validate_items([item], today=date(2026, 2, 1))
    assert not report.ok
    assert "schema" in {issue.code for issue in report.errors}


def test_missing_label_source_is_rejected() -> None:
    item = make_item()
    del item["label_source"]
    report = validate_items([item], today=date(2026, 2, 1))
    assert not report.ok
    assert any("label_source" in issue.message for issue in report.errors)


def test_missing_licence_is_rejected() -> None:
    item = make_item()
    item["provenance"] = dict(item["provenance"])
    item["provenance"]["licence"] = ""
    report = validate_items([item], today=date(2026, 2, 1))
    assert not report.ok
    assert "missing_licence" in {issue.code for issue in report.errors}
    assert any("licence" in issue.message for issue in report.errors)


def test_placeholder_licence_is_rejected() -> None:
    item = make_item()
    item["provenance"] = dict(item["provenance"], licence="TBD")
    assert "missing_licence" in _codes(item)


def test_gold_answer_must_be_one_of_options() -> None:
    item = make_item(gold_answer="Tension pneumothorax")
    assert "gold_not_in_options" in _codes(item)


def test_synthetic_item_may_not_claim_doi() -> None:
    item = make_item()
    item["provenance"] = dict(item["provenance"], doi="10.1000/xyz123")
    assert "synthetic_claims_doi" in _codes(item)


def test_paper_item_requires_doi() -> None:
    item = make_item(label_source="paper_finding")
    item["provenance"] = dict(item["provenance"], section="Results, Table 2")
    assert "missing_doi" in _codes(item)


def test_paper_item_with_doi_passes() -> None:
    item = make_item(label_source="paper_finding")
    item["provenance"] = dict(
        item["provenance"],
        doi="10.1056/NEJMoa2034577",
        section="Results, Table 2",
        licence="cc-by-4.0",
        source_title="Example trial",
    )
    codes = _codes(item)
    assert "missing_doi" not in codes
    assert "non_open_licence" not in codes


def test_duplicate_ids_are_errors() -> None:
    report = validate_items([make_item(), make_item()], today=date(2026, 2, 1))
    assert "duplicate_id" in {issue.code for issue in report.errors}


def test_duplicate_text_is_a_warning_not_an_error() -> None:
    first = make_item(id="test-0001")
    second = make_item(id="test-0002")
    report = validate_items([first, second], today=date(2026, 2, 1))
    assert report.ok
    assert "duplicate_text" in {issue.code for issue in report.warnings}


def test_option_letter_prefix_is_flagged() -> None:
    item = make_item(
        options=["A. Acute myocardial infarction", "B. Pulmonary embolism", "C. Pericarditis"],
        gold_answer="A. Acute myocardial infarction",
    )
    assert "option_has_letter_prefix" in _codes(item)


def test_duplicate_option_text_after_normalisation() -> None:
    item = make_item(
        options=["Acute myocardial infarction", "acute  myocardial infarction", "Pericarditis"],
        gold_answer="Acute myocardial infarction",
    )
    assert "duplicate_option_text" in _codes(item)


def test_single_rater_human_reference_is_flagged() -> None:
    item = make_item(
        human_reference={"rater_ids": ["r1"], "labels": {"r1": {"diagnostic_accuracy": 2}}}
    )
    assert "single_rater_reference" in _codes(item)


def test_two_rater_human_reference_is_allowed() -> None:
    item = make_item(
        human_reference={"rater_ids": ["r1", "r2"], "labels": {"r1": {}, "r2": {}}}
    )
    assert "single_rater_reference" not in _codes(item)


def test_future_publication_date_is_an_error() -> None:
    item = make_item(publication_date="2027-01-01")
    assert "future_date" in _codes(item)


def test_planted_error_without_notes_warns() -> None:
    item = make_item(label_source="planted_error", notes="")
    issue_codes = _codes(item)
    assert "planted_error_without_notes" in issue_codes


def test_open_ended_item_is_allowed_with_warning() -> None:
    item = make_item(
        options=[],
        gold_answer="Start dual antiplatelet therapy and arrange urgent angiography.",
        label_source="synthetic",
    )
    codes = _codes(item)
    assert "schema" not in codes
    assert "open_ended_gold" in codes


def test_load_items_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    rows = [make_item(id="test-0001"), make_item(id="test-0002")]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [item["id"] for item in load_items(path)] == ["test-0001", "test-0002"]


def test_load_items_rejects_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_items(path)


def test_validate_does_not_mutate_items() -> None:
    item = make_item()
    snapshot = copy.deepcopy(item)
    validate_items([item], today=date(2026, 2, 1))
    assert item == snapshot


def test_report_serialises_counts() -> None:
    report = validate_items([make_item(gold_answer="Missing option")], today=date(2026, 2, 1))
    payload = report.as_dict()
    assert payload["n_errors"] >= 1
    assert payload["counts_by_code"]["gold_not_in_options"] == 1
    assert payload["ok"] is False


def test_raise_for_errors() -> None:
    report = validate_items([make_item(gold_answer="Missing option")], today=date(2026, 2, 1))
    with pytest.raises(ItemValidationError):
        report.raise_for_errors()
