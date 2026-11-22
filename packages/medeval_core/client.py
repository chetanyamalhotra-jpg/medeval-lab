"""The single OpenAI-compatible client.

Both baseline chat models and LLM judges use :class:`LLMClient`. Transports:

* :class:`OpenAICompatibleTransport` -- any ``/chat/completions`` endpoint;
* :class:`LocalHFTransport` -- a small CPU Hugging Face model (optional);
* :class:`MockTransport` -- deterministic stand-in, labelled PIPELINE TEST ONLY.

Every call is written to a JSONL call log before the text is returned, so a
crash cannot produce an unlogged judgement.

Live-run robustness (M2):

* HTTP 429/5xx are retried with exponential backoff plus jitter; ``Retry-After``
  (delta-seconds or HTTP-date) is honoured. 401/403 raise :class:`AuthError` and
  are never retried, nor are 400/404/422 or provider refusals.
* Timeouts and connection resets map to distinct retryable exceptions.
* Usage (including reasoning tokens), the first token's logprob and the
  reasoning-exhaustion signal are parsed defensively and exposed on
  :class:`RawCompletion` (the call record copies them into ``metadata``).
* A per-client semaphore bounds in-flight calls to one connection.
* The API key is sent only to the exact configured host: HTTP redirects are
  never followed (a 3xx is a terminal error), so a redirect cannot move the
  ``Authorization`` header to another host.
"""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import requests

from .backends import (
    BACKEND_API,
    BACKEND_LOCAL_HF,
    BACKEND_LOCAL_SERVER,
    BACKEND_MOCK,
    BackendSelection,
)
from .calllog import CallRecord, CallSink, JsonlCallSink, NullCallSink, new_run_id, utc_now_iso
from .config import Settings
from .hashing import prompt_hash
from .safety import ResponseCache, SpendGuard, redact_text

#: Injected sleep hook. Tests replace this (or pass ``sleep_fn=`` to the
#: transport) so retry/backoff tests never actually sleep.
_sleep: Callable[[float], None] = time.sleep

#: Backoff jitter, as a fraction of the exponential delay (0.25 => up to +25%).
_JITTER_FRACTION = 0.25

#: A reply using at least this fraction of ``max_tokens`` on hidden reasoning
#: while showing no visible text is flagged as reasoning-exhausted.
_REASONING_EXHAUSTED_FRACTION = 0.9

#: Status codes that must never be retried.
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 422})
_AUTH_STATUS = frozenset({401, 403})


class TransportError(RuntimeError):
    """Raised when a transport cannot produce text."""


class AuthError(TransportError):
    """The provider rejected the credentials (HTTP 401/403); never retried.

    ``provider_message`` is the provider's own error text with any configured
    API key redacted; it is safe to log.
    """

    def __init__(self, status_code: int, provider_message: str = "") -> None:
        self.status_code = int(status_code)
        self.provider_message = provider_message
        detail = f"HTTP {self.status_code}: {self.provider_message}".rstrip(": ")
        super().__init__(detail)


class TransportTimeout(TransportError):
    """A physical/read timeout. Distinct from HTTP errors; retryable."""

    def __init__(self, message: str, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s
        super().__init__(message)


class TransportConnectionError(TransportError):
    """A connection reset/protocol failure. Distinct from timeouts; retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class RawCompletion:
    """One transport answer plus the provider metadata the run may need.

    ``error`` stays a human-readable detail string: :class:`LLMClient` records
    it verbatim and the study layer's failure classifier applies its own
    ``api_error:`` prefix, exactly as before. ``error_type`` is the machine
    readable companion (``"auth"``, ``"timeout"``, ``"connection"``,
    ``"server_error"``, ``"rate_limit"``, ``"http_error"``, ``"malformed"``,
    ``"refused"``).
    """

    text: str
    latency_ms: float
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    verdict_logprob: float | None = None
    reasoning_exhausted: bool = False
    error_type: str | None = None
    status_code: int | None = None
    attempts: int = 1


class ChatTransport(Protocol):
    backend: str
    model: str
    base_url: str | None

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion: ...


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "[REDACTED]")


# --------------------------------------------------------------------------- #
# Retry helpers (pure functions so tests can cover them without a server)
# --------------------------------------------------------------------------- #
def parse_retry_after(value: str | None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header, or None when unparseable.

    Accepts both forms the HTTP spec allows: delta-seconds (``"2"``) and an
    HTTP-date (``"Wed, 21 Oct 2015 07:28:00 GMT"``).
    """
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if moment is None:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)


