"""Regression tests for the v0.4.1 bug fixes (backend side).

Each test names the bug it pins, so a regression is obvious from the test name.
The browser-side fixes (duplicate page, run page, key width, live role warnings)
are pinned in ``tests/browser/test_v041_fixes.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medeval_core.config import Settings
from medeval_study import datasets, runner
from medeval_study.datasets import DatasetUnavailable
from medeval_study.runner import StudyConfig, StudyRun
from medeval_study.synthetic import (
    SYNTHETIC_DATASET_KEY,
    builtin_synthetic_bundle,
    fetch_command,
)
from medeval_web import app as app_module
from medeval_web.app import RunManager, create_app

TOKEN = "fake-token-for-v041"
AUTH = {"X-Medeval-Token": TOKEN}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    runs_root = tmp_path / "study_runs"
    runs_root.mkdir()
    monkeypatch.setattr(app_module, "run_root", lambda: runs_root)
    monkeypatch.setattr("medeval_study.registry.run_root", lambda: runs_root)
    monkeypatch.setattr(runner, "default_run_root", lambda: runs_root)
    literature = tmp_path / "literature.csv"
    literature.write_text(
        "id,doi,title,year,task,reference_standard,metric_proposed,how_we_operationalize,limitations,verified\n",
        encoding="utf-8",
    )
    return TestClient(
        create_app(
            RunManager(),
            token=TOKEN,
            allowed_hosts={"testserver"},
            local_data_dir=tmp_path / "local-data",
            connections_path=tmp_path / "local-data" / "connections.json",
            literature_path=literature,
            settings=Settings(seed=20260101, max_tokens=32),
        )
    )


def authed(client: TestClient, method: str, path: str, **kwargs):
    return client.request(method, path, headers={**AUTH, **(kwargs.pop("headers", {}) or {})}, **kwargs)


def connection_payload(name: str, **overrides) -> dict:
    payload = {
        "name": name, "provider_preset": "custom", "base_url": "http://127.0.0.1:9/v1",
        "api_style": "chat", "model": "fake-model", "family_label": f"{name} family",
        "key": f"sk-fake-{name}", "per_connection_call_cap": 10, "per_connection_usd_cap": 1.0,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Bug 4: duplicate connection names
# --------------------------------------------------------------------------- #
def test_duplicate_connection_name_is_rejected_with_a_clear_message(client: TestClient) -> None:
    first = authed(client, "POST", "/api/connections", json=connection_payload("judge-a"))
    assert first.status_code == 201, first.text
    duplicate = authed(client, "POST", "/api/connections", json=connection_payload("judge-a"))
    assert duplicate.status_code == 409
    detail = duplicate.json()["detail"]
    assert "already exists" in detail and "judge-a" in detail
    # A different name still works.
    assert authed(client, "POST", "/api/connections", json=connection_payload("judge-b")).status_code == 201


def test_duplicate_name_check_is_case_and_space_insensitive(client: TestClient) -> None:
    assert authed(client, "POST", "/api/connections", json=connection_payload("Judge A")).status_code == 201
    clash = authed(client, "POST", "/api/connections", json=connection_payload("  judge a  "))
    assert clash.status_code == 409
    assert "already exists" in clash.json()["detail"]


def test_rename_onto_an_existing_name_is_rejected(client: TestClient) -> None:
    authed(client, "POST", "/api/connections", json=connection_payload("one"))
    second = authed(client, "POST", "/api/connections", json=connection_payload("two")).json()
    clash = authed(client, "PUT", f"/api/connections/{second['id']}", json={"name": "one"})
    assert clash.status_code == 400
    assert "already exists" in clash.json()["detail"]
    # Renaming to its own name (a no-op save) is not a conflict.
    same = authed(client, "PUT", f"/api/connections/{second['id']}", json={"name": "two"})
    assert same.status_code == 200, same.text


# --------------------------------------------------------------------------- #
# Bug 6: dry run without the dataset
# --------------------------------------------------------------------------- #
def test_synthetic_bundle_is_labelled_and_valid() -> None:
    bundle = builtin_synthetic_bundle(n=8, seed=3)
    assert bundle.n == 8
    assert bundle.strategy == "synthetic-builtin"
    assert SYNTHETIC_DATASET_KEY == "synthetic_builtin"
    assert "PIPELINE TEST ONLY" in bundle.notes
    assert all(len(item.options) == 4 and item.key_letter in item.options for item in bundle.items)


def test_dry_run_falls_back_to_the_synthetic_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args, **kwargs):
        raise DatasetUnavailable("no datasets package and no network in this test")

    monkeypatch.setattr(runner, "dataset_cache_status", lambda *args, **kwargs: None)
    config = StudyConfig(
        experiment="002_judge_families", dataset="medqa_4options", n_items=6, repeats=1,
        dry_run=True, judges=("j1_deepseek",), run_id="synthetic-fallback", resume=False,
    )
    study = StudyRun(config, Settings(seed=20260101, max_tokens=32), run_root=tmp_path)
    result = study.execute()
    assert result["status"] == "completed", result
    state = json.loads((tmp_path / "synthetic-fallback" / "run.json").read_text(encoding="utf-8"))
    assert state["dataset"]["strategy"] == "synthetic-builtin"
    assert "PIPELINE TEST ONLY" in state["dataset"]["notes"]
    assert state["dataset_fallback"]
    metrics = json.loads((tmp_path / "synthetic-fallback" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["pipeline_test_only"] is True
    assert metrics.get("dataset_fallback")
    report = (tmp_path / "synthetic-fallback" / "report.md").read_text(encoding="utf-8")
    assert "PIPELINE TEST ONLY" in report


def test_real_run_without_the_dataset_is_blocked_with_the_fetch_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args, **kwargs):
        raise DatasetUnavailable("no datasets package in this test")

    monkeypatch.setattr(runner, "dataset_cache_status", lambda *args, **kwargs: None)
    config = StudyConfig(
        experiment="002_judge_families", dataset="medqa_4options", n_items=4, repeats=1,
        dry_run=False, judges=("j1_deepseek",), run_id="blocked-no-data", resume=False,
    )
    result = StudyRun(config, Settings(seed=20260101), run_root=tmp_path).execute()
    assert result["status"] == "blocked"
    reason = result["blocked_reason"]
    assert "medeval data fetch medqa_4options" in reason
    assert "dry run works without the data" in reason
    state = json.loads((tmp_path / "blocked-no-data" / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "blocked"
    assert state["blocked_reason"] == reason


def test_blocked_reason_is_what_the_run_page_shows(tmp_path: Path) -> None:
    """The run page reads ``blocked_reason``; the command must survive the round-trip."""
    from medeval_study.registry import load_run

    base = tmp_path / "study_runs" / "blocked-run"
    base.mkdir(parents=True)
    reason = (
        "the dataset could not be read. Fetch it once with: "
        f"{fetch_command('medqa_4options')} - then start the run again."
    )
    (base / "run.json").write_text(
        json.dumps({"run_id": "blocked-run", "experiment": "003_frontier_judges",
                    "status": "blocked", "blocked_reason": reason}), encoding="utf-8",
    )
    payload = load_run("blocked-run", tmp_path / "study_runs")
    assert payload is not None
    assert "medeval data fetch medqa_4options" in payload["run"]["blocked_reason"]


def test_fetch_command_is_powershell_and_names_the_dataset() -> None:
    command = fetch_command("medqa_4options")
    assert command.startswith(".\\")
    assert "-m medeval data fetch medqa_4options" in command


def test_cli_data_status_lists_datasets_and_licences(capsys: pytest.CaptureFixture[str]) -> None:
    from medeval.cli import cmd_data_status

    assert cmd_data_status(None) == 0
    output = capsys.readouterr().out
    for key in ("medmcqa", "medqa_4options", "pubmedqa", SYNTHETIC_DATASET_KEY):
        assert key in output
    assert "cc-by-sa-4.0" in output
    assert "synthetic" in output


def test_cli_data_fetch_reports_a_missing_loader_clearly(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace

    from medeval.cli import cmd_data_fetch

    def unavailable(*args, **kwargs):
        raise DatasetUnavailable("No module named 'datasets'")

    monkeypatch.setattr(datasets, "load_mcq_items", unavailable)
    code = cmd_data_fetch(Namespace(dataset="medqa", split="dev"))
    output = capsys.readouterr().out
    assert code == 1
    assert "pip install datasets" in output
    assert "Dry runs do not need the data" in output


# --------------------------------------------------------------------------- #
# Bug 2 (server side): a queued run records its start time
# --------------------------------------------------------------------------- #
def test_queued_run_has_a_start_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run queued through the manager records ``started_utc`` immediately."""
    import medeval_web.app as web_app

    runs_root = tmp_path / "study_runs"
    runs_root.mkdir()
    monkeypatch.setattr(runner, "default_run_root", lambda: runs_root)
    manager = RunManager()
    config = StudyConfig(
        experiment="002_judge_families", dataset="medqa_4options", n_items=2, repeats=1,
        dry_run=True, judges=("j1_deepseek",), run_id="queued-run", resume=False,
    )
    manager.start(config, Settings(seed=20260101))
    state = json.loads((runs_root / "queued-run" / "run.json").read_text(encoding="utf-8"))
    assert state.get("started_utc"), "a queued run must carry started_utc so the page can show it"
    assert state["started_utc"].endswith("Z")
    # Let the worker finish so it does not leak into another test.
    deadline = __import__("time").time() + 30
    while manager.is_active("queued-run") and __import__("time").time() < deadline:
        __import__("time").sleep(0.1)
    assert web_app.TERMINAL_STATUSES  # keep the import meaningful
