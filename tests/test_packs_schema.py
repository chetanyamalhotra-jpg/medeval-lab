"""Paper Pack schema, registry and scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest

from medeval_packs.dataset import load_items
from medeval_packs.registry import init_pack, list_packs, resolve_pack
from medeval_packs.schema import (
    PackValidationError,
    load_pack,
    pack_from_mapping,
)

PACKS = ("template-clinical-mcq", "template-rubric-panel", "template-structured-extraction")


def test_starter_packs_are_valid_and_have_items() -> None:
    entries = {entry.id: entry for entry in list_packs()}
    assert set(PACKS) <= set(entries)
    for pack_id in PACKS:
        entry = entries[pack_id]
        assert entry.validation.ok, entry.validation.errors
        assert entry.dataset_count and entry.dataset_count > 0
        assert entry.runnable is True
        assert entry.kind == "paper_pack"


def test_pack_hashes_are_stable_and_content_sensitive(project_root: Path) -> None:
    pack = resolve_pack("template-clinical-mcq")
    assert pack.content_hash() == pack.content_hash()
    assert pack.prompt_hash() == pack.prompt_hash()
    raw = dict(pack.raw)
    raw["title"] = raw["title"] + " (edited)"
    changed = pack_from_mapping(raw, path=pack.path)
    assert changed.content_hash() != pack.content_hash()


def test_unknown_keys_are_errors(project_root: Path) -> None:
    raw = dict(resolve_pack("template-clinical-mcq").raw)
    raw["surprise"] = True
    pack = pack_from_mapping(raw)
    validation = pack.validate()
    assert not validation.ok
    assert any("unknown key" in error for error in validation.errors)


def test_forbidden_clinical_claim_is_rejected(project_root: Path) -> None:
    raw = dict(resolve_pack("template-clinical-mcq").raw)
    methodology = dict(raw["methodology_notes"])
    methodology["summary"] = "This pack is clinically validated and clinician reviewed."
    raw["methodology_notes"] = methodology
    pack = pack_from_mapping(raw)
    assert not pack.validate().ok
    assert any("may not claim clinical validation" in error for error in pack.validate().errors)


def test_missing_graders_and_bad_semver_are_reported(project_root: Path) -> None:
    raw = dict(resolve_pack("template-clinical-mcq").raw)
    raw["graders"] = []
    raw["version"] = "1"
    pack = pack_from_mapping(raw)
    errors = " ".join(pack.validate().errors)
    assert "graders must contain at least one grader" in errors
    assert "semantic version" in errors


def test_dataset_path_cannot_escape_the_pack(project_root: Path, tmp_path: Path) -> None:
    raw = dict(resolve_pack("template-clinical-mcq").raw)
    dataset = dict(raw["dataset"])
    dataset["path"] = "../../outside.jsonl"
    raw["dataset"] = dataset
    pack = pack_from_mapping(raw, path=tmp_path)
    validation = pack.validate()
    assert not validation.ok
    assert any("inside the pack directory" in error or "not found" in error for error in validation.errors)


def test_load_pack_reports_all_errors(project_root: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad-pack"
    bad.mkdir()
    (bad / "paper.yaml").write_text("id: Bad ID\nversion: '1'\n", encoding="utf-8")
    with pytest.raises(PackValidationError) as excinfo:
        load_pack(bad)
    assert len(excinfo.value.errors) >= 3
    assert excinfo.value.errors == list(dict.fromkeys(excinfo.value.errors))


def test_load_items_never_puts_the_answer_key_in_prompt_fields() -> None:
    for pack_id in PACKS:
        pack = resolve_pack(pack_id)
        for item in load_items(pack):
            if pack.task_type == "mcq":
                assert "answer" not in item.prompt_fields
                assert item.gold["letter"] not in {"", None}
            assert item.item_hash


def test_sampling_is_deterministic_and_limit_applies() -> None:
    pack = resolve_pack("template-clinical-mcq")
    first = [item.item_id for item in load_items(pack)]
    second = [item.item_id for item in load_items(pack)]
    assert first == second
    assert len(first) == 24


@pytest.mark.parametrize("task", ["mcq", "open_ended_rubric", "structured_extraction", "triage_classification"])
def test_init_pack_creates_a_valid_template(task: str, tmp_path: Path) -> None:
    destination = tmp_path / f"starter-{task.replace('_', '-')}"
    created = init_pack(f"starter-{task.replace('_', '-')}", task, destination)
    pack = load_pack(created)
    assert pack.validate().ok, pack.validate().errors
    assert pack.id == f"starter-{task.replace('_', '-')}"
    assert "PIPELINE TEST / TEMPLATE DATA" in (created / "README.md").read_text(encoding="utf-8")


def test_init_pack_refuses_to_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "exists"
    destination.mkdir()
    (destination / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(PackValidationError):
        init_pack("exists", "mcq", destination)
