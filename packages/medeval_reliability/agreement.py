"""Agreement and reliability statistics.

Every function documents its formula and its assumptions. Functions return
``float`` and return ``nan`` (never a fabricated number) when the statistic is
mathematically undefined -- for example kappa when both raters use exactly one
category, because expected agreement is then 1 and the ratio is 0/0.

Verified against hand-computed worked examples in ``tests/test_reliability.py``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Hashable, Sequence
from typing import Any

import numpy as np

__all__ = [
    "cohen_kappa",
    "fleiss_kappa",
    "gwet_ac1",
    "icc",
    "interpret_kappa",
    "krippendorff_alpha",
    "percent_agreement",
    "undefined",
]


def undefined(value: float) -> bool:
    """True when a statistic is missing or not finite."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def percent_agreement(a: Sequence[Any], b: Sequence[Any]) -> float:
    """Raw proportion of identical ratings between two raters.

    Formula: ``n_equal / n_pairs``. Not chance-corrected; reported only
    alongside kappa because raw agreement is inflated when one category
    dominates.
    """
    if len(a) != len(b):
        raise ValueError("raters must rate the same number of units")
    if not a:
        return float("nan")
    equal = sum(1 for x, y in zip(a, b, strict=True) if x == y)
    return equal / len(a)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters, nominal categories.

    ``kappa = (p_o - p_e) / (1 - p_e)`` where ``p_o`` is observed agreement and
    ``p_e = sum_k p_a(k) * p_b(k)`` is the agreement expected from each rater's
    own marginals. Returns ``nan`` when ``p_e == 1`` (undefined).
    """
    if len(a) != len(b):
        raise ValueError("raters must rate the same number of units")
    n = len(a)
    if n == 0:
        return float("nan")
    po = percent_agreement(a, b)
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if math.isclose(pe, 1.0):
        return float("nan")
    return (po - pe) / (1.0 - pe)


def fleiss_kappa(table: Sequence[Sequence[int]]) -> float:
    """Fleiss' kappa for any number of raters, nominal categories.

    ``table[i][j]`` is how many raters assigned category ``j`` to item ``i``.
    Each item must have the same number of ratings ``m`` (>= 2).

    ``P_i = (sum_j n_ij^2 - m) / (m(m-1))``, ``P_bar = mean(P_i)``,
    ``P_e = sum_j p_j^2`` with ``p_j`` the overall proportion of ratings in
    category ``j``, ``kappa = (P_bar - P_e) / (1 - P_e)``.
    """
    counts = np.asarray(table, dtype=float)
    if counts.ndim != 2 or counts.size == 0:
        raise ValueError("table must be a non-empty 2-D array")
    per_item = counts.sum(axis=1)
    if not np.allclose(per_item, per_item[0]):
        raise ValueError("Fleiss' kappa requires the same number of raters per item")
    m = float(per_item[0])
    if m < 2:
        raise ValueError("Fleiss' kappa requires at least two raters per item")
    p_i = (np.sum(counts**2, axis=1) - m) / (m * (m - 1.0))
    p_bar = float(np.mean(p_i))
    n_ratings = counts.sum()
    p_j = counts.sum(axis=0) / n_ratings
    p_e = float(np.sum(p_j**2))
    if math.isclose(p_e, 1.0):
        return float("nan")
    return (p_bar - p_e) / (1.0 - p_e)


def gwet_ac1(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Gwet's AC1 for two raters -- a kappa paradox workaround.

    ``AC1 = (p_o - p_e) / (1 - p_e)`` with ``p_e = sum_k pi_k (1 - pi_k) / (q - 1)``
    where ``pi_k`` is the pooled prevalence of category ``k`` and ``q`` the number
    of categories. Included because high-prevalence binary ratings make kappa
    collapse towards 0 even when agreement is high; AC1 is reported beside it,
    never instead of it.
    """
    if len(a) != len(b):
        raise ValueError("raters must rate the same number of units")
    n = len(a)
    if n == 0:
        return float("nan")
    po = percent_agreement(a, b)
    pooled = Counter(a)
    pooled.update(b)
    q = len(pooled)
    if q < 2:
        return float("nan")
    p_e = sum((count / (2 * n)) * (1 - count / (2 * n)) for count in pooled.values()) / (q - 1)
    if math.isclose(p_e, 1.0):
        return float("nan")
    return (po - p_e) / (1.0 - p_e)


