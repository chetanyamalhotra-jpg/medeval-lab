"""Regression tests for the v0.5.1 fixes.

1. **No preselected provider** on the Models page: the preset dropdown starts on
   an empty "Choose a provider" option, Save stays disabled until a preset is
   chosen, and choosing one fills the base URL only (never a model or a price).
   The behaviour is driven in the browser suite; the static assertions here keep
   the markup honest even where a browser is unavailable.
2. **The archived Experiment 002 path no longer resolves a provider for new
   runs.** The ids ``j1_deepseek``/``j2_ollama`` are readable as the original
   run's configuration (and usable for its dry run), but a live run without
   connections stops with the "add a connection" message instead of falling back
   to a provider, model or environment variable.
3. **The "not a medical device" notice** exists in the app shell footer, on the
   start screen, in the first-run card, and in the header of every generated
   report (and therefore in an exported bundle).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medeval_core.config import Settings
from medeval_study import runner as runner_module
from medeval_study.registry import load_report, load_run
from medeval_study.report import MEDICAL_NOTICE
from medeval_study.runner import ARCHIVED_EXP002_JUDGE_IDS, StudyConfig, StudyRun, build_judges
from medeval_study.synthetic import builtin_synthetic_bundle
from tests.support import ROOT

FAKE_CONNECTIONS = (
    {
        "judge_id": "judge_one", "connection_id": "fake-1", "family_label": "fake family A",
        "backend": "mock", "base_url": "http://127.0.0.1:9", "model": "fake-model-a",
        "temperature": 0.3, "repeats": 1, "max_tokens": 32, "call_cap": 100, "usd_cap": 1.0,
    },
    {
        "judge_id": "judge_two", "connection_id": "fake-2", "family_label": "fake family B",
        "backend": "mock", "base_url": "http://127.0.0.1:9", "model": "fake-model-b",
        "temperature": 0.3, "repeats": 1, "max_tokens": 32, "call_cap": 100, "usd_cap": 1.0,
    },
)

#: Fake connections (no network: the transport is the labelled mock stand-in).
ARCHIVED_RUN_DIR = ROOT / "results" / "study_runs" / "exp002-main"


@pytest.fixture()
def cached_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the dataset appear cached so a run reaches the judge layer."""
    monkeypatch.setattr(
        runner_module,
        "dataset_cache_status",
        lambda *args, **kwargs: builtin_synthetic_bundle(n=12),
    )


