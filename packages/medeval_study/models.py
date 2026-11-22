"""Data model shared by the grading study.

The important separation in this module is between :class:`McqItem`, which holds
the answer key, and :class:`JudgeView`, which deliberately has no key field at
all. A judge can only ever be handed a ``JudgeView``, so "the key never enters a
judge prompt" is enforced by the type system rather than by remembering to delete
a field at the call site.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["CandidateResponse", "JudgeView", "McqItem", "normalise_stem"]


def normalise_stem(text: str) -> str:
    """Lowercase alphanumeric skeleton used to detect repeated stems.

    ``"A 54-year-old man, with chest pain."`` and ``"a 54 year old man with
    chest pain"`` normalise to the same string.
    """
    keep = [ch for ch in text.lower() if ch.isalnum() or ch.isspace()]
    return " ".join("".join(keep).split())


@dataclass(frozen=True)
class McqItem:
    """One four-option medical question with its official answer key.

    ``key_letter``/``key_text`` come from the benchmark's published key. They are
    treated as an *imperfect reference*, not as truth: no key audit is performed
    by this toolkit, and the report says so.
    """

    item_id: str
    question: str
    options: Mapping[str, str]
    key_letter: str
    key_text: str
    dataset: str
    split: str
    row_index: int
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def letters(self) -> tuple[str, ...]:
        return tuple(self.options)

    @property
    def key_index(self) -> int:
        return self.letters.index(self.key_letter)

    @property
    def stem_hash(self) -> str:
        return hashlib.sha256(normalise_stem(self.question).encode("utf-8")).hexdigest()[:16]

    def judge_view(self) -> JudgeView:
        """The only sanctioned way to hand this item to a model."""
        return JudgeView(
            item_id=self.item_id,
            question=self.question,
            options=dict(self.options),
        )

    def as_dict(self) -> dict[str, Any]:
        """Full record, including the key. Never sent to a judge, never published."""
        return {
            "item_id": self.item_id,
            "question": self.question,
            "options": dict(self.options),
            "key_letter": self.key_letter,
            "key_text": self.key_text,
            "dataset": self.dataset,
            "split": self.split,
            "row_index": self.row_index,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class JudgeView:
    """Exactly what a judge is allowed to see: question and options, no key."""

    item_id: str
    question: str
    options: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "question": self.question, "options": dict(self.options)}


@dataclass(frozen=True)
class CandidateResponse:
    """A candidate answer with a short rationale, built from a fixed template.

    ``condition`` records the intended construction (``correct`` or
    ``incorrect``); ``text`` is what the judge reads. Correctness against the key
    is *not* stored here -- it is derived in one place so that the response
    object can be rendered for a judge without carrying key information.
    """

    item_id: str
    chosen_letter: str
    chosen_text: str
    text: str
    condition: str
    template_id: str
    distractor_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "chosen_letter": self.chosen_letter,
            "chosen_text": self.chosen_text,
            "text": self.text,
            "condition": self.condition,
            "template_id": self.template_id,
            "distractor_reason": self.distractor_reason,
        }
