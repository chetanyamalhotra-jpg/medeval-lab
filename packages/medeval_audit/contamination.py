"""Cheap contamination screens.

These are screens, not proof. They detect three situations that make a benchmark
score uninterpretable:

1. the answer text is echoed inside the question stem;
2. two public benchmarks contain near-identical items (so "held-out" performance
   may be measuring the same fact twice);
3. a split repeats items from another split of the same benchmark.

No screen can establish that a model saw an item during training. Absence of
these signals is not evidence of contamination-free evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .duplicates import jaccard, normalise_text, shingles
from .loaders import AuditRecord

__all__ = ["ContaminationReport", "answer_in_stem", "cross_dataset_overlap", "run_screens"]


@dataclass(frozen=True)
class ContaminationReport:
    """Outcome of the available screens."""

    n_items: int
    answer_in_stem_rate: float
    answer_in_stem_ids: list[str]
    cross_dataset_pairs: list[dict[str, Any]]
    screens_run: list[str]
    screens_not_run: list[str]
    caveat: str = (
        "Screens only. They cannot show that a model was trained on an item, and a "
        "clean screen does not make a score contamination-free."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_items": self.n_items,
            "answer_in_stem_rate": self.answer_in_stem_rate,
            "answer_in_stem_ids": self.answer_in_stem_ids,
            "n_cross_dataset_pairs": len(self.cross_dataset_pairs),
            "cross_dataset_pairs": self.cross_dataset_pairs,
            "screens_run": self.screens_run,
            "screens_not_run": self.screens_not_run,
            "caveat": self.caveat,
        }


def answer_in_stem(
    records: Sequence[AuditRecord], *, min_answer_chars: int = 15, max_examples: int = 50
) -> tuple[float, list[str], list[dict[str, Any]]]:
    """Share of items whose question contains the gold answer verbatim.

    Only answers of at least ``min_answer_chars`` are tested, because short
    strings (``"yes"``) match by accident.
    """
    hits: list[str] = []
    examples: list[dict[str, Any]] = []
    for record in records:
        if record.gold_text is None or len(record.gold_text) < min_answer_chars:
            continue
        if normalise_text(record.gold_text) in normalise_text(record.question):
            hits.append(record.item_id)
            if len(examples) < max_examples:
                examples.append(
                    {
                        "item_id": record.item_id,
                        "gold_text": record.gold_text[:120],
                        "question": record.question[:160],
                    }
                )
    rate = len(hits) / len(records) if records else float("nan")
    return rate, hits, examples


def cross_dataset_overlap(
    left: Sequence[AuditRecord],
    right: Sequence[AuditRecord],
    *,
    threshold: float = 0.8,
    shingle_size: int = 5,
    max_pairs: int = 200,
) -> list[dict[str, Any]]:
    """Items that appear in two benchmarks with near-identical wording."""
    right_sets = [shingles(record.question, shingle_size) for record in right]
    index: dict[str, list[int]] = defaultdict(list)
    for position, shingle_set in enumerate(right_sets):
        for shingle in shingle_set:
            index[shingle].append(position)

    pairs: list[dict[str, Any]] = []
    for left_record in left:
        left_set = shingles(left_record.question, shingle_size)
        if not left_set:
            continue
        candidates: set[int] = set()
        for shingle in left_set:
            candidates.update(index.get(shingle, []))
        best_score = 0.0
        best_position = -1
        for position in candidates:
            score = jaccard(left_set, right_sets[position])
            if score > best_score:
                best_score, best_position = score, position
        if best_score >= threshold and best_position >= 0:
            pairs.append(
                {
                    "left_id": left_record.item_id,
                    "right_id": right[best_position].item_id,
                    "similarity": round(best_score, 4),
                    "left_question": left_record.question[:140],
                    "right_question": right[best_position].question[:140],
                }
            )
            if len(pairs) >= max_pairs:
                break
    return pairs


def run_screens(
    records: Sequence[AuditRecord],
    other: Sequence[AuditRecord] | None = None,
    *,
    other_name: str = "",
) -> ContaminationReport:
    """Run the screens that are possible with the data at hand."""
    rate, hits, _examples = answer_in_stem(records)
    pairs: list[dict[str, Any]] = []
    run = ["answer-in-stem"]
    not_run = [
        "training-corpus n-gram overlap (no reference corpus available offline)",
        "train-split memorisation probe (needs a model and a train split load)",
    ]
    if other is not None:
        pairs = cross_dataset_overlap(records, other)
        run.append(f"cross-dataset overlap vs {other_name or 'other dataset'}")
    return ContaminationReport(
        n_items=len(records),
        answer_in_stem_rate=rate,
        answer_in_stem_ids=hits,
        cross_dataset_pairs=pairs,
        screens_run=run,
        screens_not_run=not_run,
    )
