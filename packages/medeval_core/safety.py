"""Spend, caching and secret-redaction controls for paid model calls."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def redact_text(value: str, secrets: tuple[str | None, ...]) -> str:
    """Replace configured non-empty secrets, including echoed keys, everywhere."""
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def redact_value(value: Any, secrets: tuple[str | None, ...]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {str(k): redact_value(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, secrets) for v in value]
    return value


@dataclass(frozen=True)
class PriceCard:
    """Provider-supplied USD prices per one million tokens; None is unverified."""
    input_per_million: float | None = None
    output_per_million: float | None = None
    reasoning_per_million: float | None = None
    source: str = "unverified"

    @property
    def verified(self) -> bool:
        return self.input_per_million is not None and self.output_per_million is not None

    def estimate(self, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> float | None:
        if not self.verified:
            return None
        reasoning_rate = self.reasoning_per_million if self.reasoning_per_million is not None else self.output_per_million
        return ((input_tokens * self.input_per_million) + (output_tokens * self.output_per_million) + (reasoning_tokens * reasoning_rate)) / 1_000_000


@dataclass
class SpendGuard:
    """Hard, process-local caps checked before every non-cached network call."""
    max_calls: int = 1500
    max_usd: float = 5.0
    prices: PriceCard = PriceCard()
    calls: int = 0
    estimated_usd: float = 0.0

    def check_before_call(self, estimated_input_tokens: int, max_output_tokens: int) -> None:
        if self.calls >= self.max_calls:
            raise RuntimeError(f"spend guard: call cap reached ({self.max_calls})")
        estimate = self.prices.estimate(estimated_input_tokens, max_output_tokens)
        if estimate is not None and self.estimated_usd + estimate > self.max_usd:
            raise RuntimeError(f"spend guard: estimated USD cap would be exceeded ({self.max_usd:.2f})")

    def record(self, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> None:
        self.calls += 1
        amount = self.prices.estimate(input_tokens, output_tokens, reasoning_tokens)
        if amount is not None:
            self.estimated_usd += amount

    def summary(self) -> dict[str, Any]:
        return {"calls_made": self.calls, "estimated_usd": self.estimated_usd if self.prices.verified else None,
                "price_source": self.prices.source, "max_calls": self.max_calls, "max_usd": self.max_usd}


class ChainedGuard:
    """Enforce several :class:`SpendGuard` limits on one call.

    A connection-backed judge must respect **both** its own connection's caps
    and the run's total caps, so ``check_before_call`` consults every guard and
    ``record`` updates them all. The first cap that would be exceeded raises,
    naming which one.
    """

    def __init__(self, *guards: SpendGuard, labels: Sequence[str] = ()) -> None:
        self.guards = list(guards)
        self.labels = list(labels) or [f"guard-{index}" for index in range(len(guards))]

    def check_before_call(self, estimated_input_tokens: int, max_output_tokens: int) -> None:
        for label, guard in zip(self.labels, self.guards, strict=True):
            try:
                guard.check_before_call(estimated_input_tokens, max_output_tokens)
            except RuntimeError as exc:
                raise RuntimeError(f"{label}: {exc}") from exc

    def record(self, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> None:
        for guard in self.guards:
            guard.record(input_tokens, output_tokens, reasoning_tokens)

    def summary(self) -> dict[str, Any]:
        return {
            "guards": [
                {"label": label, **guard.summary()}
                for label, guard in zip(self.labels, self.guards, strict=True)
            ]
        }


class ResponseCache:
    """Small resumable JSON cache; only successful completions are cached."""
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.rows: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            self.rows = {}

    @staticmethod
    def key(model: str, prompt_sha256: str, temperature: float, seed: int, salt: str = "") -> str:
        """Cache key: everything that can change the model's answer.

        ``salt`` exists so that a deliberate retry of the *same* prompt (for
        example re-asking after a malformed JSON reply) does not read back the
        cached malformed answer. Cached entries are keyed on the salt, so a
        retry is a genuine second call and is logged as such.
        """
        base = f"{model}|{prompt_sha256}|{temperature:.8g}|{seed}"
        return f"{base}|{salt}" if salt else base

    def get(self, key: str) -> Mapping[str, Any] | None:
        return self.rows.get(key)

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        self.rows[key] = dict(value)
        self.path.write_text(json.dumps(self.rows, sort_keys=True, ensure_ascii=False), encoding="utf-8")
