"""LLM rubric judge built on the single OpenAI-compatible client.

Two modes:

* ``reference_mode="guided"`` -- the prompt includes the author-provided
  reference answer and required elements;
* ``reference_mode="free"`` -- the prompt contains only the question and the
  responses.

Pairs are always presented in a randomised order derived from
``(judge_id, case_id, seed)``, so the order is reproducible but not correlated
with the true answer. The verdict records which side was shown first, allowing
position bias to be measured after the fact.

With the MOCK backend the same code path runs: a policy decides the output, the
text is written as JSON, and the normal parser reads it back. That is what makes
the mock useful for testing the pipeline -- and why its results are marked
PIPELINE TEST ONLY.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any

from medeval_core.calllog import utc_now_iso
from medeval_core.client import LLMClient, parse_json_object
from medeval_criteria import Criterion, load_catalogue

from .base import PairwiseCase, PairwiseVerdict, PointwiseCase, PointwiseVerdict
from .prompts import (
    PAIRWISE_TEMPLATE_VERSION,
    POINTWISE_TEMPLATE_VERSION,
    SYSTEM_PROMPT,
    render_pairwise,
    render_pointwise,
)

__all__ = ["LLMRubricJudge", "MockPolicy", "stable_order"]


def stable_order(judge_id: str, case_id: str, seed: int) -> bool:
    """Deterministic, answer-independent coin flip for presentation order."""
    digest = hashlib.sha256(f"{judge_id}|{case_id}|{seed}".encode()).digest()
    return digest[0] % 2 == 0


@dataclass(frozen=True)
class MockPolicy:
    """Parameters of the simulated judge used to test the pipeline.

    ``accuracy`` is the probability of naming the truly worse side, ``position_bias``
    the probability of instead naming whichever side was shown first, and
    ``tie_rate`` the probability of answering "tie" regardless.
    """

    accuracy: float = 0.7
    position_bias: float = 0.0
    tie_rate: float = 0.0
    label: str = "mock"

    def decide(self, case: PairwiseCase, first_side: str, judge_id: str, seed: int) -> str:
        rng = random.Random(int(hashlib.sha256(f"{judge_id}|{case.case_id}|{seed}".encode()).hexdigest()[:12], 16))
        if case.target is None:
            return "tie"
        if rng.random() < self.tie_rate:
            return "tie"
        non_target = "A" if case.target == "B" else "B"
        if rng.random() < self.position_bias:
            return first_side
        if rng.random() < self.accuracy:
            return case.target
        return non_target


class _TextQueue:
    """Callable responder that returns the text last placed in ``text``."""

    def __init__(self) -> None:
        self.text = "{}"

    def __call__(self, messages: Any, seed: int) -> str:
        return self.text


class LLMRubricJudge:
    """Single-model rubric judge over the shared client."""

    def __init__(
        self,
        client: LLMClient,
        *,
        judge_id: str = "llm_rubric",
        reference_mode: str = "free",
        catalogue_version: str = "v0.1",
        mock_policy: MockPolicy | None = None,
        queue: _TextQueue | None = None,
    ) -> None:
        if reference_mode not in {"guided", "free"}:
            raise ValueError("reference_mode must be 'guided' or 'free'")
        self.client = client
        self.judge_id = judge_id
        self.reference_mode = reference_mode
        self.backend = client.transport.backend
        self.model = client.transport.model
        self.pipeline_test_only = client.transport.backend == "mock"
        self.catalogue = load_catalogue(version=catalogue_version)
        self.mock_policy = mock_policy
        self.queue = queue or _TextQueue()

    # -- helpers ---------------------------------------------------------
    def _reference_note(self, case: PairwiseCase | PointwiseCase) -> str | None:
        if self.reference_mode == "free":
            return None
        parts: list[str] = []
        if case.gold_answer:
            parts.append(f"Reference answer: {case.gold_answer}")
        if case.required_elements:
            parts.append("Essential elements: " + "; ".join(case.required_elements))
        if case.reference_note:
            parts.append(case.reference_note)
        return "\n".join(parts) if parts else None

    def _criterion(self, criterion_id: str) -> Criterion:
        return self.catalogue.get(criterion_id)

    # -- pairwise --------------------------------------------------------
    def compare(
        self,
        case: PairwiseCase,
        *,
        seed: int | None = None,
        swapped: bool | None = None,
    ) -> PairwiseVerdict:
        seed = self.client.settings.seed if seed is None else seed
        swap = stable_order(self.judge_id, case.case_id, seed) if swapped is None else swapped
        first_side = "B" if swap else "A"
        second_side = "A" if swap else "B"
        prompt = render_pairwise(
            question=case.question,
            first_text=case.response(first_side),
            second_text=case.response(second_side),
            reference_note=self._reference_note(case),
            criterion=self._criterion("unsafe_recommendation"),
        )

        if self.mock_policy is not None and self.client.is_mock:
            chosen = self.mock_policy.decide(case, first_side, self.judge_id, seed)
            worse = "tie" if chosen == "tie" else (1 if chosen == first_side else 2)
            self.queue.text = json.dumps(
                {
                    "worse": worse,
                    "reason": f"[MOCK {self.mock_policy.label}] simulated judgement",
                }
            )

        record = self.client.complete(
            prompt,
            system=SYSTEM_PROMPT,
            template_version=PAIRWISE_TEMPLATE_VERSION,
            seed=seed,
            metadata={"case_id": case.case_id, "judge_id": self.judge_id, "presented_first": first_side},
        )
        parsed = parse_json_object(record.output)
        error: str | None = None
        if record.error:
            error = record.error
            winner: str = "tie"
            rationale = f"transport error: {record.error}"
        elif parsed is None or "worse" not in parsed:
            error = "unparseable_output"
            winner = "tie"
            rationale = f"could not parse judge output: {record.output[:200]!r}"
        else:
            raw = parsed.get("worse")
            rationale = str(parsed.get("reason", ""))[:800]
            if raw is None or str(raw).strip().lower() == "tie":
                winner = "tie"
            elif str(raw).strip() in {"1", "2"}:
                winner = first_side if str(raw).strip() == "1" else second_side
            else:
                error = "invalid_winner"
                winner = "tie"

        return PairwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            winner=winner,  # type: ignore[arg-type]
            rationale=rationale,
            backend=record.backend,
            model=record.model,
            template_version=PAIRWISE_TEMPLATE_VERSION,
            seed=seed,
            timestamp_utc=record.timestamp_utc or utc_now_iso(),
            presented_first=first_side,
            prompt_sha256=record.prompt_sha256,
            error=error,
            pipeline_test_only=self.pipeline_test_only,
            metadata={"reference_mode": self.reference_mode, "call_id": record.call_id},
        )

    # -- pointwise -------------------------------------------------------
    def score(self, case: PointwiseCase, criterion_id: str) -> PointwiseVerdict:
        criterion = self._criterion(criterion_id)
        seed = self.client.settings.seed
        prompt = render_pointwise(
            question=case.question,
            response=case.response,
            reference_note=self._reference_note(case),
            criterion=criterion,
        )
        if self.mock_policy is not None and self.client.is_mock:
            self.queue.text = json.dumps(
                {"score": 2 if hash(case.case_id) % 2 == 0 else 0, "reason": "[MOCK] simulated score"}
            )
        record = self.client.complete(
            prompt,
            system=SYSTEM_PROMPT,
            template_version=POINTWISE_TEMPLATE_VERSION,
            seed=seed,
            metadata={"case_id": case.case_id, "criterion_id": criterion_id, "judge_id": self.judge_id},
        )
        parsed = parse_json_object(record.output)
        score: float | None = None
        label: str | None = None
        error = record.error
        if parsed is not None and "score" in parsed:
            try:
                score = float(parsed["score"])
                error = None
            except (TypeError, ValueError):
                error = "invalid_score"
        elif error is None:
            error = "unparseable_output"
        if score is not None:
            label = criterion.anchor_map().get(int(score), "off_scale")
        return PointwiseVerdict(
            case_id=case.case_id,
            judge_id=self.judge_id,
            criterion_id=criterion_id,
            score=score,
            label=label,
            rationale=str((parsed or {}).get("reason", ""))[:800],
            backend=record.backend,
            model=record.model,
            template_version=POINTWISE_TEMPLATE_VERSION,
            seed=seed,
            timestamp_utc=record.timestamp_utc or utc_now_iso(),
            prompt_sha256=record.prompt_sha256,
            error=error,
            pipeline_test_only=self.pipeline_test_only,
            metadata={"reference_mode": self.reference_mode, "call_id": record.call_id},
        )
