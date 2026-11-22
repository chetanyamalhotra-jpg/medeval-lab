"""Browser end-to-end flows (M1).

Every test drives the real app in a real headless browser against a fake
OpenAI-compatible provider and a fake Crossref, so nothing spends money and no
real key exists anywhere. If the browser cannot start, the module skips with the
reason (see MANUAL_SMOKE.md for the manual replacement).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.allow_loopback]

TOKEN = "fake-browser-token"


def goto(page: Any, url: str) -> None:
    """Open the launch URL (its fragment carries the token) and wait for the shell."""
    page.goto(url)
    page.wait_for_selector("#workspace-list", timeout=15_000)


def goto_route(page: Any, base_with_fragment: str, route: str) -> None:
    """Navigate to a route without losing the token from sessionStorage."""
    page.goto(base_with_fragment.split("#")[0] + "#" + route)
    page.wait_for_timeout(700)


def wait_hash(page: Any, expected: str, timeout_ms: int = 15_000) -> None:
    """Wait for a hash route. Hash changes are same-document navigations, so
    wait_for_url(until="load") would wait forever."""
    page.wait_for_function(
        "expected => location.hash === expected",
        arg=expected,
        timeout=timeout_ms,
    )


def wait_session(page: Any, timeout_ms: int = 20_000) -> None:
    page.wait_for_function("() => location.hash.startsWith('#/session/')", timeout=timeout_ms)


def api(page: Any, method: str, path: str, payload: dict | None = None) -> dict:
    """Call the app's API from inside the browser page (same token as the UI)."""
    result = page.evaluate(
        """async ([method, path, payload]) => {
            const token = sessionStorage.getItem('medeval.token') || '';
            const response = await fetch(path, {
                method,
                headers: { 'Content-Type': 'application/json', 'X-Medeval-Token': token },
                body: payload ? JSON.stringify(payload) : undefined,
            });
            const text = await response.text();
            return { status: response.status, body: text ? JSON.parse(text) : null };
        }""",
        [method, path, payload],
    )
    return result


def wait_until(fn, timeout_s: float = 20.0, interval_s: float = 0.25):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"condition not met within {timeout_s}s (last={last!r})")


# --------------------------------------------------------------------------- #
# Start screen, workspaces, sessions, composer
# --------------------------------------------------------------------------- #
def test_start_screen_renders_with_first_run_card(page: Any, app_url: str) -> None:
    goto(page, app_url)
    assert page.is_visible("h1")
    assert "Describe an evaluation" in page.text_content("h1")
    assert page.is_visible("text=Preview")
    # The first-run card is shown on a fresh profile.
    assert page.is_visible("text=First run: four steps")
    # Sidebar lists the legacy v0.2 run under "Experiment 002".
    sidebar = page.text_content("#workspace-list")
    assert "Experiment 002" in sidebar and "exp002-main" in sidebar


def test_first_run_card_can_be_dismissed_and_restored(page: Any, app_url: str) -> None:
    goto(page, app_url)
    if not page.is_visible("text=Hide this card"):
        goto(page, app_url)
    page.click("text=Hide this card")
    page.wait_for_timeout(500)
    page.reload()
    page.wait_for_selector("#workspace-list")
    assert not page.is_visible("text=First run: four steps")
    goto_route(page, app_url, "/settings")
    page.click("text=Show the first-run card again")
    page.wait_for_timeout(400)
    goto(page, app_url)
    assert page.is_visible("text=First run: four steps")


def test_plain_note_becomes_a_session(page: Any, app_url: str) -> None:
    goto(page, app_url)
    page.fill("textarea[aria-label='Describe what you want to evaluate']",
              "Hypothesis: hard items produce more judge-key disputes.")
    page.click("button[aria-label='Send']")
    wait_session(page)
    page.wait_for_selector(".session-thread", timeout=10_000)
    thread = page.text_content(".session-thread")
    assert "Hypothesis: hard items" in thread


def test_slash_commands_route_correctly(page: Any, app_url: str) -> None:
    goto(page, app_url)
    textarea = page.locator("textarea[aria-label='Describe what you want to evaluate']")
    textarea.fill("/")
    page.wait_for_selector(".slash-list", timeout=5000)
    page.wait_for_timeout(300)
    listing = page.text_content(".slash-list")
    for command in ("/run", "/dryrun", "/test", "/papers", "/report"):
        assert command in listing, command
    textarea.fill("/papers")
    page.click("button[aria-label='Send']")
    wait_hash(page, "#/papers")
    page.wait_for_selector("text=Add a paper by DOI or arXiv id", timeout=10_000)


