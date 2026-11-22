"""Stable hashing helpers used for provenance and prompt logging."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def sha256_text(text: str) -> str:
    """Hex SHA-256 of the UTF-8 encoding of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_dumps(obj: Any) -> str:
    """Serialise ``obj`` deterministically (sorted keys, no whitespace drift)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def prompt_hash(
    system: str | None,
    user: str,
    template_version: str,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Hash identifying an exact judge prompt.

    Two calls with the same ``prompt_hash`` sent the same bytes to the model
    (modulo the model itself), which is what makes prompt changes auditable.
    """
    payload: dict[str, Any] = {
        "system": system or "",
        "user": user,
        "template_version": template_version,
        "extra": dict(extra or {}),
    }
    return sha256_text(stable_json_dumps(payload))


def dataset_fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    """Content hash for a list of dataset rows, independent of row order.

    Used by the audit report so that "audited medmcqa 1 000 rows" can be tied to
    a specific snapshot instead of a mutable dataset name.
    """
    hashes = sorted(sha256_text(stable_json_dumps(dict(row))) for row in rows)
    return sha256_text("".join(hashes))
