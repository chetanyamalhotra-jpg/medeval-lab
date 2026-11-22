"""JSONL call logging.

Every model call -- judge, baseline or probe -- produces exactly one record. The
record is what later analysis reads, so it must be sufficient to re-derive a
judge score without re-calling the model. API keys are never logged.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .safety import redact_value


def utc_now_iso() -> str:
    """Current UTC time in ISO-8601 with explicit ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class CallRecord:
    """One model (or mock) call."""

    call_id: str
    run_id: str
    timestamp_utc: str
    backend: str
    model: str
    prompt_sha256: str
    template_version: str
    system: str | None
    prompt: str
    output: str
    temperature: float
    seed: int
    max_tokens: int
    latency_ms: float
    base_url: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "run_id": self.run_id,
            "timestamp_utc": self.timestamp_utc,
            "backend": self.backend,
            "model": self.model,
            "base_url": self.base_url,
            "prompt_sha256": self.prompt_sha256,
            "template_version": self.template_version,
            "system": self.system,
            "prompt": self.prompt,
            "output": self.output,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "latency_ms": round(self.latency_ms, 3),
            "error": self.error,
            "metadata": self.metadata,
        }


class CallSink(Protocol):
    def write(self, record: CallRecord) -> Path | None: ...


class NullCallSink:
    """Drops records (used only in unit tests)."""

    def write(self, record: CallRecord) -> Path | None:
        return None


class JsonlCallSink:
    """Append-only JSONL file of call records."""

    def __init__(self, path: Path, *, secrets: tuple[str | None, ...] = ()) -> None:
        self.path = path
        self.secrets = secrets
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Touch so that a run with zero calls still leaves evidence of its path.
        self.path.touch(exist_ok=True)

    def write(self, record: CallRecord) -> Path:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redact_value(record.as_dict(), self.secrets), ensure_ascii=False) + "\n")
        return self.path


def new_run_id(prefix: str = "run") -> str:
    """Short unique run identifier: ``<prefix>-<utcstamp>-<8 hex>``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def read_calls(path: Path) -> list[dict[str, Any]]:
    """Read a call-log JSONL file into a list of dictionaries."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def summarize_calls(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Cheap summary used in run manifests."""
    errors = sum(1 for r in records if r.get("error"))
    backends = sorted({str(r.get("backend")) for r in records})
    models = sorted({str(r.get("model")) for r in records})
    return {
        "n_calls": len(records),
        "n_errors": errors,
        "backends": backends,
        "models": models,
    }
