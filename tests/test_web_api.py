"""API tests for the local web app.

The app is a thin read layer over the run directory, so most tests use a fabricated
run on disk rather than executing a study. The one test that does start a run uses
a stub in place of :class:`StudyRun`, which keeps the test offline and fast while
still exercising the real route, the real manager and the real thread handling.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medeval_web import app as app_module
from medeval_web.app import RunManager, create_app


def write_fake_run(root: Path, run_id: str = "fake-1", status: str = "completed") -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "experiment": "002_judge_families",
                "status": status,
                "started_utc": "2026-11-17T10:00:00Z",
                "finished_utc": "2026-11-17T10:05:00Z",
                "config": {"n_items": 2, "repeats": 3, "dry_run": True},
                "plan": {"total_calls": 12},
                "usage": {"calls_made": 0, "calls_logged": 12, "cache_hits": 0, "estimated_usd": None, "price_source": "test"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "pipeline_test_only": True,
                "n_items": 2,
                "judges": {
                    "j1_mock": {
                        "spec": {"judge_id": "j1_mock", "model": "mock"},
                        "primary_repeat": {
                            "accuracy": {"estimate": 0.5, "numerator": 1, "denominator": 2, "ci95_wilson": [0.09, 0.91]},
                            "cohen_kappa_vs_key": 0.0,
                            "cohen_kappa_vs_key_ci95": None,
                            "false_accept_rate": {"estimate": 0.0, "numerator": 0, "denominator": 1, "ci95_wilson": [0.0, 0.79]},
                            "false_reject_rate": {"estimate": 1.0, "numerator": 1, "denominator": 1, "ci95_wilson": [0.21, 1.0]},
                            "calibration": {
                                "ece_5_bins": 0.1,
                                "ece_5_bins_ci95": None,
                                "brier": 0.2,
                                "brier_ci95": None,
                                "auroc_confidence_predicts_own_correctness": 0.5,
                                "auroc_confidence_predicts_own_correctness_ci95": None,
                                "reliability_bins": [{"low": 0.8, "high": 1.0, "n": 2, "mean_confidence": 0.9, "observed_accuracy": 0.5}],
                            },
                            "n_parse_failures": 0,
                            "n_api_errors": 0,
                            "n_scored": 2,
                            "n_items": 2,
                        },
                        "across_repeats": {"consistent_fraction": 1.0, "n_items_all_repeats": 2},
                    }
                },
                "cross_judge": {},
                "possible_key_errors": {"rule": {"applied": "single_judge_fallback"}, "n_flagged_possible_key_errors": 0},
                "benchmark_health": {"n_items": 2, "key_letter_distribution": {"A": 1, "B": 1}, "ceiling_per_judge": {}},
            }
        ),
        encoding="utf-8",
    )
    items = [
        {"item_id": "i1", "question": "First synthetic question?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "key_letter": "A", "key_text": "a", "condition": "correct"},
        {"item_id": "i2", "question": "Second synthetic question?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "key_letter": "A", "key_text": "a", "condition": "incorrect"},
    ]
    (run_dir / "items.jsonl").write_text("".join(json.dumps(row) + "\n" for row in items), encoding="utf-8")
    candidates = [
        {"item_id": "i1", "chosen_letter": "A", "chosen_text": "a", "text": "Answer A", "condition": "correct", "template_id": "t1"},
        {"item_id": "i2", "chosen_letter": "B", "chosen_text": "b", "text": "Answer B", "condition": "incorrect", "template_id": "t1"},
    ]
    (run_dir / "candidates.jsonl").write_text("".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8")
    judgements = [
        {"item_id": "i1", "judge_id": "j1_mock", "repeat": repeat, "verdict": "CORRECT", "confidence": 90, "parse_ok": True}
        for repeat in range(3)
    ]
    (run_dir / "judgements.jsonl").write_text("".join(json.dumps(row) + "\n" for row in judgements), encoding="utf-8")
    (run_dir / "report.md").write_text("# report\n\n## Limitations\n", encoding="utf-8")
    return run_dir


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "study_runs"
    root.mkdir()
    write_fake_run(root)
    monkeypatch.setattr("medeval_study.registry.run_root", lambda: root)
    monkeypatch.setattr(app_module, "run_root", lambda: root)
    # A run started through the API builds a StudyRun, which resolves its own
    # default root; without this patch the test would write real run
    # directories into results/study_runs.
    from medeval_study import runner as runner_module

    monkeypatch.setattr(runner_module, "default_run_root", lambda: root)
    monkeypatch.setattr(app_module, "list_runs", lambda: __import__("medeval_study.registry", fromlist=["list_runs"]).list_runs(root))
    monkeypatch.setattr(app_module, "load_run", lambda run_id: __import__("medeval_study.registry", fromlist=["load_run"]).load_run(run_id, root))
    monkeypatch.setattr(app_module, "item_table", lambda run_id: __import__("medeval_study.registry", fromlist=["item_table"]).item_table(run_id, root))
    monkeypatch.setattr(app_module, "load_report", lambda run_id: __import__("medeval_study.registry", fromlist=["load_report"]).load_report(run_id, root))
    return TestClient(create_app(RunManager(), local_data_dir=tmp_path / "local-data"))


def test_health(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["version"]


def test_index_and_assets_are_served_locally(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    for asset in ("/static/app.js", "/static/styles.css", "/favicon.svg"):
        assert client.get(asset).status_code == 200


def test_shell_and_api_are_never_cached_by_the_browser(client: TestClient) -> None:
    """A cached app.js masked a real fix once; nothing local is cacheable."""
    for path in ("/", "/static/app.js", "/static/styles.css", "/favicon.svg", "/api/health"):
        response = client.get(path)
        assert "no-store" in response.headers.get("cache-control", ""), path
        assert response.headers.get("x-content-type-options") == "nosniff", path
        assert response.headers.get("referrer-policy") == "no-referrer", path
        assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", ""), path


def test_frontend_makes_no_external_requests(client: TestClient) -> None:
    """No CDN calls at runtime: the only absolute URL allowed is the SVG namespace."""
    for asset in ("app.js", "styles.css", "index.html"):
        text = (Path(app_module.STATIC_DIR) / asset).read_text(encoding="utf-8")
        text = text.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in text and "https://" not in text, f"{asset} references a remote URL"
        assert "cdn." not in text and "googleapis" not in text


def test_hidden_attribute_always_wins_over_layout_rules(client: TestClient) -> None:
    """Regression: the command palette was permanently on screen.

    `.palette-backdrop { display: flex }` is an author rule, so it overrides the
    user-agent `[hidden] { display: none }`; the overlay stayed painted while
    ``palette.hidden`` was still ``true``, which also made the Esc handler believe
    the palette was already closed. The first rule in the stylesheet must keep the
    attribute authoritative.
    """
    css = (Path(app_module.STATIC_DIR) / "styles.css").read_text(encoding="utf-8")
    assert "[hidden]" in css and "display: none !important" in css
    first_rule = css.split("}", 1)[0]
    assert "[hidden]" in first_rule, "the [hidden] rule must come first so nothing can outrank it"
    assert css.index("[hidden]") < css.index(".palette-backdrop")
    assert 'palette.hidden = false' in (Path(app_module.STATIC_DIR) / "app.js").read_text(encoding="utf-8")


def test_narrow_viewport_overflow_guards_exist(client: TestClient) -> None:
    """Regression: the Results page scrolled sideways at about 390px width.

    Grid children default to ``min-width: auto``, so one wide table or number
    forced the whole page sideways. Tables must scroll inside their own
    container, and grid children must be allowed to shrink.
    """
    css = (Path(app_module.STATIC_DIR) / "styles.css").read_text(encoding="utf-8")
    assert ".grid > * { min-width: 0; }" in css
    assert ".table-wrap { overflow-x: auto;" in css
    assert ".matrix-wrap { overflow-x: auto;" in css
    js = (Path(app_module.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert 'class: "matrix-wrap"' in js, "the confusion matrix must sit inside a scroll container"


def test_chords_are_implemented_and_ignored_while_typing(client: TestClient) -> None:
    """The palette advertises g r / g n / g s / t; the chords must actually work."""
    js = (Path(app_module.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert "pendingChordPrefix" in js
    for key in ("r", "n", "s"):
        assert f'{key}: () => {{ location.hash = "#/' in js
    assert 'typingTarget' in js
    assert '"input" || tag === "textarea" || tag === "select"' in js


def test_theme_control_has_three_states(client: TestClient) -> None:
    html = (Path(app_module.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    for theme in ("auto", "light", "dark"):
        assert f'data-theme-choice="{theme}"' in html
    js = (Path(app_module.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert 'aria-pressed' in js, "the active theme state must be visible"


def test_runs_list_contains_the_run(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert [row["run_id"] for row in runs] == ["fake-1"]
    assert runs[0]["status"] == "completed"
    assert runs[0]["pipeline_test_only"] is True


def test_an_aborted_run_never_shows_a_headline_number(client: TestClient, tmp_path: Path) -> None:
    """A run that was aborted must not advertise accuracy, however good it looks."""
    root = tmp_path / "study_runs"
    run_dir = write_fake_run(root, run_id="fake-aborted", status="aborted")
    state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    state["started_utc"] = "2026-11-17T09:00:00Z"
    state["judges"] = [
        {"judge_id": "j1_deepseek", "available": True},
        {"judge_id": "j2_ollama", "available": False},
    ]
    (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")

    runs = {row["run_id"]: row for row in client.get("/api/runs").json()}
    aborted = runs["fake-aborted"]
    assert aborted["status"] == "aborted"
    assert aborted["headline"] == {}, "an aborted run has no reportable result"
    assert aborted["judges"] == ["j1_deepseek"]
    assert aborted["unavailable_judges"] == ["j2_ollama"]


def test_run_detail_and_404(client: TestClient) -> None:
    payload = client.get("/api/runs/fake-1").json()
    assert payload["run"]["run_id"] == "fake-1"
    assert payload["metrics"]["judges"]["j1_mock"]["primary_repeat"]["accuracy"]["estimate"] == 0.5
    assert client.get("/api/runs/nope").status_code == 404


def test_item_explorer_rows_include_local_question_text(client: TestClient) -> None:
    rows = client.get("/api/runs/fake-1/items").json()
    assert len(rows) == 2
    assert rows[0]["question"].startswith("First synthetic")
    assert rows[0]["judges"]["j1_mock"]["verdict"] == "CORRECT"
    assert rows[0]["candidate_correct"] is True


def test_report_is_plain_text(client: TestClient) -> None:
    response = client.get("/api/runs/fake-1/report")
    assert response.status_code == 200
    assert "Limitations" in response.text


def test_settings_never_exposes_a_key(client: TestClient) -> None:
    payload = client.get("/api/settings").json()
    # v0.5.1: there is no built-in provider to report, so the settings payload
    # carries connection counts instead of a named provider/model status.
    assert "judges" not in payload
    assert payload["connections"]["saved"] == 0
    assert payload["connections"]["judges_assigned"] == 0
    assert payload["connections"]["keys_in_process_memory_only"] is True
    text = json.dumps(payload).lower()
    assert "sk-" not in text
    assert "medeval_api_key" not in text
    assert payload.get("datasets")


def test_stream_closes_for_a_finished_run(client: TestClient) -> None:
    response = client.get("/api/runs/fake-1/events")
    assert response.status_code == 200
    assert "event: snapshot" in response.text
    assert "event: closed" in response.text


def test_starting_a_live_run_requires_confirmation(client: TestClient) -> None:
    response = client.post("/api/runs", json={"dry_run": False, "n_items": 2})
    assert response.status_code == 400
    assert "confirm_real_calls" in response.json()["detail"]


def test_stop_on_an_unknown_run_is_rejected(client: TestClient) -> None:
    response = client.post("/api/runs/fake-1/stop")
    assert response.status_code == 409


def test_starting_a_dry_run_through_the_api(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "study_runs"

    class StubRun:
        def __init__(self, config, settings, stop_flag=None):
            self.config = config
            self.run_id = config.run_id or "stub-1"
            self.run_dir = root / self.run_id

        def execute(self) -> dict:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / "run.json").write_text(
                json.dumps({"run_id": self.run_id, "status": "completed", "config": self.config.as_dict()}),
                encoding="utf-8",
            )
            return {"status": "completed"}

    monkeypatch.setattr(app_module, "StudyRun", StubRun)
    response = client.post("/api/runs", json={"dry_run": True, "n_items": 2, "repeats": 1})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert run_id

    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        state_path = root / run_id / "run.json"
        if state_path.is_file():
            status = json.loads(state_path.read_text(encoding="utf-8")).get("status")
            if status == "completed":
                break
        time.sleep(0.05)
    assert status == "completed"
