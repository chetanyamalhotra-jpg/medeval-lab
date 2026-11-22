"""Dataset access for the grading study.

Only public, licence-checked benchmark splits are read, through the same
three-strategy loader the audit uses (``datasets`` library, cached arrow files,
direct download). Nothing here writes to the source dataset and nothing here
redistributes it: the sampled item text stays in a git-ignored run directory.
The licence string and its evidence URL are copied into every run manifest.

The loader is pluggable: :func:`register_dataset` adds another MCQ source
without touching the study runner.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medeval_audit.loaders import DATASETS, AuditRecord, LoadedSplit, load_split
from medeval_study.models import McqItem

__all__ = [
    "DATASET_LICENCES",
    "KNOWN_DATASETS",
    "DatasetBundle",
    "DatasetLicence",
    "load_mcq_items",
    "register_dataset",
]

#: Licences recorded from the Hugging Face dataset API, with the query used.
#: ``redistribute=False`` means the item text must not be committed to the repo.
DATASET_LICENCES: dict[str, DatasetLicence] = {}


@dataclass(frozen=True)
class DatasetLicence:
    """What the licence allows and where that was read from."""

    key: str
    hf_repo: str
    licence: str
    licence_evidence: str
    redistribute: bool
    attribution: str
    homepage: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "hf_repo": self.hf_repo,
            "licence": self.licence,
            "licence_evidence": self.licence_evidence,
            "redistribute": self.redistribute,
            "attribution": self.attribution,
            "homepage": self.homepage,
        }


def _build_licences() -> dict[str, DatasetLicence]:
    out: dict[str, DatasetLicence] = {}
    for key, spec in DATASETS.items():
        out[key] = DatasetLicence(
            key=key,
            hf_repo=spec.repo_id,
            licence=spec.licence,
            licence_evidence=spec.licence_evidence,
            redistribute=False,
            attribution=(
                f"{spec.repo_id} ({spec.licence}). Item text is kept out of version control "
                "and is not redistributed by this repository."
            ),
            homepage=spec.homepage,
        )
    return out


DATASET_LICENCES.update(_build_licences())

#: Extra MCQ datasets can be added without editing the runner.
_EXTRA_LOADERS: dict[str, Callable[[str, bool], LoadedSplit]] = {}


class DatasetUnavailable(RuntimeError):
    """The dataset could not be read (no ``datasets`` package, no network, no cache).

    It is a normal state on a fresh machine, not a crash: a dry run falls back to
    the built-in synthetic sample, and a real run stops with the fetch command.
    """


#: Read-only load: no network is attempted at all. A run never downloads by
#: itself, because a missing cache must not turn into minutes of retries.
_OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
#: The fetch command may download, but with a short timeout and at most one retry.
_FETCH_ENV = {
    "HF_HUB_ETAG_TIMEOUT": "5",
    "HF_HUB_DOWNLOAD_TIMEOUT": "20",
    "HF_HUB_MAX_RETRIES": "1",
}


@contextlib.contextmanager
def offline_dataset_access() -> Iterator[None]:
    """Guarantee that a load cannot touch the network (no retries, no stalls)."""
    previous = {key: os.environ.get(key) for key in _OFFLINE_ENV}
    os.environ.update(_OFFLINE_ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def download_dataset_access() -> Iterator[None]:
    """A download may happen, but briefly: short timeouts, one retry."""
    previous = {key: os.environ.get(key) for key in _FETCH_ENV}
    os.environ.update(_FETCH_ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _cache_root() -> Path:
    """Where the loaders keep downloaded dataset files (honours the test override)."""
    override = os.environ.get("MEDEVAL_CACHE_DIR")
    if override:
        return Path(override)
    from medeval_core.paths import data_dir

    return data_dir() / "cache"


def cached_dataset_files(dataset: str, split: str = "dev") -> list[Path]:
    """Files the loaders would read from the local cache for this dataset/split.

    This is a pure filesystem check: no loader, no library and no network call, so
    asking "is it cached?" can never stall. It is the same evidence
    ``medeval data status`` reports.
    """
    cache = _cache_root()
    if not cache.is_dir():
        return []
    prefix = f"{dataset}_{split}"
    return sorted(path for path in cache.iterdir() if path.is_file() and path.name.startswith(prefix))


def dataset_cache_status(dataset: str, split: str = "dev") -> DatasetBundle | None:
    """Return the cached bundle, or None when the data is not available locally.

    The cheap file check runs first, so an absent dataset costs one directory
    listing rather than a library load that may retry the network. When files are
    present they are read with the Hugging Face offline flag forced on.
    """
    if (dataset in _EXTRA_LOADERS or dataset in DATASETS) and not cached_dataset_files(dataset, split):
        return None
    try:
        with offline_dataset_access():
            return load_mcq_items(dataset, split, allow_download=False)
    except (DatasetUnavailable, KeyError):
        return None


def register_dataset(
    licence: DatasetLicence,
    loader: Callable[[str, bool], LoadedSplit],
) -> None:
    """Register a pluggable MCQ dataset.

    ``loader(split, allow_download)`` must return a :class:`LoadedSplit` whose
    records carry ``question``, exactly four ``options`` and a ``gold_index``.
    """
    DATASET_LICENCES[licence.key] = licence
    _EXTRA_LOADERS[licence.key] = loader


@dataclass(frozen=True)
class DatasetBundle:
    """Loaded items plus the provenance needed to describe them honestly."""
    items: tuple[McqItem, ...]
    licence: DatasetLicence
    dataset: str
    split: str
    strategy: str
    source_detail: str
    n_raw_rows: int
    n_dropped: int
    drop_reasons: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    @property
    def n(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "split": self.split,
            "strategy": self.strategy,
            "source_detail": self.source_detail,
            "n_raw_rows": self.n_raw_rows,
            "n_usable_items": self.n,
            "n_dropped": self.n_dropped,
            "drop_reasons": dict(self.drop_reasons),
            "licence": self.licence.as_dict(),
            "notes": self.notes,
        }


def _to_item(record: AuditRecord, dataset: str, split: str) -> tuple[McqItem | None, str]:
    """Convert one audit record, or explain why it is unusable."""
    labels = record.option_labels or ("A", "B", "C", "D")
    if len(record.options) != 4:
        return None, "not_four_options"
    texts = [str(option).strip() for option in record.options]
    if any(not text for text in texts):
        return None, "empty_option"
    if len(set(texts)) != 4:
        return None, "duplicate_option_text"
    gold = record.gold_index
    if gold is None or not 0 <= gold < 4:
        return None, "missing_key"
    question = str(record.question).strip()
    if len(question) < 40:
        return None, "question_too_short"
    options = {str(labels[i]): texts[i] for i in range(4)}
    return (
        McqItem(
            item_id=str(record.item_id),
            question=question,
            options=options,
            key_letter=str(labels[gold]),
            key_text=texts[gold],
            dataset=dataset,
            split=split,
            row_index=record.row_index,
            meta={"licence": DATASET_LICENCES[dataset].licence, **{k: v for k, v in record.extra.items() if v is not None}},
        ),
        "",
    )


def load_mcq_items(
    dataset: str = "medqa_4options",
    split: str = "dev",
    *,
    allow_download: bool = True,
) -> DatasetBundle:
    """Load and validate every usable item in one public split.

    Raises :class:`DatasetUnavailable` when the data cannot be read because the
    loader, the ``datasets`` package or the network is missing — which is normal
    on a fresh machine and must not look like a crash. A dry run falls back to a
    built-in synthetic sample (see ``medeval_study.synthetic``); a real run stops
    with the command that fetches the data.
    """
    try:
        if dataset in _EXTRA_LOADERS:
            loaded = _EXTRA_LOADERS[dataset](split, allow_download)
        else:
            if dataset not in DATASETS:
                raise KeyError(f"unknown dataset {dataset!r}; known: {sorted(DATASET_LICENCES)}")
            loaded = load_split(dataset, split, allow_download=allow_download)
    except KeyError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise DatasetUnavailable(
            f"{dataset}/{split} could not be loaded: {type(exc).__name__}: {exc}"
        ) from exc

    licence = DATASET_LICENCES[dataset]
    items: list[McqItem] = []
    reasons: dict[str, int] = {}
    for record in loaded.records:
        item, reason = _to_item(record, dataset, split)
        if item is None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        items.append(item)
    return DatasetBundle(
        items=tuple(items),
        licence=licence,
        dataset=dataset,
        split=split,
        strategy=loaded.strategy,
        source_detail=loaded.source_detail,
        n_raw_rows=loaded.n_raw_rows,
        n_dropped=sum(reasons.values()),
        drop_reasons=reasons,
        notes=loaded.notes,
    )


KNOWN_DATASETS: Sequence[str] = tuple(DATASET_LICENCES)