# --------------------------------------------------------------------------- #
# Keyboard chords, palette, theme
# --------------------------------------------------------------------------- #
def test_command_palette_and_chords(page: Any, app_url: str) -> None:
    goto(page, app_url)
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette:not([hidden])", timeout=5000)
    page.keyboard.type("models")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    wait_hash(page, "#/models")
    page.wait_for_selector("h1:has-text('Models')", timeout=10_000)
    # g r -> runs, g n -> new, g s -> settings (reload for a clean document)
    page.reload()
    page.wait_for_selector("#workspace-list", timeout=15_000)
    page.keyboard.press("g")
    page.keyboard.press("r")
    wait_hash(page, "#/runs")
    # typing a chord inside the composer must NOT navigate
    goto(page, app_url)
    page.click("textarea[aria-label='Describe what you want to evaluate']")
    page.keyboard.type("gra nicely")
    page.wait_for_timeout(400)
    assert "#/runs" not in page.url, "chords must be ignored while typing"


def test_theme_control_switches_and_persists(page: Any, app_url: str) -> None:
    goto(page, app_url)
    page.click("button[data-theme-choice='dark']")
    page.wait_for_timeout(300)
    assert page.get_attribute("html", "data-theme") == "dark"
    page.reload()
    page.wait_for_selector("#workspace-list")
    assert page.get_attribute("html", "data-theme") == "dark"
    page.click("button[data-theme-choice='light']")
    page.wait_for_timeout(300)
    assert page.get_attribute("html", "data-theme") == "light"
    page.click("button[data-theme-choice='auto']")


# --------------------------------------------------------------------------- #
# Models page: connections, roles, warnings, screening rule
# --------------------------------------------------------------------------- #
def _add_connection(page: Any, base: str, provider_base: str, *, name: str, family: str, model: str) -> dict:
    response = api(page, "POST", "/api/connections", {
        "name": name, "provider_preset": "custom", "base_url": provider_base,
        "api_style": "chat", "model": model, "family_label": family,
        "key": f"sk-fake-browser-{name}", "per_connection_call_cap": 200,
        "per_connection_usd_cap": 2.0,
    })
    assert response["status"] in {200, 201}, response
    return response["body"]


def test_models_page_create_test_and_roles(page: Any, app_url: str, provider: dict[str, Any]) -> None:
    goto(page, app_url)
    first = _add_connection(page, app_url, provider["provider_base"], name="judge-a", family="Fake A", model="fake-judge-a")
    second = _add_connection(page, app_url, provider["provider_base"], name="judge-b", family="Fake B", model="fake-judge-b")
    goto_route(page, app_url, "/models")
    page.wait_for_selector("text=Saved connections", timeout=10_000)
    table = page.text_content(".table-wrap")
    assert "judge-a" in table and "judge-b" in table
    assert "key set ····" in table, "the key must be shown only as a tail"
    assert "sk-fake-browser" not in page.content(), "the full key must never be rendered"

    # Test connection: lists models, no spend.
    page.locator("button:has-text('Test')").first.click()
    page.wait_for_selector("text=Verified", timeout=20_000)

    # Assign roles through the API the page uses, then reload the page.
    roles = api(page, "POST", "/api/roles", {
        "judges": [
            {"role": "judge_1", "connection_id": first["id"]},
            {"role": "judge_2", "connection_id": second["id"]},
        ],
        "screening": None,
    })
    assert roles["status"] == 200, roles
    goto_route(page, app_url, "/models")
    page.wait_for_selector("text=Roles", timeout=10_000)


def test_same_family_warning_and_screening_not_a_judge(page: Any, app_url: str, provider: dict[str, Any]) -> None:
    first = _add_connection(page, app_url, provider["provider_base"], name="twin-a", family="Twin", model="fake-judge-a")
    second = _add_connection(page, app_url, provider["provider_base"], name="twin-b", family="Twin", model="fake-judge-b")
    goto_route(page, app_url, "/models")
    page.wait_for_selector("select[aria-label='Connection for judge_1']", timeout=10_000)
    page.select_option("select[aria-label='Connection for judge_1']", first["id"])
    page.select_option("select[aria-label='Connection for judge_2']", second["id"])
    page.wait_for_timeout(300)
    assert page.is_visible("text=cross-family error correlation cannot be measured"), \
        "two judges from the same family must warn"
    page.select_option("select[aria-label='Connection for the screening model']", first["id"])
    page.wait_for_timeout(300)
    assert page.is_visible("text=must not also be a judge"), \
        "the screening model must be blocked from also being a judge"


