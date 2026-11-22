"""Run manifests: the provenance record written next to every study's results.

A manifest answers, without trusting anybody's memory: which commit produced this
output, which files were modified at the time, which package and Python versions
were installed, which model ids actually answered, which seeds and prompt
versions were used, and when the run started and finished.

The manifest never contains an API key or a full prompt; prompts are recorded as
SHA-256 digests only.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from .paths import repo_root
from .version import __version__

__all__ = [
    "build_manifest",
    "git_state",
    "package_versions",
    "sha256_text",
    "utc_now",
    "write_manifest",
]

_DISTRIBUTIONS = (
    "medeval-lab",
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "PyYAML",
    "jsonschema",
    "requests",
    "python-dotenv",
    "fastapi",
    "uvicorn",
    "httpx",
    "pytest",
)


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z``."""
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(args: Sequence[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_state(cwd: Path | None = None) -> dict[str, Any]:
    """Commit id, branch, and whether the tree was dirty when the run started.

    A dirty tree is recorded rather than refused: an exploratory run on modified
    code is still worth keeping, but it must not be mistaken for a reproducible
    one later.
    """
    root = cwd or repo_root()
    commit = _git(["rev-parse", "HEAD"], root)
    status = _git(["status", "--porcelain"], root)
    dirty_files = [line for line in (status or "").splitlines() if line.strip()]
    return {
        "commit": commit,
        "short_commit": (commit or "")[:7] or None,
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], root),
        "dirty": bool(dirty_files),
        "dirty_file_count": len(dirty_files),
        "dirty_files_sample": dirty_files[:20],
    }


def package_versions() -> dict[str, str]:
    """Installed versions of the packages a result could plausibly depend on."""
    from importlib import metadata

    out: dict[str, str] = {}
    for name in _DISTRIBUTIONS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    out["medeval-package"] = __version__
    return out


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Size, mtime and SHA-256 of a file, for data provenance."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


@dataclass
class Manifest:
    """Mutable accumulator for a run manifest."""

    run_id: str
    experiment: str
    started_utc: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_manifest(
    *,
    run_id: str,
    experiment: str,
    started_utc: str | None = None,
    models: Mapping[str, str] | None = None,
    seeds: Mapping[str, Any] | None = None,
    prompt_hashes: Mapping[str, str] | None = None,
    dataset: Mapping[str, Any] | None = None,
    configuration: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Assemble the standard manifest payload for one run."""
    return {
        "run_id": run_id,
        "experiment": experiment,
        "started_utc": started_utc or utc_now(),
        "package_version": __version__,
        "git": git_state(cwd),
        "environment": {
            "python": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "packages": package_versions(),
        "models": dict(models or {}),
        "seeds": dict(seeds or {}),
        "prompt_hashes": dict(prompt_hashes or {}),
        "dataset": dict(dataset or {}),
        "configuration": dict(configuration or {}),
        "extra": dict(extra or {}),
    }


def write_manifest(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False), encoding="utf-8")
    return path


def digest_lines(lines: Iterable[str]) -> str:
    """Order-independent digest of a set of strings (e.g. sampled item ids)."""
    ordered = sorted(lines)
    return sha256_text("\n".join(ordered))
