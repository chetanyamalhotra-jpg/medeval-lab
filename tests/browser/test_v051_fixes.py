"""Browser regression tests for the v0.5.1 fixes.

1. The Models page starts with **no provider chosen**: the preset dropdown shows
   an empty "Choose a provider" option, Save is disabled, the base URL, model,
   family and price fields are empty, and choosing a preset fills the base URL
   only (never a model or a price).
2. The app shows the "not a medical device" notice on the start screen, in the
   shell footer of every page, and in the header of a report.

The app is the real one served by the shared fixtures; the only "provider" is the
local fake, and no key is real.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.allow_loopback]

from tests.browser.test_ui_flows import goto  # noqa: E402


def _models_page(page: Any, app_url: str) -> None:
    page.goto(app_url.split("#")[0] + "#/models")
    page.wait_for_selector("[data-testid='conn-preset']", timeout=20_000)


def test_models_page_starts_with_no_provider_selected(page: Any, app_url: str) -> None:
    _models_page(page, app_url)

    preset = page.locator("[data-testid='conn-preset']")
    assert preset.input_value() == "", "a fresh page must not preselect a provider"
    options = preset.locator("option").all_inner_texts()
    assert options[0] == "Choose a provider", f"first option must be the empty one, got {options[:2]!r}"
    assert "OpenAI" in options and "Ollama (local, no key)" in options, "the presets stay available"

    save = page.locator("[data-testid='save-connection']")
    assert save.is_disabled(), "Save must be disabled until a provider is chosen"
    assert "Choose a provider" in page.locator("[data-testid='preset-hint']").inner_text()

    for selector in ("#conn-url", "#conn-model", "#conn-family", "#conn-price-in", "#conn-price-out"):
        assert page.input_value(selector) == "", f"{selector} must start empty"

    # Choosing a preset enables Save and fills the base URL only.
    page.select_option("[data-testid='conn-preset']", "openai")
    page.wait_for_timeout(200)
    assert not save.is_disabled(), "choosing a preset must enable Save"
    assert page.input_value("#conn-url") == "https://api.openai.com/v1"
    assert page.input_value("#conn-model") == "", "a preset must not choose a model"
    assert page.input_value("#conn-family") == "", "a preset must not choose a family"
    assert page.input_value("#conn-price-in") == "", "a preset must not choose a price"
    assert page.input_value("#conn-price-out") == ""

    # Going back to the empty option clears the URL and disables Save again.
    page.select_option("[data-testid='conn-preset']", "")
    page.wait_for_timeout(200)
    assert page.input_value("#conn-url") == ""
    assert save.is_disabled()


def test_medical_device_notice_on_start_screen_footer_and_report(page: Any, app_url: str) -> None:
    goto(page, app_url)  # the start screen

    notice = page.locator("[data-testid='medical-device-notice']").first
    notice.wait_for(timeout=20_000)
    text = notice.inner_text().lower()
    assert "not a medical device" in text
    assert "not for patient care or clinical decisions" in text
    assert "no clinical validation" in text
    assert "exploratory" in text
    # The first-run card carries its own copy while it is shown.
    if page.locator("[data-testid='first-run-notice']").count():
        assert page.locator("[data-testid='medical-device-notice']").count() >= 2

    footer = page.locator("[data-testid='app-footer-notice']")
    assert footer.count() == 1, "the one-line notice must be in the shell footer"
    assert "not a medical device" in footer.inner_text().lower()

    # The footer lives in the shell, so it follows every page.
    _models_page(page, app_url)
    page.wait_for_selector("[data-testid='models-page']", timeout=20_000)
    assert page.locator("[data-testid='app-footer-notice']").count() == 1

    # A report carries the notice in its header: read it through the same endpoint
    # the app links to, using the browser's own session token.
    report = page.evaluate(
        """async () => {
            const token = sessionStorage.getItem('medeval.token') || '';
            const response = await fetch('/api/runs/exp002-main/report', {
                headers: {'X-Medeval-Token': token},
            });
            return {status: response.status, text: await response.text()};
        }"""
    )
    assert report["status"] == 200, report
    body = report["text"].lower()
    assert "not a medical device" in body
    assert "no clinical validation" in body
    assert "exploratory" in body
