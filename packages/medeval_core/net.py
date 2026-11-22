"""Minimal HTTP fetch helpers used by the dataset loaders.

Deliberately dependency-light (``urllib``): the loaders must work even when the
Hugging Face cache and library temp directories are unusable. Every download is
hashed and recorded so a dataset snapshot can be identified later.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["FetchResult", "fetch_json", "fetch_url", "sha256_file"]


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one download."""

    url: str
    path: Path
    sha256: str
    n_bytes: int
    from_cache: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "path": str(self.path),
            "sha256": self.sha256,
            "n_bytes": self.n_bytes,
            "from_cache": self.from_cache,
        }


def fetch_url(
    url: str,
    dest: Path,
    *,
    timeout_s: float = 120.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    """Download ``url`` to ``dest`` unless a non-empty file is already there.

    The destination directory must already exist; it is created if necessary
    with ordinary permissions (mkdtemp-style private directories break under
    restricted Windows tokens).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return FetchResult(url, dest, sha256_file(dest), dest.stat().st_size, True)

    request_headers = {"User-Agent": "medeval-lab/0.1 (+https://github.com/)"}
    request_headers.update(headers or {})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = response.read()
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(payload)
            tmp.replace(dest)
            return FetchResult(url, dest, sha256_file(dest), len(payload), False)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def fetch_json(
    url: str,
    dest: Path,
    *,
    timeout_s: float = 120.0,
    retries: int = 2,
) -> tuple[Any, FetchResult]:
    """Download and parse a JSON document, returning the object and the fetch record."""
    result = fetch_url(url, dest, timeout_s=timeout_s, retries=retries)
    with dest.open("r", encoding="utf-8") as handle:
        return json.load(handle), result
