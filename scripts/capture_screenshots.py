"""Capture documentation screenshots of every page (fake data only).

Runs the real app against a fake OpenAI-compatible provider and a fake Crossref,
then saves light/dark/phone/tablet screenshots into ``docs/screenshots/``. Only
fake data is shown: no benchmark item text, no real keys, no personal data.

Usage (needs a browser; on a sandboxed host it may need wider permissions):

    .\\.venv\\Scripts\\python.exe scripts\\capture_screenshots.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "packages"))

OUT = REPO / "docs" / "screenshots"
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 900},
    "phone": {"width": 390, "height": 844},
    "tablet": {"width": 820, "height": 1180},
}
PAGES = [
    ("start", "#/"),
    ("runs", "#/runs"),
    ("models", "#/models"),
    ("papers", "#/papers"),
    ("tests", "#/tests"),
    ("settings", "#/settings"),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from browser.conftest import _ProviderHandler, _CrossrefHandler, _serve, _free_port, _wait_ready  # noqa: PLC0415

    temp = REPO / ".tmp" / "playwright-artifacts"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(temp)
    os.environ["TEMP"] = str(temp)
    OUT.mkdir(parents=True, exist_ok=True)

    provider, provider_port = _serve(_ProviderHandler)
    crossref, crossref_port = _serve(_CrossrefHandler)
    os.environ["MEDEVAL_CROSSREF_BASE"] = f"http://127.0.0.1:{crossref_port}/works"

    import threading

    import uvicorn

    from medeval_core.config import Settings
    from medeval_study import registry
    from medeval_web import app as app_module
    from medeval_web.app import create_app

    runs_root = REPO / ".tmp" / "screenshot-runs"
    if runs_root.exists():
        import shutil

        shutil.rmtree(runs_root)
    runs_root.mkdir(parents=True)
    legacy = runs_root / "exp002-main"
    legacy.mkdir()
    (legacy / "run.json").write_text(json.dumps({
        "run_id": "exp002-main", "experiment": "002_judge_families", "status": "completed",
        "started_utc": "2026-11-17T11:00:00Z", "config": {"experiment": "002_judge_families", "n_items": 60},
        "n_judgements": 180,
    }), encoding="utf-8")
    (legacy / "metrics.json").write_text(json.dumps({"judges": {}, "pipeline_test_only": False}), encoding="utf-8")
    (legacy / "report.md").write_text("# Experiment 002 (fixture)\n", encoding="utf-8")

    app_module.run_root = lambda: runs_root  # type: ignore[assignment]
    registry.run_root = lambda: runs_root  # type: ignore[assignment]
    app_module.list_runs = lambda: registry.list_runs(runs_root)  # type: ignore[assignment]
    app_module.load_run = lambda run_id: registry.load_run(run_id, runs_root)  # type: ignore[assignment]
    app_module.load_report = lambda run_id: registry.load_report(run_id, runs_root)  # type: ignore[assignment]
    app_module.item_table = lambda run_id: registry.item_table(run_id, runs_root)  # type: ignore[assignment]

    port = _free_port()
    token = "fake-screenshot-token"
    literature = runs_root.parent / "screenshot-literature.csv"
    literature.write_text(
        "id,doi,title,year,task,reference_standard,metric_proposed,how_we_operationalize,limitations,verified\n"
        "lit_demo,10.1000/demo,A demonstration paper,2023,task,standard,metric,how,limits,yes\n",
        encoding="utf-8",
    )
    app = create_app(
        token=token,
        allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
        settings=Settings(),
        local_data_dir=runs_root.parent / "screenshot-data",
        connections_path=runs_root.parent / "screenshot-data" / "connections.json",
        literature_path=literature,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_ready(f"http://127.0.0.1:{port}/api/health")
    url = f"http://127.0.0.1:{port}/#token={token}"

    written = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="msedge")
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport, device_scale_factor=1)
                page = context.new_page()
                page.goto(url)
                page.wait_for_selector("#workspace-list", timeout=20_000)
                for theme in ("light", "dark"):
                    page.click(f"button[data-theme-choice='{theme}']")
                    page.wait_for_timeout(350)
                    for name, route in PAGES:
                        page.goto(f"http://127.0.0.1:{port}/#{route[1:]}")
                        page.wait_for_timeout(700)
                        path = OUT / f"{name}-{viewport_name}-{theme}.png"
                        page.screenshot(path=str(path), full_page=False)
                        written += 1
                        print(f"[screenshots] {path.relative_to(REPO)}")
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        provider.shutdown()
        crossref.shutdown()
        os.environ.pop("MEDEVAL_CROSSREF_BASE", None)
    print(f"[screenshots] wrote {written} files to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
