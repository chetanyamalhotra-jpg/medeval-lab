"""Bias probes for judges: position, length and self-preference.

Each probe returns a statistic plus the test used, so a report can state what
was measured rather than assert "no bias". These are diagnostics on observed
judgements, not causal claims.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats

from .agreement import krippendorff_alpha
from .bootstrap import BootstrapCI, paired_bootstrap_diff

__all__ = [
    "LengthBiasResult",
    "PositionBiasResult",
    "SelfPreferenceResult",
    "length_bias",
    "position_bias",
    "self_preference",
]


@dataclass(frozen=True)
class PositionBiasResult:
    """How often the judge preferred whichever response was shown first."""

    n_pairs: int
    first_slot_rate: float
    exact_binomial_p: float
    swap_consistency: float | None
    n_swap_pairs: int
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_pairs": self.n_pairs,
            "first_slot_rate": self.first_slot_rate,
            "exact_binomial_p": self.exact_binomial_p,
            "swap_consistency": self.swap_consistency,
            "n_swap_pairs": self.n_swap_pairs,
            "notes": self.notes,
        }


def position_bias(records: Sequence[Mapping[str, Any]]) -> PositionBiasResult:
    """Measure first-slot preference and consistency under order swap.

    ``records`` must contain, per judgement, ``case_id``, ``winner`` (the id of
    the chosen response, not its slot) and ``swapped`` (bool, whether the pair
    was presented in reversed order). ``winner_slot`` is optional; when present
    it is used directly for the first-slot rate.

    A judge free of position bias picks each slot about half the time, and picks
    the same *response* regardless of presentation order. Both are reported:
    the first is the bias symptom, the second is the stronger evidence.
    """
    if not records:
        raise ValueError("position_bias needs at least one judgement record")

    first_slot_wins = 0
    n_slot = 0
    by_case: dict[str, dict[bool, Hashable]] = {}
    for record in records:
        case_id = str(record.get("case_id"))
        if "winner_slot" in record:
            n_slot += 1
            if int(record["winner_slot"]) == 1:
                first_slot_wins += 1
        swapped = bool(record.get("swapped", False))
        winner = record.get("winner")
        if winner is not None:
            by_case.setdefault(case_id, {})[swapped] = winner

    if n_slot == 0:
        # Infer slot preference from winner ids under the convention that
        # presented order is (A, B) when swapped=False and (B, A) when True.
        for record in records:
            winner = record.get("winner")
            if winner is None:
                continue
            swapped = bool(record.get("swapped", False))
            first = "B" if swapped else "A"
            n_slot += 1
            if winner == first:
                first_slot_wins += 1

    rate = first_slot_wins / n_slot if n_slot else float("nan")
    p_value = (
        float(stats.binomtest(first_slot_wins, n_slot, 0.5, alternative="two-sided").pvalue)
        if n_slot
        else float("nan")
    )

    comparable = [case for case, winners in by_case.items() if len(winners) >= 2]
    consistent = sum(
        1 for case in comparable if len(set(by_case[case].values())) == 1
    )
    consistency = consistent / len(comparable) if comparable else None

    return PositionBiasResult(
        n_pairs=n_slot,
        first_slot_rate=rate,
        exact_binomial_p=p_value,
        swap_consistency=consistency,
        n_swap_pairs=len(comparable),
        notes="first_slot_rate=0.5 means no measurable position preference",
    )


@dataclass(frozen=True)
class LengthBiasResult:
    """Association between response length and judge score."""

    n: int
    spearman_rho: float
    permutation_p: float
    asymptotic_p: float
    seed: int
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "spearman_rho": self.spearman_rho,
            "permutation_p": self.permutation_p,
            "asymptotic_p": self.asymptotic_p,
            "seed": self.seed,
            "notes": self.notes,
        }


def length_bias(
    scores: Sequence[float],
    lengths: Sequence[float],
    *,
    n_permutations: int = 5_000,
    seed: int = 20260101,
) -> LengthBiasResult:
    """Spearman correlation between score and response length, with permutation p.

    Positive rho means longer responses score higher. The permutation test makes
    no distributional assumption; both p-values are reported because they can
    disagree at small n.
    """
    if len(scores) != len(lengths):
        raise ValueError("scores and lengths must have the same length")
    n = len(scores)
    if n < 3:
        raise ValueError("length_bias needs at least three paired observations")
    x = np.asarray(scores, dtype=float)
    y = np.asarray(lengths, dtype=float)
    if np.allclose(x, x[0]) or np.allclose(y, y[0]):
        return LengthBiasResult(n, float("nan"), float("nan"), float("nan"), seed, "constant input")
    rho = float(stats.spearmanr(x, y).statistic)
    asymptotic = float(stats.spearmanr(x, y).pvalue)
    rng = np.random.default_rng(seed)
    exceed = 0
    for _ in range(n_permutations):
        permuted = rng.permutation(y)
        perm_rho = float(stats.spearmanr(x, permuted).statistic)
        if abs(perm_rho) >= abs(rho):
            exceed += 1
    p_perm = (exceed + 1) / (n_permutations + 1)
    return LengthBiasResult(
        n=n,
        spearman_rho=rho,
        permutation_p=p_perm,
        asymptotic_p=asymptotic,
        seed=seed,
        notes="positive rho = longer responses scored higher",
    )


@dataclass(frozen=True)
class SelfPreferenceResult:
    """Paired comparison of a judge's scores on its own family's text vs others."""

    ci: BootstrapCI
    n_pairs: int
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = self.ci.as_dict()
        payload["n_pairs"] = self.n_pairs
        payload["notes"] = self.notes
        return payload


def self_preference(
    own_scores: Sequence[float],
    other_scores: Sequence[float],
    *,
    n_resamples: int = 10_000,
    seed: int = 20260101,
) -> SelfPreferenceResult:
    """Paired mean difference ``own - other`` with a bootstrap interval.

    Requires that the two score vectors refer to the same cases in the same
    order. An interval that excludes zero is evidence of a preference in this
    sample; it is not proof of a mechanism.
    """
    ci = paired_bootstrap_diff(
        own_scores, other_scores, n_resamples=n_resamples, method="bca", seed=seed
    )
    return SelfPreferenceResult(
        ci=ci,
        n_pairs=len(own_scores),
        notes="estimate is mean(own) - mean(other) over the same cases",
    )


def repeat_agreement(per_case_ratings: Sequence[Sequence[Hashable]]) -> dict[str, float]:
    """Agreement of a judge with itself across repeated (seeded) calls.

    ``per_case_ratings[i]`` is the list of categories that repeat ``j`` assigned
    to case ``i``. Returns exact agreement (mean share of the modal category),
    the unanimous-case rate, and nominal Krippendorff's alpha across repeats,
    which is the chance-corrected equivalent of self-consistency.
    """
    ratings = [list(row) for row in per_case_ratings]
    if not ratings:
        return {"n_cases": 0.0, "exact_agreement": float("nan"), "unanimous_rate": float("nan"), "alpha": float("nan")}
    n_repeats = min(len(row) for row in ratings)
    exact: list[float] = []
    unanimous = 0
    for row in ratings:
        counts: dict[Hashable, int] = {}
        for value in row:
            counts[value] = counts.get(value, 0) + 1
        if not counts:
            continue
        modal = max(counts.values())
        exact.append(modal / len(row))
        if modal == len(row):
            unanimous += 1
    return {
        "n_cases": float(len(ratings)),
        "n_repeats": float(n_repeats),
        "exact_agreement": float(np.mean(exact)) if exact else float("nan"),
        "unanimous_rate": unanimous / len(ratings),
        "alpha": krippendorff_alpha(ratings, level="nominal"),
    }
