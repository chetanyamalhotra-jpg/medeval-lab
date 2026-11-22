"""Reliability statistics verified against hand-computed worked examples.

Each expected value below is derived by hand in the comment above the assertion;
none is copied from a package's default output.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from medeval_reliability import (
    bootstrap_ci,
    cluster_bootstrap_ci,
    cohen_kappa,
    fleiss_kappa,
    gwet_ac1,
    icc,
    interpret_kappa,
    krippendorff_alpha,
    length_bias,
    mcnemar_test,
    paired_bootstrap_diff,
    percent_agreement,
    position_bias,
    proportion_ci,
    repeat_agreement,
    self_preference,
)

# Confusion matrix (A,B): (yes,yes)=20, (yes,no)=5, (no,yes)=10, (no,no)=15.
A = ["yes"] * 20 + ["yes"] * 5 + ["no"] * 10 + ["no"] * 15
B = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15


def test_percent_agreement_hand_value() -> None:
    # 35 of 50 pairs agree.
    assert percent_agreement(A, B) == pytest.approx(0.7)


def test_cohen_kappa_hand_value() -> None:
    # po = 0.7; margins A=(25,25), B=(30,20); pe = .5*.6 + .5*.4 = 0.5
    # kappa = (0.7-0.5)/(1-0.5) = 0.4
    assert cohen_kappa(A, B) == pytest.approx(0.4)


def test_cohen_kappa_perfect_agreement() -> None:
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)


def test_cohen_kappa_undefined_when_one_category() -> None:
    # Both raters use a single category: pe = 1, kappa = 0/0.
    assert math.isnan(cohen_kappa(["x", "x"], ["x", "x"]))


def test_cohen_kappa_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        cohen_kappa(["a"], ["a", "b"])


def test_gwet_ac1_hand_value() -> None:
    # po=0.7; pooled prevalence yes=55/100=0.55, no=0.45; q=2
    # pe = (0.55*0.45 + 0.45*0.55)/1 = 0.495
    # AC1 = (0.7-0.495)/(1-0.495) = 0.4059405...
    assert gwet_ac1(A, B) == pytest.approx(0.205 / 0.505)


def test_fleiss_kappa_hand_value() -> None:
    # 3 items, 2 raters: P_i = 1, 0, 1 -> P_bar = 2/3; p_j = (.5,.5); Pe = .5
    # kappa = (2/3 - 1/2)/(1/2) = 1/3
    table = [[2, 0], [1, 1], [0, 2]]
    assert fleiss_kappa(table) == pytest.approx(1 / 3)


def test_fleiss_kappa_perfect_agreement() -> None:
    assert fleiss_kappa([[3, 0], [0, 3]]) == pytest.approx(1.0)


def test_fleiss_kappa_requires_equal_rater_counts() -> None:
    with pytest.raises(ValueError):
        fleiss_kappa([[2, 0], [1, 1, 1]])


def test_krippendorff_alpha_two_raters_hand_value() -> None:
    # Do = 30/100 = 0.3 (15 discordant units, each contributing 2 coincidences)
    # n_yes = 55, n_no = 45, n = 100
    # De = 2*55*45/(100*99) = 0.5
    # alpha = 1 - 0.3/0.5 = 0.4
    assert krippendorff_alpha(list(zip(A, B, strict=True))) == pytest.approx(0.4)


def test_krippendorff_alpha_perfect() -> None:
    data = [["a", "a", "a"], ["b", "b", "b"], ["c", "c", "c"]]
    assert krippendorff_alpha(data) == pytest.approx(1.0)


def test_krippendorff_alpha_missing_values_dropped() -> None:
    # Unit 3 has a single rating, so it carries no information and is skipped.
    with_missing = [["a", "a"], ["b", "b"], ["c", None]]
    reduced = [["a", "a"], ["b", "b"]]
    assert krippendorff_alpha(with_missing) == pytest.approx(krippendorff_alpha(reduced))


def test_krippendorff_alpha_two_categories_ordinal_equals_nominal() -> None:
    data = [["yes", "yes"], ["yes", "no"], ["no", "no"], ["yes", "no"], ["no", "no"]]
    assert krippendorff_alpha(data, level="ordinal") == pytest.approx(
        krippendorff_alpha(data, level="nominal")
    )


def test_krippendorff_alpha_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        krippendorff_alpha([["a", "b"]], level="interval")


def test_icc_hand_value() -> None:
    # 4 subjects x 2 raters, values [[1,4],[2,5],[3,6],[2,3]], grand mean 3.25
    # SSR = 2*[.5625+.0625+1.5625+.5625] = 5.5      df=3 -> MSR = 1.8333...
    # SSC = 4*[1.5625+1.5625] = 12.5                df=1 -> MSC = 12.5
    # SSE = 1.5 (residuals +-0.25 x6, +-0.75 x2)    df=3 -> MSE = 0.5
    # ICC(2,1) = (1.8333-0.5)/(1.8333+0.5+2*(12.5-0.5)/4) = 0.16
    # ICC(3,1) = (1.8333-0.5)/(1.8333+0.5) = 4/7
    data = [[1, 4], [2, 5], [3, 6], [2, 3]]
    assert icc(data, model="twoway", icc_type="agreement") == pytest.approx(0.16)
    assert icc(data, model="twoway", icc_type="consistency") == pytest.approx(4 / 7)


def test_icc_oneway_matches_hand_anova() -> None:
    # Same matrix: MSR = 1.8333..., MSW = SSE/df_e = 0.5
    # ICC(1,1) = (1.8333-0.5)/(1.8333 + 1*0.5) = 4/7
    data = [[1, 4], [2, 5], [3, 6], [2, 3]]
    assert icc(data, model="oneway") == pytest.approx(4 / 7)


def test_icc_rejects_missing_data() -> None:
    with pytest.raises(ValueError):
        icc([[1, 2], [3, float("nan")]])


def test_interpret_kappa_bands() -> None:
    # Landis & Koch: 0.41-0.60 is "moderate", 0.21-0.40 is "fair".
    assert interpret_kappa(0.45) == "moderate"
    assert interpret_kappa(0.4) == "fair"
    assert interpret_kappa(float("nan")) == "undefined"
    assert interpret_kappa(-0.1) == "poor (below chance)"


def test_bootstrap_constant_data_has_zero_width() -> None:
    result = bootstrap_ci([0.5] * 10, seed=1, n_resamples=200)
    assert result.low == pytest.approx(0.5)
    assert result.high == pytest.approx(0.5)
    assert result.estimate == pytest.approx(0.5)


def test_bootstrap_is_deterministic_for_seed() -> None:
    values = [0, 1, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0]
    first = bootstrap_ci(values, seed=42, n_resamples=500)
    second = bootstrap_ci(values, seed=42, n_resamples=500)
    assert first.low == second.low and first.high == second.high


def test_bootstrap_bca_brackets_estimate() -> None:
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = bootstrap_ci(values, method="bca", seed=7, n_resamples=2000)
    assert result.low < result.estimate < result.high
    assert result.method == "bca"


def test_bootstrap_single_observation() -> None:
    result = bootstrap_ci([3.0], seed=1)
    assert (result.low, result.high, result.estimate) == (3.0, 3.0, 3.0)
    assert result.n_resamples == 0


def test_cluster_bootstrap_detects_two_clusters() -> None:
    values = [1.0, 1.0, 0.0, 0.0]
    clusters = ["a", "a", "b", "b"]
    result = cluster_bootstrap_ci(values, clusters, n_resamples=300, seed=3)
    assert result.estimate == pytest.approx(0.5)
    assert result.low <= 0.0 and result.high >= 1.0


def test_paired_bootstrap_diff_of_constant_difference() -> None:
    result = paired_bootstrap_diff([1.0, 1.0, 1.0], [0.0, 0.0, 0.0], n_resamples=200, seed=2)
    assert result.estimate == pytest.approx(1.0)
    assert result.low == pytest.approx(1.0)


def test_mcnemar_exact_hand_value() -> None:
    # b10 = 9, b01 = 1, n_discordant = 10 -> p = 2*(C(10,0)+C(10,1))/2^10 = 22/1024
    a = [True] * 9 + [False] + [True] * 10
    b = [False] * 9 + [True] + [True] * 10
    result = mcnemar_test(a, b)
    assert result["b10"] == 9 and result["b01"] == 1
    assert result["p_value"] == pytest.approx(22 / 1024)
    assert result["method"] == "exact binomial"


def test_mcnemar_no_discordance() -> None:
    result = mcnemar_test([True, False], [True, False])
    assert result["p_value"] == 1.0


def test_wilson_interval_hand_value() -> None:
    # 5/10, z=1.959964 -> (0.2366, 0.7634)
    low, high = proportion_ci(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)


def test_position_bias_detects_first_slot_preference() -> None:
    records = [
        {"case_id": f"c{i}", "winner_slot": 1, "winner": "A", "swapped": False}
        for i in range(20)
    ]
    result = position_bias(records)
    assert result.first_slot_rate == pytest.approx(1.0)
    assert result.exact_binomial_p < 1e-4
    assert result.swap_consistency is None


def test_position_bias_swap_consistency() -> None:
    # Judge always prefers response "A" whatever the slot: no position bias in
    # the swap metric, but a 50% first-slot rate when both orders are judged.
    records = [
        {"case_id": "c1", "winner": "A", "swapped": False, "winner_slot": 1},
        {"case_id": "c1", "winner": "A", "swapped": True, "winner_slot": 2},
        {"case_id": "c2", "winner": "A", "swapped": False, "winner_slot": 1},
        {"case_id": "c2", "winner": "B", "swapped": True, "winner_slot": 2},
    ]
    result = position_bias(records)
    assert result.first_slot_rate == pytest.approx(0.5)
    assert result.swap_consistency == pytest.approx(0.5)
    assert result.n_swap_pairs == 2


def test_position_bias_requires_records() -> None:
    with pytest.raises(ValueError):
        position_bias([])


def test_length_bias_perfect_rank_correlation() -> None:
    scores = [1, 2, 3, 4, 5]
    lengths = [100, 200, 300, 400, 500]
    result = length_bias(scores, lengths, n_permutations=200, seed=1)
    assert result.spearman_rho == pytest.approx(1.0)
    assert result.permutation_p <= 0.05


def test_length_bias_constant_input_is_nan() -> None:
    result = length_bias([2, 2, 2], [10, 20, 30], n_permutations=50)
    assert math.isnan(result.spearman_rho)


def test_self_preference_positive_shift() -> None:
    result = self_preference([2.0, 2.0, 1.0, 2.0], [1.0, 1.0, 0.0, 1.0], n_resamples=200, seed=1)
    assert result.ci.estimate == pytest.approx(1.0)
    assert result.ci.low > 0


def test_repeat_agreement_hand_values() -> None:
    # Case 1 unanimous, case 2 two of three, case 3 split 2/1.
    ratings = [["yes", "yes", "yes"], ["no", "no", "yes"], ["a", "a", "b"]]
    result = repeat_agreement(ratings)
    assert result["exact_agreement"] == pytest.approx((1.0 + 2 / 3 + 2 / 3) / 3)
    assert result["unanimous_rate"] == pytest.approx(1 / 3)


def test_repeat_agreement_alpha_is_nan_for_single_repeat() -> None:
    result = repeat_agreement([["yes"], ["no"]])
    assert math.isnan(result["alpha"])


def test_numpy_input_accepted() -> None:
    values = np.array([1.0, 2.0, 3.0])
    result = bootstrap_ci(values, n_resamples=100, seed=1)
    assert result.estimate == pytest.approx(2.0)