def krippendorff_alpha(
    data: Sequence[Sequence[Any]],
    *,
    level: str = "nominal",
    value_domain: Sequence[Any] | None = None,
) -> float:
    """Krippendorff's alpha for any number of raters and missing values.

    ``data`` is ``units x raters``; missing ratings are ``None`` or ``nan``.
    Implements the coincidence-matrix form:

    ``alpha = 1 - D_o / D_e``

    with ``D_o = (1/n) * sum_{c,k} o_ck * delta^2(c,k)`` and
    ``D_e = 1/(n(n-1)) * sum_{c,k} n_c n_k delta^2(c,k)``.

    Supported ``level`` values: ``nominal`` (delta = 1 for c != k) and
    ``ordinal`` (rank-based delta). Units with fewer than two ratings carry no
    information and are skipped, which is the documented Krippendorff behaviour.
    """
    matrix = [list(row) for row in data]
    if not matrix:
        return float("nan")
    n_raters = max(len(row) for row in matrix)
    if n_raters < 2:
        return float("nan")

    observations: list[list[Any]] = []
    for row in matrix:
        clean = [v for v in row if v is not None and not (isinstance(v, float) and math.isnan(v))]
        if len(clean) >= 2:
            observations.append(clean)
    if not observations:
        return float("nan")

    domain = (
        sorted({v for obs in observations for v in obs}, key=str)
        if value_domain is None
        else list(value_domain)
    )
    index = {value: i for i, value in enumerate(domain)}
    q = len(domain)
    if q < 2:
        return float("nan")

    coincidence = np.zeros((q, q), dtype=float)
    for obs in observations:
        m_u = len(obs)
        counts = Counter(obs)
        for value_c, n_c in counts.items():
            for value_k, n_k in counts.items():
                if value_c == value_k:
                    coincidence[index[value_c], index[value_k]] += n_c * (n_c - 1) / (m_u - 1)
                else:
                    coincidence[index[value_c], index[value_k]] += n_c * n_k / (m_u - 1)

    n_c = coincidence.sum(axis=1)
    total = float(n_c.sum())
    if total <= 0:
        return float("nan")

    delta = _delta_matrix(domain, level, n_c)
    d_o = float(np.sum(coincidence * delta)) / total
    d_e = float(n_c @ delta @ n_c) / (total * (total - 1.0))
    if math.isclose(d_e, 0.0):
        return float("nan")
    return 1.0 - d_o / d_e


def _delta_matrix(domain: Sequence[Any], level: str, n_c: np.ndarray) -> np.ndarray:
    q = len(domain)
    delta = np.zeros((q, q), dtype=float)
    if level == "nominal":
        for i in range(q):
            for j in range(q):
                if i != j:
                    delta[i, j] = 1.0
        return delta
    if level == "ordinal":
        # Krippendorff (2011): for c < k,
        # delta^2(c,k) = ( sum_{g=c..k} n_g - (n_c + n_k)/2 )^2
        for i in range(q):
            for j in range(i + 1, q):
                span = float(np.sum(n_c[i : j + 1]) - (n_c[i] + n_c[j]) / 2.0)
                delta[i, j] = span**2
                delta[j, i] = span**2
        return delta
    raise ValueError(f"unsupported level {level!r}; use 'nominal' or 'ordinal'")


def icc(
    data: Sequence[Sequence[float]],
    *,
    model: str = "twoway",
    icc_type: str = "agreement",
    unit: str = "single",
) -> float:
    """Intraclass correlation from a subjects x raters matrix of scores.

    ``model='twoway'`` uses the two-way ANOVA mean squares (MSR between subjects,
    MSC between raters, MSE residual). ``icc_type='agreement'`` includes rater
    variance in the denominator (ICC(2,1)); ``icc_type='consistency'`` does not
    (ICC(3,1)). ``unit='average'`` applies the Spearman-Brown adjustment for the
    mean of ``k`` raters.

    Formula (Shrout & Fleiss 1979):
    ICC(2,1) = (MSR-MSE) / (MSR + (k-1)MSE + k(MSC-MSE)/n);
    ICC(3,1) = (MSR-MSE) / (MSR + (k-1)MSE).
    """
    matrix = np.asarray(data, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("data must be a 2-D subjects x raters matrix")
    n, k = matrix.shape
    if n < 2 or k < 2:
        raise ValueError("ICC requires at least 2 subjects and 2 raters")
    if np.isnan(matrix).any():
        raise ValueError("ICC requires complete data; drop or impute missing ratings first")
    grand = float(matrix.mean())
    row_means = matrix.mean(axis=1)
    col_means = matrix.mean(axis=0)
    ssr = float(k * np.sum((row_means - grand) ** 2))
    ssc = float(n * np.sum((col_means - grand) ** 2))
    sst = float(np.sum((matrix - grand) ** 2))
    sse = sst - ssr - ssc
    df_r, df_c, df_e = n - 1, k - 1, (n - 1) * (k - 1)
    if df_e <= 0 or df_r <= 0:
        return float("nan")
    msr = ssr / df_r
    msc = ssc / df_c if df_c > 0 else float("nan")
    mse = sse / df_e

    if model == "twoway":
        if icc_type == "agreement":
            denom = msr + (k - 1) * mse + k * (msc - mse) / n
        elif icc_type == "consistency":
            denom = msr + (k - 1) * mse
        else:
            raise ValueError("icc_type must be 'agreement' or 'consistency'")
        value = (msr - mse) / denom if denom != 0 else float("nan")
    elif model == "oneway":
        # ICC(1,1): one-way random effects, raters are exchangeable.
        msw = sse / df_e
        value = (msr - msw) / (msr + (k - 1) * msw) if (msr + (k - 1) * msw) != 0 else float("nan")
    else:
        raise ValueError("model must be 'twoway' or 'oneway'")

    if unit == "average":
        if math.isclose(1.0 - value, 0.0):
            return 1.0
        return (k * value) / (1.0 + (k - 1) * value)
    if unit != "single":
        raise ValueError("unit must be 'single' or 'average'")
    return float(value)


def interpret_kappa(value: float) -> str:
    """Landis & Koch (1977) descriptive bands.

    These bands are conventions, not evidence of validity for a given task; the
    report always prints the number next to the band.
    """
    if undefined(value):
        return "undefined"
    if value < 0.0:
        return "poor (below chance)"
    if value <= 0.20:
        return "slight"
    if value <= 0.40:
        return "fair"
    if value <= 0.60:
        return "moderate"
    if value <= 0.80:
        return "substantial"
    return "almost perfect"
