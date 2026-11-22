"""The judge layer: strict JSON verdicts, one retry, and an honest failure record.

A judge is treated as a *measurement instrument*, so three things matter more
than the answer it gives:

1. **The reply is validated, not guessed at.** Only ``CORRECT``/``INCORRECT``
   plus an integer confidence in 0-100 is accepted. Anything else is a parse
   failure, counted and reported, never silently coerced into a verdict.
2. **Every attempt is logged.** The retry after a malformed reply re-asks the
   identical prompt with a different cache salt, so both attempts appear in the
   call log under the same prompt hash.
3. **Failures are data.** An API error, a malformed reply, or an unreachable
   local model is recorded against the item instead of being dropped, because
   dropping failures inflates accuracy.

The mock transport exists for ``--dry-run`` and pipeline tests only. It is
labelled ``PIPELINE TEST ONLY`` everywhere it appears, and it can only produce a
verdict because the study hands it a truth table keyed by prompt digest -- the
prompt itself never contains the key.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from medeval_core.client import LLMClient, RawCompletion, parse_json_object
from medeval_study.models import JudgeView
from medeval_study.prompts import PROMPT_VERSION, SYSTEM_PROMPT, render_prompt

__all__ = [
    "BACKEND_API",
    "BACKEND_MOCK",
    "BACKEND_OLLAMA",
    "FAILURE_API",
    "FAILURE_EMPTY",
    "FAILURE_NONE",
    "FAILURE_PARSE",
    "FAILURE_REFUSED",
    "FAILURE_TYPES",
    "JudgeRunner",
    "JudgeSpec",
    "MockJudgeTransport",
    "ParsedVerdict",
    "Verdict",
    "classify_failure",
    "parse_verdict",
]

VERDICT_CORRECT = "CORRECT"
VERDICT_INCORRECT = "INCORRECT"
_VALID_VERDICTS = {VERDICT_CORRECT, VERDICT_INCORRECT}

#: Judgement failure types (v0.3). ``refused`` covers a call the spend guard or
#: a cap stopped before it could complete.
FAILURE_NONE = "none"
FAILURE_PARSE = "parse_failure"
FAILURE_EMPTY = "empty_reply"
FAILURE_API = "api_error"
FAILURE_REFUSED = "refused"
FAILURE_TYPES = (FAILURE_NONE, FAILURE_PARSE, FAILURE_EMPTY, FAILURE_API, FAILURE_REFUSED)

BACKEND_API = "api"
BACKEND_OLLAMA = "ollama"
BACKEND_MOCK = "mock"


@dataclass(frozen=True)
class ParsedVerdict:
    """Outcome of validating one model reply."""

    ok: bool
    verdict: str | None = None
    confidence: int | None = None
    error: str | None = None
    extra_keys: tuple[str, ...] = ()


def _coerce_confidence(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not number_is_finite(value):
            return None
        number = round(value)
    elif isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            number = round(float(text))
        except ValueError:
            return None
    else:
        return None
    if not 0 <= number <= 100:
        return None
    return number


def number_is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def parse_verdict(raw_text: str) -> ParsedVerdict:
    """Validate a model reply against the documented JSON contract."""
    if not raw_text or not raw_text.strip():
        return ParsedVerdict(ok=False, error="empty_output")
    payload = parse_json_object(raw_text)
    if payload is None:
        return ParsedVerdict(ok=False, error="no_json_object")
    if "verdict" not in payload:
        return ParsedVerdict(ok=False, error="missing_verdict", extra_keys=tuple(sorted(payload)))
    verdict_raw = payload["verdict"]
    if not isinstance(verdict_raw, str):
        return ParsedVerdict(ok=False, error="verdict_not_a_string", extra_keys=tuple(sorted(payload)))
    verdict = verdict_raw.strip().upper()
    if verdict not in _VALID_VERDICTS:
        return ParsedVerdict(ok=False, error=f"invalid_verdict:{verdict_raw[:24]}", extra_keys=tuple(sorted(payload)))
    if "confidence" not in payload:
        return ParsedVerdict(ok=False, error="missing_confidence", extra_keys=tuple(sorted(payload)))
    confidence = _coerce_confidence(payload["confidence"])
    if confidence is None:
        return ParsedVerdict(
            ok=False,
            error=f"invalid_confidence:{str(payload['confidence'])[:24]}",
            extra_keys=tuple(sorted(payload)),
        )
    extra = tuple(sorted(set(payload) - {"verdict", "confidence"}))
    return ParsedVerdict(ok=True, verdict=verdict, confidence=confidence, extra_keys=extra)


@dataclass(frozen=True)
class JudgeSpec:
    """Which model is judging, from which family, under which settings."""

    judge_id: str
    family: str
    backend: str
    model: str
    base_url: str | None = None
    temperature: float = 0.3
    repeats: int = 3
    notes: str = ""
    available: bool = True
    unavailable_reason: str | None = None

    @property
    def pipeline_test_only(self) -> bool:
        return self.backend == BACKEND_MOCK

    def as_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "family": self.family,
            "backend": self.backend,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "repeats": self.repeats,
            "notes": self.notes,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "pipeline_test_only": self.pipeline_test_only,
        }


@dataclass
class Verdict:
    """One judge's answer for one item on one repeat.

    ``failure_type`` is one of :data:`FAILURE_NONE`, :data:`FAILURE_PARSE`,
    :data:`FAILURE_EMPTY`, :data:`FAILURE_API` or :data:`FAILURE_REFUSED`, and is
    the field reports aggregate on; ``error`` keeps the human-readable detail.
    """

    item_id: str
    judge_id: str
    repeat: int
    verdict: str | None
    confidence: int | None
    parse_ok: bool
    attempts: int
    model: str
    prompt_sha256: str
    latency_ms: float
    error: str | None = None
    raw_output_sha256: str | None = None
    failure_type: str = FAILURE_NONE

    @property
    def is_api_error(self) -> bool:
        return self.failure_type == FAILURE_API

    @property
    def is_parse_failure(self) -> bool:
        return self.failure_type in {FAILURE_PARSE, FAILURE_EMPTY}

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "judge_id": self.judge_id,
            "repeat": self.repeat,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "parse_ok": self.parse_ok,
            "attempts": self.attempts,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "raw_output_sha256": self.raw_output_sha256,
            "failure_type": self.failure_type,
        }


def classify_failure(error: str | None) -> str:
    """Map a stored error string to its failure type.

    Reading old v0.2 records relies on this: their ``error`` strings used the
    same ``api_error:`` / ``parse_error: empty_output`` prefixes.
    """
    if not error:
        return FAILURE_NONE
    if error.startswith("api_error"):
        return FAILURE_API
    if error == "parse_error: empty_output" or error.startswith("empty_reply"):
        return FAILURE_EMPTY
    if error.startswith("refused") or "spend guard" in error:
        return FAILURE_REFUSED
    return FAILURE_PARSE


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JudgeRunner:
    """Ask one judge one question, validate the reply, record what happened."""

    def __init__(
        self,
        spec: JudgeSpec,
        client: LLMClient,
        *,
        base_seed: int,
        retry_attempts: int = 1,
        on_call: Callable[[Verdict], None] | None = None,
    ) -> None:
        self.spec = spec
        self.client = client
        self.base_seed = base_seed
        self.retry_attempts = max(0, retry_attempts)
        self.on_call = on_call

    def seed_for(self, repeat: int) -> int:
        return self.base_seed + repeat

    def judge(self, view: JudgeView, response_text: str, *, repeat: int = 0) -> Verdict:
        """Return a validated verdict, or a failure record with the reason."""
        prompt = self._render(view, response_text)
        prompt_sha = _sha256(prompt)
        seed = self.seed_for(repeat)
        started = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        raw_sha: str | None = None
        model_name = self.spec.model

        for attempt in range(self.retry_attempts + 1):
            attempts = attempt + 1
            record = self.client.complete(
                prompt,
                system=SYSTEM_PROMPT,
                template_version=PROMPT_VERSION,
                temperature=self.spec.temperature,
                seed=seed,
                metadata={
                    "item_id": view.item_id,
                    "judge_id": self.spec.judge_id,
                    "repeat": repeat,
                    "attempt": attempt,
                },
                cache_salt="" if attempt == 0 else f"retry{attempt}",
            )
            model_name = record.model or self.spec.model
            if record.error:
                # A provider refusal or a spend-guard refusal is its own failure
                # type, not a generic API error: the run did not fail to reach
                # the provider, it was told no.
                error_text = str(record.error)
                if error_text.startswith("refused") or "spend guard" in error_text:
                    last_error = error_text if error_text.startswith("refused") else f"refused: {error_text}"
                else:
                    last_error = f"api_error: {error_text}"
                raw_sha = _sha256(record.output or "")
                break
            parsed = parse_verdict(record.output)
            if parsed.ok:
                verdict = Verdict(
                    item_id=view.item_id,
                    judge_id=self.spec.judge_id,
                    repeat=repeat,
                    verdict=parsed.verdict,
                    confidence=parsed.confidence,
                    parse_ok=True,
                    attempts=attempts,
                    model=model_name,
                    prompt_sha256=prompt_sha,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=None,
                    raw_output_sha256=_sha256(record.output or ""),
                )
                if self.on_call:
                    self.on_call(verdict)
                return verdict
            last_error = f"parse_error: {parsed.error}"
            raw_sha = _sha256(record.output or "")

        verdict = Verdict(
            item_id=view.item_id,
            judge_id=self.spec.judge_id,
            repeat=repeat,
            verdict=None,
            confidence=None,
            parse_ok=False,
            attempts=attempts,
            model=model_name,
            prompt_sha256=prompt_sha,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=last_error,
            raw_output_sha256=raw_sha,
            failure_type=classify_failure(last_error),
        )
        if self.on_call:
            self.on_call(verdict)
        return verdict

    def _render(self, view: JudgeView, response_text: str) -> str:
        return render_prompt(view, response_text)


class MockJudgeTransport:
    """Deterministic stand-in judge. PIPELINE TEST ONLY.

    It needs a truth table to imitate a model with a given accuracy, and the
    table is keyed by the SHA-256 of the rendered prompt. The study fills it when
    it renders prompts, so the key is never part of the text a judge would see.
    """

    backend = BACKEND_MOCK
    base_url = None

    def __init__(
        self,
        *,
        model: str = "mock-judge",
        accuracy: float = 0.75,
        seed: int = 20260101,
        oracle: Mapping[str, bool] | None = None,
    ) -> None:
        self.model = model
        self.accuracy = min(max(accuracy, 0.0), 1.0)
        self.seed = seed
        self.oracle: dict[str, bool] = dict(oracle or {})
        self.pipeline_test_only = True

    def set_oracle(self, prompt_sha256: str, is_correct: bool) -> None:
        self.oracle[prompt_sha256] = bool(is_correct)

    def complete(
        self,
        messages: Any,
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        started = time.perf_counter()
        prompt = str(messages[-1]["content"]) if messages else ""
        prompt_sha = _sha256(prompt)
        truth = self.oracle.get(prompt_sha)
        if truth is None:
            return RawCompletion(
                text="",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error="mock transport was asked for a prompt with no registered truth",
            )
        rng = random.Random(f"{self.seed}:{prompt_sha}:{seed}")
        agrees = rng.random() < self.accuracy
        answered_correct = truth if agrees else (not truth)
        verdict = VERDICT_CORRECT if answered_correct else VERDICT_INCORRECT
        mean = 82.0 if agrees else 61.0
        confidence = int(min(99, max(1, round(rng.gauss(mean, 11.0)))))
        payload = {"verdict": verdict, "confidence": confidence}
        return RawCompletion(
            text=json.dumps(payload),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=12,
        )
