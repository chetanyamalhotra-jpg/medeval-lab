"""M2 live-run robustness, exercised end to end against the loopback fake server.

Covers: retry budgets/backoff/``Retry-After``, auth failures (401/403) that are
never retried, timeouts and connection resets, usage-token parsing, the
reasoning-token-exhaustion signal, optional verdict logprobs, the per-connection
concurrency cap and key redaction in error messages.

No real provider is contacted: every request goes to ``127.0.0.1`` via
``tests/fake_openai_server.py`` (hence ``pytest.mark.allow_loopback``), and the
key below is an obvious fake.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
import requests

from medeval_core.calllog import JsonlCallSink
from medeval_core.client import (
    AuthError,
    LLMClient,
    OpenAICompatibleTransport,
    TransportConnectionError,
    TransportError,
    TransportTimeout,
    _extract_verdict_logprob,
    backoff_delay,
    parse_retry_after,
    parse_usage,
)
from medeval_core.config import Settings, load_settings
from medeval_study.judging import classify_failure
from tests.fake_openai_server import FAKE_FIRST_TOKEN_LOGPROB, FakeOpenAIServer

pytestmark = pytest.mark.allow_loopback

FAKE_KEY = "sk-fake-m2-robustness-key-0123456789"
MESSAGES = [{"role": "user", "content": "judge this candidate answer"}]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_settings(**overrides) -> Settings:
    payload = {
        "max_tokens": 64,
        "temperature": 0.0,
        "seed": 1,
        "timeout_s": 2.0,
        "max_concurrency": 2,
        "api_key": FAKE_KEY,
    }
    payload.update(overrides)
    return Settings(**payload)


def make_transport(server: FakeOpenAIServer, **overrides) -> tuple[OpenAICompatibleTransport, list[float]]:
    """Transport with an injected sleep recorder, so no test ever sleeps."""
    sleeps: list[float] = []
    options: dict = {
        "base_url": server.base_url,
        "model": "fake-model-a",
        "api_key": FAKE_KEY,
        "retries": 0,
        "max_attempts": 1,
        "base_delay_s": 0.5,
        "max_delay_s": 8.0,
        "sleep_fn": sleeps.append,
    }
    options.update(overrides)
    return OpenAICompatibleTransport(**options), sleeps


def make_client(
    server: FakeOpenAIServer,
    *,
    settings: Settings | None = None,
    sink: JsonlCallSink | None = None,
    **transport_overrides,
) -> tuple[LLMClient, list[float]]:
    transport, sleeps = make_transport(server, **transport_overrides)
    client = LLMClient(transport, settings or make_settings(), sink=sink)
    return client, sleeps


def run_once(transport: OpenAICompatibleTransport, *, timeout_s: float = 2.0, max_tokens: int = 64):
    return transport.complete(MESSAGES, temperature=0.0, seed=1, max_tokens=max_tokens, timeout_s=timeout_s)


# --------------------------------------------------------------------------- #
# 1. Happy path: usage fields and existing behaviour
# --------------------------------------------------------------------------- #
def test_verdicts_mode_parses_text_and_usage() -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert result.error is None
    assert '"verdict": "CORRECT"' in result.text
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (20, 12, 32)
    assert result.reasoning_tokens == 0
    assert result.verdict_logprob is None
    assert result.reasoning_exhausted is False
    assert result.attempts == 1
    assert sleeps == []
    assert len(posts) == 1
    # Existing request recording (auth/headers/bodies) is intact.
    assert posts[0]["auth_matches_expected"] is True
    assert posts[0]["headers"]["Authorization"].startswith("Bearer sk-fake")
    assert posts[0]["body"]["model"] == "fake-model-a"


# --------------------------------------------------------------------------- #
# 2. Retries: 5xx + exponential backoff, bounded budget
# --------------------------------------------------------------------------- #
def test_http_500_is_retried_with_exponential_backoff_then_surfaces() -> None:
    with FakeOpenAIServer(mode="http_500", expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server, max_attempts=3)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 3, "exactly the configured attempt budget is used"
    assert len(sleeps) == 2, "no sleep after the final failed attempt"
    assert 0.5 <= sleeps[0] <= 0.5 * 1.25
    assert 1.0 <= sleeps[1] <= 1.0 * 1.25
    assert result.error is not None and "HTTP 500" in result.error
    assert result.error_type == "server_error"
    assert result.status_code == 500
    assert result.attempts == 3


def test_http_500_budget_defaults_to_four_attempts_when_configured() -> None:
    with FakeOpenAIServer(mode="http_500", expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server, max_attempts=4)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 4
    assert len(sleeps) == 3
    assert result.attempts == 4


# --------------------------------------------------------------------------- #
# 3. Retry-After is honoured (both header forms)
# --------------------------------------------------------------------------- #
def test_http_429_retry_after_zero_retries_instantly() -> None:
    with FakeOpenAIServer(mode="http_429", expected_key=FAKE_KEY, retry_after="0") as server:
        transport, sleeps = make_transport(server, max_attempts=3)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 3
    assert sleeps == [0.0, 0.0], "Retry-After wins over exponential backoff"
    assert result.error_type == "rate_limit"
    assert result.attempts == 3


def test_http_429_numeric_retry_after_is_used_verbatim() -> None:
    with FakeOpenAIServer(mode="http_429", expected_key=FAKE_KEY, retry_after="2") as server:
        transport, sleeps = make_transport(server, max_attempts=2)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 2
    assert sleeps == [2.0]
    assert result.error_type == "rate_limit"


def test_parse_retry_after_accepts_seconds_and_http_dates() -> None:
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("not a date") is None
    future = datetime.now(UTC) + timedelta(seconds=5)
    delay = parse_retry_after(future.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert delay is not None and 3.5 <= delay <= 5.5
    past = datetime.now(UTC) - timedelta(seconds=30)
    assert parse_retry_after(past.strftime("%a, %d %b %Y %H:%M:%S GMT")) == 0.0


# --------------------------------------------------------------------------- #
# 4. Never retried: 4xx client errors and provider refusals
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [400, 404, 422])
def test_client_errors_are_never_retried(status: int) -> None:
    with FakeOpenAIServer(mode="http_error", expected_key=FAKE_KEY, error_status=status) as server:
        transport, sleeps = make_transport(server, max_attempts=4)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 1
    assert sleeps == []
    assert result.error_type == "http_error"
    assert result.status_code == status
    assert result.error is not None and f"HTTP {status}" in result.error


def test_provider_refusal_is_not_retried_and_classifies_as_refused() -> None:
    with FakeOpenAIServer(mode="refusal", expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server, max_attempts=4)
        client = LLMClient(transport, make_settings())
        record = client.complete("judge this candidate answer")
        posts = list(server.post_requests)
    assert len(posts) == 1, "a refusal is a completed answer, never retried"
    assert sleeps == []
    assert record.error is not None and record.error.startswith("refused")
    assert record.metadata["error_type"] == "refused"
    assert "assist" in record.output.lower()
    assert classify_failure(record.error) == "refused"


# --------------------------------------------------------------------------- #
# 5. Auth failures (401/403): distinct exception, no retry, no key leak
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode,status", [("http_401", 401), ("http_403", 403)])
def test_auth_failure_raises_auth_error_without_retry(mode: str, status: int) -> None:
    with FakeOpenAIServer(mode=mode, expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server, max_attempts=4)
        with pytest.raises(AuthError) as info:
            run_once(transport)
        posts = list(server.post_requests)
    assert info.value.status_code == status
    assert len(posts) == 1, "auth failures must never be retried"
    assert sleeps == []
    # The fake server echoes the bearer token back in its error body; the
    # transport must redact it before it reaches the exception.
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in info.value.provider_message


def test_auth_error_is_recorded_not_raised_by_llm_client(tmp_path) -> None:
    log_path = tmp_path / "calls.jsonl"
    with FakeOpenAIServer(mode="http_401", expected_key=FAKE_KEY) as server:
        sink = JsonlCallSink(log_path, secrets=(FAKE_KEY,))
        client, sleeps = make_client(server, settings=make_settings(), sink=sink, max_attempts=4)
        record = client.complete("judge this candidate answer")
        posts = list(server.post_requests)
        written = log_path.read_text(encoding="utf-8")
    assert len(posts) == 1
    assert sleeps == []
    assert record.error is not None and record.error.startswith("HTTP 401")
    assert record.metadata["error_type"] == "auth"
    assert record.metadata["status_code"] == 401
    assert record.output == ""
    # Existing failure classification would see this as an API failure.
    assert classify_failure(f"api_error: {record.error}") == "api_error"
    assert FAKE_KEY not in json.dumps(record.metadata)
    assert FAKE_KEY not in (record.error or "")
    assert FAKE_KEY not in written


# --------------------------------------------------------------------------- #
# 6. Timeouts and connection resets: distinct errors, retried, then surfaced
# --------------------------------------------------------------------------- #
def test_timeout_mode_is_retried_then_surfaces_as_timeout_error() -> None:
    with FakeOpenAIServer(mode="timeout", expected_key=FAKE_KEY, timeout_seconds=1.0) as server:
        transport, sleeps = make_transport(server, max_attempts=3)
        result = run_once(transport, timeout_s=0.1)
        posts = list(server.post_requests)
    assert len(posts) == 3, "a timeout may be transient, so the same budget is used"
    assert len(sleeps) == 2
    assert result.error_type == "timeout"
    assert result.error is not None and "timeout" in result.error
    assert result.attempts == 3
    assert FAKE_KEY not in (result.error or "")


def test_connection_reset_is_retried_then_surfaces_as_connection_error() -> None:
    with FakeOpenAIServer(mode="reset", expected_key=FAKE_KEY) as server:
        transport, sleeps = make_transport(server, max_attempts=3)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 3
    assert len(sleeps) == 2
    assert result.error_type == "connection"
    assert result.error is not None and "connection" in result.error
    assert result.attempts == 3


def test_slow_mode_succeeds_when_the_timeout_allows_it() -> None:
    with FakeOpenAIServer(mode="slow", expected_key=FAKE_KEY, slow_seconds=0.2) as server:
        transport, sleeps = make_transport(server)
        result = run_once(transport, timeout_s=3.0)
        posts = list(server.post_requests)
    assert result.error is None
    assert result.latency_ms >= 150.0
    assert len(posts) == 1
    assert sleeps == []


def test_physical_timeout_and_connection_error_map_to_distinct_exceptions(monkeypatch) -> None:
    # Build a transport without a server: the request itself is monkeypatched.
    transport = OpenAICompatibleTransport(base_url="http://127.0.0.1:9/v1", model="m", api_key=FAKE_KEY, retries=0)

    def raise_read_timeout(*args, **kwargs):
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "post", raise_read_timeout)
    with pytest.raises(TransportTimeout):
        transport._post_once(MESSAGES, temperature=0.0, seed=1, max_tokens=8, timeout_s=1.0)

    def raise_reset(*args, **kwargs):
        raise requests.ConnectionError("connection reset by peer")

    monkeypatch.setattr(requests, "post", raise_reset)
    with pytest.raises(TransportConnectionError):
        transport._post_once(MESSAGES, temperature=0.0, seed=1, max_tokens=8, timeout_s=1.0)

    assert issubclass(AuthError, TransportError)
    assert not issubclass(TransportTimeout, TransportConnectionError)
    assert not issubclass(TransportConnectionError, TransportTimeout)


# --------------------------------------------------------------------------- #
# 7. Empty replies and reasoning-token exhaustion
# --------------------------------------------------------------------------- #
def test_empty_mode_returns_success_with_empty_text() -> None:
    with FakeOpenAIServer(mode="empty", expected_key=FAKE_KEY) as server:
        transport, _sleeps = make_transport(server)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert len(posts) == 1
    assert result.error is None
    assert result.text == ""
    assert result.output_tokens == 12
    assert result.reasoning_exhausted is False


@pytest.mark.parametrize("mode", ["truncated", "reasoning_exhausted"])
def test_reasoning_budget_exhaustion_sets_the_flag(mode: str) -> None:
    with FakeOpenAIServer(mode=mode, expected_key=FAKE_KEY, truncate_tokens=64) as server:
        transport, _sleeps = make_transport(server)
        client = LLMClient(transport, make_settings(max_tokens=64))
        record = client.complete("judge this candidate answer")
    assert record.error is None
    assert record.output == ""
    assert record.metadata["reasoning_exhausted"] is True
    assert record.metadata["reasoning_tokens"] == 64
    assert record.metadata["output_tokens"] == 64
    assert record.metadata["reasoning_tokens"] >= 0.9 * record.max_tokens


def test_reasoning_flag_is_false_when_visible_text_survives_a_short_budget() -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        transport, _sleeps = make_transport(server)
        result = run_once(transport, max_tokens=8)
    assert result.text.strip()
    assert result.reasoning_exhausted is False


# --------------------------------------------------------------------------- #
# 8. Usage parsing robustness
# --------------------------------------------------------------------------- #
def test_parse_usage_handles_provider_variants() -> None:
    openai_style = {
        "prompt_tokens": 20,
        "completion_tokens": 12,
        "total_tokens": 32,
        "completion_tokens_details": {"reasoning_tokens": 7},
    }
    assert parse_usage(openai_style) == {
        "prompt_tokens": 20,
        "completion_tokens": 12,
        "total_tokens": 32,
        "reasoning_tokens": 7,
    }
    alternate = {
        "input_tokens": 5,
        "output_tokens": 9,
        "output_tokens_details": {"reasoning_tokens": 3},
    }
    assert parse_usage(alternate) == {
        "prompt_tokens": 5,
        "completion_tokens": 9,
        "total_tokens": 14,
        "reasoning_tokens": 3,
    }
    flat = {"prompt_tokens": 1, "completion_tokens": 2, "reasoning_tokens": 2}
    assert parse_usage(flat)["reasoning_tokens"] == 2
    assert parse_usage(flat)["total_tokens"] == 3
    # Anything unexpected degrades to zero instead of raising.
    assert parse_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}
    assert parse_usage({"prompt_tokens": "n/a", "completion_tokens": None})["total_tokens"] == 0
    assert parse_usage({"usage": {"prompt_tokens": 1}})["prompt_tokens"] == 0


# --------------------------------------------------------------------------- #
# 9. Optional verdict logprob
# --------------------------------------------------------------------------- #
def test_logprobs_mode_captures_the_first_token_logprob() -> None:
    with FakeOpenAIServer(mode="logprobs", expected_key=FAKE_KEY) as server:
        transport, _sleeps = make_transport(server, request_logprobs=True)
        client = LLMClient(transport, make_settings())
        record = client.complete("judge this candidate answer")
        posts = list(server.post_requests)
    assert posts[0]["logprobs_requested"] is True
    assert record.metadata["verdict_logprob"] == pytest.approx(FAKE_FIRST_TOKEN_LOGPROB)
    assert '"verdict": "CORRECT"' in record.output


def test_verdict_logprob_is_none_when_absent() -> None:
    with FakeOpenAIServer(mode="verdicts", expected_key=FAKE_KEY) as server:
        transport, _sleeps = make_transport(server)
        result = run_once(transport)
        posts = list(server.post_requests)
    assert posts[0]["logprobs_requested"] is None
    assert result.verdict_logprob is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "x"}}]},
        {"choices": [{"message": {"content": "x"}, "logprobs": None}]},
        {"choices": [{"logprobs": {"content": "not a list"}}]},
        {"choices": [{"logprobs": {"content": [42]}}]},
        {"choices": [{"logprobs": {"content": [{"logprob": "low"}]}}]},
        {"choices": [{"logprobs": {"content": [{"logprob": True}]}}]},
    ],
)
def test_verdict_logprob_parser_never_breaks_on_unexpected_shapes(payload: dict) -> None:
    assert _extract_verdict_logprob(payload) is None


# --------------------------------------------------------------------------- #
# 10. Per-connection concurrency cap
# --------------------------------------------------------------------------- #
def test_per_connection_concurrency_limit_bounds_in_flight_calls() -> None:
    with FakeOpenAIServer(mode="slow", expected_key=FAKE_KEY, slow_seconds=0.1) as server:
        transport = OpenAICompatibleTransport(
            base_url=server.base_url, model="fake-model-a", api_key=FAKE_KEY, retries=0, max_attempts=1
        )
        client = LLMClient(transport, make_settings(max_concurrency=2, timeout_s=5.0))
        barrier = threading.Barrier(6)
        records = []
        records_lock = threading.Lock()

        def worker(index: int) -> None:
            barrier.wait(timeout=10)
            record = client.complete(f"judge this candidate answer {index}")
            with records_lock:
                records.append(record)

        threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        max_active = server.max_active_requests
    assert len(records) == 6
    assert all(record.error is None for record in records)
    assert client.concurrency_limit == 2
    assert max_active <= 2, "the client semaphore must cap in-flight calls at the setting"


# --------------------------------------------------------------------------- #
# 11. Backoff helper and configuration surface
# --------------------------------------------------------------------------- #
def test_backoff_delay_is_exponential_bounded_and_jittered() -> None:
    first = backoff_delay(0, base_delay_s=0.5, max_delay_s=8.0)
    second = backoff_delay(1, base_delay_s=0.5, max_delay_s=8.0)
    assert 0.5 <= first <= 0.5 * 1.25
    assert 1.0 <= second <= 1.0 * 1.25
    assert backoff_delay(10, base_delay_s=0.5, max_delay_s=8.0) == 8.0
    assert backoff_delay(0, base_delay_s=0.0, max_delay_s=8.0) == 0.0


def test_settings_defaults_for_robustness(tmp_path) -> None:
    settings = load_settings(dotenv_paths=[tmp_path / "absent.env"])
    assert settings.timeout_s == 120.0
    assert settings.retry_attempts == 4
    assert settings.retry_base_delay_s == 0.5
    assert settings.retry_max_delay_s == 8.0
    assert settings.max_concurrency == 2
    assert settings.request_logprobs is False
    safe = settings.safe_dict()
    assert safe["retry_attempts"] == 4
    assert "api_key" not in safe


def test_settings_read_robustness_env_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDEVAL_TIMEOUT_S", "30")
    monkeypatch.setenv("MEDEVAL_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("MEDEVAL_RETRY_BASE_DELAY_S", "0.25")
    monkeypatch.setenv("MEDEVAL_RETRY_MAX_DELAY_S", "4")
    monkeypatch.setenv("MEDEVAL_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("MEDEVAL_REQUEST_LOGPROBS", "1")
    settings = load_settings(dotenv_paths=[tmp_path / "absent.env"])
    assert settings.timeout_s == 30.0
    assert settings.retry_attempts == 5
    assert settings.retry_base_delay_s == 0.25
    assert settings.retry_max_delay_s == 4.0
    assert settings.max_concurrency == 3
    assert settings.request_logprobs is True
