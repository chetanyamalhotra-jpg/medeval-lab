"""Dataset loading for the benchmark audit.

Three strategies are tried in order, and the one that worked is recorded:

1. ``datasets.load_dataset`` -- the normal path on an unrestricted machine;
2. reading the Hugging Face datasets cache directly (``Dataset.from_file``);
3. downloading the public JSON/Parquet files and parsing them locally.

Strategy 3 exists because on some locked-down Windows setups the ``datasets``
builder cannot create its cache lock, and because the toolkit must remain usable
without a writable global cache. Nothing is ever modified in the source dataset:
rows are normalised into :class:`AuditRecord` objects and the original row is
kept verbatim under ``raw``.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medeval_core.net import FetchResult, fetch_url
from medeval_core.paths import data_dir

__all__ = [
    "DATASETS",
    "AuditRecord",
    "DatasetSpec",
    "LoadedSplit",
    "load_split",
    "normalise_records",
]

MEDMCQA_LABELS = ["A", "B", "C", "D"]


@dataclass(frozen=True)
class DatasetSpec:
    """Static description of a public dataset, including its verified licence."""

    key: str
    repo_id: str
    licence: str
    licence_evidence: str
    splits: tuple[str, ...]
    homepage: str = ""
    notes: str = ""

    @property
    def hub_api(self) -> str:
        return f"https://huggingface.co/api/datasets/{self.repo_id}"


#: Licences below were read from the Hugging Face dataset API on 2026-11-15
#: (see data/datasets.csv for the recorded evidence strings).
DATASETS: dict[str, DatasetSpec] = {
    "medmcqa": DatasetSpec(
        key="medmcqa",
        repo_id="openlifescienceai/medmcqa",
        licence="apache-2.0",
        licence_evidence="https://huggingface.co/api/datasets/openlifescienceai/medmcqa -> cardData.license=['apache-2.0']",
        splits=("train", "validation", "test"),
        homepage="https://huggingface.co/datasets/openlifescienceai/medmcqa",
        notes="Official MedMCQA release; `cop` is a 0-based index into A-D.",
    ),
    "medqa_4options": DatasetSpec(
        key="medqa_4options",
        repo_id="GBaker/MedQA-USMLE-4-options-hf",
        licence="cc-by-sa-4.0",
        licence_evidence="https://huggingface.co/api/datasets/GBaker/MedQA-USMLE-4-options-hf -> cardData.license='cc-by-sa-4.0'",
        splits=("train", "dev", "test"),
        homepage="https://huggingface.co/datasets/GBaker/MedQA-USMLE-4-options-hf",
        notes="4-option mirror of MedQA-USMLE; `label` is a 0-based index.",
    ),
    "pubmedqa": DatasetSpec(
        key="pubmedqa",
        repo_id="bigbio/pubmed_qa",
        licence="mit",
        licence_evidence="https://huggingface.co/api/datasets/bigbio/pubmed_qa -> cardData.license='mit'",
        splits=("train", "validation", "test"),
        homepage="https://huggingface.co/datasets/bigbio/pubmed_qa",
        notes="Uses a legacy loading script; skipped unless it loads. Not a multiple-choice task in v0.1.",
    ),
}


@dataclass(frozen=True)
class AuditRecord:
    """One item in the normalised form the audit operates on."""

    dataset: str
    row_index: int
    item_id: str
    question: str
    options: tuple[str, ...]
    option_labels: tuple[str, ...]
    gold_index: int | None
    gold_text: str | None
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "row_index": self.row_index,
            "item_id": self.item_id,
            "question": self.question,
            "options": list(self.options),
            "option_labels": list(self.option_labels),
            "gold_index": self.gold_index,
            "gold_text": self.gold_text,
            "extra": self.extra,
        }


@dataclass(frozen=True)
class LoadedSplit:
    """A loaded split plus the provenance of how it was obtained."""

    spec: DatasetSpec
    split: str
    records: tuple[AuditRecord, ...]
    strategy: str
    source_detail: str
    n_raw_rows: int
    notes: str = ""

    @property
    def n(self) -> int:
        return len(self.records)


def _cache_dir() -> Path:
    override = os.environ.get("MEDEVAL_CACHE_DIR")
    if override:
        return Path(override)
    return data_dir() / "cache"


def _cached_arrows(repo_id: str) -> list[str]:
    base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "datasets"
    safe = repo_id.replace("/", "___")
    pattern = str(base / safe / "**" / "*.arrow")
    return sorted(glob.glob(pattern, recursive=True))


def _load_from_datasets_library(repo_id: str, split: str) -> Any:
    from datasets import load_dataset  # local import: optional heavy dependency

    return load_dataset(repo_id, split=split)


def _load_from_cached_arrow(repo_id: str, split: str) -> Any:
    from datasets import Dataset

    candidates = [p for p in _cached_arrows(repo_id) if split in Path(p).name]
    if not candidates:
        raise FileNotFoundError(f"no cached arrow file for {repo_id} split={split}")
    return Dataset.from_file(candidates[0])


def _load_medqa_json(split: str) -> tuple[list[dict[str, Any]], FetchResult]:
    filename = {"train": "train.json", "dev": "dev.json", "test": "test.json"}.get(split, f"{split}.json")
    url = f"https://huggingface.co/datasets/GBaker/MedQA-USMLE-4-options-hf/resolve/main/{filename}"
    dest = _cache_dir() / f"medqa_4options_{split}.json"
    fetch = fetch_url(url, dest)
    text = dest.read_text(encoding="utf-8")
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        # Despite the .json suffix the hub file is JSON Lines.
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError(f"unexpected JSON shape in {dest}")
    return rows, fetch


def _load_medmcqa_parquet(split: str) -> tuple[list[dict[str, Any]], FetchResult]:
    filename = f"data/{split}-00000-of-00001.parquet"
    url = f"https://huggingface.co/datasets/openlifescienceai/medmcqa/resolve/main/{filename}"
    dest = _cache_dir() / f"medmcqa_{split}.parquet"
    fetch = fetch_url(url, dest)
    from datasets import Dataset

    dataset = Dataset.from_parquet(str(dest))
    return [dict(row) for row in dataset], fetch


def _records_medmcqa(rows: list[dict[str, Any]]) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    for index, row in enumerate(rows):
        options = (str(row["opa"]), str(row["opb"]), str(row["opc"]), str(row["opd"]))
        gold_raw = row.get("cop")
        gold_index = int(gold_raw) if gold_raw is not None else None
        records.append(
            AuditRecord(
                dataset="medmcqa",
                row_index=index,
                item_id=str(row.get("id", f"medmcqa-{index}")),
                question=str(row.get("question", "")),
                options=options,
                option_labels=tuple(MEDMCQA_LABELS),
                gold_index=gold_index,
                gold_text=options[gold_index] if gold_index is not None and 0 <= gold_index < 4 else None,
                extra={
                    "subject_name": row.get("subject_name"),
                    "topic_name": row.get("topic_name"),
                    "choice_type": row.get("choice_type"),
                    "exp": row.get("exp"),
                },
                raw=dict(row),
            )
        )
    return records


def _records_medqa(rows: list[dict[str, Any]]) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    for index, row in enumerate(rows):
        options = (
            str(row["ending0"]),
            str(row["ending1"]),
            str(row["ending2"]),
            str(row["ending3"]),
        )
        gold_raw = row.get("label")
        gold_index = int(gold_raw) if gold_raw is not None else None
        records.append(
            AuditRecord(
                dataset="medqa_4options",
                row_index=index,
                item_id=str(row.get("id", f"medqa-{index}")),
                question=str(row.get("sent1", "")),
                options=options,
                option_labels=tuple(MEDMCQA_LABELS),
                gold_index=gold_index,
                gold_text=options[gold_index] if gold_index is not None and 0 <= gold_index < 4 else None,
                extra={"sent2": row.get("sent2")},
                raw=dict(row),
            )
        )
    return records


def normalise_records(dataset: str, rows: list[dict[str, Any]]) -> list[AuditRecord]:
    """Convert raw rows into :class:`AuditRecord` objects."""
    if dataset == "medmcqa":
        return _records_medmcqa(rows)
    if dataset == "medqa_4options":
        return _records_medqa(rows)
    raise ValueError(f"no normaliser for dataset {dataset!r}")


def load_split(dataset: str, split: str, *, allow_download: bool = True) -> LoadedSplit:
    """Load one split using the first strategy that works, recording which one."""
    if dataset not in DATASETS:
        raise KeyError(f"unknown dataset {dataset!r}; known: {sorted(DATASETS)}")
    spec = DATASETS[dataset]
    strategies: list[tuple[str, Any]] = []
    if os.environ.get("MEDEVAL_SKIP_DATASETS_BUILDER") != "1":
        # The builder writes a path-derived lock file into the HF cache; on
        # Windows that name can exceed MAX_PATH, so it is skippable.
        strategies.append(("datasets.load_dataset", lambda: _load_from_datasets_library(spec.repo_id, split)))
    strategies.append(("hf-cache-arrow", lambda: _load_from_cached_arrow(spec.repo_id, split)))
    if dataset == "medqa_4options" and allow_download:
        strategies.append(("direct-json", lambda: _load_medqa_json(split)))
    if dataset == "medmcqa" and allow_download:
        strategies.append(("direct-parquet", lambda: _load_medmcqa_parquet(split)))

    errors: list[str] = []
    for name, loader in strategies:
        try:
            payload = loader()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:180]}")
            continue
        detail = ""
        fetch: FetchResult | None = None
        if isinstance(payload, tuple):
            rows, fetch = payload
            detail = f"{fetch.url} sha256={fetch.sha256[:16]}"
        else:
            rows = [dict(row) for row in payload]
            detail = f"rows read via {name}"
        records = normalise_records(dataset, rows)
        return LoadedSplit(
            spec=spec,
            split=split,
            records=tuple(records),
            strategy=name,
            source_detail=detail,
            n_raw_rows=len(rows),
            notes="; ".join(errors),
        )
    raise RuntimeError(
        f"could not load {dataset}/{split}; attempted: " + " | ".join(errors)
    )
