"""Judge protocol, case/verdict types and shared helpers.

Every verdict carries its own provenance: which judge, which rubric template,
which prompt hash, which seed, which backend and model. That is what lets a
later reviewer audit a score without re-running anything.

Three judge families implement the protocol:

* :class:`medeval_judges.exact_match.ExactMatchJudge` -- deterministic, no model;
* :class:`medeval_judges.rule.RuleJudge` -- deterministic rules, no model;
* :class:`medeval_judges.llm.LLMRubricJudge` -- the single OpenAI-compatible
  client, optionally with reference guidance;
* :class:`medeval_judges.jury.JuryJudge` -- aggregates several judges.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

Winner = Literal["A", "B", "tie"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class PointwiseCase:
    """One response to one question, scored against one criterion."""

    case_id: str
    item_id: str
    question: str
    response: str
    gold_answer: str | None = None
    options: tuple[str, ...] = ()
    required_elements: tuple[str, ...] = ()
    reference_note: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PairwiseCase:
    """Two responses to the same question; the judge states which is worse.

    ``target`` is the side that contains the planted or reference-defined error.
    Judges never receive ``target``; analysis compares verdicts against it.
    """

    case_id: str
    item_id: str
    question: str
    response_a: str
    response_b: str
    gold_answer: str | None = None
    required_elements: tuple[str, ...] = ()
    reference_note: str | None = None
    target: str | None = None  # "A" or "B", used only by analysis
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def response(self, side: str) -> str:
        if side == "A":
            return self.response_a
        if side == "B":
            return self.response_b
        raise ValueError(f"side must be 'A' or 'B', got {side!r}")


@dataclass(frozen=True)
class PointwiseVerdict:
    """A score for one response on one criterion."""

    case_id: str
    judge_id: str
    criterion_id: str
    score: float | None
    label: str | None
    rationale: str
    backend: str
    model: str
    template_version: str
    seed: int
    timestamp_utc: str
    prompt_sha256: str | None = None
    error: str | None = None
    pipeline_test_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.score is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "judge_id": self.judge_id,
            "criterion_id": self.criterion_id,
            "score": self.score,
            "label": self.label,
            "rationale": self.rationale,
            "backend": self.backend,
            "model": self.model,
            "template_version": self.template_version,
            "seed": self.seed,
            "timestamp_utc": self.timestamp_utc,
            "prompt_sha256": self.prompt_sha256,
            "error": self.error,
            "pipeline_test_only": self.pipeline_test_only,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class PairwiseVerdict:
    """Which of two responses is worse, with the presentation order recorded."""

    case_id: str
    judge_id: str
    winner: Winner
    rationale: str
    backend: str
    model: str
    template_version: str
    seed: int
    timestamp_utc: str
    presented_first: str = "A"
    prompt_sha256: str | None = None
    error: str | None = None
    pipeline_test_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.winner in {"A", "B", "tie"}

    @property
    def winner_response_id(self) -> str:
        """Winner expressed as a stable response id, independent of slot order."""
        if self.winner == "tie":
            return "tie"
        return self.winner

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "judge_id": self.judge_id,
            "winner": self.winner,
            "winner_response_id": self.winner_response_id,
            "rationale": self.rationale,
            "backend": self.backend,
            "model": self.model,
            "template_version": self.template_version,
            "seed": self.seed,
            "timestamp_utc": self.timestamp_utc,
            "presented_first": self.presented_first,
            "prompt_sha256": self.prompt_sha256,
            "error": self.error,
            "pipeline_test_only": self.pipeline_test_only,
            "metadata": self.metadata,
        }


class PointwiseJudge(Protocol):
    judge_id: str
    backend: str
    pipeline_test_only: bool

    def score(self, case: PointwiseCase, criterion_id: str) -> PointwiseVerdict: ...


class PairwiseJudge(Protocol):
    judge_id: str
    backend: str
    pipeline_test_only: bool

    def compare(
        self,
        case: PairwiseCase,
        *,
        seed: int | None = None,
        swapped: bool | None = None,
    ) -> PairwiseVerdict: ...


def new_verdict_id(prefix: str = "v") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def spread_verdicts(verdicts: list[Any]) -> dict[str, Any]:
    """Summarise verdict success and backend provenance for a result file."""
    errors = [v.as_dict() for v in verdicts if getattr(v, "error", None)]
    backends = sorted({getattr(v, "backend", "") for v in verdicts})
    models = sorted({getattr(v, "model", "") for v in verdicts})
    return {
        "n_verdicts": len(verdicts),
        "n_errors": len(errors),
        "errors": errors[:20],
        "backends": backends,
        "models": models,
        "pipeline_test_only": all(getattr(v, "pipeline_test_only", False) for v in verdicts),
    }