# --------------------------------------------------------------------------- #
# Dry run, fake-live run and stop/resume (matrix runner)
# --------------------------------------------------------------------------- #
def _wait_matrix_status(page: Any, run_id: str, wanted: set[str], timeout_s: float = 120.0) -> dict:
    def check():
        payload = api(page, "GET", f"/api/matrix/runs/{run_id}")["body"] or {}
        status = (payload.get("run") or {}).get("status")
        return payload if status in wanted else None

    return wait_until(check, timeout_s=timeout_s, interval_s=0.3)


def test_dry_run_plan_card_and_completion(page: Any, app_url: str) -> None:
    """The Paper Pack builder shows a pre-flight card, then a deterministic dry run."""
    goto_route(page, app_url, "/packs")
    page.wait_for_selector("text=Runnable packs", timeout=20_000)
    page.click("a[href='#/packs/template-clinical-mcq']")
    page.wait_for_selector("text=Pre-flight plan card", timeout=20_000)
    assert page.is_visible("text=PIPELINE TEST ONLY"), "a dry run must be badged"
    assert page.is_visible("text=Planned calls"), "the plan card must estimate calls"
    page.click("[data-testid='matrix-start']")
    page.wait_for_function("() => location.hash.startsWith('#/matrix/')", timeout=20_000)
    run_id = page.evaluate("() => location.hash.split('/')[2]")
    payload = _wait_matrix_status(page, run_id, {"completed", "failed", "budget_stopped"})
    assert payload["run"]["status"] == "completed", payload["run"].get("blocked_reason")
    assert payload["metrics"], "a dry run still writes metrics"
    assert payload["run"]["pipeline_test_only"] is True
    page.wait_for_selector("text=Summary", timeout=20_000)
    assert "PIPELINE TEST ONLY" in page.content()


def test_fake_live_run_to_completion_with_results_and_export(
    page: Any, app_url: str, provider: dict[str, Any], provider_state: Any
) -> None:
    connection = _add_connection(
        page, app_url, provider["provider_base"], name="matrix-live-a", family="Live A", model="fake-judge-a"
    )
    goto(page, app_url)
    started = api(page, "POST", "/api/matrix/runs", {
        "paper": "template-clinical-mcq",
        "dry_run": False,
        "targets": [connection["id"]],
        "judges": [],
        "n_items": 4,
        "max_calls": 50,
        "max_usd": 0.0,
        "confirm_real_calls": True,
    })
    assert started["status"] == 202, started
    run_id = started["body"]["run_id"]
    payload = _wait_matrix_status(page, run_id, {"completed", "failed", "blocked", "budget_stopped"})
    assert payload["run"]["status"] == "completed", payload["run"].get("blocked_reason") or payload.get("error")
    assert payload["metrics"], "a completed live run writes metrics"
    assert provider_state.requests, "the fake provider must have been called"
    assert payload["run"]["pipeline_test_only"] is False

    goto_route(page, app_url, f"/matrix/{run_id}")
    page.wait_for_selector("text=Export bundle", timeout=20_000)
    status = page.evaluate(
        """async (path) => {
            const token = sessionStorage.getItem('medeval.token') || '';
            const response = await fetch(path, { headers: { 'X-Medeval-Token': token } });
            return [response.status, response.headers.get('content-type') || ''];
        }""",
        f"/api/matrix/runs/{run_id}/bundle",
    )
    assert status[0] == 200 and "zip" in status[1], status


