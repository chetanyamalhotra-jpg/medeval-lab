"""Strict prompt rendering for Paper Packs.

Templates use ``$variable`` / ``${variable}`` placeholders (``string.Template``),
not ``str.format``: JSON braces appear literally in extraction prompts, and a
format string would treat them as fields.  Substitution is strict, so an unknown
placeholder or a missing variable is an error before any call is made.
"""

from __future__ import annotations

import json
import string
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["PromptRenderError", "render_template"]


class PromptRenderError(ValueError):
    """A template cannot be rendered with the variables a pack provides."""


def render_template(template: str, variables: Mapping[str, Any], *, where: str = "prompt") -> str:
    """Render a strict ``$variable`` template.

    Values are converted to text with stable formatting: lists become newline
    bullets, mappings become sorted ``key: value`` lines and everything else uses
    ``str``.  A missing variable raises :class:`PromptRenderError` naming the
    variable (never a key or an answer).
    """
    if not isinstance(template, str) or not template.strip():
        raise PromptRenderError(f"{where} is empty")
    expanded: dict[str, str] = {}
    for name, value in variables.items():
        expanded[name] = _as_text(value)
    try:
        return string.Template(template).substitute(expanded)
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise PromptRenderError(
            f"{where} references ${missing}, which this pack does not provide; "
            f"available variables: {', '.join(sorted(expanded))}"
        ) from exc
    except ValueError as exc:
        raise PromptRenderError(f"{where} is not a valid template: {exc}") from exc


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_as_text(item)}" for key, item in sorted(value.items(), key=lambda pair: str(pair[0])))
    if isinstance(value, Sequence):
        return "\n".join(f"- {_as_text(item)}" for item in value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
