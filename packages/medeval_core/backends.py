"""Backend detection.

The toolkit never assumes a model is available. It resolves, in order:

1. an explicitly configured OpenAI-compatible endpoint (base_url + model +
   api_key all present) -- this is how a user switches to a real API;
2. a local OpenAI-compatible model server (Ollama, LM Studio, vLLM, ...);
3. a small local Hugging Face model on CPU (only if ``torch``/``transformers``
   are importable and ``MEDEVAL_HF_MODEL`` names a model);
4. the deterministic MOCK judge.

The selection is recorded in every result file via :meth:`BackendSelection.as_dict`.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Settings

#: Candidate local OpenAI-compatible endpoints probed when nothing is configured.
DEFAULT_LOCAL_ENDPOINTS: tuple[str, ...] = (
    "http://127.0.0.1:11434/v1",  # Ollama
    "http://127.0.0.1:1234/v1",  # LM Studio
    "http://127.0.0.1:8080/v1",  # vLLM / llama.cpp server
    "http://127.0.0.1:8000/v1",
)

BACKEND_API = "openai-compatible-api"
BACKEND_LOCAL_SERVER = "local-model-server"
BACKEND_LOCAL_HF = "local-hf-cpu"
BACKEND_MOCK = "mock"

_VALID_OVERRIDES = {"auto", "api", "local", "hf", "mock"}


@dataclass(frozen=True)
class BackendSelection:
    """Which backend will answer, and why that one was chosen."""

    name: str
    model: str
    base_url: str | None
    reason: str
    api_key: str | None = field(default=None, repr=False)
    probe_detail: str | None = None
    requested_override: str = "auto"

    @property
    def is_mock(self) -> bool:
        return self.name == BACKEND_MOCK

    @property
    def pipeline_test_only(self) -> bool:
        """True when results cannot say anything about real judge behaviour."""
        return self.is_mock

    @property
    def label(self) -> str:
        if self.is_mock:
            return "MOCK judge (PIPELINE TEST ONLY)"
        return f"{self.name}:{self.model}"

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "reason": self.reason,
            "probe_detail": self.probe_detail,
            "requested_override": self.requested_override,
            "pipeline_test_only": self.pipeline_test_only,
            "label": self.label,
        }


class BackendUnavailable(RuntimeError):
    """Raised when an explicitly requested backend cannot be used."""


def _http_get_json(url: str, timeout_s: float) -> object | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = response.read().decode("utf-8", errors="replace")
        return json.loads(payload)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def probe_endpoint(base_url: str, timeout_s: float = 1.5) -> str | None:
    """Return a served model id if ``base_url`` looks like a live OpenAI API.

    Only ``GET {base_url}/models`` is used: it is read-only and available on
    Ollama, LM Studio, vLLM and most gateways.
    """
    base = base_url.rstrip("/")
    payload = _http_get_json(f"{base}/models", timeout_s)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if isinstance(first, dict) and isinstance(first.get("id"), str):
        return first["id"]
    return "unknown"


def _hf_available() -> tuple[bool, str]:
    if importlib.util.find_spec("torch") is None:
        return False, "torch not importable"
    if importlib.util.find_spec("transformers") is None:
        return False, "transformers not importable"
    return True, "torch and transformers importable"


def select_backend(settings: Settings, probe_timeout_s: float = 1.5) -> BackendSelection:
    """Resolve the backend according to the documented priority order."""
    override = (settings.backend_override or "auto").lower()
    if override not in _VALID_OVERRIDES:
        raise BackendUnavailable(
            f"MEDEVAL_BACKEND={override!r} is not one of {sorted(_VALID_OVERRIDES)}"
        )

    def _mock(reason: str) -> BackendSelection:
        return BackendSelection(
            name=BACKEND_MOCK,
            model="mock-judge-v1",
            base_url=None,
            reason=reason,
            requested_override=override,
        )

    if override == "mock":
        return _mock("explicitly requested via MEDEVAL_BACKEND=mock")

    if override == "api":
        if not settings.has_api_credentials:
            raise BackendUnavailable(
                "MEDEVAL_BACKEND=api requires MEDEVAL_BASE_URL, MEDEVAL_MODEL and "
                "MEDEVAL_API_KEY to all be set"
            )
        return BackendSelection(
            name=BACKEND_API,
            model=settings.model or "",
            base_url=settings.base_url,
            reason="explicit MEDEVAL_BACKEND=api with credentials present",
            api_key=settings.api_key,
            requested_override=override,
        )

    if override == "local":
        candidates = [settings.base_url] if settings.base_url else list(DEFAULT_LOCAL_ENDPOINTS)
        for candidate in candidates:
            if not candidate:
                continue
            served = probe_endpoint(candidate, probe_timeout_s)
            if served:
                return BackendSelection(
                    name=BACKEND_LOCAL_SERVER,
                    model=settings.model or served,
                    base_url=candidate,
                    reason="explicit MEDEVAL_BACKEND=local and endpoint answered /models",
                    probe_detail=f"served model: {served}",
                    requested_override=override,
                )
        raise BackendUnavailable(
            "MEDEVAL_BACKEND=local but no local OpenAI-compatible server answered /models "
            f"(tried: {', '.join(c for c in candidates if c)})"
        )

    if override == "hf":
        ok, detail = _hf_available()
        if not ok:
            raise BackendUnavailable(f"MEDEVAL_BACKEND=hf but {detail}")
        if not settings.hf_model:
            raise BackendUnavailable("MEDEVAL_BACKEND=hf requires MEDEVAL_HF_MODEL")
        return BackendSelection(
            name=BACKEND_LOCAL_HF,
            model=settings.hf_model,
            base_url=None,
            reason="explicit MEDEVAL_BACKEND=hf",
            probe_detail=detail,
            requested_override=override,
        )

    # auto ---------------------------------------------------------------
    if settings.has_api_credentials:
        # An explicitly configured endpoint is never probed: remote gateways may
        # not implement /models and credentials should not be spent on probing.
        return BackendSelection(
            name=BACKEND_API,
            model=settings.model or "",
            base_url=settings.base_url,
            reason="MEDEVAL_BASE_URL, MEDEVAL_MODEL and MEDEVAL_API_KEY are all set",
            api_key=settings.api_key,
            requested_override=override,
        )

    candidates: list[str] = []
    if settings.base_url:
        candidates.append(settings.base_url)
    candidates.extend(ep for ep in DEFAULT_LOCAL_ENDPOINTS if ep not in candidates)
    for candidate in candidates:
        served = probe_endpoint(candidate, probe_timeout_s)
        if served:
            return BackendSelection(
                name=BACKEND_LOCAL_SERVER,
                model=settings.model or served,
                base_url=candidate,
                reason="local OpenAI-compatible server detected on /models",
                probe_detail=f"served model: {served}",
                requested_override=override,
            )

    ok, hf_detail = _hf_available()
    if ok and settings.hf_model:
        return BackendSelection(
            name=BACKEND_LOCAL_HF,
            model=settings.hf_model,
            base_url=None,
            reason="no model server found; local HF model configured",
            probe_detail=hf_detail,
            requested_override=override,
        )

    reason = "no API credentials, no local model server, and no usable local HF model"
    if not ok:
        reason += f" ({hf_detail})"
    elif not settings.hf_model:
        reason += " (MEDEVAL_HF_MODEL not set)"
    return _mock(reason)
