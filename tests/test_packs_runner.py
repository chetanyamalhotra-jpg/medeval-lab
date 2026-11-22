"""Matrix runner: dry runs, cache, resume, budget stop, regrade and exports."""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

import pytest

from medeval_core.client import RawCompletion
from medeval_packs import (
    MatrixConfig,
    MatrixRun,
    ModelBinding,
    load_matrix_run,
    regrade_run,
    resolve_pack,
)
from medeval_study.report import MEDICAL_NOTICE


class FakeTransport:
    """A local stand-in transport: no socket, deterministic completion."""

    def __init__(self, text: str = "ANSWER: A\n\nSynthetic.") -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages, *, temperature, seed, max_tokens, timeout_s):
        self.calls += 1
        return RawCompletion(
            text=self.text,
            latency_ms=1.5,
            input_tokens=25,
            output_tokens=5,
            total_tokens=30,
        )


@pytest.fixture()
def pack():
    return resolve_pack("template-clinical-mcq")


@pytest.fixture()
def roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "runs", tmp_path / "cache"


def config(*, dry_run: bool = True, n_items: int = 6, **overrides) -> MatrixConfig:
    defaults = {
        "targets": (
            ModelBinding(id="m-a", label="Model A", model="mock-a"),
            ModelBinding(id="m-b", label="Model B", model="mock-b"),
        ),
        "judges": (),
        "dry_run": dry_run,
        "n_items": n_items,
        "max_calls": 100,
        "max_usd": 0.0,
    }
    defaults.update(overrides)
    return MatrixConfig(**defaults)


def test_dry_run_writes_metrics_and_every_export(pack, roots) -> None:
    run_root, cache_root = roots
    result = MatrixRun(pack, config(), run_root=run_root, cache_root=cache_root).execute()
    assert result["status"] == "completed"
    metrics = result["metrics"]
    assert metrics["pipeline_test_only"] is True
    assert set(metrics["targets"]) == {"m-a", "m-b"}
    assert metrics["overall"]["mean_primary"] is not None
    assert metrics["pairwise"]
    run_dir = Path(result["run_dir"])
    for name in ("run.json", "events.jsonl", "outputs.jsonl", "scores.jsonl", "metrics.json",
                 "report.md", "results_table.tex", "citations.bib", "bundle.zip", "manifest.json"):
        assert (run_dir / name).is_file(), name
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# ")
    assert MEDICAL_NOTICE in report.splitlines()[2]
    latex = (run_dir / "results_table.tex").read_text(encoding="utf-8")
    assert latex.startswith("% " + MEDICAL_NOTICE)
    bib = (run_dir / "citations.bib").read_text(encoding="utf-8")
    assert "medeval_lab_v060" in bib
    with zipfile.ZipFile(run_dir / "bundle.zip") as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "metrics.json", "report.md", "results_table.tex", "citations.bib",
                "items.csv", "run.json", "README.txt"} <= names
        assert "sk-" not in archive.read("README.txt").decode("utf-8")


def test_run_json_records_hashes_and_never_a_key(pack, roots) -> None:
    run_root, cache_root = roots
    binding = ModelBinding(
        id="m-a", label="Model A", model="mock-a", api_key="sk-fake-secret", base_url="https://example.invalid/v1"
    )
    result = MatrixRun(pack, config(targets=(binding,), dry_run=True), run_root=run_root, cache_root=cache_root).execute()
    state = Path(result["run_dir"]) / "run.json"
    text = state.read_text(encoding="utf-8")
    assert "sk-fake-secret" not in text
    payload = json.loads(text)
    assert payload["paper"]["pack_hash"] == pack.content_hash()
    assert payload["paper"]["prompt_hash"] == pack.prompt_hash()
    assert payload["pipeline_test_only"] is True


def test_cache_makes_a_second_run_free(pack, roots) -> None:
    run_root, cache_root = roots
    first = MatrixRun(pack, config(dry_run=True), run_root=run_root, cache_root=cache_root).execute()
    assert first["status"] == "completed"
    second = MatrixRun(pack, config(dry_run=True), run_root=run_root, cache_root=cache_root).execute()
    assert second["status"] == "completed"
    assert second["usage"]["cache_hits"] == 12
    assert second["usage"]["mock_calls"] == 0
    assert any(cache_root.glob("*/*.json"))


def test_stop_then_resume_never_duplicates_a_call(pack, roots) -> None:
    run_root, cache_root = roots
    stop = threading.Event()
    seen = {"count": 0}

    def progress(event):
        if event.get("type") == "target_output":
            seen["count"] += 1
            if seen["count"] >= 4:
                stop.set()

    first = MatrixRun(
        pack, config(dry_run=True), run_root=run_root, cache_root=cache_root,
        progress=progress, stop_event=stop,
    ).execute()
    assert first["status"] == "stopped"
    assert first["metrics"]["n_outputs"] == 4
    run_id = first["run_id"]
    resumed = MatrixRun(
        pack,
        MatrixConfig(
            targets=(ModelBinding(id="m-a", label="Model A", model="mock-a"),
                     ModelBinding(id="m-b", label="Model B", model="mock-b")),
            judges=(), dry_run=True, n_items=6, max_calls=100, max_usd=0.0, run_id=run_id,
        ),
        run_root=run_root, cache_root=cache_root,
    ).execute()
    assert resumed["status"] == "completed"
    payload = load_matrix_run(run_id, root=run_root)
    assert payload is not None
    keys = [(row["target_id"], row["item_id"]) for row in payload["outputs"]]
    assert len(keys) == len(set(keys)) == 12
    score_keys = [(row["target_id"], row["item_id"], row["grader_id"], row.get("judge_id")) for row in payload["scores"]]
    assert len(score_keys) == len(set(score_keys))


