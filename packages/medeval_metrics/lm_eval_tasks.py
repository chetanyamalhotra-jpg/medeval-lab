"""``process_results`` hooks for harness tasks that carry confidence information.

The harness discards choice log-probabilities after computing accuracy, so a
Brier score or an NLL has to be produced inside ``process_results``. These
functions are referenced from the task YAMLs in ``data/tasks/`` and deliberately
do not import ``lm_eval``: they are plain functions of ``(doc, results)``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "doc_to_text_medmcqa",
    "process_results_mc",
    "process_results_medmcqa",
    "process_results_medqa",
]

EPS = 1e-12


def _extract_loglikelihoods(results: Sequence[Any]) -> list[float]:
    """Accept either bare floats or ``(loglikelihood, is_greedy)`` pairs."""
    values: list[float] = []
    for item in results:
        if isinstance(item, (list, tuple)):
            values.append(float(item[0]))
        else:
            values.append(float(item))
    return values


def _softmax(logits: Sequence[float]) -> list[float]:
    if not logits:
        return []
    peak = max(logits)
    exponents = [math.exp(value - peak) for value in logits]
    total = sum(exponents)
    return [value / total for value in exponents]


def process_results_mc(
    results: Sequence[Any],
    gold_index: int,
    n_choices: int,
) -> dict[str, float | int]:
    """Per-document ``acc``/Brier/NLL/confidence from choice log-likelihoods.

    ``p_correct`` is the softmax probability of the gold choice; the Brier
    contribution is ``(p_correct - correct)^2`` and the NLL is
    ``-log(p_correct)``. All four values are per-document, which is what the
    harness needs before it aggregates.
    """
    lls = _extract_loglikelihoods(results)
    if len(lls) != n_choices:
        raise ValueError(f"expected {n_choices} choice log-likelihoods, got {len(lls)}")
    if not (0 <= gold_index < n_choices):
        raise ValueError(f"gold index {gold_index} outside [0, {n_choices})")
    probabilities = _softmax(lls)
    predicted = max(range(n_choices), key=lambda i: lls[i])
    correct = int(predicted == gold_index)
    p_correct = probabilities[gold_index]
    return {
        "acc": correct,
        "medeval_brier": (p_correct - correct) ** 2,
        "medeval_nll": -math.log(p_correct + EPS),
        "medeval_mean_confidence": max(probabilities),
    }


def process_results_medmcqa(doc: Mapping[str, Any], results: Sequence[Any]) -> dict[str, float | int]:
    """MedMCQA: ``cop`` is a 0-based integer index into A-D (checked by the audit)."""
    gold = doc.get("cop")
    if gold is None:
        gold = doc.get("cop_idx")
    if isinstance(gold, str):
        gold = int(gold)
    return process_results_mc(results, int(gold), 4)


def process_results_medqa(doc: Mapping[str, Any], results: Sequence[Any]) -> dict[str, float | int]:
    """MedQA (4 options): ``label`` is a 0-based integer index into A-D."""
    return process_results_mc(results, int(doc["label"]), 4)


def doc_to_text_medmcqa(doc: Mapping[str, Any]) -> str:
    """Prompt identical in shape to the upstream medmcqa task."""
    choices = [doc["opa"], doc["opb"], doc["opc"], doc["opd"]]
    labels = ["A", "B", "C", "D"]
    lines = [f"{label}. {choice}" for label, choice in zip(labels, choices, strict=True)]
    return f"Question: {doc['question']}\nChoices:\n" + "\n".join(lines) + "\nAnswer:"


def doc_to_text_medqa(doc: Mapping[str, Any]) -> str:
    """Prompt identical in shape to the upstream medqa_4options task."""
    choices = [doc["ending0"], doc["ending1"], doc["ending2"], doc["ending3"]]
    labels = ["A", "B", "C", "D"]
    lines = [f"{label}. {choice}" for label, choice in zip(labels, choices, strict=True)]
    return f"Question: {doc['sent1']}\n" + "\n".join(lines) + "\nAnswer:"