def backoff_delay(
    attempt: int,
    *,
    base_delay_s: float,
    max_delay_s: float,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with bounded jitter for a 0-based retry ``attempt``.

    The jitter only ever *adds*: the result lies in
    ``[base * 2**attempt, base * 2**attempt * (1 + _JITTER_FRACTION)]``, capped
    at ``max_delay_s``. That keeps sleeping test-friendly (bounds are assertable)
    while still de-synchronising parallel callers.
    """
    generator = rng or random
    delay = max(0.0, base_delay_s) * (2**max(0, attempt))
    delay *= 1.0 + generator.uniform(0.0, _JITTER_FRACTION)
    if max_delay_s > 0:
        delay = min(delay, max_delay_s)
    return max(0.0, delay)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _first_nonzero_int(*values: Any) -> int:
    for value in values:
        number = _as_nonnegative_int(value)
        if number:
            return number
    return 0


def parse_usage(usage: Any) -> dict[str, int]:
    """Extract usage counters from the provider's ``usage`` object.

    Providers disagree on field names (``prompt_tokens`` vs ``input_tokens``,
    ``completion_tokens_details.reasoning_tokens`` vs ``output_tokens_details``
    vs a flat ``reasoning_tokens``), so every known variant is read and
    anything unexpected is treated as zero rather than raising.
    """
    data = _as_mapping(usage)
    completion_details = _as_mapping(data.get("completion_tokens_details"))
    output_details = _as_mapping(data.get("output_tokens_details"))
    prompt_tokens = _first_nonzero_int(data.get("prompt_tokens"), data.get("input_tokens"))
    completion_tokens = _first_nonzero_int(data.get("completion_tokens"), data.get("output_tokens"))
    reasoning_tokens = _first_nonzero_int(
        completion_details.get("reasoning_tokens"),
        output_details.get("reasoning_tokens"),
        data.get("reasoning_tokens"),
        data.get("reasoning_output_tokens"),
    )
    total_tokens = _first_nonzero_int(data.get("total_tokens")) or (prompt_tokens + completion_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _extract_message_text(data: Mapping[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response contained no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError("malformed choice")
    message = first.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        # Reasoning models return ``content: null`` when the whole budget went
        # to hidden reasoning; that is an empty reply, not a malformed one.
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        # Some gateways return a list of content parts.
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, Mapping)]
            return "".join(str(p) for p in parts)
    text = first.get("text")
    if isinstance(text, str):
        return text
    raise ValueError("response contained no message content")


def _extract_refusal(data: Mapping[str, Any]) -> str | None:
    """Return the provider's refusal text, if this response is a refusal."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if isinstance(message, Mapping):
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            return refusal.strip()
    if first.get("finish_reason") == "content_filter":
        return "provider content filter"
    return None


def _extract_verdict_logprob(data: Mapping[str, Any]) -> float | None:
    """Logprob of the first generated token of reply, when the provider sent it.

    Chat-completions logprobs live under ``choices[0].logprobs.content`` (a list
    of ``{token, logprob, ...}``). Any other/legacy shape yields None; this must
    never be required and never break parsing.
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    logprobs = first.get("logprobs")
    if not isinstance(logprobs, Mapping):
        return None
    content = logprobs.get("content")
    if not isinstance(content, list) or not content:
        return None
    first_token = content[0]
    if not isinstance(first_token, Mapping):
        return None
    value = first_token.get("logprob")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _reasoning_exhausted(text: str, reasoning_tokens: int, max_tokens: int) -> bool:
    if text.strip() or max_tokens <= 0:
        return False
    return reasoning_tokens >= _REASONING_EXHAUSTED_FRACTION * max_tokens


def _provider_message(response: requests.Response) -> str:
    """Provider error text for redaction: JSON ``error.message`` or raw body."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:400]
    error = _as_mapping(payload).get("error")
    if isinstance(error, str) and error:
        return error[:400]
    message = _as_mapping(error).get("message")
    if isinstance(message, str) and message:
        return message[:400]
    return response.text[:400]


class OpenAICompatibleTransport:
    """POST ``/chat/completions`` against a single configured endpoint.

    ``max_attempts`` is the total number of attempts per call (the documented
    setting is ``Settings.retry_attempts``, default 4). ``retries`` is the legacy
    "extra attempts" spelling still used by older callers: when ``max_attempts``
    is omitted the budget is ``retries + 1``, preserving old behaviour.

    ``timeout_s`` set on the constructor is the per-connection override; when it
    is None the per-call ``timeout_s`` argument is used. ``sleep_fn`` (or the
    module-level :data:`_sleep` hook) makes backoff injectable for tests.
    """

    backend: str
    model: str
    base_url: str

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        backend: str = BACKEND_API,
        retries: int = 2,
        max_attempts: int | None = None,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
        timeout_s: float | None = None,
        request_logprobs: bool = False,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.backend = backend
        self.retries = max(0, int(retries))
        #: Total attempts per call (documented setting: Settings.retry_attempts).
        self.max_attempts = max(1, int(max_attempts)) if max_attempts is not None else self.retries + 1
        self.base_delay_s = max(0.0, float(base_delay_s))
        self.max_delay_s = max(0.0, float(max_delay_s))
        self.timeout_s = timeout_s
        self.request_logprobs = bool(request_logprobs)
        self._sleep_fn = sleep_fn

    # -- single-shot request, mapping transport failures to distinct errors -- #
    def _post_once(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> requests.Response:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": seed,
            "stream": False,
        }
        if self.request_logprobs:
            payload["logprobs"] = True
        try:
            # Redirects are NOT followed. requests would otherwise follow a 3xx
            # to any host and re-send the Authorization header, which is exactly
            # how a provider (or a device on the network) could collect the key.
            # A redirect is reported as a terminal error instead.
            return requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_s,
                allow_redirects=False,
            )
        except requests.Timeout as exc:  # includes ConnectTimeout and ReadTimeout
            raise TransportTimeout(f"{type(exc).__name__}: {exc}", timeout_s=timeout_s) from exc
        except requests.ConnectionError as exc:  # connection reset / refused / aborted
            raise TransportConnectionError(f"{type(exc).__name__}: {exc}") from exc
        except requests.RequestException as exc:
            raise TransportConnectionError(f"{type(exc).__name__}: {exc}") from exc

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = backoff_delay(attempt, base_delay_s=self.base_delay_s, max_delay_s=self.max_delay_s)
        sleep = self._sleep_fn if self._sleep_fn is not None else _sleep
        sleep(delay)

    def _retry_after_or_backoff(self, attempt: int, response: requests.Response) -> None:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if retry_after is None:
            self._sleep_before_retry(attempt)
            return
        # Retry-After wins over exponential backoff, but a hostile/absurd value
        # is capped by retry_max_delay_s so one call cannot stall the run.
        delay = min(retry_after, self.max_delay_s) if self.max_delay_s > 0 else retry_after
        sleep = self._sleep_fn if self._sleep_fn is not None else _sleep
        sleep(delay)

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        started = time.perf_counter()
        effective_timeout = max(0.001, float(self.timeout_s if self.timeout_s is not None else timeout_s))
        attempts = 0
        last_detail = "unknown transport error"
        last_type: str | None = "unknown"
        last_status: int | None = None
        for attempt in range(self.max_attempts):
            attempts = attempt + 1
            try:
                response = self._post_once(
                    messages,
                    temperature=temperature,
                    seed=seed,
                    max_tokens=max_tokens,
                    timeout_s=effective_timeout,
                )
            except AuthError:
                raise
            except TransportTimeout as exc:
                last_detail = f"timeout after {effective_timeout:g}s: {exc}"
                last_type, last_status = "timeout", None
                if attempts < self.max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                break
            except TransportConnectionError as exc:
                last_detail = f"connection_error: {exc}"
                last_type, last_status = "connection", None
                if attempts < self.max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                break

            latency_ms = (time.perf_counter() - started) * 1000.0
            status = response.status_code
            if 300 <= status < 400:
                # Terminal, never retried and never followed: the key must only
                # ever be sent to the exact host the owner configured.
                from urllib.parse import urlsplit

                location = response.headers.get("Location") or ""
                target_host = (urlsplit(location).hostname or "a different host") if location else "a different host"
                last_status = status
                last_type = "redirect"
                last_detail = (
                    f"provider answered with a redirect (HTTP {status}) to {target_host}; refusing to follow it "
                    "because following it would resend your API key there. Set the connection's base URL to the "
                    "final endpoint instead."
                )
                break
            if status in _AUTH_STATUS:
                # Distinct, never-retried credential failure. LLMClient records
                # it on the call record instead of letting it escape complete().
                raise AuthError(status, _redact(_provider_message(response), self._api_key))
            if status == 429 or 500 <= status <= 599:
                last_status = status
                last_type = "rate_limit" if status == 429 else "server_error"
                last_detail = f"HTTP {status}: {_redact(response.text[:400], self._api_key)}"
                if attempts < self.max_attempts:
                    self._retry_after_or_backoff(attempt, response)
                    continue
                break
            if status != 200:
                # 400/404/422 and any other client error: terminal, never retried.
                last_status = status
                last_type = "http_error"
                last_detail = f"HTTP {status}: {_redact(response.text[:400], self._api_key)}"
                break

            try:
                data = response.json()
            except ValueError:
                last_detail, last_type = "malformed_response: body is not JSON", "malformed"
                break
            if not isinstance(data, Mapping):
                last_detail, last_type = "malformed_response: body is not a JSON object", "malformed"
                break

            refusal = _extract_refusal(data)
            if refusal is not None:
                # A provider refusal is a completed, non-retryable answer.
                return RawCompletion(
                    text=refusal,
                    latency_ms=latency_ms,
                    error=f"refused: {_redact(refusal, self._api_key)}",
                    error_type="refused",
                    status_code=status,
                    attempts=attempts,
                )

            try:
                text = _extract_message_text(data)
            except ValueError as exc:
                last_detail, last_type = f"malformed_response: {exc}", "malformed"
                break

            usage = parse_usage(data.get("usage"))
            reasoning_tokens = usage["reasoning_tokens"]
            return RawCompletion(
                text=text,
                latency_ms=latency_ms,
                input_tokens=usage["prompt_tokens"],
                output_tokens=usage["completion_tokens"],
                total_tokens=usage["total_tokens"],
                reasoning_tokens=reasoning_tokens,
                verdict_logprob=_extract_verdict_logprob(data),
                reasoning_exhausted=_reasoning_exhausted(text, reasoning_tokens, max_tokens),
                status_code=status,
                attempts=attempts,
            )

        return RawCompletion(
            text="",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=last_detail,
            error_type=last_type,
            status_code=last_status,
            attempts=attempts,
        )


