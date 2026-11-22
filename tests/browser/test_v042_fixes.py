"""Browser regression tests for the v0.4.2 fixes.

Bug 1's user-facing half: when a staged Experiment 003 run cannot continue, the
Stages card must explain why in the same words the run page uses.
Bug 2's user-facing half: a run that is reading its dataset says
"loading dataset…", not "queued".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.allow_loopback]


def _write_run(runs_root: Path, run_id: str, *, status: str, **extra: Any) -> Path:
    base = runs_root / run_id
    base.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "experiment": "003_frontier_judges",
        "status": status,
        "started_utc": "2026-11-21T10:00:00Z",
        "config": {
            "experiment": "003_frontier_judges", "dataset": "medqa_4options", "split": "dev",
            "n_items": 140, "seed": 20261118, "repeats": 1, "pilot_items": 40,
            "pilot_min_disagreements": 5, "screening_size": 600,
            "connection_judges": [], "screening_binding": None,
        },
        **extra,
    }
    (base / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return base


def test_stages_card_shows_the_blocked_reason(page: Any, app_url: str, browser_runs_root: Path) -> None:
    reason = (
        "screening found 0 eligible items in medqa_4options/dev: 0 item(s) loaded and 60 excluded "
        "by the Experiment 002 list. Fix: run "
        ".\\\\.venv\\\\Scripts\\\\python.exe -m medeval data fetch medqa_4options to fetch the real "
        "data, then start the run again."
    )
    _write_run(browser_runs_root, "ui-blocked-exp003", status="blocked",
               blocked_reason=reason,
               screening={"n_screened": 0, "stop_reason": "no_eligible_items", "n_eligible": 0})
    page.goto(app_url.split("#")[0] + "#/run/ui-blocked-exp003")
    page.wait_for_selector("[data-testid='stages-blocked']", timeout=20_000)
    card = page.locator("[data-testid='stages-blocked']").inner_text()
    assert "0 eligible items" in card
    assert "medeval data fetch medqa_4options" in card
    # The run page banner carries the same words.
    assert "medeval data fetch medqa_4options" in page.locator("#view").inner_text()


def test_no_stage_can_be_confirmed_for_a_blocked_run(page: Any, app_url: str, browser_runs_root: Path) -> None:
    _write_run(browser_runs_root, "ui-blocked-exp003-2", status="blocked",
               blocked_reason="screening found 0 eligible items; run medeval data fetch medqa_4options")
    page.goto(app_url.split("#")[0] + "#/run/ui-blocked-exp003-2")
    page.wait_for_selector("[data-testid='stages-blocked']", timeout=20_000)
    page.wait_for_timeout(500)
    assert page.locator("button:has-text('Confirm & run')").count() == 0
    assert page.locator("[data-testid='results-link']").count() == 0


def test_loading_state_is_explained_on_the_run_page(
    page: Any, app_url: str, browser_runs_root: Path
) -> None:
    _write_run(browser_runs_root, "ui-loading-run", status="loading")
    page.goto(app_url.split("#")[0] + "#/run/ui-loading-run")
    page.wait_for_selector("[data-testid='run-crumbs']", timeout=20_000)
    page.wait_for_timeout(600)
    text = page.locator("#view").inner_text()
    assert "loading dataset" in text.lower(), f"the page should say what is happening, got: {text[:200]!r}"
    assert "queued" not in text.lower()
