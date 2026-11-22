"""v0.4 API surface: staged Experiment 003, review queue, export, preferences.

These tests drive the HTTP layer only, with a hand-built run directory, so they
never load a dataset and never touch a provider.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medeval_web import app as app_module
from medeval_web.app import RunManager, create_app

TOKEN = "tok-v04"
AUTH = {"X-Medeval-Token": TOKEN}


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "study_runs"
    root.mkdir()

    def _run_root() -> Path:
        return root

    monkeypatch.setattr(app_module, "run_root", _run_root)
    monkeypatch.setattr("medeval_study.registry.run_root", _run_root)
    from medeval_study import registry

    monkeypatch.setattr(app_module, "list_runs", lambda: registry.list_runs(root))
    monkeypatch.setattr(app_module, "load_run", lambda run_id: registry.load_run(run_id, root))
    monkeypatch.setattr(app_module, "load_report", lambda run_id: registry.load_report(run_id, root))
    monkeypatch.setattr(app_module, "item_table", lambda run_id: registry.item_table(run_id, root))
    # export.py resolves the run directory through medeval_study.registry.run_root as well
    monkeypatch.setattr("medeval_web.export.run_root", _run_root)
    return root


def make_exp003_run(root: Path, run_id: str = "exp003-test", *, with_config: bool = True) -> Path:
    base = root / run_id
    base.mkdir(parents=True)
    config = {
        "experiment": "003_frontier_judges", "dataset": "synthetic", "split": "dev", "n_items": 140,
        "seed": 20261118, "repeats": 1, "temperature": 0.3, "primary_repeat": 0, "max_api_calls": 500,
        "allow_download": False, "dry_run": False, "judges": [],
        "connection_judges": [
            {"judge_id": "judge_one", "connection_id": "conn-1", "family_label": "A", "model": "m1"},
            {"judge_id": "judge_two", "connection_id": "conn-2", "family_label": "B", "model": "m2"},
        ],
        "consistency_subsample": 0, "pilot_items": 40, "pilot_min_disagreements": 5,
        "screening_size": 600,
        "screening_binding": {"connection_id": "conn-screen", "family_label": "S", "model": "ms"},
        "pace_seconds": 0.0, "stage": "screening",
    }
    if not with_config:
        config = {}
    state = {
        "run_id": run_id, "experiment": "003_frontier_judges", "status": "awaiting_confirmation",
        "started_utc": "2026-11-19T10:00:00Z", "config": config,
        "stages_completed": ["screening"], "stage": "screening",
        "screening": {
            "n_screened": 150, "n_correct": 50, "n_wrong": 100, "stop_reason": "target_met",
            "mock": False, "hard_target": 100, "cap": 1000,
        },
    }
    (base / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (base / "metrics.json").write_text(json.dumps({"experiment": "003_frontier_judges", "judges": {}}, indent=2), encoding="utf-8")
    (base / "items.csv").write_text("item_id,judge_id,verdict,confidence\ndev-1,judge_one,CORRECT,95\n", encoding="utf-8")
    (base / "items.jsonl").write_text(
        "\n".join(json.dumps({"item_id": "dev-1", "question": "", "options": {}, "key_letter": "A"})
                  for _ in range(1)) + "\n",
        encoding="utf-8",
    )
    (base / "report.md").write_text("# Report\n\nDry-run mock pipeline test.\n", encoding="utf-8")
    (base / "judgements.jsonl").write_text(
        json.dumps({"item_id": "dev-1", "judge_id": "judge_one", "repeat": 0, "verdict": "CORRECT",
                    "confidence": 95, "parse_ok": True}) + "\n",
        encoding="utf-8",
    )
    manifest = {"run_id": run_id, "experiment": "003_frontier_judges", "versions": {}, "extra": {}}
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (base / "screening.jsonl").write_text(
        "\n".join(json.dumps({"item_id": f"dev-{index}", "correct": index >= 100})
                  for index in range(150)) + "\n",
        encoding="utf-8",
    )
    return base


@pytest.fixture()
def client(tmp_path: Path, runs_root: Path) -> TestClient:
    # A real (non-dry) run keeps its committed screening record under
    # results/experiment_003/; point that at the temp run directory instead so
    # the tests never touch the repository's results.
    from medeval_study.exp003 import Exp003Study

    original = Exp003Study.screening_path
    Exp003Study.screening_path = lambda self: self.run_dir / "screening.jsonl"
    try:
        yield TestClient(
            create_app(
                RunManager(),
                token=TOKEN,
                allowed_hosts={"testserver"},
                local_data_dir=tmp_path / "local-data",
                connections_path=tmp_path / "connections.json",
                literature_path=tmp_path / "literature.csv",
            )
        )
    finally:
        Exp003Study.screening_path = original


def authed(client: TestClient, method: str, path: str, **kwargs):
    headers = {**AUTH, **(kwargs.pop("headers", {}) or {})}
    return client.request(method, path, headers=headers, **kwargs)


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #
def test_stage_plan_lists_the_four_stages_with_costs(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    payload = authed(client, "GET", "/api/runs/exp003-test/stages").json()
    names = [row["name"] for row in payload["stages"]]
    assert names == ["screening", "pool", "pilot", "remaining"]
    assert payload["next_stage"] == "pool"
    assert payload["status"] == "awaiting_confirmation"
    by_name = {row["name"]: row for row in payload["stages"]}
    assert by_name["screening"]["status"] == "done"
    assert by_name["pilot"]["worst_case_calls"] == 40 * 2
    assert by_name["pilot"]["needs_confirmation"] is True
    assert by_name["screening"]["detail"].startswith("150 screened")
    assert payload["screening"]["stop_reason"] == "target_met"


def test_stages_endpoint_rejects_a_non_exp003_run(client: TestClient, runs_root: Path) -> None:
    base = runs_root / "exp002-other"
    base.mkdir()
    (base / "run.json").write_text(
        json.dumps({"run_id": "exp002-other", "experiment": "002_judge_families", "status": "completed",
                    "config": {"experiment": "002_judge_families"}}),
        encoding="utf-8",
    )
    response = authed(client, "GET", "/api/runs/exp002-other/stages")
    assert response.status_code == 400


def test_advance_requires_explicit_confirmation(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    response = authed(client, "POST", "/api/runs/exp003-test/advance", json={"stage": "pool"})
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]


def test_advance_refuses_when_keys_are_no_longer_in_memory(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    response = authed(client, "POST", "/api/runs/exp003-test/advance", json={"stage": "pool", "confirm": True})
    assert response.status_code == 409
    assert "re-enter" in response.json()["detail"]


def test_advance_rejects_an_unknown_stage(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    response = authed(client, "POST", "/api/runs/exp003-test/advance", json={"stage": "everything", "confirm": True})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Review queue
# --------------------------------------------------------------------------- #
def test_review_queue_offers_the_four_labels(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    payload = authed(client, "GET", "/api/runs/exp003-test/review").json()
    assert payload["labels"] == ["key looks correct", "key looks wrong", "ambiguous", "unsure"]
    assert payload["sensitivity_label"] == "key looks wrong"
    assert "one reviewer's opinion" in payload["caveat"]
    assert payload["items"] and payload["items"][0]["item_id"] == "dev-1"


def test_review_label_roundtrip_and_validation(client: TestClient, runs_root: Path, tmp_path: Path) -> None:
    make_exp003_run(runs_root)
    bad = authed(client, "POST", "/api/runs/exp003-test/review",
                 json={"item_id": "dev-1", "label": "definitely right"})
    assert bad.status_code == 400
    saved = authed(client, "POST", "/api/runs/exp003-test/review",
                   json={"item_id": "dev-1", "label": "key looks wrong", "note": "the key names the wrong option"})
    assert saved.status_code == 200
    assert saved.json()["label"] == "key looks wrong"
    payload = authed(client, "GET", "/api/runs/exp003-test/review").json()
    assert payload["items"][0]["label"] == "key looks wrong"
    file = tmp_path / "local-data" / "review_notes.json"
    assert file.is_file() and "the key names the wrong option" in file.read_text(encoding="utf-8")
    # clearing works
    cleared = authed(client, "POST", "/api/runs/exp003-test/review", json={"item_id": "dev-1", "label": ""})
    assert cleared.json()["label"] == ""


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_export_bundle_has_the_expected_files_and_no_notes(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    response = authed(client, "POST", "/api/runs/exp003-test/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    bundle = Path(runs_root.parent / "bundle.zip")
    bundle.write_bytes(response.content)
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {"metrics.json", "items.csv", "report.md", "manifest.json", "README.txt"} <= names
        assert "review_notes.json" not in names
        readme = archive.read("README.txt").decode("utf-8")
    assert "no benchmark item text" in readme
    assert "Reviewer notes are NOT included" in readme


def test_export_includes_notes_only_when_asked(client: TestClient, runs_root: Path) -> None:
    make_exp003_run(runs_root)
    authed(client, "POST", "/api/runs/exp003-test/review",
           json={"item_id": "dev-1", "label": "ambiguous", "note": "local impression"})
    response = authed(client, "POST", "/api/runs/exp003-test/export?include_notes=true")
    assert response.status_code == 200
    bundle = Path(runs_root.parent / "bundle-notes.zip")
    bundle.write_bytes(response.content)
    with zipfile.ZipFile(bundle) as archive:
        assert "review_notes.json" in archive.namelist()
        assert "may quote item text" in archive.read("README.txt").decode("utf-8")


def test_export_refuses_text_in_a_bundle_file(client: TestClient, runs_root: Path) -> None:
    base = make_exp003_run(runs_root, run_id="exp003-leaky")
    long_question = (
        "A 45-year-old man presents to the emergency department with crushing substernal chest pain "
        "radiating to the left arm, diaphoresis and nausea that began two hours ago after exertion."
    )
    (base / "metrics.json").write_text(
        json.dumps({"judges": {}, "question": long_question}),
        encoding="utf-8",
    )
    response = authed(client, "POST", "/api/runs/exp003-leaky/export")
    assert response.status_code == 409
    assert "refused" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Preferences (first-run card)
# --------------------------------------------------------------------------- #
def test_first_run_preferences_roundtrip(client: TestClient) -> None:
    start = authed(client, "GET", "/api/preferences").json()
    assert start["first_run_dismissed"] is False
    authed(client, "POST", "/api/preferences", json={"first_run_dismissed": True})
    assert authed(client, "GET", "/api/preferences").json()["first_run_dismissed"] is True
    bad = authed(client, "POST", "/api/preferences", json={"api_key": "nope"})
    assert bad.status_code == 400
