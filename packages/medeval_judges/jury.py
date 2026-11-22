"""Multi-judge jury aggregation.

A jury is only meaningful when its members are genuinely different instruments:
different model families, or a model judge combined with a deterministic judge.
Combining two runs of the same model with different seeds measures noise, not
diversity, and the jury result records its members so this cannot be glossed over.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from medeval_core.calllog import utc_now_iso

from .base import PairwiseCase, PairwiseVerdict, PointwiseCase, PointwiseVerdict
from .prompts_versions import JURY_TEMPLATE_VERSION

__all__ = ["JuryJudge"]


@dataclass(frozen=True)
class JuryJudge:
    """Majority vote over pairwise verdicts; mean over pointwise scores."""

    members: Sequence[Any]
    judge_id: str = "jury"
    rule: str = "majority"

    def __post_init__(self) -> None:
        if len(self.members) < 2:
            raise ValueError("a jury needs at least two members")
        object.__setattr__(self, "backend", "ensemble")
        object.__setattr__(self, "model", "+".join(str(getattr(m, "judge_id", "?")) for m in self.members))
        object.__setattr__(
            self,
            "pipeline_test_only",
            all(bool(getattr(m, "pipeline_test_only", False)) for m in self.members),
        )

    @property
    def member_ids(self) -> list[str]:
        return [str(getattr(member, "judge_id", "unknown")) for member in self.members]

    def compare(
        self,
        case: PairwiseCase,
        *,
        seed: int | None = None,
        swapped: bool | None = None,
    ) -> PairwiseVerdict:
        verdicts = [member.compare(case, seed=seed, swapped=swapped) for member in self.members]
        votes = [verdict.winner for verdict in verdicts if verdict.error is None]
        errors = [verdict.error for verdict in verdicts if verdict.error is not None]
        counts = Counter(votes)
        winner = "tie"
        if self.rule == "majority" and counts:
            top, count = counts.most_common(1)[0]
            if count > len(votes) / 2:
                winner = top
        elif self.rule == "unanimous" and counts and len(counts) == 1:
            winner = votes[0]
        else:
            raise ValueError(f"unknown jury rule {self.rule!r}")
        return PairwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            winner=winner,  # type: ignore[arg-type]
            rationale="votes: " + ", ".join(f"{member}={verdict.winner}" for member, verdict in zip(self.members, verdicts, strict=True)),
            backend="ensemble",
            model=self.model,
            template_version=JURY_TEMPLATE_VERSION,
            seed=seed or 0,
            timestamp_utc=utc_now_iso(),
            presented_first="B" if swapped else "A",
            error=";" .join(errors) if errors and not votes else None,
            pipeline_test_only=self.pipeline_test_only,
            metadata={
                "members": self.member_ids,
                "rule": self.rule,
                "votes": [verdict.as_dict() for verdict in verdicts],
            },
        )

    def score(self, case: PointwiseCase, criterion_id: str) -> PointwiseVerdict:
        verdicts = [member.score(case, criterion_id) for member in self.members]
        scores = [verdict.score for verdict in verdicts if verdict.score is not None]
        errors = [verdict.error for verdict in verdicts if verdict.error is not None]
        mean = sum(scores) / len(scores) if scores else None
        labels = Counter(verdict.label for verdict in verdicts if verdict.label)
        return PointwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            criterion_id=criterion_id,
            score=mean,
            label=labels.most_common(1)[0][0] if labels else None,
            rationale="scores: " + ", ".join(f"{m.judge_id}={v.score}" for m, v in zip(self.members, verdicts, strict=True)),
            backend="ensemble",
            model=self.model,
            template_version=JURY_TEMPLATE_VERSION,
            seed=0,
            timestamp_utc=utc_now_iso(),
            error=";".join(errors) if errors and not scores else None,
            pipeline_test_only=self.pipeline_test_only,
            metadata={"members": self.member_ids, "votes": [v.as_dict() for v in verdicts]},
        )
