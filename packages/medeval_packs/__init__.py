"""Paper Packs: declarative medical-AI evaluation protocols.

A pack is data (``papers/<id>/paper.yaml`` plus a local dataset) executed by
:class:`~medeval_packs.runner.MatrixRun`.  The package exposes the schema,
registry, graders, cache, statistics analysis and exporters, and nothing in it
chooses a provider, model, family or price.

This is a research benchmarking harness.  It is not a medical device, has no
clinical validation and no clinician has reviewed its content.
"""

from __future__ import annotations

from medeval_study.report import MEDICAL_NOTICE

from .analysis import analyse_matrix
from .cache import ContentAddressedCache, cache_key
from .dataset import PackItem, load_items
from .registry import PackEntry, archived_studies, init_pack, list_packs, resolve_pack
from .runner import (
    MatrixConfig,
    MatrixRun,
    ModelBinding,
    default_matrix_root,
    load_matrix_run,
    regrade_run,
)
from .schema import PackValidation, PackValidationError, PaperPack, load_pack

__all__ = [
    "MEDICAL_NOTICE",
    "ContentAddressedCache",
    "MatrixConfig",
    "MatrixRun",
    "ModelBinding",
    "PackEntry",
    "PackItem",
    "PackValidation",
    "PackValidationError",
    "PaperPack",
    "analyse_matrix",
    "archived_studies",
    "cache_key",
    "default_matrix_root",
    "init_pack",
    "list_packs",
    "load_items",
    "load_matrix_run",
    "load_pack",
    "regrade_run",
    "resolve_pack",
]
