"""End-to-end study runs with mock judges: resume, caps, artefacts, no secrets.

These tests monkeypatch the dataset loader, so they never touch the network and
never depend on a downloaded benchmark. Everything else is the real runner: the
same cache, the same append-only judgement log, the same report writer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_core.config import Settings
from medeval_study import runner as runner_module
from medeval_study.datasets import DatasetBundle, DatasetLicence
from medeval_study.models import McqItem
from medeval_study.runner import StudyConfig, StudyRun, rebuild_run

SECRET = "sk-test-should-never-be-written-0123456789"

OPTIONS = {
    "A": "Acute myocardial infarction",
    "B": "Unstable angina pectoris",
    "C": "Pulmonary embolism",
    "D": "Aortic dissection",
}


def make_item(index: int) -> McqItem:
    return McqItem(
        item_id=f"syn-{index:03d}",
        question=(
            f"Synthetic vignette number {index}: a patient presents with a presentation that needs a "
            "single best answer, and the stem is long enough to be accepted by the loader."
        ),
        options=dict(OPTIONS),
        key_letter="ABCD"[index % 4],
        key_text=OPTIONS["ABCD"[index % 4]],
        dataset="synthetic",
        split="dev",
        row_index=index,
        meta={},
    )


def bundle(n: int = 6) -> DatasetBundle:
    licence = DatasetLicence(
        key="synthetic",
        hf_repo="none/synthetic",
        licence="synthetic-test",
        licence_evidence="test fixture",
        redistribute=True,
        attribution="test fixture",
    )
    items = tuple(make_item(index) for index in range(n))
    return DatasetBundle(
        items=items,
        licence=licence,
        dataset="synthetic",
        split="dev",
        strategy="test",
        source_detail="fixture",
        n_raw_rows=n,
        n_dropped=0,
    )


def settings() -> Settings:
    return Settings(
        base_url=None,
        model=None,
        api_key=SECRET,
        max_tokens=64,
        temperature=0.3,
        seed=7,
        retries=1,
        max_calls=100,
        max_usd=1.0,
        input_usd_per_million=0.30,
        output_usd_per_million=1.20,
        price_source="test-prices",
    )


@pytest.fixture()
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_module, "dataset_cache_status", lambda *args, **kwargs: bundle())

    def _run(run_id: str = "t1", **overrides) -> StudyRun:
        config = StudyConfig(
            dataset="synthetic",
            n_items=overrides.pop("n_items", 6),
            repeats=overrides.pop("repeats", 2),
            dry_run=True,
            run_id=run_id,
            max_api_calls=overrides.pop("max_api_calls", 100),
            seed=overrides.pop("seed", 7),
            # A new run has no default judges (v0.5: bring your own connection).
            # These ids are the archived Experiment 002 configuration, named here
            # on purpose: its dry run maps them to j1_mock/j2_mock.
            judges=overrides.pop("judges", ("j1_deepseek", "j2_ollama")),
            **overrides,
        )
        return StudyRun(config, settings(), run_root=tmp_path)

    return _run


def test_dry_run_completes_and_writes_every_artefact(study, tmp_path: Path) -> None:
    result = study().execute()
    assert result["status"] == "completed"
    run_dir = tmp_path / "t1"
    for name in ("run.json", "manifest.json", "metrics.json", "report.md", "items.csv", "judgements.jsonl"):
        assert (run_dir / name).is_file(), f"{name} missing"
    judgements = (run_dir / "judgements.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(judgements) == 6 * 2 * 2, "items x repeats x judges"

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["pipeline_test_only"] is True
    assert set(metrics["judges"]) == {"j1_mock", "j2_mock"}


def test_report_has_limitations_and_states_it_is_a_pipeline_test(study, tmp_path: Path) -> None:
    study().execute()
    report = (tmp_path / "t1" / "report.md").read_text(encoding="utf-8")
    assert "## Limitations" in report
    assert "PIPELINE TEST ONLY" in report
    assert "No clinician review" in report
    assert "exploratory" in report.lower()
    assert "Synthetic vignette number 0" not in report, "benchmark/item text must not be published in the report"


def test_no_secret_is_written_anywhere_in_the_run(study, tmp_path: Path) -> None:
    study().execute()
    for path in (tmp_path / "t1").rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(encoding="utf-8", errors="ignore"), f"secret leaked into {path.name}"


def test_resume_makes_no_new_calls(study, tmp_path: Path) -> None:
    first = study().execute()
    assert first["status"] == "completed"
    calls_path = tmp_path / "t1" / "calls.jsonl"
    before = len(calls_path.read_text(encoding="utf-8").strip().splitlines())

    second = study().execute()
    assert second["status"] == "completed"
    after = len(calls_path.read_text(encoding="utf-8").strip().splitlines())
    assert after == before, "a resumed run must not repeat completed judge calls"
    assert second["usage"]["calls_made"] == 0


def test_config_lock_refuses_a_changed_seed(study, tmp_path: Path) -> None:
    study().execute()
    calls_path = tmp_path / "t1" / "calls.jsonl"
    before = len(calls_path.read_text(encoding="utf-8").strip().splitlines())

    result = study(seed=999999).execute()
    assert result["status"] == "refused"
    diffs = result["config_lock_diffs"]
    assert any(diff.startswith("seed:") for diff in diffs)
    after = len(calls_path.read_text(encoding="utf-8").strip().splitlines())
    assert after == before, "a locked resume must be refused before any call"
    state = json.loads((tmp_path / "t1" / "run.json").read_text(encoding="utf-8"))
    assert "config lock" in state["refused_reason"]


def test_config_lock_refuses_changed_items_and_judges(study, tmp_path: Path) -> None:
    study().execute()
    result = study(n_items=3, judges=("j1_deepseek",)).execute()
    assert result["status"] == "refused"
    fields = {diff.split(":")[0] for diff in result["config_lock_diffs"]}
    assert {"n_items", "judges"} <= fields


def test_config_lock_allows_an_identical_resume(study, tmp_path: Path) -> None:
    study().execute()
    result = study().execute()
    assert result["status"] == "completed"
    assert "config_lock_diffs" not in result


def test_judgements_carry_failure_type(study, tmp_path: Path) -> None:
    study().execute()
    rows = [
        json.loads(line)
        for line in (tmp_path / "t1" / "judgements.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows
    assert all(row["failure_type"] == "none" for row in rows)


def test_plan_above_the_call_cap_is_refused_before_any_call(study, tmp_path: Path) -> None:
    result = study(max_api_calls=3).execute()
    assert result["status"] == "refused"
    assert not (tmp_path / "t1" / "calls.jsonl").exists()
    state = json.loads((tmp_path / "t1" / "run.json").read_text(encoding="utf-8"))
    assert "max-api-calls" in state["refused_reason"]


def test_seed_changes_the_sample_but_not_the_contract(study, tmp_path: Path) -> None:
    study("a", seed=1).execute()
    study("b", seed=2).execute()
    ids_a = [json.loads(line)["item_id"] for line in (tmp_path / "a" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    ids_b = [json.loads(line)["item_id"] for line in (tmp_path / "b" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ids_a) == len(ids_b) == 6
    assert len(set(ids_a)) == 6


def test_manifest_records_provenance_without_secrets(study, tmp_path: Path) -> None:
    study().execute()
    manifest = json.loads((tmp_path / "t1" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_hashes"]["prompt_version"] == "mcq_judge_v1"
    if not (Path(__file__).resolve().parents[1] / ".git").exists():
        pytest.skip("no .git directory (running from an extracted archive)")
    assert manifest["git"]["commit"] is not None
    assert manifest["seeds"]["sampling"] == 7
    assert set(manifest["models"]) == {"j1_mock", "j2_mock"}
    assert manifest["dataset"]["licence"]["licence"] == "synthetic-test"
    assert "api_key" not in json.dumps(manifest).lower()


def test_judgements_are_validated_json(study, tmp_path: Path) -> None:
    study().execute()
    rows = [
        json.loads(line)
        for line in (tmp_path / "t1" / "judgements.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for row in rows:
        assert row["parse_ok"] is True
        assert row["verdict"] in {"CORRECT", "INCORRECT"}
        assert isinstance(row["confidence"], int) and 0 <= row["confidence"] <= 100
        assert len(row["prompt_sha256"]) == 64


def test_items_csv_has_no_question_text(study, tmp_path: Path) -> None:
    study().execute()
    text = (tmp_path / "t1" / "items.csv").read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert "question" not in header.lower()
    assert "Synthetic vignette" not in text


def test_rebuild_recomputes_metrics_without_calling_a_model(study, tmp_path: Path) -> None:
    study().execute()
    calls_before = len((tmp_path / "t1" / "calls.jsonl").read_text(encoding="utf-8").strip().splitlines())
    rebuilt = rebuild_run("t1", run_root=tmp_path)
    calls_after = len((tmp_path / "t1" / "calls.jsonl").read_text(encoding="utf-8").strip().splitlines())
    assert calls_after == calls_before
    assert rebuilt["n_judgements"] == 24
    assert "reliability_bins" in rebuilt["metrics"]["judges"]["j1_mock"]["primary_repeat"]["calibration"]


def test_rebuild_refuses_a_run_without_local_item_files(study, tmp_path: Path) -> None:
    study().execute()
    (tmp_path / "t1" / "items.jsonl").unlink()
    with pytest.raises(FileNotFoundError):
        rebuild_run("t1", run_root=tmp_path)
