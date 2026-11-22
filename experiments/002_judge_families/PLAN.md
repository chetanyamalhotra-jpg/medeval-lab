# Experiment 002 — judge error correlation and confidence (pre-registration)

Status: **exploratory**. This document is written and committed *before* the first
real judge call. Deviations are appended to the log at the bottom; the original
text is not edited after the fact.

Date written: 2026-11-17. Commit with this file: see `git log -- experiments/002_judge_families/PLAN.md`.

## Question and hypotheses

**Question.** Do two judges from different model families make correlated errors
when grading medical answers, and does judge confidence flag the cases where they
are wrong?

**H1 (error correlation).** The two judges' error indicators are positively
correlated: the observed both-wrong rate exceeds the rate implied by independent
errors at the same marginal error rates.

**H2 (confidence is informative).** Within a judge, confidence is higher when the
verdict is right than when it is wrong: AUROC of confidence for own correctness
above 0.5, with a bootstrap interval excluding 0.5.

**H3 (calibration is imperfect).** Expected calibration error is materially above
zero, i.e. stated confidence is not a reliable probability.

These are directional expectations, not confirmatory tests. With 60 items, no
result here is powered to detect a small effect, and all three can fail for
reasons that have nothing to do with model quality (bad keys, ambiguous items).

## Data

* Source: `GBaker/MedQA-USMLE-4-options-hf`, split `dev`, 1,272 rows.
* Licence: **cc-by-sa-4.0**, read from the Hugging Face dataset API
  (`https://huggingface.co/api/datasets/GBaker/MedQA-USMLE-4-options-hf`). The
  item text is **not redistributed**: it stays in a git-ignored run directory and
  only item ids, verdicts and hashes are committed.
* Sample: 60 items, seed `20260101`. The pool is first de-duplicated by
  normalised stem (lowest row index wins), then ordered by
  `sha256(seed:draw:item_id)` and the first 60 taken. Duplicate stems discarded
  are reported.
* No MedMCQA data is used. No item-integrity analysis of MedMCQA is performed, in
  line with the OSF registration.

## Candidate answers

Each item gets exactly one candidate answer, built without any model:

* 30 items are answered with the key option (**correct** condition), 30 with a
  distractor (**incorrect** condition). The split is decided by a seed
  independent of the sampling draw, so condition cannot correlate with position.
* The distractor is the wrong option with the highest content-word Jaccard overlap
  with the key option (a "plausible" adjacent differential); ties and zero-overlap
  cases are broken by a seeded hash and labelled as such.
* The rationale is one of six fixed templates, selected by a hash of the item id.
  The same six templates are used for both conditions, so phrasing cannot leak the
  condition. The rationale contains no case-specific clinical content and is
  described as template filler in every report.
* DeepSeek is never used to generate the candidate text (J1 is a DeepSeek model
  and would otherwise be judging its own family's prose).

## Judges

| id | family | default | runtime |
| --- | --- | --- | --- |
| `j1_deepseek` | DeepSeek | `deepseek-flash` (the documented current name; `deepseek-chat` is accepted and resolves to the same model) | remote API |
| `j2_ollama` | Llama | `llama3.2:3b` | local, CPU, Ollama |

Both receive the question, the four options and the candidate answer — never the
key. They must reply with exactly
`{"verdict": "CORRECT" | "INCORRECT", "confidence": <integer 0-100>}`.

* Temperature 0.3, `max_tokens` 512, three repeats per item per judge with
  `seed = base_seed + repeat_index`.
* A malformed reply is re-asked **once** with the identical prompt (a different
  cache salt, so it is a genuine second call). A second failure is recorded as a
  parse failure and the item stays in the denominator of nothing: parse failures
  are reported separately and excluded from accuracy, which is itself stated as
  accuracy over scored items.
* API errors are recorded per item with the redacted error string.

## Metrics and estimators

* Proportions (accuracy, false accept, false reject, consistency, ceiling): Wilson
  95% intervals.
* Cohen's kappa (judge vs key; J1 vs J2), phi coefficient between error
  indicators, ECE over 5 bins, Brier score, AUROC: **percentile bootstrap over
  items, 1,000 resamples**, pairs resampled jointly.
* Both-wrong rate versus independence: observed, expected under
  `p1 × p2`, excess ratio, a model-based binomial p-value (anti-conservative,
  because the marginals are estimated from the same data) **and** a permutation
  test that shuffles one judge's error indicators, preserving both marginals.
* Paired comparisons: exact McNemar for error-pattern asymmetry.
* Consistency: fraction of items with the identical verdict across all repeats.
* Benchmark health: ceiling rate (items right on every repeat) and the score
  distribution (verdict counts, confidence histogram).
* No normal-approximation interval is used anywhere.

## Decision rules

* H1 is called **supported** only if the phi interval excludes 0 and the
  permutation p-value is below 0.05. Otherwise it is reported as "not
  supported" or "inconclusive" by interval width, never as a null finding.
* H2 is called **supported** only if the AUROC interval excludes 0.5.
* If more than 10% of calls fail to parse, the results are reported as
  **unreliable** and no headline number is given.
* Items where both judges confidently disagree with the key (both ≥ 80) are
  flagged as **possible key errors** for manual review. Results are reported both
  with and without them. No key is ever changed.

## Stopping rules

* Hard cap: `--max-api-calls` (default 500) and `US$5`. The plan is refused before
  any call if it exceeds the cap; the guard stops the run mid-flight if retries
  would cross it, and the run is then marked `budget_stopped` and remains
  resumable.
* Stage 1: dry run with mock judges (no network).
* Stage 2: 5-item real run to prove the API path and parsing.
* Stage 3: the full 60-item run, only if stage 2 produced zero API errors.

## Planned deviation already known at pre-registration

Ollama is **not installed** on the execution machine at the time of writing
(probed: no `ollama` on `PATH`, nothing listening on `127.0.0.1:11434`). The
specification says such a run must fail with instructions rather than substitute
a judge, and the owner has elected to leave J2 blocked for now.

**Consequence, declared in advance:** the run will execute with J1 only. H1
cannot be tested and will be reported as **not measured**, not as "no
correlation". H2 and H3 are unaffected because they are within-judge properties.
A single-judge, weaker flag for manual review replaces the two-judge
possible-key-error rule and is labelled as such.

## What the results can and cannot support

Can support: statements about how often this one judge agreed with this key on
this sample, whether its confidence separated its right from its wrong verdicts,
and how consistent it was across three repeats at one temperature.

Cannot support: clinical validity, medical safety, model ranking, a claim that a
benchmark key is wrong, cross-family error correlation (blocked), generalisation
beyond this sample, or any composite score.

## Deviations log

(append-only; empty at pre-registration)

**2026-11-17 — truncation defect, run re-executed (before any result was
interpreted).** The first full execution (`exp002-full`) made 204 calls, of which
42 returned an empty reply: `output_tokens == reasoning_tokens == 512` on every
one. The provider's thinking mode spends the `max_tokens` budget on hidden
reasoning, so with `max_tokens=512` no visible JSON was emitted on the harder
items. Seven of 180 judgements were lost, and all 53 surviving judgements agreed
with the key — a subset that is plausibly biased towards the items the model
finished quickly.

Deviation: `MEDEVAL_MAX_TOKENS` raised from **512 to 2048** so that a reply can
contain reasoning *and* the JSON object. Nothing else changed: the sample, seed,
prompt version, temperature, repeats, metrics and decision rules are identical,
and no result from the aborted run was used. The aborted run is kept in
`results/study_runs/exp002-full` with `status: aborted` and the reason recorded in
its `run.json`; a fresh run was executed end to end.
