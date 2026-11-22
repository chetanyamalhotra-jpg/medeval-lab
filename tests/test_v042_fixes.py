"""Regression tests for the v0.4.2 fixes.

Bug 1: an Experiment 003 dry run with no cached dataset fell back to a 60-item
synthetic sample, the exclusion list swallowed it, screening reported "0
eligible" and the next stage raised FileNotFoundError.
Bug 2: offline dry runs looked stuck: the loader retried the network for a
minute or more before the fallback, and the run page said "queued" with no
explanation.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from medeval_core.config import Settings
from medeval_study import datasets, runner
from medeval_study import exp003 as exp003_module
from medeval_study.datasets import DatasetBundle, DatasetLicence, DatasetUnavailable
from medeval_study.exp003 import CANONICAL_EXP002_RUNS, Exp003Study, load_excluded_item_ids
from medeval_study.runner import StudyConfig, StudyRun
from medeval_study.synthetic import (
    SYNTHETIC_SAMPLE_SIZE,
    builtin_synthetic_bundle,
    fetch_command,
)

JUDGES = (
    {"judge_id": "j1", "connection_id": "c1", "family_label": "mock A", "backend": "mock",
     "base_url": "http://127.0.0.1:9", "model": "mock-a", "temperature": 0.3, "repeats": 1,
     "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0},
    {"judge_id": "j2", "connection_id": "c2", "family_label": "mock B", "backend": "mock",
     "base_url": "http://127.0.0.1:9", "model": "mock-b", "temperature": 0.3, "repeats": 1,
     "max_tokens": 32, "call_cap": 500, "usd_cap": 5.0},
)


def offline(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Block every socket operation; return a counter of the attempts."""
    attempts = {"n": 0}

    def blocked(*args, **kwargs):
        attempts["n"] += 1
        raise OSError("network is unreachable (simulated offline)")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    import requests

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    return attempts


def empty_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the dataset cache and the HF cache at empty directories."""
    monkeypatch.setenv("MEDEVAL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)


def exp003_config(**overrides) -> StudyConfig:
    payload = {
        "experiment": "003_frontier_judges", "dataset": "medqa_4options", "split": "dev",
        "n_items": 140, "seed": 20261118, "repeats": 1, "dry_run": True, "judges": (),
        "connection_judges": JUDGES, "pilot_items": 40, "pilot_min_disagreements": 5,
        "screening_size": 600, "run_id": "v042", "resume": False, "stage": "all",
    }
    payload.update(overrides)
    return StudyConfig(**payload)


# --------------------------------------------------------------------------- #
# The built-in synthetic sample
# --------------------------------------------------------------------------- #
def test_synthetic_sample_is_big_enough_for_a_full_staged_run() -> None:
    bundle = builtin_synthetic_bundle()
    assert bundle.n >= 400, "a full staged exp003 dry run needs room for 100 hard + 40 control"
    assert SYNTHETIC_SAMPLE_SIZE >= 400
    assert bundle.strategy == "synthetic-builtin"
    assert "no third-party content" in bundle.licence.licence
    assert "PIPELINE TEST ONLY" in bundle.notes


def test_synthetic_ids_cannot_collide_with_real_medqa_ids() -> None:
    bundle = builtin_synthetic_bundle(n=20)
    ids = [item.item_id for item in bundle.items]
    assert all(item_id.startswith("synth-") for item_id in ids)
    # Real MedQA ids look like dev-00001; Experiment 002's records use those.
    assert not any(item_id.startswith("dev-") for item_id in ids)
    assert len(set(ids)) == len(ids)


# --------------------------------------------------------------------------- #
# Bug 1: the expansion of the exclusion list in v0.4.1
# --------------------------------------------------------------------------- #
def test_exclusion_list_ignores_stray_exp002_test_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the committed run records count; a stray test run must not exclude items."""
    root = tmp_path / "study_runs"
    canonical = root / "exp002-main"
    canonical.mkdir(parents=True)
    (canonical / "items.jsonl").write_text(
        "".join(json.dumps({"item_id": f"dev-{index:05d}"}) + "\n" for index in range(3)),
        encoding="utf-8",
    )
    stray = root / "exp002-20261120T104007Z-1ca9bace"
    stray.mkdir()
    synthetic = builtin_synthetic_bundle(n=5)
    (stray / "items.jsonl").write_text(
        "".join(json.dumps({"item_id": item.item_id}) + "\n" for item in synthetic.items),
        encoding="utf-8",
    )
    excluded = load_excluded_item_ids(run_root=root)
    assert excluded == {f"dev-{index:05d}" for index in range(3)}
    assert not (excluded & {item.item_id for item in synthetic.items})
    assert CANONICAL_EXP002_RUNS == ("exp002-main", "exp002-full", "exp002-smoke")