class LocalHFTransport:
    """Small causal LM on CPU, loaded lazily on first use."""

    backend = BACKEND_LOCAL_HF

    def __init__(self, model_name: str, max_context: int = 2048) -> None:
        self.model = model_name
        self.base_url = None
        self._max_context = max_context
        self._pipe: Any = None

    def _ensure_loaded(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        try:
            from transformers import pipeline  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on optional install
            raise TransportError(
                "transformers is not installed; local HF judge unavailable"
            ) from exc
        self._pipe = pipeline(
            "text-generation",
            model=self.model,
            device=-1,
            torch_dtype="auto",
        )
        return self._pipe

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        started = time.perf_counter()
        try:
            pipe = self._ensure_loaded()
            prompt = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages) + "\n\nASSISTANT:"
            out = pipe(
                prompt,
                max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=max(temperature, 1e-5),
                return_full_text=False,
            )
            text = out[0]["generated_text"] if out else ""
            return RawCompletion(text=text.strip(), latency_ms=(time.perf_counter() - started) * 1000.0)
        except Exception as exc:  # pragma: no cover - depends on optional install
            return RawCompletion(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
                error_type="transport",
            )


class MockTransport:
    """Deterministic stand-in model.

    It is *not* a model: it echoes whatever ``responder`` returns. Judges that
    use it label their output PIPELINE TEST ONLY.
    """

    backend = BACKEND_MOCK
    base_url = None

    def __init__(self, responder: Any | None = None, model: str = "mock-judge-v1") -> None:
        self.model = model
        self._responder = responder

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        started = time.perf_counter()
        text = "MOCK" if self._responder is None else str(self._responder(list(messages), seed))
        # A tiny sleep keeps latency fields realistic without wasting time.
        return RawCompletion(text=text, latency_ms=(time.perf_counter() - started) * 1000.0)


