"""Exact-match judge for multiple-choice answers.

Deterministic and cheap. It is the reference point every probabilistic judge
should be compared against on tasks that have a single correct option.
"""

from __future__ import annotations

import re

from medeval_core.calllog import utc_now_iso

from .base import PairwiseCase, PairwiseVerdict, PointwiseCase, PointwiseVerdict
from .prompts_versions import EXACT_TEMPLATE_VERSION

__all__ = ["ExactMatchJudge", "extract_choice"]

_LETTER_PATTERNS = (
    re.compile(r"final\s+answer\s*(?:is|:)?\s*\(?([A-H])\)?", re.IGNORECASE),
    re.compile(r"answer\s*(?:is|:)\s*\(?([A-H])\)?\b", re.IGNORECASE),
    re.compile(r"^\s*\(?([A-H])\)?\s*[.):]?\s*$", re.IGNORECASE),
    re.compile(r"\boption\s*\(?([A-H])\)?\b", re.IGNORECASE),
)


def extract_choice(response: str, options: tuple[str, ...] = ()) -> str | None:
    """Return the answer letter stated in ``response``, or None.

    Letter patterns are tried in order of specificity. If no letter is found, the
    option texts are matched verbatim (longest first) so an answer written as text
    still counts.
    """
    if not response:
        return None
    for pattern in _LETTER_PATTERNS:
        match = pattern.search(response)
        if match:
            return match.group(1).upper()
    if options:
        normalised = response.lower()
        for index, option in sorted(enumerate(options), key=lambda pair: -len(pair[1])):
            if len(option) >= 4 and option.lower() in normalised:
                return chr(ord("A") + index)
    stripped = response.strip()
    if len(stripped) == 1 and stripped.isalpha():
        return stripped.upper()
    return None


class ExactMatchJudge:
    """Scores a response 2 for an exact choice match, 0 otherwise."""

    def __init__(self, judge_id: str = "exact_match") -> None:
        self.judge_id = judge_id
        self.backend = "deterministic"
        self.model = "exact-match"
        self.pipeline_test_only = False

    def score(self, case: PointwiseCase, criterion_id: str = "diagnostic_accuracy") -> PointwiseVerdict:
        gold = case.gold_answer or ""
        extracted = extract_choice(case.response, case.options)
        gold_letter = extract_choice(gold, case.options) if gold else None

        if extracted is None:
            score: float | None = None
            label = "unparseable"
            rationale = "no answer letter or option text could be extracted"
        elif gold_letter is None:
            score = None
            label = "no_reference"
            rationale = "the item has no parseable reference answer"
        else:
            score = 2.0 if extracted == gold_letter else 0.0
            label = "correct" if score == 2.0 else "incorrect"
            rationale = f"extracted {extracted}, reference {gold_letter}"

        return PointwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            criterion_id=criterion_id,
            score=score,
            label=label,
            rationale=rationale,
            backend=self.backend,
            model=self.model,
            template_version=EXACT_TEMPLATE_VERSION,
            seed=0,
            timestamp_utc=utc_now_iso(),
            error=None if score is not None else "could not score",
            metadata={"extracted": extracted, "gold_letter": gold_letter},
        )

    def compare(self, case: PairwiseCase, *, seed: int | None = None, swapped: bool | None = None) -> PairwiseVerdict:
        """Not meaningful for open-ended response comparisons."""
        return PairwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            winner="tie",
            rationale="exact match cannot compare free-text responses",
            backend=self.backend,
            model=self.model,
            template_version=EXACT_TEMPLATE_VERSION,
            seed=seed or 0,
            timestamp_utc=utc_now_iso(),
            error="unsupported_pairwise",
        )
