"""Filesystem layout helpers.

Every path is derived from the repository root so the project works from any
working directory and after being copied elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root.

    Resolution order: ``MEDEVAL_ROOT`` environment variable, then the nearest
    ancestor of this file that contains ``pyproject.toml``, then the current
    working directory.
    """
    env = os.environ.get("MEDEVAL_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return parent
    return Path.cwd().resolve()


def data_dir() -> Path:
    return repo_root() / "data"


def items_dir() -> Path:
    return data_dir() / "items"


def literature_csv() -> Path:
    return data_dir() / "literature.csv"


def criteria_dir() -> Path:
    return repo_root() / "packages" / "medeval_criteria" / "catalogue"


def rubrics_dir() -> Path:
    return data_dir() / "rubrics"


def results_dir() -> Path:
    """Results root. ``MEDEVAL_RESULTS_DIR`` redirects it (used by tests/CI)."""
    env = os.environ.get("MEDEVAL_RESULTS_DIR")
    if env:
        return Path(env).resolve()
    return repo_root() / "results"


def experiments_dir() -> Path:
    return repo_root() / "experiments"


def schemas_dir() -> Path:
    """Directory holding the shipped JSON Schema documents."""
    return Path(__file__).resolve().parent / "schemas"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
