"""Tests for configuration, backend selection, client logging and hashing."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from medeval_core.backends import (
    BACKEND_API,
    BACKEND_MOCK,
    BackendUnavailable,
    probe_endpoint,
    select_backend,
)
from medeval_core.calllog import JsonlCallSink, read_calls, summarize_calls
from medeval_core.client import MockTransport, OpenAICompatibleTransport, make_client, parse_json_object
from medeval_core.config import load_settings
from medeval_core.hashing import prompt_hash, sha256_text, stable_json_dumps


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEDEVAL_BASE_URL",
        "MEDEVAL_MODEL",
        "MEDEVAL_API_KEY",
        "MEDEVAL_BACKEND",
        "MEDEVAL_HF_MODEL",
        "MEDEVAL_TIMEOUT_S",
        "MEDEVAL_MAX_TOKENS",
        "MEDEVAL_TEMPERATURE",
        "MEDEVAL_SEED",
        "MEDEVAL_RETRIES",
        "MEDEVAL_MAX_CALLS",
        "MEDEVAL_MAX_USD",
        "MEDEVAL_INPUT_USD_PER_1M",
        "MEDEVAL_OUTPUT_USD_PER_1M",
        "MEDEVAL_REASONING_USD_PER_1M",
        "MEDEVAL_PRICE_SOURCE",
        "MEDEVAL_PRICE_SOURCE_URL",
        "MEDEVAL_PRICE_SOURCE_DATE",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_stable_json_dumps_is_order_independent() -> None:
    assert stable_json_dumps({"a": 1, "b": 2}) == stable_json_dumps({"b": 2, "a": 1})


def test_prompt_hash_changes_with_prompt() -> None:
    first = prompt_hash(None, "prompt A", "v1")
    second = prompt_hash(None, "prompt B", "v1")
    assert first != second
    assert prompt_hash(None, "prompt A", "v1") == first
    assert prompt_hash(None, "prompt A", "v2") != first


def test_sha256_known_value() -> None:
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_load_settings_defaults(tmp_path: Path) -> None:
    settings = load_settings(dotenv_paths=[tmp_path / "absent.env"])
    assert settings.backend_override == "auto"
    assert settings.temperature == 0.0
    assert settings.has_api_credentials is False
    assert "api_key" not in settings.safe_dict()


def test_load_settings_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MEDEVAL_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("MEDEVAL_MODEL", "model-x")
    monkeypatch.setenv("MEDEVAL_API_KEY", "secret-key-value")
    monkeypatch.setenv("MEDEVAL_TEMPERATURE", "0.2")
    settings = load_settings(dotenv_paths=[tmp_path / "absent.env"])
    assert settings.has_api_credentials
    assert settings.temperature == 0.2
    assert settings.safe_dict()["api_key_present"] is True
    assert "secret-key-value" not in json.dumps(settings.safe_dict())


def test_dotenv_file_is_loaded_but_real_env_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MEDEVAL_MODEL=from-file\nMEDEVAL_BASE_URL=http://file.invalid/v1\n", encoding="utf-8")
    monkeypatch.setenv("MEDEVAL_MODEL", "from-env")
    settings = load_settings(dotenv_paths=[env_file])
    assert settings.model == "from-env"
    assert settings.base_url == "http://file.invalid/v1"
    assert settings.env_files_loaded == (str(env_file),)


def test_duplicate_dotenv_paths_are_loaded_once(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MEDEVAL_MODEL=from-file\n", encoding="utf-8")
    settings = load_settings(dotenv_paths=[env_file, env_file])
    assert settings.env_files_loaded == (str(env_file),)


def test_backend_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("medeval_core.backends.probe_endpoint", lambda *a, **k: None)
    selection = select_backend(load_settings(dotenv_paths=[]))
    assert selection.name == BACKEND_MOCK
    assert selection.pipeline_test_only is True
    assert "MOCK" in selection.label


def test_backend_prefers_configured_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDEVAL_BASE_URL", "http://api.invalid/v1")
    monkeypatch.setenv("MEDEVAL_MODEL", "model-x")
    monkeypatch.setenv("MEDEVAL_API_KEY", "k")
    selection = select_backend(load_settings(dotenv_paths=[]))
    assert selection.name == BACKEND_API
    assert selection.pipeline_test_only is False
    assert selection.as_dict()["pipeline_test_only"] is False


def test_backend_detects_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "medeval_core.backends.probe_endpoint",
        lambda url, timeout=1.5: "llama3:8b" if "11434" in url else None,
    )
    selection = select_backend(load_settings(dotenv_paths=[]))
    assert selection.name == "local-model-server"
    assert selection.model == "llama3:8b"


def test_explicit_mock_override() -> None:
    os.environ["MEDEVAL_BACKEND"] = "mock"
    try:
        selection = select_backend(load_settings(dotenv_paths=[]))
        assert selection.name == BACKEND_MOCK
    finally:
        del os.environ["MEDEVAL_BACKEND"]


def test_explicit_api_without_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDEVAL_BACKEND", "api")
    with pytest.raises(BackendUnavailable):
        select_backend(load_settings(dotenv_paths=[]))


def test_invalid_override_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDEVAL_BACKEND", "gpt5")
    with pytest.raises(BackendUnavailable):
        select_backend(load_settings(dotenv_paths=[]))


def test_probe_endpoint_returns_none_when_unreachable() -> None:
    assert probe_endpoint("http://127.0.0.1:9/v1", timeout_s=0.2) is None


def test_mock_client_logs_call(tmp_path: Path) -> None:
    settings = load_settings(dotenv_paths=[])
    selection = select_backend(settings)
    log_path = tmp_path / "calls.jsonl"
    client = make_client(selection, settings, log_path=log_path, run_id="run-test")
    record = client.complete("hello", system="you are a test", template_version="v1")
    assert record.output == "MOCK"
    assert record.backend == BACKEND_MOCK
    rows = read_calls(log_path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-test"
    assert rows[0]["prompt"] == "hello"
    assert rows[0]["prompt_sha256"] == record.prompt_sha256
    assert summarize_calls(rows)["n_calls"] == 1


def test_mock_client_uses_responder() -> None:
    settings = load_settings(dotenv_paths=[])
    selection = select_backend(settings)
    client = make_client(selection, settings, mock_responder=lambda messages, seed: f"seed={seed}")
    assert client.complete("x", seed=7).output == "seed=7"


def test_api_key_is_never_written_to_call_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = JsonlCallSink(tmp_path / "calls.jsonl")
    transport = MockTransport(model="mock-judge-v1")
    settings = load_settings(dotenv_paths=[])
    from medeval_core.client import LLMClient

    client = LLMClient(transport, settings, sink=sink, run_id="r1")
    client.complete("prompt with no secret")
    text = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert "api_key" not in text


def test_fake_key_is_redacted_from_every_output_field(tmp_path: Path) -> None:
    fake = "FAKE_DEEPSEEK_KEY_123"
    sink = JsonlCallSink(tmp_path / "calls.jsonl", secrets=(fake,))
    settings = load_settings(dotenv_paths=[])
    from medeval_core.client import LLMClient
    client = LLMClient(MockTransport(responder=lambda *_: fake), settings, sink=sink, run_id="redaction")
    client.complete(f"prompt {fake}", system=f"system {fake}", metadata={"echo": fake})
    assert fake not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


def test_openai_transport_redacts_key_on_error() -> None:
    transport = OpenAICompatibleTransport(
        base_url="http://127.0.0.1:9/v1", model="m", api_key="supersecret", retries=0
    )
    result = transport.complete(
        [{"role": "user", "content": "hi"}],
        temperature=0.0,
        seed=1,
        max_tokens=8,
        timeout_s=0.2,
    )
    assert result.error is not None
    assert "supersecret" not in result.error


def test_parse_json_object_handles_fences() -> None:
    assert parse_json_object('```json\n{"score": 2}\n```') == {"score": 2}
    assert parse_json_object("no json here") is None
    assert parse_json_object('{"a": 1} trailing') == {"a": 1}
