"""Configuration for the single OpenAI-compatible client.

All model access -- baseline chat models *and* LLM judges -- goes through the
same three environment variables (``MEDEVAL_BASE_URL``, ``MEDEVAL_MODEL``,
``MEDEVAL_API_KEY``). API keys are read from the environment or from a ``.env``
file that is git-ignored; they are never written to code, reason strings, call
logs or result files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import repo_root

#: Environment variable names, in the order they are consulted.
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "base_url": ("MEDEVAL_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
    "model": ("MEDEVAL_MODEL", "OPENAI_MODEL"),
    "api_key": ("MEDEVAL_API_KEY", "OPENAI_API_KEY"),
}

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings (never contains a human-readable key)."""

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)
    backend_override: str = "auto"
    hf_model: str | None = None
    timeout_s: float = 120.0
    max_tokens: int = 512
    temperature: float = 0.0
    seed: int = 20260101
    #: Legacy "extra attempts" spelling; the per-call cap is ``retry_attempts``.
    retries: int = 2
    #: Total attempts per call (first try included) for HTTP 429/5xx/timeouts.
    retry_attempts: int = 4
    #: Exponential backoff base and cap, in seconds.
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    #: Per-connection cap on in-flight calls (one LLMClient per connection).
    max_concurrency: int = 2
    #: Ask the provider for token logprobs (optional verdict_logprob capture).
    request_logprobs: bool = False
    max_calls: int = 1500
    max_usd: float = 5.0
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    reasoning_usd_per_million: float | None = None
    price_source: str = "unverified"
    price_source_url: str | None = None
    price_source_date: str | None = None
    env_files_loaded: tuple[str, ...] = ()

    @property
    def has_api_credentials(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def safe_dict(self) -> dict[str, object]:
        """Settings suitable for logging: the API key is reduced to a boolean."""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_present": bool(self.api_key),
            "backend_override": self.backend_override,
            "hf_model": self.hf_model,
            "timeout_s": self.timeout_s,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "seed": self.seed,
            "retries": self.retries,
            "retry_attempts": self.retry_attempts,
            "retry_base_delay_s": self.retry_base_delay_s,
            "retry_max_delay_s": self.retry_max_delay_s,
            "max_concurrency": self.max_concurrency,
            "request_logprobs": self.request_logprobs,
            "max_calls": self.max_calls,
            "max_usd": self.max_usd,
            "price_source": self.price_source,
            "prices_configured": self.input_usd_per_million is not None and self.output_usd_per_million is not None,
            "price_source_url": self.price_source_url,
            "price_source_date": self.price_source_date,
            "env_files_loaded": list(self.env_files_loaded),
        }


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def _load_dotenv_files(paths: list[Path]) -> tuple[str, ...]:
    """Populate ``os.environ`` from existing ``.env`` files, without overriding.

    ``python-dotenv`` is used when importable; a minimal parser covers the
    ``KEY=VALUE`` / ``export KEY=VALUE`` / ``# comment`` subset otherwise. Real
    environment variables always win, which keeps CI and shell overrides
    authoritative.
    """
    loaded: list[str] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.is_file():
            continue
        try:
            from dotenv import load_dotenv  # type: ignore[import-not-found]

            load_dotenv(dotenv_path=path, override=False)
        except Exception:  # pragma: no cover - fallback path
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :]
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        loaded.append(str(path))
    return tuple(loaded)


def load_settings(dotenv_paths: list[Path] | None = None) -> Settings:
    """Read settings from the environment (after optional ``.env`` loading)."""
    if dotenv_paths is None:
        dotenv_paths = [] if os.environ.get("PYTEST_MEDEVAL_DISABLE_DOTENV") == "1" else [repo_root() / ".env", Path.cwd() / ".env"]
    loaded = _load_dotenv_files(dotenv_paths)

    timeout_raw = os.environ.get("MEDEVAL_TIMEOUT_S", "120")
    max_tokens_raw = os.environ.get("MEDEVAL_MAX_TOKENS", "512")
    temperature_raw = os.environ.get("MEDEVAL_TEMPERATURE", "0.0")
    seed_raw = os.environ.get("MEDEVAL_SEED", "20260101")
    retries_raw = os.environ.get("MEDEVAL_RETRIES", "2")
    retry_attempts_raw = os.environ.get("MEDEVAL_RETRY_ATTEMPTS", "4")
    retry_base_delay_raw = os.environ.get("MEDEVAL_RETRY_BASE_DELAY_S", "0.5")
    retry_max_delay_raw = os.environ.get("MEDEVAL_RETRY_MAX_DELAY_S", "8")
    max_concurrency_raw = os.environ.get("MEDEVAL_MAX_CONCURRENCY", "2")
    request_logprobs_raw = os.environ.get("MEDEVAL_REQUEST_LOGPROBS", "0")
    max_calls_raw = os.environ.get("MEDEVAL_MAX_CALLS", "1500")
    max_usd_raw = os.environ.get("MEDEVAL_MAX_USD", "5")

    override = (os.environ.get("MEDEVAL_BACKEND") or "auto").strip().lower()
    if override in {"0", ""}:
        override = "auto"

    def _as_float(raw: str, default: float) -> float:
        try:
            return float(raw)
        except ValueError:
            return default

    def _as_int(raw: str, default: int) -> int:
        try:
            return int(raw)
        except ValueError:
            return default

    def _optional_nonnegative_float(name: str) -> float | None:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return None
        value = _as_float(raw, -1.0)
        return value if value >= 0 else None

    return Settings(
        base_url=_first_env(_ENV_ALIASES["base_url"]),
        model=_first_env(_ENV_ALIASES["model"]),
        api_key=_first_env(_ENV_ALIASES["api_key"]),
        backend_override=override,
        hf_model=_first_env(("MEDEVAL_HF_MODEL",)),
        timeout_s=max(0.001, _as_float(timeout_raw, 120.0)),
        max_tokens=_as_int(max_tokens_raw, 512),
        temperature=_as_float(temperature_raw, 0.0),
        seed=_as_int(seed_raw, 20260101),
        retries=max(0, _as_int(retries_raw, 2)),
        retry_attempts=max(1, _as_int(retry_attempts_raw, 4)),
        retry_base_delay_s=max(0.0, _as_float(retry_base_delay_raw, 0.5)),
        retry_max_delay_s=max(0.0, _as_float(retry_max_delay_raw, 8.0)),
        max_concurrency=max(1, _as_int(max_concurrency_raw, 2)),
        request_logprobs=request_logprobs_raw.strip().lower() in _TRUE,
        max_calls=max(1, _as_int(max_calls_raw, 1500)),
        max_usd=max(0.0, _as_float(max_usd_raw, 5.0)),
        input_usd_per_million=_optional_nonnegative_float("MEDEVAL_INPUT_USD_PER_1M"),
        output_usd_per_million=_optional_nonnegative_float("MEDEVAL_OUTPUT_USD_PER_1M"),
        reasoning_usd_per_million=_optional_nonnegative_float("MEDEVAL_REASONING_USD_PER_1M"),
        price_source=os.environ.get("MEDEVAL_PRICE_SOURCE", "unverified").strip() or "unverified",
        price_source_url=os.environ.get("MEDEVAL_PRICE_SOURCE_URL") or None,
        price_source_date=os.environ.get("MEDEVAL_PRICE_SOURCE_DATE") or None,
        env_files_loaded=loaded,
    )
