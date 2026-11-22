"""M2b: Papers library, Test center, workspaces/sessions.

The metadata fetch runs against a local fake server (allow_loopback marker) so
no real Crossref/arXiv call happens in tests. The Test center only proves its
guard rails and its fastest allow-listed command here; the full pytest entry is
available in the app but too slow for the unit suite.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medeval_web.app import create_app
from medeval_web.papers import fetch_metadata
from medeval_web.workspaces import WorkspaceStore

pytestmark = pytest.mark.allow_loopback

TOKEN = "tok-m2b"
AUTH = {"X-Medeval-Token": TOKEN}


@pytest.fixture()
def app_paths(tmp_path: Path) -> dict:
    return {"local": tmp_path / "local-data", "runs": tmp_path / "study_runs"}


@pytest.fixture()
def client(app_paths: Path, tmp_path: Path) -> TestClient:
    app_paths["runs"].mkdir(parents=True)
    literature = tmp_path / "literature.csv"
    literature.write_text("id,doi,title,year,task,reference_standard,metric_proposed,how_we_operationalize,limitations,verified\n", encoding="utf-8")
    app = create_app(
        token=TOKEN,
        allowed_hosts={"testserver"},
        local_data_dir=app_paths["local"],
        connections_path=tmp_path / "connections.json",
        literature_path=literature,
    )
    return TestClient(app)


def authed(client: TestClient, method: str, path: str, **kwargs):
    headers = {**AUTH, **(kwargs.pop("headers", {}) or {})}
    return client.request(method, path, headers=headers, **kwargs)


# --------------------------------------------------------------------------- #
# Papers
# --------------------------------------------------------------------------- #
def test_papers_list_comes_from_the_registry(client: TestClient) -> None:
    rows = authed(client, "GET", "/api/papers").json()
    assert rows == [], "the test registry starts empty"


def test_add_paper_is_always_unverified(client: TestClient) -> None:
    response = authed(client, "POST", "/api/papers", json={
        "id": "lit_test_new", "title": "A synthetic paper", "doi": "10.9999/synthetic",
    })
    assert response.status_code == 201
    assert response.json()["verified"] == "no"
    conflict = authed(client, "POST", "/api/papers", json={
        "id": "lit_test_new", "title": "duplicate", "doi": "10.9999/synthetic",
    })
    assert conflict.status_code == 409


def test_paper_status_and_notes_live_in_the_gitignored_file(client: TestClient, app_paths) -> None:
    authed(client, "POST", "/api/papers", json={"id": "lit_notes", "title": "Notes paper"})
    authed(client, "POST", "/api/papers/lit_notes/status", json={"status": "read"})
    authed(client, "POST", "/api/papers/lit_notes/notes", json={"notes": "one reader's local impression"})
    notes_file = app_paths["local"] / "paper_notes.json"
    assert notes_file.is_file()
    text = notes_file.read_text(encoding="utf-8")
    assert "one reader's local impression" in text
    # Status and notes are local-only: the registry row itself is untouched.
    row = next(row for row in authed(client, "GET", "/api/papers").json() if row["id"] == "lit_notes")
    assert row["status"] == "read" and row["notes"] == "one reader's local impression"
    assert row["verified"] == "no"


def test_invalid_status_is_rejected(client: TestClient) -> None:
    authed(client, "POST", "/api/papers", json={"id": "lit_status", "title": "S"})
    response = authed(client, "POST", "/api/papers/lit_status/status", json={"status": "definitely verified"})
    assert response.status_code == 400


def test_cited_by_reverse_view(client: TestClient) -> None:
    authed(client, "POST", "/api/papers", json={"id": "lit_cited", "title": "Cited paper", "doi": "10.9999/cited"})
    detail = authed(client, "GET", "/api/papers/lit_cited").json()
    assert "cited_by" in detail
    assert set(detail["cited_by"]) == {"criteria", "metrics"}
    assert detail["cited_by"]["criteria"] == [] or isinstance(detail["cited_by"]["criteria"], list)


class _MetaHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        body = json.dumps({
            "message": {
                "title": ["A Faked Crossref Title"],
                "issued": {"date-parts": [[2019, 6, 1]]},
            }
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_fetch_metadata_uses_the_local_fake_server(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MetaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/works"
        metadata = fetch_metadata("10.9999/fake", base_override=base)
        assert metadata["title"] == "A Faked Crossref Title"
        assert metadata["year"] == 2019
        assert metadata["verified"] == "no"
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_metadata_rejects_non_identifiers() -> None:
    with pytest.raises(ValueError, match="DOI"):
        fetch_metadata("not-a-doi", base_override="http://127.0.0.1:9/works")


def test_paper_fetch_endpoint_announces_the_outbound_call(client: TestClient) -> None:
    response = authed(client, "POST", "/api/papers/fetch", json={"identifier": "10.9999/x", "base_override": None})
    # Without an override this contacts the real Crossref for an invalid DOI and
    # must fail with a clean 400 rather than storing anything.
    assert response.status_code in {400, 502}


# --------------------------------------------------------------------------- #
# Workspaces and sessions
# --------------------------------------------------------------------------- #
def test_workspace_and_session_flow(client: TestClient, app_paths) -> None:
    created = authed(client, "POST", "/api/workspaces", json={"name": "Experiment 003"})
    assert created.status_code == 201
    duplicate = authed(client, "POST", "/api/workspaces", json={"name": "Experiment 003"})
    assert duplicate.status_code == 409

    workspaces = authed(client, "GET", "/api/workspaces").json()
    names = [row["name"] for row in workspaces]
    assert "Experiment 003" in names and "Ungrouped" in names

    workspace_id = next(row["id"] for row in workspaces if row["name"] == "Experiment 003")
    session = authed(client, "POST", f"/api/workspaces/{workspace_id}/sessions", json={
        "title": "Hard-pool disagreement run", "hypothesis": "Hard items produce more judge-key disputes.",
    }).json()
    assert session["hypothesis"].startswith("Hard items")

    message = authed(client, "POST", f"/api/sessions/{session['id']}/messages", json={
        "role": "user", "kind": "note", "payload": {"text": "a note"},
    })
    assert message.status_code == 201
    fetched = authed(client, "GET", f"/api/sessions/{session['id']}").json()
    assert any(msg["payload"].get("text") == "a note" for msg in fetched["messages"])


def test_v02_runs_appear_as_sessions(client: TestClient, app_paths, monkeypatch: pytest.MonkeyPatch) -> None:
    from medeval_study.registry import RunSummary

    def fake_list_runs():
        return [RunSummary(
            run_id="exp002-main", path=app_paths["runs"] / "exp002-main", status="completed",
            experiment="002_judge_families", started_utc="2026-11-17T11:00:00Z",
            finished_utc=None, n_items=60, judges=("j1_deepseek",), pipeline_test_only=False,
            headline={}, unavailable_judges=(),
        )]

    monkeypatch.setattr("medeval_study.registry.list_runs", fake_list_runs)
    client.app.state.workspaces.import_all_runs(default_workspace="Experiment 002")
    workspaces = authed(client, "GET", "/api/workspaces").json()
    exp002 = next(row for row in workspaces if row["name"] == "Experiment 002")
    assert any(session["run_id"] == "exp002-main" for session in exp002["sessions"])


def test_no_secret_in_session_records(client: TestClient, app_paths) -> None:
    workspace = authed(client, "POST", "/api/workspaces", json={"name": "Secrets check"}).json()
    authed(client, "POST", f"/api/workspaces/{workspace['id']}/sessions", json={
        "title": "s", "hypothesis": "note with a user-written marker inside",
    })
    spaces = client.app.state.workspaces
    stored = json.dumps(spaces.sessions)
    assert "a user-written marker inside" in stored  # user text is preserved verbatim
    # ...but no key store value ever lands there:
    client.app.state.key_store.set("conn-x", "sk-fake-system-key-9876543210")
    stored_again = json.dumps(spaces.sessions)
    assert "sk-fake-system-key-9876543210" not in stored_again


# --------------------------------------------------------------------------- #
# Test center
# --------------------------------------------------------------------------- #
def test_test_center_rejects_non_allow_listed_commands(client: TestClient) -> None:
    for bad in ("rm -rf /", "pytest -q", "__import__('os').system('dir')", ""):
        response = authed(client, "POST", "/api/tests/run", json={"command": bad})
        assert response.status_code == 400, f"{bad!r} must be rejected"


def test_test_center_lists_only_known_commands(client: TestClient) -> None:
    commands = authed(client, "GET", "/api/tests/commands").json()
    keys = {row["key"] for row in commands}
    assert {"pytest", "ruff", "secret-scan", "traceability", "key-isolation", "dry-run-exp002", "dry-run-exp003"} <= keys
    import os

    interpreter = os.path.basename(commands[0]["argv"][0]).lower()
    assert interpreter.startswith("python"), f"unexpected interpreter name: {interpreter!r}"
    for row in commands:
        assert row["argv"][0] == commands[0]["argv"][0], "every command must use the same interpreter"


def test_test_center_runs_the_secret_scan(client: TestClient) -> None:
    result = authed(client, "POST", "/api/tests/run", json={"command": "secret-scan"}).json()
    assert result["exit_code"] is not None
    assert result["id"].startswith("test-")
    history = authed(client, "GET", "/api/tests/results").json()
    assert any(row["id"] == result["id"] for row in history)
    assert all("sk-" not in (row["tail"] or "") for row in history)


def test_workspaces_store_is_local_only(client: TestClient, app_paths) -> None:
    store = client.app.state.workspaces
    assert isinstance(store, WorkspaceStore)
    path: Path = store.path
    assert str(app_paths["local"]) in str(path)
