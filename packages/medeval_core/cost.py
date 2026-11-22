"""Token and cost estimation, printed *before* any paid call is made.

The estimator is deliberately crude and deliberately conservative: about four
characters per token for English prose. It is used to refuse a run whose
worst-case spend exceeds the configured cap, so underestimating is the failure
mode that matters, and the formula errs high by counting the full ``max_tokens``
allowance for every call rather than the expected reply length.

Nothing here is a price feed. Prices come from the environment (see
:mod:`medeval_core.config`) and are reported with the source string the user
supplied, or as ``unverified``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .safety import PriceCard

__all__ = ["CallPlan", "estimate_tokens", "plan_summary", "preview_cost"]

_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for one message."""
    if not text:
        return 1
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


@dataclass(frozen=True)
class CallPlan:
    """How many calls a run will make and how big each one is."""

    calls: int
    prompt_tokens_each: int
    max_output_tokens_each: int
    label: str = ""

    @property
    def input_tokens(self) -> int:
        return self.calls * self.prompt_tokens_each

    @property
    def output_tokens_upper_bound(self) -> int:
        return self.calls * self.max_output_tokens_each

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "calls": self.calls,
            "prompt_tokens_each": self.prompt_tokens_each,
            "max_output_tokens_each": self.max_output_tokens_each,
            "input_tokens_upper_bound": self.input_tokens,
            "output_tokens_upper_bound": self.output_tokens_upper_bound,
        }


def preview_cost(plan: CallPlan, prices: PriceCard) -> dict[str, Any]:
    """Worst-case USD for a plan, or ``None`` when prices are unverified."""
    amount = prices.estimate(plan.input_tokens, plan.output_tokens_upper_bound)
    return {
        "calls": plan.calls,
        "input_tokens_upper_bound": plan.input_tokens,
        "output_tokens_upper_bound": plan.output_tokens_upper_bound,
        "usd_upper_bound": amount,
        "prices_verified": prices.verified,
        "price_source": prices.source,
    }


def plan_summary(plans: Sequence[CallPlan], prices: PriceCard) -> dict[str, Any]:
    """Aggregate several :class:`CallPlan` objects into one honest preview."""
    total_calls = sum(p.calls for p in plans)
    input_tokens = sum(p.input_tokens for p in plans)
    output_tokens = sum(p.output_tokens_upper_bound for p in plans)
    amount = prices.estimate(input_tokens, output_tokens)
    return {
        "per_judge": [p.as_dict() for p in plans],
        "total_calls": total_calls,
        "input_tokens_upper_bound": input_tokens,
        "output_tokens_upper_bound": output_tokens,
        "usd_upper_bound": amount,
        "prices_verified": prices.verified,
        "price_source": prices.source,
        "estimator": f"ceil(characters/{_CHARS_PER_TOKEN:g}) per message, full max_tokens for every reply",
    }


def summarise_usage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Add up recorded usage from call-log rows."""
    input_tokens = sum(int(row.get("input_tokens") or 0) for row in rows)
    output_tokens = sum(int(row.get("output_tokens") or 0) for row in rows)
    reasoning_tokens = sum(int(row.get("reasoning_tokens") or 0) for row in rows)
    return {
        "calls": len(rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
    }
