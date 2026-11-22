"""Shared pytest fixtures.

Adds ``packages/`` to ``sys.path`` so the test suite runs without installing the
project (CI installs it editable, but a plain ``pytest`` from the repo root must
also work).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
if str(PACKAGES) not in sys.path:
    sys.path.insert(0, str(PACKAGES))


_REAL_SESSION_REQUEST = requests.sessions.Session.request


@pytest.fixture(autouse=True)
def _isolate_model_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test offline and independent of caller credentials or `.env`.

    Tests marked ``@pytest.mark.allow_loopback`` may still talk to a local fake
    server on 127.0.0.1/localhost — that is how the OpenAI-compatible transport
    is exercised end to end without touching any real endpoint. Everything else
    is refused.
    """
    for name in list(os.environ):
        if name.startswith("MEDEVAL_"):
            monkeypatch.delenv(name, raising=False)
    # ``load_settings`` honours this test-only switch for its *default* paths;
    # explicit temporary dotenv paths remain testable.
    monkeypatch.setenv("PYTEST_MEDEVAL_DISABLE_DOTENV", "1")

    allow_loopback = request.node.get_closest_marker("allow_loopback") is not None

    def no_network(session: Any, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        if allow_loopback:
            from urllib.parse import urlsplit

            host = (urlsplit(str(url)).hostname or "").lower()
            if host in {"127.0.0.1", "localhost", "::1"}:
                return _REAL_SESSION_REQUEST(session, method, url, *args, **kwargs)
        raise requests.RequestException("network disabled by test fixture")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    # Backend detection normally uses urllib for a local `/models` probe.
    # Stub the transport itself so direct probe tests are offline as well.
    import medeval_core.backends as backends
    monkeypatch.setattr(backends, "_http_get_json", lambda *args, **kwargs: None)


def make_item(**overrides: Any) -> dict[str, Any]:
    """A minimal valid synthetic item; overrides are applied on top."""
    item: dict[str, Any] = {
        "id": "test-0001",
        "text": "A 54-year-old man presents with crushing central chest pain radiating to the left arm.",
        "options": [
            "Acute myocardial infarction",
            "Pulmonary embolism",
            "Aortic dissection",
            "Pericarditis",
        ],
        "gold_answer": "Acute myocardial infarction",
        "label_source": "synthetic",
        "provenance": {
            "doi": None,
            "section": None,
            "licence": "synthetic-original",
            "extraction_date": "2026-01-15",
            "source_title": None,
        },
        "publication_date": "2026-01-15",
        "split": "dev",
        "version": "v0.1",
        "review_status": "single-rater, author-reviewed",
        "human_reference": None,
        "notes": "Synthetic vignette written for the v0.1 seed set.",
    }
    item.update(overrides)
    return item


@pytest.fixture()
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """Workspace-local replacement for pytest's built-in ``tmp_path``.

    The built-in creates owner-only temporary directories, which some restricted
    Windows tokens (and therefore CI-in-a-sandbox setups) cannot read back. This
    version keeps everything under ``.tmp/pytest-tmp`` in the repo.
    """
    import shutil

    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in request.node.name)[:80]
    path = ROOT / ".tmp" / "pytest-tmp" / safe
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def valid_item() -> dict[str, Any]:
    return make_item()


@pytest.fixture()
def project_root() -> Path:
    return ROOT