def build_transport(
    selection: BackendSelection,
    settings: Settings,
    *,
    mock_responder: Any | None = None,
) -> ChatTransport:
    """Construct the transport matching a resolved backend selection."""
    if selection.name == BACKEND_MOCK:
        return MockTransport(responder=mock_responder, model=selection.model)
    if selection.name in {BACKEND_API, BACKEND_LOCAL_SERVER}:
        if not selection.base_url:
            raise TransportError(f"backend {selection.name} has no base_url")
        return OpenAICompatibleTransport(
            base_url=selection.base_url,
            model=selection.model,
            api_key=selection.api_key,
            backend=selection.name,
            retries=settings.retries,
            max_attempts=settings.retry_attempts,
            base_delay_s=settings.retry_base_delay_s,
            max_delay_s=settings.retry_max_delay_s,
            request_logprobs=settings.request_logprobs,
        )
    if selection.name == BACKEND_LOCAL_HF:
        return LocalHFTransport(selection.model)
    raise TransportError(f"unknown backend: {selection.name}")


class LLMClient:
    """Thin wrapper adding provenance hashing, call logging and concurrency caps.

    Errors from the transport are captured onto the call record instead of
    propagating out of :meth:`complete`; only ``AuthError`` and generic
    ``TransportError`` are caught here, and they become ``error_type``
    ``"auth"``/``"transport"`` records.
    """

    def __init__(
        self,
        transport: ChatTransport,
        settings: Settings,
        *,
        sink: CallSink | None = None,
        run_id: str | None = None,
        backend_label: str | None = None,
        cache: ResponseCache | None = None,
        guard: SpendGuard | None = None,
        extra_secrets: tuple[str, ...] = (),
    ) -> None:
        self.transport = transport
        self.settings = settings
        self.run_id = run_id or new_run_id()
        self._sink: CallSink = sink or NullCallSink()
        self.backend_label = backend_label or f"{transport.backend}:{transport.model}"
        self.cache = cache
        self.guard = guard
        #: Every secret this client must keep out of logs, caches and errors.
        self._secrets: tuple[str, ...] = tuple(
            secret for secret in (settings.api_key, *extra_secrets) if secret
        )
        #: Per-connection cap on in-flight calls (a future parallel runner can
        #: use one client from several threads); sequential callers are unaffected.
        self.concurrency_limit = max(1, int(settings.max_concurrency))
        self._semaphore = threading.BoundedSemaphore(self.concurrency_limit)

    @property
    def is_mock(self) -> bool:
        return self.transport.backend == BACKEND_MOCK

    def _record_guard_usage(self, completion: RawCompletion, prompt_text: str) -> None:
        if self.guard and self.transport.backend != BACKEND_MOCK:
            self.guard.record(
                completion.input_tokens or max(1, len(prompt_text) // 4),
                completion.output_tokens,
                completion.reasoning_tokens,
            )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        template_version: str = "v1",
        temperature: float | None = None,
        seed: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        cache_salt: str = "",
    ) -> CallRecord:
        """Send one completion and log it. Errors are recorded, not raised."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        temperature = self.settings.temperature if temperature is None else temperature
        seed = self.settings.seed if seed is None else seed
        prompt_text = (system or "") + prompt
        key = ResponseCache.key(
            self.transport.model,
            prompt_hash(system, prompt, template_version),
            temperature,
            seed,
            cache_salt,
        )
        cached = self.cache.get(key) if self.cache else None
        cache_hit = cached is not None
        if cached:
            completion = RawCompletion(**dict(cached))
        else:
            if self.guard and self.transport.backend != BACKEND_MOCK:
                self.guard.check_before_call(max(1, len(prompt_text) // 4), self.settings.max_tokens)
            call_started = time.perf_counter()
            with self._semaphore:
                try:
                    completion = self.transport.complete(
                        messages,
                        temperature=temperature,
                        seed=seed,
                        max_tokens=self.settings.max_tokens,
                        timeout_s=self.settings.timeout_s,
                    )
                except AuthError as exc:
                    completion = RawCompletion(
                        text="",
                        latency_ms=(time.perf_counter() - call_started) * 1000.0,
                        error=f"HTTP {exc.status_code}: {exc.provider_message}".rstrip(": "),
                        error_type="auth",
                        status_code=exc.status_code,
                        attempts=1,
                    )
                except TransportError as exc:
                    completion = RawCompletion(
                        text="",
                        latency_ms=(time.perf_counter() - call_started) * 1000.0,
                        error=f"{type(exc).__name__}: {exc}",
                        error_type="transport",
                        attempts=1,
                    )
            self._record_guard_usage(completion, prompt_text)
            if self.cache and completion.error is None:
                self.cache.put(key, {
                    "text": redact_text(completion.text, self._secrets),
                    "latency_ms": completion.latency_ms,
                    "error": None,
                    "input_tokens": completion.input_tokens,
                    "output_tokens": completion.output_tokens,
                    "reasoning_tokens": completion.reasoning_tokens,
                    "total_tokens": completion.total_tokens,
                    "verdict_logprob": completion.verdict_logprob,
                    "reasoning_exhausted": completion.reasoning_exhausted,
                    "error_type": completion.error_type,
                    "status_code": completion.status_code,
                    "attempts": completion.attempts,
                })
        record_metadata: dict[str, Any] = {
            **dict(metadata or {}),
            "cache_hit": cache_hit,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "reasoning_tokens": completion.reasoning_tokens,
            "total_tokens": completion.total_tokens,
            "verdict_logprob": completion.verdict_logprob,
            "reasoning_exhausted": completion.reasoning_exhausted,
            "attempts": completion.attempts,
            "error_type": completion.error_type,
            "status_code": completion.status_code,
        }
        record = CallRecord(
            call_id=uuid.uuid4().hex,
            run_id=self.run_id,
            timestamp_utc=utc_now_iso(),
            backend=self.transport.backend,
            model=self.transport.model,
            base_url=self.transport.base_url,
            prompt_sha256=prompt_hash(system, prompt, template_version),
            template_version=template_version,
            system=system,
            prompt=prompt,
            output=redact_text(completion.text, self._secrets),
            temperature=temperature,
            seed=seed,
            max_tokens=self.settings.max_tokens,
            latency_ms=completion.latency_ms,
            error=redact_text(completion.error, self._secrets) if completion.error else None,
            metadata=record_metadata,
        )
        self._sink.write(record)
        return record

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.transport.backend,
            "model": self.transport.model,
            "base_url": self.transport.base_url,
            "run_id": self.run_id,
            "pipeline_test_only": self.is_mock,
            "concurrency_limit": self.concurrency_limit,
        }


def make_client(
    selection: BackendSelection,
    settings: Settings,
    *,
    run_id: str | None = None,
    log_path: Any | None = None,
    mock_responder: Any | None = None,
    extra_secrets: tuple[str, ...] = (),
) -> LLMClient:
    """Build a logging client for a resolved backend selection.

    ``extra_secrets`` (for example other connections' keys) are redacted from
    logs and cached output alongside the client's own key.
    """
    transport = build_transport(selection, settings, mock_responder=mock_responder)
    secrets = tuple(secret for secret in (settings.api_key, *extra_secrets) if secret)
    sink: CallSink = (
        NullCallSink() if log_path is None else JsonlCallSink(log_path, secrets=secrets)
    )
    return LLMClient(
        transport,
        settings,
        sink=sink,
        run_id=run_id,
        backend_label=selection.label,
        extra_secrets=extra_secrets,
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a single JSON object from model text."""
    if not text:
        return None
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
