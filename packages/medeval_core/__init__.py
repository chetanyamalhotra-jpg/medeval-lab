"""medeval_core: shared foundations for the medeval-lab toolkit.

Public surface is intentionally small: paths, configuration/backends, the single
OpenAI-compatible client, call logging, item schema validation and hashing.
"""

from __future__ import annotations

from .config import Settings, load_settings
from .hashing import prompt_hash, sha256_text, stable_json_dumps
from .paths import (
    criteria_dir,
    data_dir,
    experiments_dir,
    items_dir,
    literature_csv,
    repo_root,
    results_dir,
    rubrics_dir,
)
from .version import (
    CRITERIA_VERSION,
    DATASET_VERSIONS,
    ITEMS_VERSION,
    METRICS_VERSION,
    RUBRIC_VERSION,
    __version__,
)

__all__ = [
    "CRITERIA_VERSION",
    "DATASET_VERSIONS",
    "ITEMS_VERSION",
    "METRICS_VERSION",
    "RUBRIC_VERSION",
    "Settings",
    "__version__",
    "criteria_dir",
    "data_dir",
    "experiments_dir",
    "items_dir",
    "literature_csv",
    "load_settings",
    "prompt_hash",
    "repo_root",
    "results_dir",
    "rubrics_dir",
    "sha256_text",
    "stable_json_dumps",
]