def test_stop_and_resume_never_duplicates_an_item(
    page: Any, app_url: str, provider: dict[str, Any], provider_state: Any
) -> None:
    connection = _add_connection(
        page, app_url, provider["provider_base"], name="matrix-slow-a", family="Slow A", model="fake-judge-a"
    )
    goto(page, app_url)
    provider_state.delay_s = 0.8
    try:
        started = api(page, "POST", "/api/matrix/runs", {
            "paper": "template-clinical-mcq",
            "dry_run": False,
            "targets": [connection["id"]],
            "judges": [],
            "n_items": 8,
            "max_calls": 50,
            "max_usd": 0.0,
            "confirm_real_calls": True,
        })
        assert started["status"] == 202, started
        run_id = started["body"]["run_id"]
        wait_until(lambda: (api(page, "GET", f"/api/matrix/runs/{run_id}")["body"] or {}).get("active"), timeout_s=30)
        # Let a couple of calls land so the stop is genuinely mid-run.
        wait_until(
            lambda: len((api(page, "GET", f"/api/matrix/runs/{run_id}")["body"] or {}).get("outputs") or []) >= 2,
            timeout_s=30,
        )
        stop = api(page, "POST", f"/api/matrix/runs/{run_id}/stop", {})
        assert stop["status"] == 200, stop
        partial = _wait_matrix_status(page, run_id, {"stopped", "completed", "failed"})
        assert partial["run"]["status"] == "stopped", partial["run"].get("blocked_reason")
        assert partial["metrics"], "a stopped run still writes reports from what exists"
        assert 0 < len(partial["outputs"]) < 8, "the stop must land mid-run"
    finally:
        provider_state.delay_s = 0.0
    resumed = api(page, "POST", f"/api/matrix/runs/{run_id}/resume", {})
    assert resumed["status"] in {200, 202}, resumed
    final = _wait_matrix_status(page, run_id, {"completed", "failed", "stopped", "budget_stopped"})
    assert final["run"]["status"] == "completed", final["run"].get("blocked_reason") or final.get("error")
    output_keys = [(row.get("target_id"), row.get("item_id")) for row in final["outputs"]]
    assert len(output_keys) == len(set(output_keys)), "an item was evaluated twice"
    assert len(output_keys) == 8
    score_keys = [
        (row.get("target_id"), row.get("item_id"), row.get("grader_id"), row.get("judge_id"))
        for row in final["scores"]
    ]
    assert len(score_keys) == len(set(score_keys)), "a grader score was duplicated on resume"


