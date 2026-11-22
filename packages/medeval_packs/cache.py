"""Content-addressed response cache for the v0.6 matrix runner.

Layout::

    .medeval_cache/<first two hex chars>/<sha256>.json

The key is ``sha256(model_id + params + rendered_prompt + item_hash)``.  The same
request therefore maps to the same file regardless of run id, target label or
process, which is what makes stop/resume and repeated experiments free of
duplicate paid calls.

Writes are atomic: the payload goes to a temporary file in the same directory and
is then ``os.replace``-d over the final name.  A crash either leaves the old
entry or the new one, never a half-written JSON file.

The cache never stores an API key, a header or a URL.  ``prune`` is available for
the UI's "clear cache" action and never touches anything outside its root.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medeval_core.hashing import sha256_text, stable_json_dumps

__all__ = ["CACHE_DIRNAME", "ContentAddressedCache", "cache_key"]


CACHE_DIRNAME = ".medeval_cache"


def cache_key(
    model_id: str,
    params: Mapping[str, Any],
    rendered_prompt: str,
    item_hash: str,
) -> str:
    """The documented cache key: ``sha256(model + params + prompt + item_hash)``."""
    payload = "|".join(
        (
            str(model_id),
            stable_json_dumps(dict(params)),
            rendered_prompt,
            str(item_hash),
        )
    )
    return sha256_text(payload)


@dataclass(frozen=True)
class CacheEntry:
    key: str
    model: str
    prompt_sha256: str
    item_hash: str
    text: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    latency_ms: float
    created_utc: str
    metadata: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "item_hash": self.item_hash,
            "text": self.text,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "latency_ms": self.latency_ms,
            "created_utc": self.created_utc,
            "metadata": dict(self.metadata),
        }


def _replace_with_retry(source: str, destination: Path, *, attempts: int = 20, delay_s: float = 0.02) -> None:
    """Atomic replace with a short Windows sharing-violation retry."""
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


class ContentAddressedCache:
    """Crash-safe local cache of successful model completions."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path.cwd() / CACHE_DIRNAME
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        if not key or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("cache key must be a lowercase hex digest")
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        path = self._path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, Mapping) or payload.get("key") != key:
            return None
        try:
            return CacheEntry(
                key=key,
                model=str(payload.get("model") or ""),
                prompt_sha256=str(payload.get("prompt_sha256") or ""),
                item_hash=str(payload.get("item_hash") or ""),
                text=str(payload.get("text") or ""),
                input_tokens=int(payload.get("input_tokens") or 0),
                output_tokens=int(payload.get("output_tokens") or 0),
                reasoning_tokens=int(payload.get("reasoning_tokens") or 0),
                latency_ms=float(payload.get("latency_ms") or 0.0),
                created_utc=str(payload.get("created_utc") or ""),
                metadata=dict(payload.get("metadata") or {}),
            )
        except (TypeError, ValueError):
            return None

    def put(self, entry: CacheEntry) -> Path:
        path = self._path(entry.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(entry.as_dict(), stream, ensure_ascii=False, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                _replace_with_retry(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return path

    def stats(self) -> dict[str, Any]:
        count = 0
        bytes_total = 0
        for path in self.iter_entries():
            count += 1
            try:
                bytes_total += path.stat().st_size
            except OSError:
                continue
        return {"root": str(self.root), "entries": count, "bytes": bytes_total}

    def iter_entries(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*/*.json")):
            if path.is_file():
                yield path

    def prune(self) -> dict[str, Any]:
        """Delete the whole cache tree (only inside ``root``)."""
        removed = 0
        if self.root.is_dir():
            for path in self.iter_entries():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
            for directory in sorted(self.root.iterdir()):
                if directory.is_dir() and len(directory.name) == 2:
                    shutil.rmtree(directory, ignore_errors=True)
        return {"root": str(self.root), "removed": removed}
