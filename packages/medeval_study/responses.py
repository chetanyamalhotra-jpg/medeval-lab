"""Candidate answer construction, without any model in the loop.

The specification forbids generating these rationales with DeepSeek: J1 is a
DeepSeek model, and a judge is known to favour text from its own family. Every
candidate here is therefore assembled from a fixed template and the item's own
option text. Nothing clinical is invented.

Two properties are enforced by construction and checked by tests:

* **The template pool is shared.** Correct and incorrect candidates are rendered
  with the same six templates, selected by a hash of the item id. If correct
  answers always used one phrasing, a judge could score well on style alone.
* **The wrong option is plausible on purpose.** It is the distractor with the
  highest lexical overlap with the key option, which tends to pick the
  differential that shares vocabulary with the right answer (for example
  "unstable angina" against "acute myocardial infarction") rather than an
  unrelated one. Ties and zero-overlap cases are broken by a seeded hash and
  labelled as such.

The rationale text is therefore *template filler*, not medical reasoning, and the
report says so in the limitations. What a judge is really being asked is whether
the option the candidate named is the correct one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from medeval_study.models import CandidateResponse, McqItem

__all__ = ["TEMPLATES", "build_candidate_response", "choose_distractor"]

#: Fixed templates. ``{letter}`` and ``{text}`` are the only substitutions.
TEMPLATES: tuple[tuple[str, str], ...] = (
    ("t1", "The best answer is {letter}: {text}. This is the option that accounts for the details in the question stem."),
    ("t2", "I would choose {letter}, {text}. It fits the presentation better than the alternatives listed."),
    ("t3", "Answer: {letter} ({text}). Of the four options this one is the most coherent with the described findings."),
    ("t4", "{letter}. {text}. This option explains the presented findings most completely."),
    ("t5", "The correct option is {letter}, {text}, because it matches the pattern described in the stem."),
    ("t6", "Choosing {letter} - {text}. The remaining options do not fit the case as well."),
)

_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at", "by", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "which", "who", "whom", "whose", "what", "when", "where", "why", "how", "not", "no", "nor", "but", "if", "then", "than", "so", "as", "from", "into", "over", "under", "patient", "patients", "man", "woman", "year", "old", "years", "presents", "presented", "showed", "shows", "most", "likely", "best", "following"]
)


def _content_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if len(word) > 3 and word not in _STOPWORDS}


def _overlap(left: str, right: str) -> float:
    a, b = _content_tokens(left), _content_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class DistractorChoice:
    letter: str
    text: str
    reason: str
    overlap: float


def choose_distractor(item: McqItem, *, seed: int) -> DistractorChoice:
    """Pick the wrong option that looks most like the key, deterministically."""
    candidates = [
        (letter, text)
        for letter, text in item.options.items()
        if letter != item.key_letter
    ]
    scored = sorted(
        ((_overlap(text, item.key_text), letter, text) for letter, text in candidates),
        key=lambda row: (-row[0], row[1]),
    )
    best_overlap, best_letter, best_text = scored[0]
    if best_overlap > 0:
        tied = [row for row in scored if abs(row[0] - best_overlap) < 1e-9]
        if len(tied) > 1:
            pick = _seeded_choice([(row[1], row[2]) for row in tied], seed=seed, purpose=item.item_id)
            return DistractorChoice(pick[0], pick[1], "lexical_overlap_tie_broken_by_seed", best_overlap)
        return DistractorChoice(best_letter, best_text, "lexical_overlap_with_key", best_overlap)
    pick = _seeded_choice([(row[1], row[2]) for row in scored], seed=seed, purpose=item.item_id)
    return DistractorChoice(pick[0], pick[1], "no_lexical_overlap_seeded_choice", 0.0)


def _seeded_choice(options: Sequence[tuple[str, str]], *, seed: int, purpose: str) -> tuple[str, str]:
    ordered = sorted(
        options,
        key=lambda row: hashlib.sha256(f"{seed}:{purpose}:{row[0]}".encode()).hexdigest(),
    )
    return ordered[0]


def build_candidate_response(item: McqItem, *, condition: str, seed: int) -> CandidateResponse:
    """Render one candidate answer for ``condition``.

    ``condition`` is ``"correct"`` (name the key option) or ``"incorrect"``
    (name the most plausible distractor).
    """
    if condition == "correct":
        letter, text = item.key_letter, item.key_text
        reason = "key_option"
    elif condition == "incorrect":
        choice = choose_distractor(item, seed=seed)
        letter, text = choice.letter, choice.text
        reason = choice.reason
    else:
        raise ValueError(f"unknown condition {condition!r}")

    template_id, template = TEMPLATES[
        int(hashlib.sha256(f"{seed}:{item.item_id}:template".encode()).hexdigest()[:8], 16) % len(TEMPLATES)
    ]
    rendered = template.format(letter=letter, text=text)
    return CandidateResponse(
        item_id=item.item_id,
        chosen_letter=letter,
        chosen_text=text,
        text=rendered,
        condition=condition,
        template_id=template_id,
        distractor_reason=reason,
    )


def template_inventory() -> dict[str, Any]:
    """Helper for the report: how many templates exist and what they look like."""
    return {
        "n_templates": len(TEMPLATES),
        "template_ids": [tid for tid, _ in TEMPLATES],
        "rationale_is_template_filler": True,
    }