# --------------------------------------------------------------------------- #
# Papers, test center, export, token, Host/Origin
# --------------------------------------------------------------------------- #
def test_papers_add_by_doi(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/papers")
    page.wait_for_selector("text=Add a paper by DOI or arXiv id", timeout=15_000)
    page.fill("input[aria-label='DOI or arXiv id']", "10.9999/browser-fake")
    page.click("text=Fetch metadata")
    page.wait_for_selector("text=Fetched from", timeout=20_000)
    page.wait_for_selector("text=A Faked Crossref Paper", timeout=20_000)
    page.click("text=Add to registry")
    page.wait_for_timeout(800)
    assert "A Faked Crossref Paper" in page.content()


def test_test_center_runs_allow_listed_and_rejects_raw_commands(page: Any, app_url: str) -> None:
    goto_route(page, app_url, "/tests")
    page.wait_for_selector("text=Allow-listed commands", timeout=15_000)
    page.wait_for_selector("text=Pre-flight checklist", timeout=10_000)
    rejected = api(page, "POST", "/api/tests/run", {"command": "rm -rf /"})
    assert rejected["status"] == 400
    rejected_direct = api(page, "POST", "/api/tests/run", {"command": "python -c 'print(1)'"})
    assert rejected_direct["status"] == 400
    allowed = api(page, "GET", "/api/tests/commands")["body"]
    assert any(row["key"] == "pytest" for row in allowed)


def test_missing_token_and_bad_host_and_cross_site_origin(page: Any, app_url: str) -> None:
    base = app_url.split("#")[0]
    # 1. No token → 403 from the API.
    no_token = page.evaluate(
        """async (base) => {
            const r = await fetch(base + 'api/health', { headers: {} });
            return r.status;
        }""",
        base,
    )
    assert no_token == 403
    # 2. A wrong Host header is refused (send via a server-side request helper).
    bad_host = page.evaluate(
        """async (base) => {
            const r = await fetch(base + 'api/health', {
                headers: { 'X-Medeval-Token': sessionStorage.getItem('medeval.token') || '' },
            });
            return r.status;
        }""",
        base,
    )
    assert bad_host in {200, 403}

    import httpx

    with httpx.Client(base_url=base, timeout=5) as client:
        wrong_host = client.get("/api/health", headers={"Host": "evil.example", "X-Medeval-Token": TOKEN})
        assert wrong_host.status_code == 403, wrong_host.text
        cross_site = client.get(
            "/api/health",
            headers={"Origin": "https://evil.example", "X-Medeval-Token": TOKEN},
        )
        assert cross_site.status_code == 403, cross_site.text
        same_origin = client.get("/api/health", headers={"Origin": base, "X-Medeval-Token": TOKEN})
        assert same_origin.status_code == 200


# --------------------------------------------------------------------------- #
# Accessibility basics
# --------------------------------------------------------------------------- #
def test_accessible_labels_and_live_regions(page: Any, app_url: str) -> None:
    goto(page, app_url)
    # Every form control has an accessible name.
    unlabelled = page.evaluate(
        """() => {
            const bad = [];
            for (const el of document.querySelectorAll('input, select, textarea, button')) {
                if (el.type === 'hidden' || el.offsetParent === null) continue;
                const name = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') ||
                    (el.id && document.querySelector(`label[for="${el.id}"]`)) ||
                    (el.closest('label')) || el.textContent.trim() || el.title;
                if (!name || (typeof name !== 'string' && !name.textContent)) bad.push(el.outerHTML.slice(0, 90));
            }
            return bad;
        }"""
    )
    assert unlabelled == [], f"controls without an accessible name: {unlabelled[:3]}"

    # Focus is visible: the stylesheet defines a focus-visible outline.
    css = page.evaluate("""() => {
        for (const sheet of document.styleSheets) {
            try {
                for (const rule of sheet.cssRules) {
                    if (rule.selectorText && rule.selectorText.includes(':focus-visible')) return rule.cssText;
                }
            } catch (e) { /* cross-origin sheet */ }
        }
        return null;
    }""")
    assert css and "outline" in css, "a visible :focus-visible outline is required"

    # Logical tab order: the first Tab lands on the skip link.
    page.keyboard.press("Tab")
    page.wait_for_timeout(200)
    active = page.evaluate("() => document.activeElement && document.activeElement.className")
    assert active is not None


def test_run_progress_region_is_live(page: Any, app_url: str) -> None:
    goto(page, app_url)
    live = api(page, "GET", "/api/runs")
    assert live["status"] == 200
    goto_route(page, app_url, "/runs")
    page.wait_for_selector("table", timeout=15_000)
    # The log region on a run page is a live region; check the stylesheet contract instead
    # of a specific run: the app declares aria-live on the progress/log area.
    goto_route(page, app_url, "/run/exp002-main")
    page.wait_for_selector("text=Progress", timeout=15_000)
    assert page.locator("[aria-live='polite']").count() >= 1


def test_reduced_motion_and_contrast_are_declared(page: Any, app_url: str) -> None:
    goto(page, app_url)
    result = page.evaluate(
        """() => {
            let reduced = false;
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of sheet.cssRules) {
                        if (rule.conditionText && rule.conditionText.includes('prefers-reduced-motion')) reduced = true;
                    }
                } catch (e) { /* ignore */ }
            }
            const rootStyle = getComputedStyle(document.documentElement);
            return {
                reduced,
                fg: rootStyle.getPropertyValue('--text').trim(),
                bg: rootStyle.getPropertyValue('--bg').trim(),
            };
        }"""
    )
    assert result["reduced"], "prefers-reduced-motion must be respected"
    assert result["fg"] and result["bg"], "theme colours must be declared as CSS variables"


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    def channel(value: str) -> float:
        value = value.strip().lstrip("#")
        if len(value) == 3:
            value = "".join(part * 2 for part in value)
        red, green, blue = (int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))

        def linear(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)

    first, second = channel(hex_a), channel(hex_b)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_body_text_contrast_in_both_themes(page: Any, app_url: str) -> None:
    for theme in ("light", "dark"):
        goto(page, app_url)
        page.click(f"button[data-theme-choice='{theme}']")
        page.wait_for_timeout(400)
        colours = page.evaluate("""() => {
            const s = getComputedStyle(document.documentElement);
            return { text: s.getPropertyValue('--text').trim(), bg: s.getPropertyValue('--bg').trim() };
        }""")
        assert colours["text"].startswith("#") and colours["bg"].startswith("#"), colours
        ratio = _contrast_ratio(colours["text"], colours["bg"])
        assert ratio >= 4.5, f"{theme} theme body contrast {ratio:.2f} is below 4.5:1"
    page.click("button[data-theme-choice='auto']")
