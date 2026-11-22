"""Deterministic mock targets and judges for pipeline tests and dry runs.

Everything this module produces is labelled ``PIPELINE TEST ONLY``.  The mock is
deliberately *not* a model: it is a hash-based stand-in whose only job is to make
the wiring observable (correct, incorrect, abstained and malformed outputs all
appear), so a dry run can exercise every branch without a network call.

The mock reads the item's gold fields to choose a plausible reply.  That is safe
only because it never leaves this process and every artefact records
``pipeline_test_only: true``.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from medeval_core.client import RawCompletion

from .dataset import PackItem

__all__ = ["MockJudge", "MockTarget"]


def _bucket(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return int(digest, 16) / 0xFFFFFFFFFFFF


class MockTarget:
    """Deterministic target model stand-in. PIPELINE TEST ONLY."""

    backend = "mock"
    pipeline_test_only = True

    def __init__(self, model: str, *, seed: int = 20260101) -> None:
        self.model = model
        self.seed = seed
        # Models differ a little so a matrix run has something to compare.
        self.accuracy = 0.55 + 0.30 * _bucket("accuracy", model, str(seed))

    def complete(self, item: PackItem) -> RawCompletion:
        started = time.perf_counter()
        text = self._text(item)
        latency = (time.perf_counter() - started) * 1000.0 + 8.0 + 40.0 * _bucket("latency", self.model, item.item_id)
        return RawCompletion(
            text=text,
            latency_ms=latency,
            input_tokens=max(1, len(str(item.prompt_fields)) // 4),
            output_tokens=max(1, len(text) // 4),
        )

    def _text(self, item: PackItem) -> str:
        roll = _bucket("output", self.model, item.item_id)
        if item.task_type == "mcq":
            options: dict[str, str] = dict(item.gold.get("options") or {})
            correct = str(item.gold.get("letter") or "A")
            if roll < 0.04:
                return "ABSTAIN: the vignette does not contain enough information."
            if roll < 0.07:
                return "I am not able to determine the answer from this vignette."
            if roll < self.accuracy:
                return f"ANSWER: {correct}\n\nThis option best fits the described findings."
            wrong = next((letter for letter in sorted(options) if letter != correct), "B")
            return f"ANSWER: {wrong}\n\nThis option seems most consistent with the presentation."
        if item.task_type == "structured_extraction":
            gold = item.gold.get("fields") or {}
            fields: dict[str, Any] = {}
            for name, value in sorted(gold.items()):
                keep = _bucket("field", self.model, item.item_id, str(name)) < 0.8
                fields[name] = value if keep else ""
            if roll < 0.05:
                return '{"fields": ' + json.dumps(fields) + "}"  # missing closing brace on purpose
            return json.dumps({"fields": fields}, ensure_ascii=False)
        if item.task_type == "triage_classification":
            gold = str(item.gold.get("label") or "")
            return gold if roll < self.accuracy else "routine"
        reference = str(item.gold.get("reference_answer") or "")
        rubric = item.gold.get("rubric") or []
        points = []
        for criterion in rubric if isinstance(rubric, list) else []:
            points.append(f"- {criterion}: addressed" if _bucket("rubric", self.model, item.item_id, str(criterion)) < 0.8 else f"- {criterion}: not addressed")
        body = reference if reference else "A cautious assessment based on the presented findings."
        if roll < 0.06:
            return "I cannot answer this question."
        return body + ("\n\n" + "\n".join(points) if points else "")


class MockJudge:
    """Deterministic rubric judge. PIPELINE TEST ONLY."""

    backend = "mock"
    pipeline_test_only = True

    def __init__(self, model: str, *, family: str = "mock-family", seed: int = 20260101) -> None:
        self.model = model
        self.family = family
        self.seed = seed
        self.base_score = 2.5 + 1.5 * _bucket("judge-bias", model, family, str(seed))

    def complete(self, item: PackItem, target_text: str, scale_min: float, scale_max: float) -> RawCompletion:
        started = time.perf_counter()
        # Deliberate disagreement: each judge applies its own stable bias.
        jitter = (_bucket("judge", self.model, item.item_id) - 0.5) * 2.0
        score = min(scale_max, max(scale_min, round(self.base_score + jitter)))
        rationale = (
            "Mock rationale (PIPELINE TEST ONLY): assessed against the declared rubric; "
            "this is wiring evidence, not a quality judgement."
        )
        text = json.dumps({"score": score, "rationale": rationale})
        return RawCompletion(
            text=text,
            latency_ms=(time.perf_counter() - started) * 1000.0 + 5.0,
            input_tokens=max(1, (len(target_text) + 200) // 4),
            output_tokens=max(1, len(text) // 4),
        )
