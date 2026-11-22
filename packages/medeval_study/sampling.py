"""Deterministic sampling with no duplicate stems.

Two decisions are recorded here rather than buried in the runner:

* **De-duplication comes first.** The pool is grouped by normalised stem and one
  row per group survives, chosen by the lowest row index so the result does not
  depend on dictionary ordering. The number of rows discarded this way is
  reported.
* **Condition assignment is independent of the sample draw.** Which items get a
  correct candidate answer and which get a wrong one is decided by a second,
  differently seeded hash. If the same hash decided both, the condition could
  correlate with position in the pool, and a judge sensitive to question style
  could appear better or worse than it is.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from medeval_study.models import McqItem, normalise_stem

__all__ = ["SampledItem", "SamplingPlan", "select_items"]

CONDITION_CORRECT = "correct"
CONDITION_INCORRECT = "incorrect"


def _seeded_key(seed: int, purpose: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{purpose}:{value}".encode()).hexdigest()


@dataclass(frozen=True)
class SampledItem:
    """One item plus the condition its candidate answer will be built for."""

    item: McqItem
    condition: str

    @property
    def expects_correct(self) -> bool:
        return self.condition == CONDITION_CORRECT

    def as_dict(self) -> dict[str, Any]:
        return {"item_id": self.item.item_id, "condition": self.condition}


@dataclass(frozen=True)
class SamplingPlan:
    """The sampled set and enough bookkeeping to reproduce it."""

    seed: int
    n_requested: int
    items: tuple[SampledItem, ...]
    pool_size: int
    pool_unique_stems: int
    discarded_duplicate_stems: int
    condition_counts: dict[str, int] = field(default_factory=dict)
    note: str = ""

    @property
    def n(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "n_requested": self.n_requested,
            "n_selected": self.n,
            "pool_size": self.pool_size,
            "pool_unique_stems": self.pool_unique_stems,
            "discarded_duplicate_stems": self.discarded_duplicate_stems,
            "condition_counts": dict(self.condition_counts),
            "note": self.note,
        }


def _dedupe_by_stem(items: Sequence[McqItem]) -> tuple[list[McqItem], int]:
    best: dict[str, McqItem] = {}
    for item in sorted(items, key=lambda row: (row.row_index, row.item_id)):
        key = normalise_stem(item.question)
        if not key:
            continue
        best.setdefault(key, item)
    unique = sorted(best.values(), key=lambda row: (row.row_index, row.item_id))
    return unique, len(items) - len(unique)


def select_items(items: Sequence[McqItem], *, n: int, seed: int) -> SamplingPlan:
    """Pick ``n`` items with distinct stems and assign answer conditions."""
    if n <= 0:
        raise ValueError("n must be positive")
    pool, discarded = _dedupe_by_stem(items)
    if len(pool) < n:
        raise ValueError(
            f"asked for {n} items but only {len(pool)} distinct stems are available "
            f"after de-duplication ({len(items)} rows supplied)"
        )
    ordered = sorted(pool, key=lambda row: _seeded_key(seed, "draw", row.item_id))
    chosen = ordered[:n]

    # Balanced assignment, independent of the draw order.
    n_correct = n // 2 + n % 2
    by_condition = sorted(chosen, key=lambda row: _seeded_key(seed, "condition", row.item_id))
    correct_ids = {row.item_id for row in by_condition[:n_correct]}
    sampled = tuple(
        SampledItem(
            item=row,
            condition=CONDITION_CORRECT if row.item_id in correct_ids else CONDITION_INCORRECT,
        )
        for row in chosen
    )
    counts = {
        CONDITION_CORRECT: sum(1 for row in sampled if row.condition == CONDITION_CORRECT),
        CONDITION_INCORRECT: sum(1 for row in sampled if row.condition == CONDITION_INCORRECT),
    }
    stems = {normalise_stem(row.item.question) for row in sampled}
    return SamplingPlan(
        seed=seed,
        n_requested=n,
        items=sampled,
        pool_size=len(items),
        pool_unique_stems=len(pool),
        discarded_duplicate_stems=discarded,
        condition_counts=counts,
        note=(
            f"{len(stems)} distinct stems among {len(sampled)} sampled items; "
            f"{discarded} row(s) discarded as duplicate stems before drawing"
        ),
    )


def iter_conditions(items: Iterable[SampledItem]) -> dict[str, int]:
    """Small helper for summaries."""
    counts: dict[str, int] = {}
    for row in items:
        counts[row.condition] = counts.get(row.condition, 0) + 1
    return counts
