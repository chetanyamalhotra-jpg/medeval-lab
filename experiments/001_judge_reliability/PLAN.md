# Experiment 001 — Judge reliability under planted error

**Status:** pre-registered before execution (see §7 for deviations actually taken).
**Version:** v0.1.0 · **Toolkit:** medeval-lab 0.1.0 · **Criteria:** v0.1 · **Rubric:** v0.1

## 1. Question

When an LLM or a rule system is used as a *measurement instrument* for medical
answer safety, how much can its verdicts be trusted, and how much of what it
reports is an artefact of presentation order, length, or seed?

This experiment tests the measurement instrument, not any model's medical
ability. It does **not** produce a clinical claim and cannot validate a judge for
clinical use.

## 2. Hypotheses (fixed before running)

- **H1 (rule judge is better than chance).** The deterministic rule judge
  identifies the planted-error response with accuracy above 0.5, and its misses
  concentrate in error types its rule file does not cover.
- **H2 (judges measure different things).** Agreement between the rule judge and
  an LLM-style judge, measured by Cohen's kappa on the same 50 cases, is below
  0.6. A high value would suggest the rule judge is a proxy for the LLM judge; a
  low value means they cannot be substituted.
- **H3 (position bias is detectable).** A judge with an injected first-slot
  preference (negative control) shows a first-slot rate that excludes 0.5, while
  unbiased judges do not.
- **H4 (self-consistency differs by instrument).** The deterministic rule judge
  is exactly self-consistent across seeds (agreement 1.0). A sampling judge
  (simulated here) is not, by construction.
- **H5 (the analysis recovers known parameters).** Bootstrap confidence
  intervals for the simulated judges' accuracy contain the accuracy configured in
  the simulation, and the point estimates are within Monte-Carlo error of it.

## 3. Data

- 50 synthetic clinical cases (`experiments/001_judge_reliability/data/case_specs_v0.1.json`),
  each pairing a clinically appropriate response with one containing a single
  planted error.
- Error types and counts: wrong_diagnosis 15, contraindicated_drug 10,
  wrong_dose 10, unsafe_recommendation 8, critical_omission 7.
- Items are original wording, marked `label_source: synthetic`, with no DOI. No
  patient data. Nothing in this set is clinically validated.
- Reference standard: **planted ground truth** (the construction of the case
  defines which response is worse). This is one of the three permitted
  reference standards; it says nothing about real world performance.

## 4. Judges (configurations, fixed before running)

| id | instrument | backend | notes |
|---|---|---|---|
| `rule_based` | deterministic rule file v0.1 | no model | rules cover contraindications, dose ceilings, omissions |
| `llm_free_mock` | rubric judge, reference-free | MOCK | simulated judge, accuracy 0.75 |
| `llm_guided_mock` | rubric judge, reference-guided | MOCK | simulated judge, accuracy 0.80 |
| `jury_mock` | majority vote of the two mock judges | MOCK | aggregation check |
| `llm_biased_mock` | rubric judge with injected position preference | MOCK | negative control, first-slot rate 0.8 |

All LLM-family judges go through the single OpenAI-compatible client. With no API
key, no local server and no CPU model installed, the client resolves to the MOCK
backend; every mock verdict is stamped PIPELINE TEST ONLY.

## 5. Metrics and analysis (fixed before running)

- **Primary:** accuracy against planted ground truth, 95% BCa bootstrap interval
  (10 000 resamples, seed 20260101), per judge; subgroup accuracy by error type.
- **Agreement:** pairwise Cohen's kappa and percent agreement on the 50 cases.
- **Self-consistency:** three seeds (20260101–20260103); exact agreement and
  Krippendorff's alpha (nominal) across seeds.
- **Position bias:** every case judged in both presentation orders; first-slot
  rate with exact binomial test, and swap consistency (same response chosen in
  both orders).
- **Length:** paired difference between the length of the response called worse
  and the other response, with a bootstrap interval (positive = prefers longer).
- **Paired comparisons:** McNemar exact test between judge pairs.
- No composite score is computed or reported.

## 6. Stopping rules

- Exactly 50 cases, exactly the five configurations above, no interim analysis.
- No case is excluded after seeing results. A failed call stays in the data as an
  error and is counted.
- If a backend other than MOCK becomes available before the run, it is used and
  the configuration table is updated with the real backend name; otherwise the
  run proceeds on MOCK and every conclusion is restricted accordingly.

## 7. What the results can and cannot support

**Can support:** whether the analysis code recovers known parameters; whether the
rule judge's coverage of planted error types is adequate; whether position bias
and seed sensitivity are measurable at n=50; how wide 50-case intervals are.

**Cannot support:** any statement about a real LLM judge's medical reliability;
any clinical claim; any generalisation beyond these 50 synthetic cases.

## 8. Deviations log

Deviations, if any, are recorded in the generated
`results/experiment_001/report.md` under "Deviations" and in `VERIFY_ME.md`.