def test_budget_stop_saves_completed_items(pack, roots) -> None:
    run_root, cache_root = roots
    transport = FakeTransport()
    binding = ModelBinding(
        id="m-a", label="Model A", model="live-a", base_url="https://example.invalid/v1",
        price_in_per_mtok=1.0, price_out_per_mtok=2.0,
    )
    run = MatrixRun(
        pack,
        MatrixConfig(targets=(binding,), judges=(), dry_run=False, n_items=6, max_calls=3, max_usd=5.0),
        run_root=run_root,
        cache_root=cache_root,
        transport_factory=lambda _binding: transport,
    )
    result = run.execute()
    assert result["status"] == "budget_stopped"
    assert "call cap" in (result["reason"] or "")
    assert transport.calls == 3
    assert result["metrics"]["n_outputs"] == 3
    assert (Path(result["run_dir"]) / "report.md").is_file()


def test_auth_failure_stops_the_run(pack, roots) -> None:
    run_root, cache_root = roots

    class AuthTransport:
        def complete(self, messages, **kwargs):
            return RawCompletion(text="", latency_ms=1, error="HTTP 401: bad key", error_type="auth")

    binding = ModelBinding(id="m-a", label="A", model="live-a", base_url="https://example.invalid/v1")
    result = MatrixRun(
        pack, MatrixConfig(targets=(binding,), judges=(), dry_run=False, n_items=4),
        run_root=run_root, cache_root=cache_root, transport_factory=lambda _binding: AuthTransport(),
    ).execute()
    assert result["status"] == "failed"
    assert "authentication failed" in (result["reason"] or "")


def test_regrade_never_calls_a_target(pack, roots) -> None:
    run_root, cache_root = roots
    first = MatrixRun(pack, config(dry_run=True), run_root=run_root, cache_root=cache_root).execute()
    regrade = regrade_run(first["run_id"], pack, root=run_root, cache_root=cache_root)
    assert regrade["status"] == "completed"
    assert regrade["usage"]["target_calls"] == 0
    assert regrade["metrics"]["targets"]
    assert Path(regrade["paths"]["report"]).is_file()
    assert regrade["run_id"].startswith(first["run_id"] + "-regrade-")


def test_resume_refuses_a_changed_pack(pack, roots, tmp_path: Path) -> None:
    run_root, cache_root = roots
    first = MatrixRun(pack, config(dry_run=True), run_root=run_root, cache_root=cache_root).execute()
    # A pack with a different prompt produces a different hash.
    raw = dict(pack.raw)
    raw["prompt_template"] = {**raw["prompt_template"], "system": "A changed system prompt."}
    from medeval_packs.schema import pack_from_mapping

    changed = pack_from_mapping(raw, path=pack.path)
    with pytest.raises(ValueError, match="different version of the pack"):
        MatrixRun(
            changed,
            MatrixConfig(targets=config().targets, dry_run=True, run_id=first["run_id"], n_items=2),
            run_root=run_root, cache_root=cache_root,
        ).prepare()


def test_rubric_pack_produces_judge_reliability(pack, roots) -> None:
    run_root, cache_root = roots
    rubric = resolve_pack("template-rubric-panel")
    judges = (
        ModelBinding(id="j-a", label="Judge A", model="mock-j-a", family="family-a", role="judge"),
        ModelBinding(id="j-b", label="Judge B", model="mock-j-b", family="family-b", role="judge"),
    )
    result = MatrixRun(
        rubric,
        MatrixConfig(
            targets=(ModelBinding(id="m-a", label="A", model="mock-a"),),
            judges=judges,
            dry_run=True, n_items=8, max_calls=100, max_usd=0.0,
        ),
        run_root=run_root,
        cache_root=cache_root,
    ).execute()
    assert result["status"] == "completed"
    reliability = result["metrics"]["judge_reliability"]
    assert reliability["available"] is True
    assert reliability["cross_family"]
    assert result["metrics"]["primary_metric"] == "rubric_score"

    # A regrade can swap the judge panel without touching a target model.
    regraded = regrade_run(
        result["run_id"],
        rubric,
        judges=(ModelBinding(id="j-c", label="Judge C", model="mock-j-c", family="family-c", role="judge"),),
        dry_run=True,
        root=run_root,
        cache_root=cache_root,
    )
    assert regraded["usage"]["target_calls"] == 0
    assert regraded["metrics"]["judge_reliability"]["judges"] == ["j-c"]
    assert regraded["metrics"]["targets"]["m-a"]["n_items"] == 8
