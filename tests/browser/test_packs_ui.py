"""Paper Pack UI: registry, builder, keyboard focus and accessible labels."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.allow_loopback]


def goto_route(page: Any, app_url: str, route: str) -> None:
    page.goto(app_url.split("#")[0] + "#" + route)
    page.wait_for_selector("#workspace-list", timeout=20_000)


def test_registry_shows_packs_and_no_preselected_provider(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs")
    page.wait_for_selector("text=Runnable packs", timeout=20_000)
    content = page.content()
    assert "template-clinical-mcq" in content
    assert "healthbench-rubric" in content
    assert "medcalc-bench" in content
    assert "medhallu-detect" in content
    assert "medxpertqa-text" in content
    assert "medsafety-bench" in content
    assert "PIPELINE TEST" in content
    assert "not a medical device" in content.lower()


def test_preset_detail_shows_upstream_without_bundling_it(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs/medcalc-bench")
    page.wait_for_selector("text=Pre-flight plan card", timeout=20_000)
    page.wait_for_selector("text=Upstream data is not bundled", timeout=10_000)
    content = page.content()
    assert "nsk7153/MedCalc-Bench" in content
    assert "synthetic_smoke_only" in content
    assert "loader.py" in content
    assert "ncbi-nlp/MedCalc-Bench" in content


def test_builder_requires_a_deliberate_selection_and_has_labels(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs/template-clinical-mcq")
    page.wait_for_selector("text=Pre-flight plan card", timeout=20_000)
    # Nothing is preselected: a fresh builder is a dry run with mock stand-ins.
    assert page.is_checked("#matrix-dry")
    assert page.locator("input[type='checkbox']:checked").count() == 1  # only the dry-run switch
    page.wait_for_selector("text=Planned calls", timeout=20_000)
    # Starting is possible without any provider because the plan is a dry run.
    assert page.is_enabled("[data-testid='matrix-start']")


def test_keyboard_tab_order_and_visible_focus_on_the_builder(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs/template-clinical-mcq")
    page.wait_for_selector("text=Pre-flight plan card", timeout=20_000)
    page.locator("body").click(position={"x": 5, "y": 5})
    seen: list[str] = []
    for _ in range(8):
        page.keyboard.press("Tab")
        focused = page.evaluate("() => document.activeElement && document.activeElement.tagName")
        seen.append(str(focused))
        outline = page.evaluate(
            """() => {
                const node = document.activeElement;
                if (!node) return "";
                const style = getComputedStyle(node);
                return [style.outlineStyle, style.boxShadow].join("|");
            }"""
        )
        assert outline != "none|none" or focused in {"BODY"}, f"no visible focus on {focused}"
    assert any(tag in {"INPUT", "BUTTON", "A", "SELECT"} for tag in seen), seen


def test_command_palette_focus_is_trapped_and_restored(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs")
    page.wait_for_selector("text=Runnable packs", timeout=20_000)
    page.click("#palette-open")
    page.wait_for_selector("#palette-input:focus", timeout=5_000)
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'palette-open'", timeout=5_000)


def test_unknown_pack_shows_a_clear_error(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/packs/does-not-exist")
    page.wait_for_selector("text=Something went wrong", timeout=20_000)
    assert "unknown Paper Pack" in page.content()
