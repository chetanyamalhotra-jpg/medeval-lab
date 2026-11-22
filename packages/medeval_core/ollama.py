"""Ollama availability, used for the local cross-family judge (J2).

The v0.2 specification is explicit about the failure mode: if Ollama is not
installed or not reachable, the run must fail with a clear message and
instructions, never silently fall back to a different judge. This module owns
that check and the message.

Ollama exposes an OpenAI-compatible endpoint at ``{base}/v1``, so the ordinary
:class:`medeval_core.client.OpenAICompatibleTransport` is reused for the calls;
only discovery differs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

__all__ = [
    "DEFAULT_OLLAMA_BASE_URL",
    "DEFAULT_OLLAMA_MODEL",
    "INSTALL_INSTRUCTIONS",
    "OllamaStatus",
    "OllamaUnavailable",
    "ollama_openai_base_url",
    "probe_ollama",
    "require_ollama",
]

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"

INSTALL_INSTRUCTIONS = """\
Ollama was not reachable, so the local cross-family judge (J2) cannot run.

J2 is a local, CPU-only model. It is a deliberate part of the design: judging with two models from one family cannot separate correlated error from shared family bias. Install and start it, then re-run:

    winget install --id Ollama.Ollama
    # or download the installer from https://ollama.com/download/windows

    ollama serve
    ollama pull llama3.2:3b

The pull is about 2 GB; CPU inference is fine. Then check the endpoint:

    Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing

If the model or the port is different, set them explicitly:

    $env:MEDEVAL_JUDGE2_MODEL = "llama3.2:3b"
    $env:MEDEVAL_JUDGE2_BASE_URL = "http://127.0.0.1:11434"

No other judge is substituted automatically. A run that cannot reach this endpoint records a blocked measurement instead of inventing a second judge.
"""


class OllamaUnavailable(RuntimeError):
    """Raised when the configured local judge endpoint cannot be used."""


@dataclass(frozen=True)
class OllamaStatus:
    """Result of probing a local Ollama server."""

    base_url: str
    reachable: bool
    models: tuple[str, ...] = ()
    served_by: str | None = None
    detail: str = ""

    @property
    def model_names(self) -> tuple[str, ...]:
        return self.models

    def as_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "reachable": self.reachable,
            "models": list(self.models),
            "served_by": self.served_by,
            "detail": self.detail,
        }


def ollama_openai_base_url(base_url: str = DEFAULT_OLLAMA_BASE_URL) -> str:
    """Map an Ollama root URL to its OpenAI-compatible ``/v1`` URL."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def probe_ollama(base_url: str = DEFAULT_OLLAMA_BASE_URL, timeout_s: float = 2.0) -> OllamaStatus:
    """Check ``GET /api/tags``; never raises for a normal 'not running' case."""
    base = base_url.rstrip("/")
    url = f"{base}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return OllamaStatus(
            base_url=base,
            reachable=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    try:
        payload = json.loads(raw)
    except ValueError:
        return OllamaStatus(base_url=base, reachable=False, detail="endpoint answered with non-JSON")
    rows = payload.get("models") if isinstance(payload, dict) else None
    models: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                models.append(row["name"])
    version = None
    if isinstance(payload, dict):
        version = payload.get("version")
    return OllamaStatus(
        base_url=base,
        reachable=True,
        models=tuple(sorted(models)),
        served_by=str(version) if version else None,
        detail="answered /api/tags",
    )


def require_ollama(
    *,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout_s: float = 2.0,
) -> OllamaStatus:
    """Return the status, or raise :class:`OllamaUnavailable` with instructions."""
    status = probe_ollama(base_url, timeout_s)
    if not status.reachable:
        raise OllamaUnavailable(
            f"{INSTALL_INSTRUCTIONS}\nProbe detail: {status.base_url} -> {status.detail}"
        )
    if status.models and model not in status.models:
        raise OllamaUnavailable(
            f"Ollama is running at {status.base_url} but model {model!r} is not pulled.\n"
            f"Available models: {', '.join(status.models) or '(none)'}\n\n"
            f"    ollama pull {model}\n\n"
            "Or set MEDEVAL_JUDGE2_MODEL to one of the available names."
        )
    return status
