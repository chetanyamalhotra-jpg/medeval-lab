# Which statistic, and when it will mislead you

A short field guide to `medeval_reliability`. Every formula below is implemented
in `packages/medeval_reliability/` and tested against a hand-computed example in
`tests/test_reliability.py`.

## Agreement between two or more raters

| Situation | Use | Do not use |
|---|---|---|
| 2 raters, nominal, balanced prevalence | Cohen's kappa | — |
| 2 raters, one category dominant (>80%) | kappa **and** Gwet's AC1 **and** percent agreement | kappa alone |
| ≥3 raters, fixed number per item | Fleiss' kappa | Cohen's kappa |
| ≥3 raters, missing ratings | Krippendorff's alpha | Fleiss' kappa |
| Ordered categories | Krippendorff's alpha (ordinal) | nominal kappa |
| Continuous scores | ICC(2,1) agreement; ICC(3,1) consistency | Pearson r |

**The prevalence paradox is not theoretical.** In Experiment 001 the rule judge and
a mock judge agreed on 76% of 50 cases, yet Cohen's kappa was −0.07 because both
were usually right and the marginals were skewed. Gwet's AC1 on the same data was
0.70. Report all three; interpret none alone.

## Calibration

- **Brier** `mean((p − y)²)` — proper, decomposable per item, comparable across
  models with the same items.
- **ECE** `Σ_b (n_b/n)·|acc_b − conf_b|` — intuitive but binning-dependent,
  biased at small n, and **not decomposable**, so it cannot be a harness metric.
  Always report bin count and n.
- **NLL** — proper, punishes confident errors hardest; reported beside Brier.

## Uncertainty

- **Percentile bootstrap** — defaults for exploratory numbers.
- **BCa bootstrap** — primary intervals in Experiment 001; corrects bias and skew.
- **Cluster bootstrap** — use when items share a stem or belong to a perturbation
  group; resampling correlated items understates the interval.
- **Wilson interval** — proportions with small n; better than Wald near 0/1.
- **Paired bootstrap / McNemar** — two systems on the *same* items. Unpaired
  comparisons throw away the correlation and widen intervals needlessly.

## Bias probes

- **Position** — first-slot rate with an exact binomial test, plus *swap
  consistency* (does the same response win in both orders?). The second is the
  stronger evidence; the first is the symptom.
- **Length** — Spearman correlation between length and score with a permutation
  p-value. A null result at n=50 is not evidence of no bias; report the interval.
- **Self-preference** — paired mean difference between a judge's scores on its own
  family's text and others', with a bootstrap interval.
- **Self-consistency** — repeat with several seeds and report exact agreement and
  Krippendorff's alpha across repeats.

## Hard rules in this codebase

1. Undefined statistics return `nan`, never a fabricated number (e.g. kappa when
   one category is used exclusively).
2. Missing ratings are excluded and counted; they are never treated as agreement.
3. No composite score is used for a conclusion.
4. Every interval records its seed, method and number of resamples.
