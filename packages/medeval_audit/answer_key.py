"""Answer-key sanity checks and option-index-base detection.

Checks are structural: missing keys, out-of-range indices, empty fields,
conflicting key columns, degenerate key distributions, and two shortcut
baselines ("always the first option", "always the longest option") that expose
answer-position and answer-length artefacts. Items are flagged, never changed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats

from .loaders import AuditRecord

__all__ = [
    "AnswerKeyReport",
    "check_answer_key",
    "detect_index_base",
    "shortcut_baselines",
]

#: Column names that could carry a gold key in these public datasets.
_KEY_COLUMNS = (
    "cop",
    "cop_idx",
    "label",
    "answer",
    "answer_idx",
    "correct",
    "correct_option",
    "gold",
    "target",
)


@dataclass(frozen=True)
class AnswerKeyReport:
    """Everything the audit found about the answer key of one split."""

    n_items: int
    n_missing_gold: int
    n_out_of_range: int
    n_empty_question: int
    n_empty_option: int
    n_duplicate_options: int
    index_base: str
    index_base_evidence: str
    gold_distribution: dict[str, int]
    uniformity_p_value: float | None
    always_first_accuracy: float
    longest_option_accuracy: float
    mean_gold_option_length: float
    mean_other_option_length: float
    conflicting_key_ids: list[str] = field(default_factory=list)
    conflicting_key_details: list[dict[str, Any]] = field(default_factory=list)
    flagged_item_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_items": self.n_items,
            "n_missing_gold": self.n_missing_gold,
            "n_out_of_range": self.n_out_of_range,
            "n_empty_question": self.n_empty_question,
            "n_empty_option": self.n_empty_option,
            "n_duplicate_options": self.n_duplicate_options,
            "index_base": self.index_base,
            "index_base_evidence": self.index_base_evidence,
            "gold_distribution": self.gold_distribution,
            "uniformity_p_value": self.uniformity_p_value,
            "always_first_accuracy": self.always_first_accuracy,
            "longest_option_accuracy": self.longest_option_accuracy,
            "mean_gold_option_length": self.mean_gold_option_length,
            "mean_other_option_length": self.mean_other_option_length,
            "conflicting_key_ids": self.conflicting_key_ids,
            "conflicting_key_details": self.conflicting_key_details,
            "flagged_item_ids": self.flagged_item_ids,
            "notes": self.notes,
        }


def detect_index_base(values: Sequence[int], n_options: int) -> tuple[str, str]:
    """Infer whether gold indices are 0-based or 1-based.

    Returns ``(base, evidence)``. The distinction matters because the harness
    maps ``doc_to_target`` through ``doc_to_choice`` positionally: feeding a
    1-based key to a list of choices silently shifts every answer by one.
    """
    clean = [int(v) for v in values if v is not None]
    if not clean:
        return "unknown", "no numeric gold indices"
    low, high = min(clean), max(clean)
    if low >= 0 and high <= n_options - 1:
        if low == 0:
            return (
                "zero_based",
                f"indices span [{low}, {high}] with 0 present, inside [0, {n_options - 1}]",
            )
        return (
            "zero_based_without_zero",
            f"indices span [{low}, {high}] inside [0, {n_options - 1}] but 0 never occurs; base is ambiguous",
        )
    if low >= 1 and high <= n_options:
        return (
            "one_based",
            f"indices span [{low}, {high}] inside [1, {n_options}]; a positional mapping must subtract 1",
        )
    return (
        "invalid",
        f"indices span [{low}, {high}], outside both [0, {n_options - 1}] and [1, {n_options}]",
    )


def shortcut_baselines(records: Sequence[AuditRecord]) -> tuple[float, float, float, float]:
    """Accuracy of two trivial strategies plus mean option lengths.

    Returns ``(always_first_accuracy, longest_option_accuracy,
    mean_gold_length, mean_other_length)``. High values mean the key can be
    predicted without any medical knowledge.
    """
    first_correct = 0
    longest_correct = 0
    scored = 0
    gold_lengths: list[int] = []
    other_lengths: list[int] = []
    for record in records:
        if record.gold_index is None or not record.options:
            continue
        if not (0 <= record.gold_index < len(record.options)):
            continue
        scored += 1
        if record.gold_index == 0:
            first_correct += 1
        lengths = [len(option) for option in record.options]
        longest_index = int(np.argmax(lengths))
        if longest_index == record.gold_index:
            longest_correct += 1
        gold_lengths.append(lengths[record.gold_index])
        other_lengths.extend(
            length for index, length in enumerate(lengths) if index != record.gold_index
        )
    if scored == 0:
        return (float("nan"), float("nan"), float("nan"), float("nan"))
    return (
        first_correct / scored,
        longest_correct / scored,
        float(np.mean(gold_lengths)) if gold_lengths else float("nan"),
        float(np.mean(other_lengths)) if other_lengths else float("nan"),
    )


def _conflicting_keys(record: AuditRecord) -> dict[str, Any] | None:
    """Detect two key columns that disagree inside one raw row."""
    raw = record.raw or {}
    present: dict[str, Any] = {}
    for column in _KEY_COLUMNS:
        if column in raw and raw[column] is not None:
            present[column] = raw[column]
    if len(present) < 2:
        return None
    numeric: dict[str, int] = {}
    for column, value in present.items():
        try:
            numeric[column] = int(value)
        except (TypeError, ValueError):
            continue
    if len(numeric) < 2:
        return None
    values = set(numeric.values())
    if len(values) > 1:
        return {"item_id": record.item_id, "row_index": record.row_index, "values": numeric}
    return None


def check_answer_key(
    records: Sequence[AuditRecord],
    *,
    n_options: int = 4,
    max_examples: int = 50,
) -> AnswerKeyReport:
    """Run every structural answer-key check on one split."""
    n = len(records)
    missing = 0
    out_of_range = 0
    empty_question = 0
    empty_option = 0
    duplicate_options = 0
    flagged: list[str] = []
    conflicts: list[dict[str, Any]] = []
    notes: list[str] = []

    gold_values: list[int] = []
    distribution: Counter[str] = Counter()
    for record in records:
        if not record.question.strip():
            empty_question += 1
            flagged.append(record.item_id)
        if any(not option.strip() for option in record.options):
            empty_option += 1
            flagged.append(record.item_id)
        normalised = [" ".join(option.lower().split()) for option in record.options]
        if len(set(normalised)) != len(normalised):
            duplicate_options += 1
            flagged.append(record.item_id)
        if record.gold_index is None:
            missing += 1
            flagged.append(record.item_id)
            continue
        if not (0 <= record.gold_index < n_options):
            out_of_range += 1
            flagged.append(record.item_id)
            continue
        gold_values.append(record.gold_index)
        distribution[str(record.gold_index)] += 1
        conflict = _conflicting_keys(record)
        if conflict is not None:
            conflicts.append(conflict)
            flagged.append(record.item_id)

    index_base, evidence = detect_index_base(gold_values, n_options)
    first_acc, longest_acc, gold_len, other_len = shortcut_baselines(records)

    uniformity_p: float | None = None
    if gold_values:
        counts = np.array([distribution.get(str(i), 0) for i in range(n_options)], dtype=float)
        if counts.sum() >= 20 and counts.min() > 0:
            uniformity_p = float(stats.chisquare(counts).pvalue)
        else:
            notes.append(
                "key distribution chi-square skipped: fewer than 20 keyed items or an empty category"
            )

    if index_base == "one_based":
        notes.append(
            "one_based indices detected: a task that uses doc_to_target=<index> with a positional "
            "doc_to_choice list would map every answer to the wrong option"
        )
    if index_base == "invalid":
        notes.append("gold indices outside both plausible bases; the key is internally inconsistent")
    if len(conflicts) > 0:
        notes.append("two key columns disagree for at least one item")
    if longest_acc > 0.4:
        notes.append(
            "the longest option is correct far more often than chance; option length leaks the answer"
        )

    return AnswerKeyReport(
        n_items=n,
        n_missing_gold=missing,
        n_out_of_range=out_of_range,
        n_empty_question=empty_question,
        n_empty_option=empty_option,
        n_duplicate_options=duplicate_options,
        index_base=index_base,
        index_base_evidence=evidence,
        gold_distribution=dict(sorted(distribution.items())),
        uniformity_p_value=uniformity_p,
        always_first_accuracy=first_acc,
        longest_option_accuracy=longest_acc,
        mean_gold_option_length=gold_len,
        mean_other_option_length=other_len,
        conflicting_key_ids=[c["item_id"] for c in conflicts],
        conflicting_key_details=conflicts[:max_examples],
        flagged_item_ids=sorted(set(flagged)),
        notes=notes,
    )