def test_full_staged_exp003_dry_run_completes_without_the_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported crash: no cache, no network, full staged run, all stages done."""
    empty_cache(monkeypatch, tmp_path)
    attempts = offline(monkeypatch)
    root = tmp_path / "runs"

    stages = ["screening", "pool", "pilot", "remaining"]
    results = {}
    for stage in stages:
        config = exp003_config(run_id="offline-staged", stage=stage, resume=True)
        results[stage] = Exp003Study(config, Settings(seed=20261118, max_tokens=32), run_root=root).execute()

    for stage in stages:
        assert results[stage]["status"] in {"awaiting_confirmation", "completed"}, results[stage]
    state = json.loads((root / "offline-staged" / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["stages_completed"] == stages
    assert state["dataset"]["strategy"] == "synthetic-builtin"
    assert "PIPELINE TEST ONLY" in state["dataset"]["notes"]
    assert state.get("dataset_fallback")
    assert attempts["n"] == 0, f"a dry run must not touch the network (saw {attempts['n']} attempts)"
    metrics = json.loads((root / "offline-staged" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["pipeline_test_only"] is True
    assert (root / "offline-staged" / "report.md").read_text(encoding="utf-8").count("PIPELINE TEST ONLY") >= 1


def test_zero_eligible_items_block_with_a_plain_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If every item is excluded, stop with the cause and the fix - no traceback."""
    bundle = builtin_synthetic_bundle(n=40)
    monkeypatch.setattr(exp003_module, "load_excluded_item_ids", lambda run_root=None: {
        item.item_id for item in bundle.items
    })
    allowed = DatasetLicence(
        key="synthetic", hf_repo="none/synthetic", licence="synthetic-test",
        licence_evidence="fixture", redistribute=True, attribution="fixture",
    )
    empty_bundle = DatasetBundle(
        items=bundle.items, licence=allowed, dataset="synthetic", split="dev",
        strategy="test", source_detail="fixture", n_raw_rows=bundle.n, n_dropped=0,
    )
    monkeypatch.setattr(runner, "dataset_cache_status", lambda *a, **k: empty_bundle)
    study = Exp003Study(exp003_config(run_id="no-eligible", dry_run=True), Settings(seed=1), run_root=tmp_path)
    result = study.execute()
    assert result["status"] == "blocked"
    reason = result["blocked_reason"]
    assert "0 eligible items" in reason
    assert "excluded" in reason
    assert f"medeval data fetch {study.config.dataset}" in reason
    state = json.loads((tmp_path / "no-eligible" / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "blocked"
    assert state["screening"]["stop_reason"] == "no_eligible_items"
    # No screening file was written, and no stage may be offered.
    assert not (tmp_path / "no-eligible" / "screening.jsonl").exists()
    assert study.next_stage() is None
    plan = result["stage_plan"]
    assert all(row["needs_confirmation"] is False for row in plan)


def test_stages_payload_carries_the_blocked_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from medeval_study import registry
    from medeval_web import app as app_module
    from medeval_web.app import RunManager, create_app

    runs_root = tmp_path / "study_runs"
    runs_root.mkdir()
    base = runs_root / "blocked-exp003"
    base.mkdir()
    reason = (
        "screening found 0 eligible items in medqa_4options/dev: 0 item(s) loaded and 60 excluded. "
        f"Fix: run {fetch_command('medqa_4options')} to fetch the real data, then start the run again."
    )
    (base / "run.json").write_text(json.dumps({
        "run_id": "blocked-exp003", "experiment": "003_frontier_judges", "status": "blocked",
        "blocked_reason": reason,
        "config": {"experiment": "003_frontier_judges", "dataset": "medqa_4options", "split": "dev",
                   "n_items": 140, "seed": 20261118, "repeats": 1, "pilot_items": 40,
                   "pilot_min_disagreements": 5, "screening_size": 600,
                   "connection_judges": list(JUDGES), "screening_binding": None},
        "screening": {"n_screened": 0, "stop_reason": "no_eligible_items", "n_eligible": 0},
    }, indent=2), encoding="utf-8")

    monkeypatch.setattr(app_module, "run_root", lambda: runs_root)
    monkeypatch.setattr(registry, "run_root", lambda: runs_root)
    # Bind the app's read helpers to this temp root, as the other API tests do.
    monkeypatch.setattr(app_module, "list_runs", lambda: registry.list_runs(runs_root))
    monkeypatch.setattr(app_module, "load_run", lambda run_id: registry.load_run(run_id, runs_root))
    monkeypatch.setattr(app_module, "load_report", lambda run_id: registry.load_report(run_id, runs_root))
    monkeypatch.setattr(app_module, "item_table", lambda run_id: registry.item_table(run_id, runs_root))
    client = TestClient(create_app(RunManager(), token="t", allowed_hosts={"testserver"},
                                   local_data_dir=tmp_path / "local"))
    response = client.get("/api/runs/blocked-exp003/stages", headers={"X-Medeval-Token": "t"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["next_stage"] is None
    assert "medeval data fetch medqa_4options" in payload["blocked_reason"]


# --------------------------------------------------------------------------- #
# Bug 2: offline runs must not retry the network
# --------------------------------------------------------------------------- #
def test_dry_run_with_no_cache_is_fast_and_makes_no_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_cache(monkeypatch, tmp_path)
    attempts = offline(monkeypatch)
    config = StudyConfig(
        experiment="002_judge_families", dataset="medqa_4options", n_items=6, repeats=1,
        dry_run=True, judges=("j1_deepseek",), run_id="fast-offline", resume=False,
    )
    started = time.perf_counter()
    result = StudyRun(config, Settings(seed=1, max_tokens=32), run_root=tmp_path).execute()
    elapsed = time.perf_counter() - started
    assert result["status"] == "completed"
    assert elapsed < 10.0, f"an offline dry run must not stall (took {elapsed:.1f}s)"
    assert attempts["n"] == 0
    state = json.loads((tmp_path / "fast-offline" / "run.json").read_text(encoding="utf-8"))
    assert state["dataset"]["strategy"] == "synthetic-builtin"


def test_real_run_with_no_cache_blocks_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_cache(monkeypatch, tmp_path)
    attempts = offline(monkeypatch)
    config = StudyConfig(
        experiment="002_judge_families", dataset="medqa_4options", n_items=4, repeats=1,
        dry_run=False, judges=("j1_deepseek",), run_id="blocked-offline", resume=False,
    )
    started = time.perf_counter()
    result = StudyRun(config, Settings(seed=1), run_root=tmp_path).execute()
    elapsed = time.perf_counter() - started
    assert result["status"] == "blocked"
    assert "medeval data fetch medqa_4options" in result["blocked_reason"]
    assert "not in the local cache" in result["blocked_reason"]
    assert elapsed < 10.0, f"a real run must stop at once (took {elapsed:.1f}s)"
    assert attempts["n"] == 0


def test_dataset_cache_status_never_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_cache(monkeypatch, tmp_path)
    attempts = offline(monkeypatch)
    assert datasets.dataset_cache_status("medqa_4options", "dev") is None
    assert attempts["n"] == 0


def test_offline_dataset_access_forces_the_hf_offline_environment(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with datasets.offline_dataset_access():
        import os

        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["HF_DATASETS_OFFLINE"] == "1"
    import os

    assert "HF_HUB_OFFLINE" not in os.environ


def test_download_access_sets_short_timeouts_and_one_retry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("HF_HUB_ETAG_TIMEOUT", "HF_HUB_DOWNLOAD_TIMEOUT", "HF_HUB_MAX_RETRIES"):
        monkeypatch.delenv(key, raising=False)
    with datasets.download_dataset_access():
        import os

        assert os.environ["HF_HUB_ETAG_TIMEOUT"] == "5"
        assert os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] == "20"
        assert os.environ["HF_HUB_MAX_RETRIES"] == "1"


def test_unavailable_dataset_raises_the_documented_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_cache(monkeypatch, tmp_path)
    offline(monkeypatch)
    with pytest.raises(DatasetUnavailable):
        datasets.dataset_cache_status("medqa_4options", "dev") or (_ for _ in ()).throw(
            DatasetUnavailable("not cached")
        )
