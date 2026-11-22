"""The five built-in preset Paper Packs: metadata, smoke data and loaders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_packs import MatrixConfig, MatrixRun, ModelBinding, resolve_pack
from medeval_packs.loaders import main as loader_main
from medeval_packs.loaders import verify_and_write
from medeval_packs.registry import list_packs
from medeval_packs.schema import PackValidationError, pack_from_mapping

PRESETS = (
    "healthbench-rubric",
    "medcalc-bench",
    "medhallu-detect",
    "medxpertqa-text",
    "medsafety-bench",
)
TAG = "PIPELINE TEST / SYNTHETIC SMOKE DATA"


def test_all_presets_are_discoverable_valid_and_declare_an_upstream() -> None:
    entries = {entry.id: entry for entry in list_packs()}
    for preset in PRESETS:
        entry = entries[preset]
        assert entry.preset is True
        assert entry.validation.ok, entry.validation.errors
        assert entry.source_dataset["url"] or entry.source_dataset["huggingface"]
        assert entry.source_dataset["license"]
        assert entry.adaptation_status
        assert entry.loader == "loader.py"
        assert 3 <= (entry.dataset_count or 0) <= 5


def test_presets_bundle_only_synthetic_smoke_rows() -> None:
    for preset in PRESETS:
        pack = resolve_pack(preset)
        path = pack.path / "smoke.jsonl"
        assert path.is_file()
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert 3 <= len(lines) <= 5, preset
        for line in lines:
            row = json.loads(line)
            assert TAG in json.dumps(row), f"{preset} row is not tagged synthetic"
        # No raw upstream dataset is checked in: only smoke.jsonl and loader.py.
        data_files = {p.name for p in (pack.path / "data").glob("*")} if (pack.path / "data").is_dir() else set()
        assert not data_files, f"{preset} unexpectedly bundles {data_files}"
        assert not list(pack.path.glob("*.csv")), f"{preset} bundles a CSV"
        assert (pack.path / "loader.py").is_file()


def test_preset_metadata_is_not_a_clinical_claim() -> None:
    for preset in PRESETS:
        pack = resolve_pack(preset)
        assert pack.methodology.kind in {
            "faithful_protocol_reproduction",
            "inspired_adaptation",
            "synthetic_pipeline_template",
        }
        assert pack.source_dataset.license
        assert "not" in pack.methodology.limitations.casefold() or "no " in pack.methodology.limitations.casefold()


def test_a_preset_must_declare_source_and_adaptation() -> None:
    raw = dict(resolve_pack("medcalc-bench").raw)
    raw["preset"] = True
    raw.pop("source_dataset", None)
    raw.pop("adaptation_status", None)
    pack = pack_from_mapping(raw, path=None)
    errors = " ".join(pack.validate().errors)
    assert "source_dataset.url or source_dataset.huggingface" in errors
    assert "adaptation_status" in errors


def _run_preset(preset: str, roots: tuple[Path, Path]) -> dict:
    pack = resolve_pack(preset)
    run_root, cache_root = roots
    judges = ()
    if any(grader.type == "llm_rubric" for grader in pack.graders):
        judges = (
            ModelBinding(id="j-a", label="Judge A", model="mock-j-a", family="family-a", role="judge"),
            ModelBinding(id="j-b", label="Judge B", model="mock-j-b", family="family-b", role="judge"),
        )
    config = MatrixConfig(
        targets=(
            ModelBinding(id="m-a", label="Target A", model="mock-a"),
            ModelBinding(id="m-b", label="Target B", model="mock-b"),
        ),
        judges=judges,
        dry_run=True,
        n_items=4,
        max_calls=80,
        max_usd=0.0,
    )
    return MatrixRun(pack, config, run_root=run_root, cache_root=cache_root).execute()


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_runs_offline_on_smoke_data(preset: str, tmp_path: Path) -> None:
    result = _run_preset(preset, (tmp_path / "runs", tmp_path / "cache"))
    assert result["status"] == "completed"
    assert result["metrics"]["pipeline_test_only"] is True
    assert result["metrics"]["targets"]
    assert Path(result["paths"]["report"]).is_file()
    assert Path(result["paths"]["bundle"]).is_file()


def test_preset_specific_metrics(tmp_path: Path) -> None:
    roots = (tmp_path / "runs", tmp_path / "cache")
    health = _run_preset("healthbench-rubric", roots)
    assert health["metrics"]["primary_metric"] == "rubric_score"
    assert health["metrics"]["judge_reliability"]["available"] is True
    assert health["metrics"]["judge_reliability"]["cross_family"]

    calc = _run_preset("medcalc-bench", roots)
    assert calc["metrics"]["primary_metric"] == "numeric_match_tolerance"
    row = next(iter(calc["metrics"]["targets"].values()))
    assert "numeric_match_exact" in row["secondary"]
    assert "field_f1" in row["secondary"]

    hallu = _run_preset("medhallu-detect", roots)
    assert hallu["metrics"]["primary_metric"] == "macro_f1"
    row = next(iter(hallu["metrics"]["targets"].values()))
    assert row["classification"] is not None
    assert set(row["classification"]["per_class"]) >= {"hallucinated", "not-hallucinated", "not-sure"}
    assert "coverage" in row["secondary"]
    assert "abstention" in row["secondary"]

    xpert = _run_preset("medxpertqa-text", roots)
    assert xpert["metrics"]["primary_metric"] == "mcq_accuracy"
    assert xpert["metrics"]["pairwise"][0]["method"] == "mcnemar"

    safety = _run_preset("medsafety-bench", roots)
    assert safety["metrics"]["primary_metric"] == "refusal"
    assert safety["metrics"]["judge_reliability"]["available"] is True
    row = next(iter(safety["metrics"]["targets"].values()))
    assert "rubric_score" in row["secondary"]


def test_loader_verifies_and_copies_a_local_file(tmp_path: Path) -> None:
    pack = resolve_pack("medcalc-bench")
    rows = [json.loads(line) for line in (pack.path / "smoke.jsonl").read_text(encoding="utf-8").splitlines()]
    destination = verify_and_write(pack, rows, out_dir=tmp_path / "loaded")
    assert destination.is_file()
    assert len(destination.read_text(encoding="utf-8").splitlines()) == len(rows)
    # A second write needs --force.
    with pytest.raises(PackValidationError):
        verify_and_write(pack, rows, out_dir=tmp_path / "loaded")


def test_loader_rejects_a_bad_row_and_writes_nothing(tmp_path: Path) -> None:
    pack = resolve_pack("medcalc-bench")
    with pytest.raises(PackValidationError) as excinfo:
        verify_and_write(pack, [{"id": "broken"}], out_dir=tmp_path / "bad")
    assert "schema verification" in str(excinfo.value)
    assert not (tmp_path / "bad" / "dataset.jsonl").exists()


def test_loader_cli_local_source_is_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_dir = resolve_pack("medcalc-bench").path
    output = tmp_path / "cli-loaded"
    code = loader_main(pack_dir, ["--source", str(pack_dir / "smoke.jsonl"), "--out", str(output)])
    assert code == 0
    assert (output / "dataset.jsonl").is_file()
    assert "no network" not in capsys.readouterr().out.casefold()


def test_loader_download_requires_an_explicit_licence_acknowledgement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack_dir = resolve_pack("medcalc-bench").path
    code = loader_main(pack_dir, ["--download", "--out", str(tmp_path / "never")])
    assert code == 2
    assert "accept-license" in capsys.readouterr().err


def test_medeval_data_is_gitignored(project_root: Path) -> None:
    text = (project_root / ".gitignore").read_text(encoding="utf-8")
    assert ".medeval_data/" in text
