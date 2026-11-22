"""The judge prompt, its version, and the only sanctioned way to render it.

``render_prompt`` accepts a :class:`~medeval_study.models.JudgeView` and the
candidate's text. It cannot accept a key, because ``JudgeView`` has no key field:
the isolation is a property of the signature, not of a call-site convention.

That also gives the test suite a strong check that does not depend on string
matching: rendering the same item with a different official key produces a
byte-identical prompt, because the key is not an input.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from medeval_study.models import JudgeView

__all__ = [
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "prompt_fingerprint",
    "render_options_block",
    "render_prompt",
]

#: Bump on any wording change; the version is written into every manifest.
PROMPT_VERSION = "mcq_judge_v1"

SYSTEM_PROMPT = (
    "You are an expert medical educator grading one candidate answer to a single-answer "
    "multiple-choice medical question. Decide only whether the option the candidate selected "
    "is the correct answer to the question. Do not write a new answer, do not explain, and do "
    "not add commentary. Reply with exactly one JSON object and nothing else."
)

USER_TEMPLATE = """\
Question:
{question}

Options:
{options}

Candidate answer:
{response}

Return exactly one JSON object in this shape and nothing else:
{{"verdict": "CORRECT" or "INCORRECT", "confidence": <integer 0-100>}}
The confidence is your probability, in percent, that your own verdict is right."""

#: Human-readable statement of the required reply, used in reports and tests.
RESPONSE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["verdict", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["CORRECT", "INCORRECT"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "additionalProperties": True,
}


def render_options_block(options: Mapping[str, str]) -> str:
    """Render ``A) ...`` lines in letter order."""
    return "\n".join(f"{letter}) {text}" for letter, text in options.items())


def render_prompt(view: JudgeView, response_text: str) -> str:
    """Render the judge prompt for one item and one candidate answer."""
    return USER_TEMPLATE.format(
        question=view.question.strip(),
        options=render_options_block(view.options),
        response=response_text.strip(),
    )


def prompt_fingerprint() -> dict[str, str]:
    """Stable identifiers for the prompt configuration recorded in manifests."""
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(SYSTEM_PROMPT.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(USER_TEMPLATE.encode("utf-8"))
    return {
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "user_template_sha256": hashlib.sha256(USER_TEMPLATE.encode("utf-8")).hexdigest(),
        "prompt_config_sha256": digest.hexdigest(),
    }
