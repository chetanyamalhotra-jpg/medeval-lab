"""A local fake OpenAI-compatible server for tests.

Runs a real HTTP server on a random loopback port so the whole judge path —
transport, client, sink, cache, runner — is exercised with no network and no
spend. Behaviour is controlled by a small scriptable state:

* ``mode="verdicts"`` — answers with a strict JSON verdict (optionally wrong on
  purpose, for disagreement tests);
* ``mode="malformed"`` — answers with prose (parse-failure path);
* ``mode="truncated"`` — answers with an empty body but reasoning tokens
  (the v0.2 truncation defect);
* ``mode="http_500"`` — every call fails with HTTP 500 (forced-error path);
* ``mode="http_429"`` — every call fails with HTTP 429/``Retry-After``;
* ``mode="http_error"`` — every call fails with ``error_status`` (default 422),
  for the never-retried client-error cases (400/404/422, ...);
* ``mode="redirect"`` — every call answers ``307`` with ``Location:
  redirect_target``, which the client must refuse to follow (that is how a
  redirect could otherwise deliver the Authorization header to another host);
* ``mode="http_401"`` / ``mode="http_403"`` — credential rejection; the error
  body echoes the supplied bearer token so key-redaction can be asserted;
* ``mode="slow"`` — sleeps ``slow_seconds`` and then answers normally;
* ``mode="timeout"`` — sleeps ``timeout_seconds`` (longer than a short client
  timeout) then answers;
* ``mode="reset"`` — closes the socket without sending a response;
* ``mode="empty"`` — HTTP 200 with empty content;
* ``mode="refusal"`` — HTTP 200 with a provider refusal (``message.refusal``);
* ``mode="reasoning_exhausted"`` — empty content and
  ``completion_tokens_details.reasoning_tokens == max_tokens``;
* ``mode="logprobs"`` — a valid verdict plus a chat-completions ``logprobs``
  object whose first generated token has logprob ``-0.42``.

Every request and response is recorded so tests can assert on what was sent;
that includes the auth check, headers, parsed body and whether logprobs were
requested. ``active_requests``/``max_active_requests`` let concurrency-limit
tests observe how many handler threads ran at once.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = ["FakeOpenAIServer"]

#: Logprob attached to the first generated token in ``mode="logprobs"``.
FAKE_FIRST_TOKEN_LOGPROB = -0.42


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass  # keep test output clean

    def _json(self, status: int, payload: dict[str, Any], headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # The client may have timed out and closed the socket on purpose
            # (``mode="timeout"``); that is the scenario under test, not a bug.
            self.close_connection = True

    def do_GET(self) -> None:
        server: FakeOpenAIServer = self.server.fake  # type: ignore[attr-defined]
        server.requests.append({"method": "GET", "path": self.path})
        if self.path.rstrip("/").endswith("/models"):
            server._sync_state()
            self._json(200, {"data": [{"id": name} for name in server.model_names]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        server: FakeOpenAIServer = self.server.fake  # type: ignore[attr-defined]
        with server._activity_lock:
            server.active_requests += 1
            server.max_active_requests = max(server.max_active_requests, server.active_requests)
        try:
            self._handle_post(server)
        finally:
            with server._activity_lock:
                server.active_requests -= 1

    def _handle_post(self, server: FakeOpenAIServer) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        auth = self.headers.get("Authorization", "")
        supplied_key = auth.removeprefix("Bearer ").strip()
        server.requests.append({
            "method": "POST",
            "path": self.path,
            "auth_present": bool(supplied_key),
            "auth_matches_expected": supplied_key == server.expected_key if server.expected_key else None,
            "model": payload.get("model"),
            "max_tokens": payload.get("max_tokens"),
            "temperature": payload.get("temperature"),
            "logprobs_requested": payload.get("logprobs"),
            "headers": dict(self.headers.items()),
            "body": payload,
        })
        server._sync_state()
        if server.mode == "http_500":
            self._json(500, {"error": {"message": "fake server forced failure"}})
            return
        if server.mode == "http_429":
            self._json(
                429,
                {"error": {"message": "fake server forced rate limit"}},
                headers={"Retry-After": server.retry_after},
            )
            return
        if server.mode == "http_error":
            self._json(
                server.error_status,
                {"error": {"message": f"fake server forced HTTP {server.error_status}"}},
            )
            return
        if server.mode == "redirect":
            self._json(
                307,
                {"error": {"message": "fake server forced a redirect"}},
                headers={"Location": server.redirect_target},
            )
            return
        if server.mode in {"http_401", "http_403"}:
            status = 401 if server.mode == "http_401" else 403
            # Deliberately echo the credential to prove the client redacts it.
            self._json(status, {"error": {"message": f"invalid api key: {supplied_key}"}})
            return
        if server.mode == "reset":
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.connection.close()
            return
        if server.mode == "slow":
            time.sleep(server.slow_seconds)
        elif server.mode == "timeout":
            time.sleep(server.timeout_seconds)
        if not supplied_key or (server.expected_key and supplied_key != server.expected_key):
            self._json(401, {"error": {"message": "invalid api key"}})
            return
        usage = {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}
        if server.mode == "truncated":
            usage = {
                "prompt_tokens": 20,
                "completion_tokens": server.truncate_tokens,
                "total_tokens": 20 + server.truncate_tokens,
                "completion_tokens_details": {"reasoning_tokens": server.truncate_tokens},
            }
            self._json(200, {"model": payload.get("model"), "choices": [{"message": {"content": ""}}], "usage": usage})
            return
        if server.mode == "reasoning_exhausted":
            budget = int(payload.get("max_tokens") or server.truncate_tokens)
            usage = {
                "prompt_tokens": 20,
                "completion_tokens": budget,
                "total_tokens": 20 + budget,
                "completion_tokens_details": {"reasoning_tokens": budget},
            }
            self._json(200, {
                "model": payload.get("model"),
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
                "usage": usage,
            })
            return
        if server.mode == "empty":
            self._json(200, {
                "model": payload.get("model"),
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": usage,
            })
            return
        if server.mode == "refusal":
            self._json(200, {
                "model": payload.get("model"),
                "choices": [{
                    "message": {"content": "", "refusal": "I can't assist with that request."},
                    "finish_reason": "stop",
                }],
                "usage": usage,
            })
            return
        content = json.dumps({"verdict": server.verdict, "confidence": server.confidence})
        if server.mode == "malformed":
            content = "I think the answer is CORRECT because the reasoning holds."
        response: dict[str, Any] = {
            "model": payload.get("model"),
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage,
        }
        if server.mode == "logprobs":
            response["choices"][0]["logprobs"] = {
                "content": [
                    {"token": "{", "logprob": FAKE_FIRST_TOKEN_LOGPROB, "bytes": None, "top_logprobs": []},
                    {"token": "\"", "logprob": -0.05, "bytes": None, "top_logprobs": []},
                ]
            }
        self._json(200, response)


class FakeOpenAIServer:
    """Context manager around a loopback :class:`ThreadingHTTPServer`."""

    def __init__(
        self,
        *,
        mode: str = "verdicts",
        model_names: tuple[str, ...] = ("fake-model-a", "fake-model-b"),
        expected_key: str | None = None,
        verdict: str = "CORRECT",
        confidence: int = 90,
        truncate_tokens: int = 512,
        retry_after: str = "0",
        error_status: int = 422,
        slow_seconds: float = 0.25,
        timeout_seconds: float = 2.0,
        redirect_target: str = "",
    ) -> None:
        self.mode = mode
        self.model_names = list(model_names)
        self.expected_key = expected_key
        self.verdict = verdict
        self.confidence = confidence
        self.truncate_tokens = truncate_tokens
        #: Value of the ``Retry-After`` header sent by ``mode="http_429"``.
        self.retry_after = retry_after
        #: Status code sent by ``mode="http_error"``.
        self.error_status = error_status
        self.slow_seconds = slow_seconds
        self.timeout_seconds = timeout_seconds
        #: ``Location`` sent by ``mode="redirect"`` (usually another fake server).
        self.redirect_target = redirect_target
        self.requests: list[dict[str, Any]] = []
        self._activity_lock = threading.Lock()
        self.active_requests = 0
        self.max_active_requests = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def post_requests(self) -> list[dict[str, Any]]:
        """Only the ``/chat/completions`` POSTs (probes may add GET rows)."""
        return [row for row in self.requests if row.get("method") == "POST"]

    def _sync_state(self) -> None:
        """The handler reads live attributes, so nothing to do; kept for clarity."""
        return None

    def __enter__(self) -> FakeOpenAIServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
