"""Minimal provider discovery; model IDs are never guessed."""
from __future__ import annotations

from typing import Any

import requests

from .safety import redact_text


def list_models(base_url: str, api_key: str | None, timeout_s: float = 20.0) -> list[str]:
    """Return model IDs from the configured OpenAI-compatible ``/models`` API."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.get(base_url.rstrip("/") + "/models", headers=headers, timeout=timeout_s)
        response.raise_for_status()
        payload: Any = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return sorted(str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id"))
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise RuntimeError(redact_text(f"model discovery failed: {type(exc).__name__}: {exc}", (api_key,))) from exc
