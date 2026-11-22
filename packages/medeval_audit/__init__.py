"""Benchmark item-integrity audit.

What this package does: find duplicates, check answer keys and index bases,
quantify shortcut baselines and run cheap contamination screens on public
datasets. What it does not do: decide whether an item is clinically correct.
"""

from __future__ import annotations

from .answer_key import AnswerKeyReport, check_answer_key, detect_index_base, shortcut_baselines
from .contamination import ContaminationReport, answer_in_stem, cross_dataset_overlap, run_screens
from .duplicates import DuplicateGroup, DuplicateReport, find_duplicates, jaccard, normalise_text, shingles
from .loaders import DATASETS, AuditRecord, DatasetSpec, LoadedSplit, load_split, normalise_records
from .pipeline import DEFAULT_SPLITS, AuditRun, load_audit_records, materialize_jsonl, run_audit
from .report import DatasetAudit, audit_split, render_markdown, write_report

__all__ = [
    "DATASETS",
    "DEFAULT_SPLITS",
    "AnswerKeyReport",
    "AuditRecord",
    "AuditRun",
    "ContaminationReport",
    "DatasetAudit",
    "DatasetSpec",
    "DuplicateGroup",
    "DuplicateReport",
    "LoadedSplit",
    "answer_in_stem",
    "audit_split",
    "check_answer_key",
    "cross_dataset_overlap",
    "detect_index_base",
    "find_duplicates",
    "jaccard",
    "load_audit_records",
    "load_split",
    "materialize_jsonl",
    "normalise_records",
    "normalise_text",
    "render_markdown",
    "run_audit",
    "run_screens",
    "shingles",
    "shortcut_baselines",
    "write_report",
]
