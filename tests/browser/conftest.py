"""Browser end-to-end fixtures: the real app, a fake provider, and Playwright.

The app is started in-process with a launch token; "provider" traffic goes to a
tiny local OpenAI-compatible server and "Crossref" traffic to a fake metadata
server, both on loopback. No real network call is made and no key is real.

If Chromium (or Edge) cannot start in this environment the whole module skips
with a clear reason — see MANUAL_SMOKE.md for the click-through replacement.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from medeval_core.config import Settings

pytest.importorskip("playwright", reason="Playwright is not installed (pip install playwright)")

TOKEN = "fake-browser-token"


# --------------------------------------------------------------------------- #
# Local servers
# --------------------------------------------------------------------------- #
class _ProviderHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions returning a strict-JSON verdict."""

    delay_s = 0.0
    requests: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def _reply(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._reply({"object": "list", "data": [{"id": "fake-judge-a"}, {"id": "fake-judge-b"}]})
            return
        self._reply({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        _ProviderHandler.requests.append({"path": self.path, "body": body})
        if _ProviderHandler.delay_s:
            time.sleep(_ProviderHandler.delay_s)
        # Alternate CORRECT/INCORRECT deterministically so both error directions exist.
        verdict = "CORRECT" if len(_ProviderHandler.requests) % 2 else "INCORRECT"
        self._reply({
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": body.get("model") or "fake-judge-a",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps({"verdict": verdict, "confidence": 91})},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128},
        })


class _CrossrefHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        body = json.dumps({
            "message": {"title": ["A Faked Crossref Paper"], "issued": {"date-parts": [[2024, 3, 1]]}}
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except urllib.error.HTTPError:  # 403 is fine: the app is up
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"the app did not start at {url}")


@pytest.fixture(scope="session")
def servers() -> Iterator[dict[str, Any]]:
    provider, provider_port = _serve(_ProviderHandler)
    crossref, crossref_port = _serve(_CrossrefHandler)
    yield {
        "provider_port": provider_port,
        "crossref_port": crossref_port,
        "provider_base": f"http://127.0.0.1:{provider_port}/v1",
        "crossref_base": f"http://127.0.0.1:{crossref_port}/works",
    }
    provider.shutdown()
    crossref.shutdown()


@pytest.fixture(scope="session")
def browser_runs_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The run directory the browser app uses (tests can add fixture runs to it)."""
    root = tmp_path_factory.mktemp("browser-runs") / "study_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def app_url(
    browser_runs_root: Path, servers: dict[str, Any]
) -> Iterator[str]:
    """Start the real app with a token; return the launch URL."""
    import uvicorn

    from medeval_study import registry
    from medeval_study import runner as runner_module
    from medeval_web import app as app_module
    from medeval_web.app import create_app

    runs_root = browser_runs_root

    # One v0.2-style run so the "Experiment 002" workspace has a session.
    legacy = runs_root / "exp002-main"
    legacy.mkdir()
    (legacy / "run.json").write_text(json.dumps({
        "run_id": "exp002-main", "experiment": "002_judge_families", "status": "completed",
        "started_utc": "2026-11-17T11:00:00Z", "finished_utc": "2026-11-17T11:20:00Z",
        "config": {"experiment": "002_judge_families", "n_items": 60, "repeats": 3},
        "usage": {"calls_made": 187}, "n_judgements": 180,
    }), encoding="utf-8")
    (legacy / "metrics.json").write_text(json.dumps({
        "experiment": "002_judge_families", "pipeline_test_only": False,
        "judges": {"j1_deepseek": {"primary_repeat": {
            "accuracy": {"estimate": 0.983, "denominator": 59, "ci95_wilson": [0.91, 0.997]},
            "cohen_kappa_vs_key": 0.966,
        }}},
    }), encoding="utf-8")
    from medeval_study.report import MEDICAL_NOTICE

    (legacy / "report.md").write_text(
        "# Experiment 002\n\n"
        f"> {MEDICAL_NOTICE}\n\n"
        "Synthetic fixture report with the same notice header a generated report has.\n",
        encoding="utf-8",
    )


    real_run_root = app_module.run_root
    app_module.run_root = lambda: runs_root  # type: ignore[assignment]
    # A run started from the app builds a StudyRun, which resolves its own
    # default root; without this the tests write into results/study_runs.
    runner_module.default_run_root = lambda: runs_root  # type: ignore[assignment]
    registry_run_root = registry.run_root
    registry.run_root = lambda: runs_root  # type: ignore[assignment]
    app_module.list_runs = lambda: registry.list_runs(runs_root)  # type: ignore[assignment]
    app_module.load_run = lambda run_id: registry.load_run(run_id, runs_root)  # type: ignore[assignment]
    app_module.load_report = lambda run_id: registry.load_report(run_id, runs_root)  # type: ignore[assignment]
    app_module.item_table = lambda run_id: registry.item_table(run_id, runs_root)  # type: ignore[assignment]

    port = _free_port()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    local_data = runs_root.parent / "local-data"
    literature = runs_root.parent / "literature.csv"
    literature.write_text(
        "id,doi,title,year,task,reference_standard,metric_proposed,how_we_operationalize,limitations,verified\n"
        "lit_seed,10.1000/seed,A seeded paper,2020,task,standard,metric,how,limits,yes\n",
        encoding="utf-8",
    )
    app = create_app(
        token=TOKEN,
        allowed_hosts=hosts,
        settings=Settings(seed=20261118, max_tokens=64, timeout_s=30),
        local_data_dir=local_data,
        connections_path=local_data / "connections.json",
        literature_path=literature,
        metadata_base=servers["crossref_base"],
        matrix_root=runs_root.parent / "matrix-runs",
        cache_root=runs_root.parent / "matrix-cache",
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_ready(f"http://127.0.0.1:{port}/api/health")
    try:
        yield f"http://127.0.0.1:{port}/#token={TOKEN}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app_module.run_root = real_run_root  # type: ignore[assignment]
        registry.run_root = registry_run_root  # type: ignore[assignment]
        _ProviderHandler.requests.clear()
        _ProviderHandler.delay_s = 0.0


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    """A headless Chromium via the installed Edge/Chrome, or None (tests skip)."""
    from playwright.sync_api import sync_playwright

    # Playwright's artifacts need a writable temp dir; the sandboxed TMP is not.
    temp = Path(__file__).resolve().parents[2] / ".tmp" / "playwright"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(temp)
    os.environ["TEMP"] = str(temp)
    try:
        context_manager = sync_playwright()
        playwright = context_manager.__enter__()
    except Exception as exc:
        pytest.skip(
            "the Playwright driver could not start in this environment, so the browser suite did not run: "
            f"{type(exc).__name__}: {str(exc).splitlines()[0][:140]} - see MANUAL_SMOKE.md"
        )
        return
    try:
        launched = None
        errors: list[str] = []
        for channel in ("msedge", "chrome", None):
            try:
                launched = (
                    playwright.chromium.launch(headless=True, channel=channel)
                    if channel
                    else playwright.chromium.launch(headless=True)
                )
                break
            except Exception as exc:
                errors.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0][:140]}")
        if launched is None:
            pytest.skip(
                "no browser could be started in this environment, so the browser suite did not run. "
                "Tried: " + " | ".join(errors) + " - see MANUAL_SMOKE.md for the click-through checklist."
            )
            return
        try:
            yield launched
        finally:
            launched.close()
    finally:
        context_manager.__exit__(None, None, None)


@pytest.fixture()
def page(browser: Any, app_url: str):
    if browser is None:
        pytest.skip("no browser available in this environment")
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        # Always start on the app origin: sessionStorage and fetch do not exist
        # on about:blank, and the token lives in sessionStorage.
        page.goto(app_url)
        page.wait_for_selector("#workspace-list", timeout=20_000)
        yield page
    finally:
        context.close()


@pytest.fixture()
def provider(servers: dict[str, Any]) -> dict[str, Any]:
    _ProviderHandler.delay_s = 0.0
    _ProviderHandler.requests.clear()
    return servers


@pytest.fixture()
def provider_state() -> Any:
    """The fake provider's request log and delay control."""
    return _ProviderHandler
