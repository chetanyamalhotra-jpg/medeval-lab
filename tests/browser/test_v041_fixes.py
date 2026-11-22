"""Browser regression tests for the v0.4.1 UI fixes.

Each test pins one reported bug:
1. a re-render must not stack a duplicate page,
2. the run page must not print "null", must show a real start time, and must not
   offer Results/Items for a run with no results,
3. the API key input must be as wide as the other fields,
5. the role warnings must appear before Save.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.allow_loopback]

from tests.browser.test_ui_flows import api, goto  # noqa: E402

TOKEN = "fake-browser-token"


def _add_connection(page: Any, provider_base: str, name: str, family: str) -> dict:
    created = api(page, "POST", "/api/connections", {
        "name": name, "provider_preset": "custom", "base_url": provider_base,
        "api_style": "chat", "model": "fake-judge-a", "family_label": family,
        "key": f"sk-fake-{name}", "per_connection_call_cap": 50, "per_connection_usd_cap": 1.0,
    })
    assert created["status"] in {200, 201}, created
    return created["body"]


# --------------------------------------------------------------------------- #
# Bug 1: a re-render must replace the page, not stack a copy
# --------------------------------------------------------------------------- #
def test_saving_a_connection_shows_exactly_one_models_page(
    page: Any, app_url: str, provider: dict[str, Any]
) -> None:
    goto(page, app_url)
    page.goto(app_url.split("#")[0] + "#/models")
    page.wait_for_selector("[data-testid='models-page']", timeout=15_000)
    assert page.locator("h1:has-text('Models')").count() == 1

    # Create a connection through the same form the owner uses. Since v0.5.1 no
    # provider is preselected, so choose the custom preset first (which enables
    # Save) and then type the endpoint details.
    page.select_option("[data-testid='conn-preset']", "custom")
    page.fill("[data-testid='connection-name']", "ui-save-check")
    page.fill("[data-testid='connection-key']", "sk-fake-ui-save")
    page.fill("#conn-url", provider["provider_base"])
    page.fill("#conn-model", "fake-judge-a")
    page.fill("#conn-family", "UI A")
    page.click("[data-testid='save-connection']")
    page.wait_for_timeout(1200)

    assert page.locator("[data-testid='models-page']").count() == 1, "the page stacked after save"
    assert page.locator("h1:has-text('Models')").count() == 1
    # And the new connection is listed once.
    assert page.locator("td:has-text('ui-save-check')").count() == 1


def test_forget_key_and_delete_do_not_stack_the_page(
    page: Any, app_url: str, provider: dict[str, Any]
) -> None:
    connection = _add_connection(page, provider["provider_base"], "ui-forget", "UI F")
    page.goto(app_url.split("#")[0] + "#/models")
    page.wait_for_selector("[data-testid='models-page']", timeout=15_000)
    page.on("dialog", lambda dialog: dialog.accept())
    row = page.locator(f"tr:has-text('{connection['name']}')")
    row.locator("[data-testid='connection-test']").wait_for()
    row.locator("button:has-text('Forget key')").click()
    page.wait_for_timeout(1200)
    assert page.locator("[data-testid='models-page']").count() == 1
    row = page.locator(f"tr:has-text('{connection['name']}')")
    row.locator("button:has-text('Delete')").click()
    page.wait_for_timeout(1200)
    assert page.locator("[data-testid='models-page']").count() == 1
    assert page.locator(f"td:has-text('{connection['name']}')").count() == 0


def test_adding_a_paper_shows_exactly_one_papers_page(page: Any, app_url: str) -> None:
    page.goto(app_url.split("#")[0] + "#/papers")
    page.wait_for_selector("[data-testid='papers-page']", timeout=15_000)
    page.fill("[data-testid='paper-doi']", "10.9999/v041-ui")
    page.click("[data-testid='paper-fetch']")
    try:
        page.wait_for_selector("text=Fetched from", timeout=20_000)
    except Exception as exc:
        text = page.locator("#view").inner_text()
        raise AssertionError(f"fetch panel never appeared; page said:\n{text[-600:]}") from exc
    page.click("[data-testid='paper-add']")
    page.wait_for_timeout(1200)
    assert page.locator("[data-testid='papers-page']").count() == 1, "the page stacked after add"
    assert page.locator("h1:has-text('Papers')").count() == 1
    assert page.locator("text=A Faked Crossref Paper").count() >= 1


# --------------------------------------------------------------------------- #
# Bug 2: the run page
# --------------------------------------------------------------------------- #
def _write_run(runs_root: Path, run_id: str, *, status: str, **overrides: Any) -> Path:
    base = runs_root / run_id
    base.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id, "experiment": "002_judge_families", "status": status,
        "started_utc": "2026-11-20T09:30:00Z",
        "config": {"experiment": "002_judge_families", "n_items": 6, "repeats": 1},
        "n_judgements": 0,
        **overrides,
    }
    (base / "run.json").write_text(json.dumps(state), encoding="utf-8")
    return base


def test_run_page_never_prints_null(
    page: Any, app_url: str, browser_runs_root: Path
) -> None:
    _write_run(browser_runs_root, "ui-failed-run", status="failed", error="provider said no")
    page.goto(app_url.split("#")[0] + "#/run/ui-failed-run")
    page.wait_for_selector("[data-testid='run-counters']", timeout=20_000)
    page.wait_for_timeout(800)
    counters = page.locator("[data-testid='run-counters']").inner_text()
    assert "null" not in counters.lower(), f"the status line printed a JS null: {counters!r}"


def test_run_page_shows_the_real_start_time(
    page: Any, app_url: str, browser_runs_root: Path
) -> None:
    _write_run(browser_runs_root, "ui-started-run", status="completed")
    page.goto(app_url.split("#")[0] + "#/run/ui-started-run")
    page.wait_for_selector("[data-testid='run-crumbs']", timeout=20_000)
    page.wait_for_timeout(600)
    header = page.locator("h1").inner_text()
    text = page.locator("#view").inner_text()
    assert "started —" not in text, "the page printed a started placeholder with no time"
    assert "2026" in text, f"the header should show the real start time, got: {header!r}"


def test_run_page_hides_outputs_and_disables_stop_for_a_failed_run(
    page: Any, app_url: str, browser_runs_root: Path
) -> None:
    _write_run(browser_runs_root, "ui-failed-run-2", status="failed", error="key rejected")
    page.goto(app_url.split("#")[0] + "#/run/ui-failed-run-2")
    page.wait_for_selector("[data-testid='run-outputs']", timeout=20_000)
    page.wait_for_timeout(500)
    assert page.locator("[data-testid='results-link']").count() == 0
    assert page.locator("[data-testid='items-link']").count() == 0
    assert page.locator("[data-testid='no-outputs']").count() == 1
    stop = page.locator("[data-testid='stop-run']")
    assert stop.count() == 1
    assert stop.is_disabled(), "Stop must be disabled for a run that is not active"


# --------------------------------------------------------------------------- #
# Bug 3: the API key input is full width
# --------------------------------------------------------------------------- #
def test_api_key_input_is_as_wide_as_the_other_fields(page: Any, app_url: str) -> None:
    page.goto(app_url.split("#")[0] + "#/models")
    page.wait_for_selector("[data-testid='connection-key']", timeout=15_000)
    key_box = page.locator("[data-testid='connection-key']").bounding_box()
    name_box = page.locator("[data-testid='connection-name']").bounding_box()
    assert key_box and name_box
    # The name field is half of a two-column row; the key row is full width, so
    # it must be at least as wide as the name field (it was ~180px before the fix).
    assert key_box["width"] >= name_box["width"] * 0.95, (
        f"key input is {key_box['width']:.0f}px wide vs name {name_box['width']:.0f}px"
    )


# --------------------------------------------------------------------------- #
# Bug 5: role warnings appear before Save
# --------------------------------------------------------------------------- #
def test_role_warnings_appear_on_change_before_save(
    page: Any, app_url: str, provider: dict[str, Any]
) -> None:
    first = _add_connection(page, provider["provider_base"], "rol-a", "Same Family")
    second = _add_connection(page, provider["provider_base"], "rol-b", "Same Family")
    page.goto(app_url.split("#")[0] + "#/models")
    page.wait_for_selector("[data-testid='role-judge-1']", timeout=15_000)

    page.select_option("[data-testid='role-judge-1']", first["id"])
    page.select_option("[data-testid='role-judge-2']", second["id"])
    page.wait_for_timeout(400)
    # No Save has been pressed yet.
    assert page.locator("[data-testid='family-warning']").count() == 1, \
        "the same-family warning must appear as soon as the roles are chosen"

    page.select_option("[data-testid='role-screening']", first["id"])
    page.wait_for_timeout(400)
    assert page.locator("[data-testid='screening-conflict']").count() == 1, \
        "the screening-is-a-judge error must appear before Save"

    # The save-time check still exists: saving the conflicting roles is refused.
    rejected = api(page, "POST", "/api/roles", {
        "judges": [{"role": "judge_1", "connection_id": first["id"]},
                   {"role": "judge_2", "connection_id": second["id"]}],
        "screening": first["id"],
    })
    assert rejected["status"] == 400
    assert "screening" in str(rejected["body"]).lower()
