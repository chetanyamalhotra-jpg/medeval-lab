"""Security and connection tests: launch token, Host/Origin, memory-only keys.

These tests cover the M2 guarantees that matter most:

* no ``/api/*`` request works without the launch token, and a wrong token fails;
* a non-loopback ``Host`` header is refused (DNS-rebinding protection) and a
  cross-site ``Origin`` is refused (another page cannot drive the API);
* keys live only in process memory: the connections file never contains a key,
  the browser-facing view shows only ``key_set`` and the last 4 characters, and
  stopping the app forgets every key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medeval_web.app import create_app
from medeval_web.connections import (
    PRESETS,
    ConnectionStore,
    KeyStore,
    RoleAssignments,
)

TOKEN = "fake-token-abcdef"
HOSTS = {"127.0.0.1:8765", "localhost:8765", "testserver"}
AUTH = {"X-Medeval-Token": TOKEN}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "study_runs"
    root.mkdir()
    app = create_app(
        token=TOKEN,
        allowed_hosts=HOSTS,
        connections_path=tmp_path / "connections.json",
        local_data_dir=tmp_path / "local-data",
    )
    return TestClient(app)


def authed(client: TestClient, method: str, path: str, **kwargs) -> object:
    headers = {**AUTH, **(kwargs.pop("headers", {}) or {})}
    return client.request(method, path, headers=headers, **kwargs)


# --------------------------------------------------------------------------- #
# Launch token
# --------------------------------------------------------------------------- #
def test_api_requires_the_launch_token(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 403
    assert client.get("/api/runs").status_code == 403
    assert client.get("/api/health", headers={"X-Medeval-Token": "wrong-token"}).status_code == 403
    assert client.get("/api/health", headers=AUTH).status_code == 200


def test_the_token_is_never_accepted_in_the_url(client: TestClient) -> None:
    """A token in a query string would leak into logs; only the header counts."""
    response = client.get("/api/health", params={"token": TOKEN})
    assert response.status_code == 403


def test_static_shell_is_served_without_a_token(client: TestClient) -> None:
    """The shell must load so the browser can read the token from the fragment."""
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


# --------------------------------------------------------------------------- #
# Host and Origin
# --------------------------------------------------------------------------- #
def test_non_loopback_host_is_refused(client: TestClient) -> None:
    response = client.get("/api/health", headers={**AUTH, "Host": "evil.example.com:8765"})
    assert response.status_code == 403


def test_localhost_host_is_accepted(client: TestClient) -> None:
    response = client.get("/api/health", headers={**AUTH, "Host": "localhost:8765"})
    assert response.status_code == 200


def test_cross_site_origin_is_refused(client: TestClient) -> None:
    response = client.get(
        "/api/health",
        headers={**AUTH, "Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403


def test_same_origin_is_accepted(client: TestClient) -> None:
    response = client.get(
        "/api/health",
        headers={**AUTH, "Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200


def test_no_cors_headers_are_ever_sent(client: TestClient) -> None:
    response = client.get("/api/health", headers=AUTH)
    assert "access-control-allow-origin" not in {key.lower() for key in response.headers}


# --------------------------------------------------------------------------- #
# Connections and keys
# --------------------------------------------------------------------------- #
def test_connection_crud_and_key_tail(client: TestClient) -> None:
    created = authed(client, "POST", "/api/connections", json={
        "name": "DeepSeek main",
        "provider_preset": "deepseek",
        "model": "deepseek-flash",
        "family_label": "DeepSeek",
        "key": "sk-test-1234567890abcd",
    })
    assert created.status_code == 201
    payload = created.json()
    assert payload["key_set"] is True
    assert payload["key_last4"] == "abcd"
    assert "key" not in payload
    assert "sk-test-1234567890abcd" not in created.text

    listed = authed(client, "GET", "/api/connections").json()
    assert len(listed) == 1
    assert listed[0]["key_last4"] == "abcd"

    forgotten = authed(client, "DELETE", f"/api/connections/{payload['id']}/key")
    assert forgotten.json()["key_set"] is False
    assert authed(client, "GET", "/api/connections").json()[0]["key_last4"] is None


def test_the_key_never_touches_disk(client: TestClient, tmp_path: Path) -> None:
    created = authed(client, "POST", "/api/connections", json={
        "name": "No disk",
        "provider_preset": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "some/model",
        "key": "sk-or-v1-fake-never-on-disk-9988",
    }).json()
    stored = (tmp_path / "connections.json").read_text(encoding="utf-8")
    assert "sk-or-v1-fake-never-on-disk-9988" not in stored
    assert "api_key" not in stored
    authed(client, "DELETE", f"/api/connections/{created['id']}")


def test_key_store_is_memory_only_and_forgets_everything() -> None:
    store = KeyStore()
    store.set("a", "sk-key-aaaa")
    store.set("b", "sk-key-bbbb")
    assert store.tail("a") == "aaaa"
    assert store.get("missing") is None
    assert store.forget_all() == 2
    assert store.get("a") is None
    assert store.all_keys() == ()
    with pytest.raises(ValueError):
        store.set("c", "   ")


def test_screening_model_cannot_also_be_a_judge(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "connections.json")
    store.create({"name": "One", "provider_preset": "deepseek", "base_url": "https://api.deepseek.com"})
    connection_id = store.list()[0].id

    _roles, problems = store.set_roles({
        "judges": [{"role": "judge_1", "connection_id": connection_id}],
        "screening": connection_id,
    })
    assert problems and "must not also be a judge" in problems[0]
    assert store.roles().screening is None

    _roles, problems = store.set_roles({
        "judges": [
            {"role": "judge_1", "connection_id": connection_id},
            {"role": "judge_2", "connection_id": connection_id},
        ],
    })
    assert problems and "cannot hold two judge roles" in problems[0]


def test_role_validation_requires_a_judge(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "connections.json")
    _roles, problems = store.set_roles({"judges": []})
    assert problems and "at least one judge" in problems[0]
    assert RoleAssignments().as_dict()["screening"] is None


def test_preset_base_urls_match_verified_documentation() -> None:
    """These URLs were read from each provider's official docs on 2026-11-18."""
    assert PRESETS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert PRESETS["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert PRESETS["openai"]["base_url"] == "https://api.openai.com/v1"
    assert PRESETS["ollama"]["base_url"] == "http://localhost:11434/v1"
    assert PRESETS["ollama"]["requires_key"] is False


def test_connection_validation_rejects_bad_fields(client: TestClient) -> None:
    response = authed(client, "POST", "/api/connections", json={
        "name": "Bad",
        "provider_preset": "custom",
        "base_url": "not-a-url",
    })
    assert response.status_code == 400
    assert "base_url" in response.json()["detail"]


def test_settings_page_shows_key_tail_only(client: TestClient) -> None:
    created = authed(client, "POST", "/api/connections", json={
        "name": "Tail check",
        "provider_preset": "openai",
        "model": "gpt-something",
        "key": "sk-fake-secret-value-7777",
    }).json()
    listing = authed(client, "GET", "/api/connections").json()
    row = next(row for row in listing if row["id"] == created["id"])
    text = json.dumps(listing)
    assert row["key_last4"] == "7777"
    assert "7777" in text and "sk-fake-secret-value" not in text
