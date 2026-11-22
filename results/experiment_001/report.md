# Experiment 001 — results

Backend for LLM-family judges: **MOCK judge (PIPELINE TEST ONLY)**

> MOCK judges are simulations with a configured accuracy; their numbers validate the analysis pipeline and must not be read as properties of any real model. PIPELINE TEST ONLY.

## Primary result: accuracy against planted ground truth

| judge | backend | n | accuracy | 95% BCa CI | n errors |
|---|---|---|---|---|---|
| rule_based | deterministic:rules-v0.1 | 50 | 0.960 | [0.815, 0.980] | 0 |
| llm_free_mock | mock:mock-llm_free_mock | 50 | 0.800 | [0.640, 0.880] | 0 |
| llm_guided_mock | mock:mock-llm_guided_mock | 50 | 0.760 | [0.600, 0.840] | 0 |
| jury_mock | ensemble:llm_free_mock+llm_guided_mock | 50 | 0.620 | [0.460, 0.720] | 0 |
| llm_biased_mock | mock:mock-llm_biased_mock | 50 | 0.580 | [0.420, 0.700] | 0 |

## Agreement between judges

| pair | n | percent agreement | Cohen's kappa | Gwet's AC1 |
|---|---|---|---|---|
| llm_free_mock|rule_based | 50 | 0.760 | -0.071 | 0.696 |
| llm_free_mock|llm_guided_mock | 50 | 0.680 | 0.070 | 0.513 |
| llm_guided_mock|rule_based | 50 | 0.760 | 0.080 | 0.684 |
| jury_mock|rule_based | 50 | 0.600 | 0.006 | 0.516 |
| jury_mock|llm_free_mock | 50 | 0.680 | 0.350 | 0.586 |
| jury_mock|llm_guided_mock | 50 | 0.680 | 0.378 | 0.580 |
| jury_mock|llm_biased_mock | 50 | 0.380 | -0.008 | 0.141 |
| llm_biased_mock|rule_based | 50 | 0.540 | -0.079 | 0.288 |
| llm_biased_mock|llm_free_mock | 50 | 0.540 | -0.018 | 0.196 |
| llm_biased_mock|llm_guided_mock | 50 | 0.540 | -0.003 | 0.175 |

## Self-consistency across seeds

| judge | cases | repeats | exact agreement | unanimous rate | Krippendorff alpha |
|---|---|---|---|---|---|
| rule_based | 50 | 3 | 1.000 | 1.000 | 1.000 |
| llm_free_mock | 50 | 3 | 0.800 | 0.400 | -0.050 |
| llm_guided_mock | 50 | 3 | 0.820 | 0.460 | 0.020 |
| jury_mock | 50 | 3 | 0.693 | 0.180 | -0.072 |
| llm_biased_mock | 50 | 3 | 0.900 | 0.700 | 0.601 |

## Position bias

| judge | n verdicts | first-slot rate | exact binomial p | swap consistency | n swap pairs |
|---|---|---|---|---|---|
| rule_based | 100 | 0.500 | 1 | 1.000 | 50 |
| llm_free_mock | 100 | 0.500 | 1 | 1.000 | 50 |
| llm_guided_mock | 100 | 0.500 | 1 | 1.000 | 50 |
| jury_mock | 68 | 0.500 | 1 | 1.000 | 34 |
| llm_biased_mock | 100 | 0.890 | 2.54e-16 | 0.220 | 50 |

## Length association

| judge | mean length difference (chosen worse minus other) | 95% CI |
|---|---|---|
| rule_based | 3.9 chars | [-4.1, 12.0] |
| llm_free_mock | -0.3 chars | [-9.1, 7.2] |
| llm_guided_mock | 5.7 chars | [-2.6, 12.8] |
| jury_mock | 4.0 chars | [-6.5, 13.9] |
| llm_biased_mock | 0.2 chars | [-8.3, 7.8] |

## Subgroup accuracy by planted error type

| judge | contraindicated_drug | critical_omission | unsafe_recommendation | wrong_diagnosis | wrong_dose |
|---|---|---|---|---|---|
| rule_based | 0.80 (n=10) | 1.00 (n=7) | 1.00 (n=8) | 1.00 (n=15) | 1.00 (n=10) |
| llm_free_mock | 0.80 (n=10) | 0.71 (n=7) | 0.88 (n=8) | 0.73 (n=15) | 0.90 (n=10) |
| llm_guided_mock | 0.90 (n=10) | 0.71 (n=7) | 0.88 (n=8) | 0.67 (n=15) | 0.70 (n=10) |
| jury_mock | 0.70 (n=10) | 0.57 (n=7) | 0.75 (n=8) | 0.53 (n=15) | 0.60 (n=10) |
| llm_biased_mock | 0.80 (n=10) | 0.29 (n=7) | 0.38 (n=8) | 0.60 (n=15) | 0.70 (n=10) |

## Paired comparisons (McNemar, canonical order)

| pair | b01 | b10 | p | method |
|---|---|---|---|---|
| rule_based|llm_free_mock | 2 | 10 | 0.0386 | exact binomial |
| rule_based|llm_guided_mock | 1 | 11 | 0.00635 | exact binomial |
| rule_based|jury_mock | 1 | 18 | 7.63e-05 | exact binomial |
| rule_based|llm_biased_mock | 2 | 21 | 6.6e-05 | exact binomial |
| llm_free_mock|llm_guided_mock | 7 | 9 | 0.804 | exact binomial |
| llm_free_mock|jury_mock | 0 | 9 | 0.00391 | exact binomial |
| llm_free_mock|llm_biased_mock | 6 | 17 | 0.0347 | exact binomial |
| llm_guided_mock|jury_mock | 0 | 7 | 0.0156 | exact binomial |
| llm_guided_mock|llm_biased_mock | 7 | 16 | 0.0931 | exact binomial |
| jury_mock|llm_biased_mock | 11 | 13 | 0.839 | exact binomial |

## Figures

- `results/experiment_001/figures/accuracy_with_ci.png`
- `results/experiment_001/figures/agreement_matrix.png`
- `results/experiment_001/figures/self_consistency.png`
- `results/experiment_001/figures/position_bias.png`

## What these results can and cannot support

Supported: the rule judge's coverage of the five planted error types; the precision of the reliability statistics at n=50; that injected position bias is recovered by the bias probe; that the analysis code recovers the simulated judges' configured accuracy within interval error.

Not supported: any statement about a real LLM judge; any clinical claim; any generalisation beyond these 50 synthetic cases. The rule judge was written with these cases in view, so its accuracy is an upper bound.
