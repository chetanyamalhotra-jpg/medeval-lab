"""Harness-compatible metric and aggregation plugins.

Registered through ``pyproject.toml`` entry points (``lm_eval.metrics`` and
``lm_eval.aggregations``), so ``lm-eval`` discovers them without editing the
upstream checkout.

Why only *some* of our metrics appear here: the harness applies a metric
function to the list of per-document values, which is a mean of per-item
contributions. ``ece`` is not decomposable that way and is therefore computed
post-hoc from logged probabilities instead of being registered.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
from lm_eval.api.registry import register_aggregation, register_metric


def _flatten(items: Iterable[Any]) -> np.ndarray:
    """Flatten the harness' per-document (possibly nested) value lists."""
    out: list[float] = []
    for item in items:
        if isinstance(item, (list, tuple, np.ndarray)):
            out.extend(float(x) for x in np.asarray(item, dtype=float).ravel() if not np.isnan(x))
        elif item is not None and not (isinstance(item, float) and np.isnan(item)):
            out.append(float(item))
    return np.asarray(out, dtype=float)


@register_aggregation("medeval_worst_decile")
def medeval_worst_decile(items: Sequence[Any]) -> float:
    """10th percentile of per-item values -- the performance of the weakest decile.

    For ``acc`` this answers "what does the model score on its worst tenth of
    items", which a mean hides. Meaningless with fewer than 10 items; the
    harness prints the value anyway, so read it next to ``n``.
    """
    values = _flatten(items)
    if values.size == 0:
        return float("nan")
    return float(np.quantile(values, 0.10))


@register_aggregation("medeval_trimmed_mean")
def medeval_trimmed_mean(items: Sequence[Any], proportion: float = 0.10) -> float:
    """Mean after dropping the lowest and highest ``proportion`` of values."""
    values = _flatten(items)
    if values.size == 0:
        return float("nan")
    if values.size < 4:
        return float(values.mean())
    k = int(np.floor(values.size * proportion))
    ordered = np.sort(values)
    trimmed = ordered[k : values.size - k] if k else ordered
    return float(trimmed.mean())


@register_metric(metric="medeval_acc", higher_is_better=True)
def medeval_acc_metric(items: Sequence[Any]) -> float:
    """Reference implementation of accuracy; registered so the plugin path is exercised."""
    values = _flatten(items)
    return float(values.mean()) if values.size else float("nan")


@register_metric(metric="medeval_brier", higher_is_better=False)
def medeval_brier_metric(items: Sequence[Any]) -> float:
    """Mean Brier contribution; each document supplies ``(p_correct - correct)^2``."""
    values = _flatten(items)
    return float(values.mean()) if values.size else float("nan")


@register_metric(metric="medeval_nll", higher_is_better=False)
def medeval_nll_metric(items: Sequence[Any]) -> float:
    """Mean negative log-likelihood of the gold choice."""
    values = _flatten(items)
    return float(values.mean()) if values.size else float("nan")


@register_metric(metric="medeval_mean_confidence", higher_is_better=True)
def medeval_mean_confidence_metric(items: Sequence[Any]) -> float:
    """Mean probability the model assigned to its own chosen answer."""
    values = _flatten(items)
    return float(values.mean()) if values.size else float("nan")
