"""Audit orchestration: load, materialise, audit, report."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medeval_core.paths import data_dir, results_dir

from .loaders import DATASETS, AuditRecord, LoadedSplit, load_split
from .report import DatasetAudit, audit_split, write_report

__all__ = [
    "DEFAULT_SPLITS",
    "AuditRun",
    "materialize_jsonl",
    "run_audit",
]

#: The labelled split used for auditing each dataset.
DEFAULT_SPLITS: dict[str, str] = {
    "medmcqa": "validation",
    "medqa_4options": "dev",
    "pubmedqa": "test",
}


@dataclass
class AuditRun:
    """Result of one audit run."""

    audits: list[DatasetAudit]
    skipped: list[dict[str, str]]
    json_path: Path
    markdown_path: Path
    loaded: dict[str, LoadedSplit]

    def as_dict(self) -> dict[str, Any]:
        return {
            "datasets": [audit.as_dict() for audit in self.audits],
            "skipped": self.skipped,
            "json_path": str(self.json_path),
            "markdown_path": str(self.markdown_path),
        }


def materialize_jsonl(loaded: LoadedSplit, out_path: Path) -> Path:
    """Write the original rows of a split as JSONL for local task definitions.

    The rows are the dataset's own fields, unmodified, so the harness task sees
    exactly what the public dataset provides. Writing the snapshot also pins the
    rows by content hash in the audit report.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for record in loaded.records:
            handle.write(json.dumps(record.raw, ensure_ascii=False) + "\n")
    return out_path


def run_audit(
    datasets: Sequence[str] = ("medmcqa", "medqa_4options", "pubmedqa"),
    *,
    out_dir: Path | None = None,
    cache_dir: Path | None = None,
    near_dup_threshold: float = 0.85,
    allow_download: bool = True,
    materialize: bool = True,
) -> AuditRun:
    """Audit the requested datasets, recording (not hiding) failures.

    A dataset that cannot be loaded is reported in ``skipped`` with the exact
    error; the remaining datasets are still audited.
    """
    out_dir = out_dir or (results_dir() / "audit")
    cache_out = cache_dir or (data_dir() / "cache")
    loaded: dict[str, LoadedSplit] = {}
    skipped: list[dict[str, str]] = []

    for key in datasets:
        if key not in DATASETS:
            skipped.append({"dataset": key, "reason": "unknown dataset key"})
            continue
        split = DEFAULT_SPLITS.get(key, "validation")
        try:
            loaded[key] = load_split(key, split, allow_download=allow_download)
        except Exception as exc:
            skipped.append(
                {
                    "dataset": key,
                    "split": split,
                    "reason": f"{type(exc).__name__}: {str(exc)[:600]}",
                }
            )

    if materialize:
        for key, split_loaded in loaded.items():
            materialize_jsonl(split_loaded, cache_out / f"{key}_{split_loaded.split}.jsonl")

    audits: list[DatasetAudit] = []
    for key, split_loaded in loaded.items():
        other_key = next((k for k in loaded if k != key), None)
        audits.append(
            audit_split(
                split_loaded,
                near_dup_threshold=near_dup_threshold,
                other=loaded[other_key].records if other_key else None,
                other_name=other_key or "",
            )
        )

    json_path, md_path = write_report(audits, out_dir, skipped=skipped)
    return AuditRun(
        audits=audits,
        skipped=skipped,
        json_path=json_path,
        markdown_path=md_path,
        loaded=loaded,
    )


def load_audit_records(dataset: str, split: str | None = None) -> list[AuditRecord]:
    """Convenience: load normalised records for later analysis steps."""
    key_split = split or DEFAULT_SPLITS.get(dataset, "validation")
    return list(load_split(dataset, key_split).records)
