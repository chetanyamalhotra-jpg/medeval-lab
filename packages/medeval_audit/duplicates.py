"""Exact and near-duplicate detection for benchmark items.

Duplicate items inflate apparent performance and shrink the effective sample
size, so they are reported rather than removed. Near-duplicates are found with
word-shingle Jaccard similarity, using an inverted index for candidate
generation so the audit stays linear-ish on a few thousand items.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .loaders import AuditRecord

__all__ = [
    "DuplicateGroup",
    "DuplicateReport",
    "find_duplicates",
    "jaccard",
    "normalise_text",
    "shingles",
]

_PUNCT = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")
_STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "for",
    "with",
    "and",
    "or",
    "to",
    "is",
    "are",
    "which",
    "what",
    "following",
    "patient",
}


def normalise_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    cleaned = _PUNCT.sub(" ", text.lower())
    return _SPACE.sub(" ", cleaned).strip()


def shingles(text: str, size: int = 3) -> frozenset[str]:
    """Word n-gram shingles, with stopwords dropped to reduce boilerplate overlap."""
    tokens = [token for token in normalise_text(text).split() if token not in _STOPWORDS]
    if len(tokens) < size:
        return frozenset([" ".join(tokens)]) if tokens else frozenset()
    return frozenset(" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two shingle sets; 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    return intersection / len(a | b)


@dataclass(frozen=True)
class DuplicateGroup:
    """A set of items considered duplicates of each other."""

    kind: str  # "exact" | "near" | "option_set"
    item_ids: tuple[str, ...]
    row_indices: tuple[int, ...]
    similarity: float
    example: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item_ids": list(self.item_ids),
            "row_indices": list(self.row_indices),
            "similarity": round(self.similarity, 4),
            "example": self.example,
        }


@dataclass(frozen=True)
class DuplicateReport:
    """Counts plus the flagged groups themselves."""

    n_items: int
    n_exact_groups: int
    n_near_groups: int
    n_option_set_groups: int
    groups: tuple[DuplicateGroup, ...]
    threshold: float
    shingle_size: int

    @property
    def flagged_item_ids(self) -> list[str]:
        ids: list[str] = []
        for group in self.groups:
            if group.kind in {"exact", "near"}:
                ids.extend(group.item_ids)
        return sorted(set(ids))

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_items": self.n_items,
            "n_exact_groups": self.n_exact_groups,
            "n_near_groups": self.n_near_groups,
            "n_option_set_groups": self.n_option_set_groups,
            "threshold": self.threshold,
            "shingle_size": self.shingle_size,
            "n_flagged_items": len(self.flagged_item_ids),
            "flagged_item_ids": self.flagged_item_ids,
            "groups": [group.as_dict() for group in self.groups],
        }


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _group_by(
    records: Sequence[AuditRecord], key_fn: Any, kind: str, similarity: float
) -> list[DuplicateGroup]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        buckets[key_fn(record)].append(index)
    groups: list[DuplicateGroup] = []
    for key, indices in buckets.items():
        if len(indices) < 2 or not key:
            continue
        groups.append(
            DuplicateGroup(
                kind=kind,
                item_ids=tuple(records[i].item_id for i in indices),
                row_indices=tuple(indices),
                similarity=similarity,
                example=records[indices[0]].question[:160],
            )
        )
    return groups


def find_duplicates(
    records: Sequence[AuditRecord],
    *,
    threshold: float = 0.85,
    shingle_size: int = 3,
    max_df: int = 80,
) -> DuplicateReport:
    """Find exact, near and option-set duplicates.

    ``threshold`` is the Jaccard cutoff for near duplicates. ``max_df`` limits
    candidate generation to shingles that are not ubiquitous boilerplate.
    """
    exact_groups = _group_by(
        records, lambda r: normalise_text(r.question), "exact", 1.0
    )
    option_groups = _group_by(
        records,
        lambda r: " | ".join(sorted(normalise_text(option) for option in r.options))
        if r.options
        else "",
        "option_set",
        1.0,
    )

    shingle_sets = [shingles(record.question, shingle_size) for record in records]
    index: dict[str, list[int]] = defaultdict(list)
    for i, shingle_set in enumerate(shingle_sets):
        for shingle in shingle_set:
            index[shingle].append(i)
    candidates: set[tuple[int, int]] = set()
    for _shingle, items in index.items():
        if len(items) < 2 or len(items) > max_df:
            continue
        for a_pos in range(len(items)):
            for b_pos in range(a_pos + 1, len(items)):
                candidates.add((items[a_pos], items[b_pos]))

    union = _UnionFind(len(records))
    pair_similarity: dict[tuple[int, int], float] = {}
    for i, j in candidates:
        score = jaccard(shingle_sets[i], shingle_sets[j])
        if score >= threshold:
            union.union(i, j)
            pair_similarity[(i, j)] = score

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(records)):
        root = union.find(i)
        clusters[root].append(i)
    near_groups: list[DuplicateGroup] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        best = max(
            (pair_similarity.get((min(a, b), max(a, b)), threshold) for a in members for b in members if a < b),
            default=threshold,
        )
        near_groups.append(
            DuplicateGroup(
                kind="near",
                item_ids=tuple(records[i].item_id for i in members),
                row_indices=tuple(members),
                similarity=best,
                example=records[members[0]].question[:160],
            )
        )

    exact_ids = {frozenset(g.row_indices) for g in exact_groups}
    near_groups = [g for g in near_groups if frozenset(g.row_indices) not in exact_ids]

    return DuplicateReport(
        n_items=len(records),
        n_exact_groups=len(exact_groups),
        n_near_groups=len(near_groups),
        n_option_set_groups=len(option_groups),
        groups=tuple(exact_groups + option_groups + near_groups),
        threshold=threshold,
        shingle_size=shingle_size,
    )


def question_option_overlap(records: Iterable[AuditRecord]) -> float:
    """Share of items whose question text nearly repeats one of its own options."""
    items = list(records)
    if not items:
        return float("nan")
    hits = 0
    for record in items:
        question_shingles = shingles(record.question, 2)
        if not question_shingles:
            continue
        for option in record.options:
            if jaccard(question_shingles, shingles(option, 2)) >= 0.6:
                hits += 1
                break
    return hits / len(items)