def config(**overrides: object) -> StudyConfig:
    payload: dict[str, object] = {
        "experiment": "002_judge_families",
        "dataset": "medqa_4options",
        "split": "dev",
        "n_items": 4,
        "repeats": 1,
        "dry_run": False,
        "judges": (),
        "connection_judges": (),
        "run_id": "v051",
        "resume": False,
    }
    payload.update(overrides)
    return StudyConfig(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. No preselected provider in the Models form
# --------------------------------------------------------------------------- #
def test_models_form_has_no_preselected_provider() -> None:
    app_js = (ROOT / "packages" / "medeval_web" / "static" / "app.js").read_text(encoding="utf-8")
    assert '"Choose a provider"' in app_js
    assert 'el("option", { value: "", selected: true }, "Choose a provider")' in app_js
    # Save starts disabled and is only enabled by choosing a preset.
    assert '"data-testid": "save-connection", disabled: true' in app_js
    # A preset fills the base URL; the model/price fields stay the owner's choice.
    preset_handler = app_js.split("function applyPreset()", 1)[1].split("const presetSelect", 1)[0]
    assert "baseUrlInput.value = preset.base_url" in preset_handler
    assert "modelInput.value" not in preset_handler
    assert "priceIn.value" not in preset_handler
    assert "priceOut.value" not in preset_handler
    # The app no longer reads a built-in provider status from /api/settings.
    assert "settings.judges" not in app_js


# --------------------------------------------------------------------------- #
# 2. The archived Experiment 002 path is read-only for new runs
# --------------------------------------------------------------------------- #
def test_live_run_without_connections_stops_with_the_add_a_connection_message(
    cached_dataset: None, tmp_path: Path
) -> None:
    result = StudyRun(config(), Settings(seed=1), run_root=tmp_path).execute()
    assert result["status"] == "blocked"
    reason = result["blocked_reason"]
    assert "add a connection on the Models page" in reason
    assert "connections.yaml" in reason
    assert "A dry run needs neither" in reason
    state = json.loads((tmp_path / "v051" / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "blocked"
    assert state["blocked_reason"] == reason


def test_live_run_with_two_fake_connections_works(cached_dataset: None, tmp_path: Path) -> None:
    run = StudyRun(config(connection_judges=FAKE_CONNECTIONS), Settings(seed=1), run_root=tmp_path)
    result = run.execute()
    assert result["status"] == "completed", result
    metrics = result["metrics"]
    assert set(metrics["judges"]) == {"judge_one", "judge_two"}
    assert metrics["pipeline_test_only"] is True, "mock stand-ins must stay labelled"
    state = json.loads((tmp_path / "v051" / "run.json").read_text(encoding="utf-8"))
    assert [judge["judge_id"] for judge in state["judges"]] == ["judge_one", "judge_two"]
    assert state["judges"][0]["family"] == "fake family A"


def test_archived_judge_ids_are_not_resolved_to_a_provider_for_a_live_run() -> None:
    config_live = config(judges=("j1_deepseek", "j2_ollama"))
    specs, detail = build_judges(config_live, Settings(seed=1))
    assert [spec.judge_id for spec in specs] == ["j1_deepseek", "j2_ollama"]
    assert all(spec.available is False for spec in specs), "a live run must not use the archived pair"
    for spec in specs:
        assert "original run's configuration" in (spec.unavailable_reason or "")
        assert "add one on the Models page" in (spec.unavailable_reason or "")
    assert detail["resolved"]["j1_deepseek"] == "unavailable: archived configuration (read-only)"


def test_archived_judge_ids_still_work_for_their_dry_run() -> None:
    specs, detail = build_judges(config(judges=("j1_deepseek", "j2_ollama"), dry_run=True), Settings(seed=1))
    assert [spec.judge_id for spec in specs] == ["j1_mock", "j2_mock"]
    assert all(spec.available for spec in specs)
    assert all("PIPELINE TEST ONLY" in spec.notes for spec in specs)
    assert detail["resolved"]["j1_deepseek"] == "replaced by j1_mock (--dry-run)"
    assert ARCHIVED_EXP002_JUDGE_IDS == ("j1_deepseek", "j2_ollama")


def test_runner_has_no_builtin_provider_defaults() -> None:
    source = (ROOT / "packages" / "medeval_study" / "runner.py").read_text(encoding="utf-8")
    for forbidden in ("deepseek-chat", "llama3.2:3b", "MEDEVAL_JUDGE1_MODEL", "MEDEVAL_JUDGE2_MODEL", "DEEPSEEK_API_KEY"):
        assert forbidden not in source, f"new-run code must not carry the default {forbidden!r}"
    assert "available_judge_summary" not in source
    import medeval_study

    assert not hasattr(medeval_study, "available_judge_summary")


def test_archived_run_can_still_be_read_and_is_not_modified() -> None:
    before = {name: (ARCHIVED_RUN_DIR / name).read_bytes() for name in ("run.json", "report.md")}
    # Resolve the committed run explicitly: the browser session fixture points the
    # registry's default root at its own fake runs, so the default would otherwise
    # return a synthetic exp002-main instead of the archived one.
    payload = load_run("exp002-main", ARCHIVED_RUN_DIR.parent)
    assert payload is not None
    run = payload["run"]
    assert run["run_id"] == "exp002-main"
    assert "j1_deepseek" in json.dumps(run)
    report = load_report("exp002-main", ARCHIVED_RUN_DIR.parent)
    assert report is not None and "Experiment 002" in report
    after = {name: (ARCHIVED_RUN_DIR / name).read_bytes() for name in ("run.json", "report.md")}
    assert before == after, "reading the archived run must not modify it"


# --------------------------------------------------------------------------- #
# 3. The "not a medical device" notice
# --------------------------------------------------------------------------- #
def test_report_header_carries_the_medical_device_notice(cached_dataset: None, tmp_path: Path) -> None:
    StudyRun(config(dry_run=True, connection_judges=FAKE_CONNECTIONS), Settings(seed=1), run_root=tmp_path).execute()
    report = (tmp_path / "v051" / "report.md").read_text(encoding="utf-8")
    assert MEDICAL_NOTICE in report
    header = report.split("## Limitations", 1)[0]
    assert "not a medical device" in header.lower()
    assert "patient care or clinical decisions" in header
    assert "No clinical validation" in header
    assert "exploratory" in header.lower()
    # The notice is part of the writer, so it comes before the run's own content.
    assert report.index(MEDICAL_NOTICE) < report.index("- Run id:")


def test_app_shell_and_start_screen_carry_the_notice() -> None:
    index = (ROOT / "packages" / "medeval_web" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "packages" / "medeval_web" / "static" / "app.js").read_text(encoding="utf-8")
    # A one-line notice in the footer of every page (the shell is shared).
    assert 'data-testid="app-footer-notice"' in index
    assert "not a medical device" in index.lower()
    # The full notice on the start screen and in the first-run card.
    assert '"data-testid": "medical-device-notice"' in app_js
    assert "view.append(head, medicalNotice(), composer, spendHint)" in app_js
    assert 'el("h2", {}, "First run: four steps"),\n      medicalNotice()' in app_js
