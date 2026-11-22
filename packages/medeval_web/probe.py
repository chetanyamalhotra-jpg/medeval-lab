"""Connection probing: verify a provider endpoint before it is used for a run.

Two levels, both owner-initiated from the Models page:

1. **List models** (``GET {base}/models``) — no spend. This is the primary check.
2. **One-token call** (``POST {base}/chat/completions`` with ``max_tokens=1``) —
   tiny but real spend, so it only runs behind an explicit confirmation from the
   browser, for the endpoints that publish no model list.

Every failure is reported with the key redacted, and no response body is echoed
verbatim: provider error bodies are truncated and passed through redaction.
"""

from __future__ import annotations

from typing import Any

import requests

from medeval_core.safety import redact_text
from medeval_web.connections import Connection

__all__ = ["probe_connection"]


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def probe_connection(
    connection: Connection,
    *,
    api_key: str | None,
    confirm_one_token_call: bool = False,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Check a connection. Never raises for a normal 'unreachable' answer."""
    secret_values = (api_key,) if api_key else ()
    base = connection.base_url.rstrip("/")
    if base.endswith("/v1"):
        models_url = f"{base}/models"
        chat_url = f"{base}/chat/completions"
    else:
        models_url = f"{base}/v1/models"
        chat_url = f"{base}/v1/chat/completions"

    result: dict[str, Any] = {
        "connection_id": connection.id,
        "name": connection.name,
        "base_url": connection.base_url,
        "models_url": models_url,
        "reachable": False,
        "models": [],
        "n_models": 0,
        "verified": False,
        "one_token_call": None,
        "error": None,
    }

    try:
        response = requests.get(models_url, headers=_headers(api_key), timeout=timeout_s)
        if response.status_code != 200:
            result["error"] = f"HTTP {response.status_code}: {redact_text(response.text[:300], secret_values)}"
            return result
        payload = response.json()
    except requests.RequestException as exc:
        result["error"] = redact_text(f"{type(exc).__name__}: {exc}", secret_values)
        return result
    except ValueError:
        result["error"] = "endpoint answered with non-JSON"
        return result

    rows = payload.get("data") if isinstance(payload, dict) else None
    models: list[str] = []
    if isinstance(rows, list):
        models = sorted(
            str(row.get("id"))
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("id")
        )
    result["reachable"] = True
    result["models"] = models
    result["n_models"] = len(models)
    result["verified"] = bool(models)
    if connection.model and connection.model in models:
        result["configured_model_visible"] = True

    if confirm_one_token_call:
        try:
            response = requests.post(
                chat_url,
                headers=_headers(api_key),
                json={
                    "model": connection.model or (models[0] if models else ""),
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
                timeout=timeout_s,
            )
            if response.status_code == 200:
                body = response.json()
                returned_model = body.get("model") if isinstance(body, dict) else None
                result["one_token_call"] = {
                    "ok": True,
                    "model_returned": returned_model,
                    "usage": body.get("usage") if isinstance(body, dict) else None,
                }
            else:
                result["one_token_call"] = {
                    "ok": False,
                    "error": f"HTTP {response.status_code}: {redact_text(response.text[:300], secret_values)}",
                }
        except requests.RequestException as exc:
            result["one_token_call"] = {
                "ok": False,
                "error": redact_text(f"{type(exc).__name__}: {exc}", secret_values),
            }
    return result
