# Experiment 003 — frontier judges on a difficulty-enriched MedQA sample (pre-registration)

Status: **exploratory**. This document is committed **before** any code that runs
the experiment exists. Deviations go in the log at the bottom; the text above it
is never edited after the fact.

Date written: 2026-11-18.

## Why this design

Experiment 002 showed the problem this experiment is built around: a strong judge
on a random MedQA sample sits at the ceiling (58 of 59 scored items correct,
confidence always 95–100), leaving almost no errors to correlate. So the item
pool is **enriched for difficulty by a rule fixed in advance**: a screening model
answers many items closed-book, and the pool oversamples the items it got wrong.

This enrichment has a consequence that is stated here, up front, and belongs in
every report of this experiment: **hard-pool items are more likely to contain key
errors** (that is partly *why* they are hard). So judge-versus-key disagreement on
hard-pool items is **not the same as judge error**. It is exactly the interesting
signal — but it needs human review before anyone calls it an error by either side.

## Question

With three or more strong judges from different model families:

1. how often does each disagree with the key;
2. how often do they disagree with each other;
3. are their disagreements correlated (the v0.2 question, finally measurable if
   there are enough errors);
4. does stated confidence separate their right verdicts from their wrong ones;
5. how many items do the judges **unanimously dispute against the key** (the
   review queue).

## Data and selection (all rules fixed before any call)

* Source: `GBaker/MedQA-USMLE-4-options-hf`, split `dev` — the same loader and
  licence handling as v0.2 (cc-by-sa-4.0; item text is **never redistributed**;
  git-ignored run files hold the text, committed files hold ids, verdicts,
  confidences and aggregates).
* **New seed** for this experiment: `20261118` (distinct from Experiment 002's
  `20260101`).
* **Exclusion:** every item id used in Experiment 002 (the 60 sampled there) is
  excluded from the pool before screening. The exclusion list comes from
  `results/study_runs/exp002-main/items.jsonl` (committed run record) and is
  re-derivable from the sampling code.
* **Screening step:** the connection in the **Screening role** answers
  N_screen = 600 items closed-book with a letter-only prompt (temperature 0).
  Only item ids and correct/incorrect flags are saved, in a committed file
  (`results/experiment_003/screening.jsonl`). The screening model's key lives in
  the app's memory only, like every judge key. Screening is run by the owner from
  the app; the agent never makes the live calls.
* **Hard pool** = items the screening model got wrong. **Stratified sample:**
  100 items from the hard pool plus 40 items sampled from the rest (the control
  stratum), seed `20261118`. If the hard pool has fewer than 100 items, all of it
  is used and the shortfall is recorded.
* Candidates: the **same fixed-template builder as v0.2** — half the items get a
  candidate naming the key option, half get the most lexically similar distractor;
  six shared templates chosen by a hash of the item id. No model writes candidates.

## Judges

Three or more saved connections in Judge roles, from **different model families**
(the owner sets `family_label` per connection; the app warns when two judges
share a provider or family label and the report repeats the warning). The
screening connection must not also be a judge (blocked in the UI, validated in
the runner).

Each judge receives the question, the four options and the candidate answer —
never the key — and must return
`{"verdict": "CORRECT"|"INCORRECT", "confidence": <0-100>}`. One retry on
malformed output, then an honest `failure_type` on the judgement.

## Pilot gate (pre-registered)

Before spending on the full sample: run the **first 40 hard-pool items** through
**all** judges. Count every judge-versus-key disagreement across all judges on
those items.

* If the total is **below 5**, **stop**. Report the ceiling, state that the
  difficulty enrichment did not produce enough signal, and make **no correlation
  claim** of any kind.
* Otherwise continue with the remaining items.

The gate exists so the owner does not pay for a full run that cannot answer the
question. It is a stopping rule, not an analysis result.

## Metrics (all with intervals; Wilson for proportions, percentile bootstrap with
1,000 resamples over items for the rest; no normal approximations anywhere)

* Per judge: accuracy against the key (both variants from v0.3 — failures
  excluded, and failures counted as wrong), false-accept rate, false-reject rate,
  Cohen's kappa.
* Per judge pair: phi coefficient between error indicators, observed both-wrong
  count against the count expected under independence (binomial **and**
  permutation test), McNemar on error patterns.
* Stratified: every judge metric and pair statistic is computed **separately for
  the hard pool and the random control**, because a difference between strata is
  itself a finding about where the difficulty lives.
* Confidence: ECE (5 bins), Brier, AUROC of confidence predicting own correctness,
  and **the share of confidences at or above 95 — flagged in the report if nearly
  all of them are** (the v0.2 failure mode, watched for explicitly).
* Consistency: on the consistency subsample if the owner enabled it.
* Cost and tokens per judge, from the call log.
* **No multiple-comparison claims.** With this many judges and pairs, nothing here
  survives a correction; the report says so instead of picking a favourite.

## Flag rule ("Flagged for review")

An item is **flagged for review** when at least two judges, **and all judges that
returned a verdict for that item**, disagree with the key with confidence ≥ 90 on
every repeat. The flags are written to `review_queue.csv` containing **item ids
only** — a prompt for a human to look, never a finding that a key is wrong, and
the report says so in the same words as v0.2.

## Single-reviewer notes (local only, not evidence)

In the item drawer the owner may record a label — `key looks correct`,
`key looks wrong`, `ambiguous`, `unsure` — and a free-text note. These live in a
**git-ignored local file** and are displayed **everywhere** with the caveat that
they are one reviewer's opinion: not clinical validation, not a reference
standard, not a second rater. There is no clinician in this project.

## Auto-generated limitations (mandatory in every report)

1. **Key noise.** The official key is treated as an imperfect reference; hard-pool
   selection makes key errors *more likely*, so disagreement is not error.
2. **Hard-pool selection by a screening model.** The strata are defined by
   whatever the screening model happened to get wrong, including its own blind
   spots; the "hard" stratum is not a validated difficulty measure.
3. **Judges share training data.** Different model families are not independent
   instruments; correlated error may reflect shared pretraining text rather than
   shared reasoning.
4. **Template-filler candidates.** Rationales are fixed templates with no
   case-specific clinical content; this measures whether a judge recognises the
   correct option, not whether it can grade an argument.
5. **Single reviewer.** Any review note is one person's opinion, with no
   inter-rater check.
6. **Small sample.** ~140 items; every interval is wide; nothing survives a
   multiple-comparison correction.
7. **Exploratory.** A measurement exercise for the toolkit; no confirmatory test,
   no clinical claim, no model ranking.

## Stopping rules and spend

* The pilot gate above (5 disagreements on 40 hard items, or stop).
* Per-connection caps from each judge's connection, plus the run-level
  `--max-api-calls` and USD cap, enforced before every call by the chained guard.
* The agent never makes live calls; every live stage is run by the owner from the
  app, which shows the plan and requires explicit confirmation.

## What the results can and cannot support

Can support: descriptive statements about how often these specific judges
disagreed with this key and with each other on this enriched sample, whether
their errors co-occurred, and what a human should review first.

Cannot support: clinical validity, key-error claims, model ranking, a composite
score, independence claims between judges, or generalisation beyond this sample,
these prompts and this temperature.

## Deviations log

(append-only)

### 2026-11-19 — amendment 1: adaptive screening instead of a fixed 600 items

**Status: pre-data.** No screening call and no judge call has been made for this
experiment (the only runs so far are dry runs with mock judges and a mock
screener). This amendment is therefore part of the design, not a response to
anything observed.

**Why.** The plan above fixed N_screen = 600 items. A strong screening model on
MedQA dev may miss only 5–10 % of items, which at 600 screened items yields
roughly 30–60 hard items — well short of the 100 the stratification needs, while
still paying for 600 calls. A sequential rule reaches the target at the lowest
expected cost and stops as soon as the target is met.

**Amended screening rule (fixed before any call).**

1. Eligible items = MedQA-USMLE 4-option `dev` minus every item id used in
   Experiment 002 (unchanged).
2. Shuffle the eligible items with the pre-registered seed `20261118`.
3. Screen in **batches of 50**. After each batch, count hard items (items the
   screening model answered incorrectly).
4. **Stop screening** when any of these holds, and record which one:
   * `target_met` — 100 hard items found;
   * `exhausted` — every eligible item has been screened;
   * `cap_reached` — the screening cap is hit. The cap is the lesser of 1 000
     screened items and the screening connection's own call cap / the run's call
     cap, whichever binds first.
5. **Control stratum:** 40 items drawn at random, with seed `20261118`, from the
   screened items the model answered **correctly**. (The previous text said
   "from the rest"; this makes it explicit that controls are screened-correct
   items, so both strata come from the same screened pool.)
6. **If fewer than 100 hard items are found**, all of them are used and the
   shortfall is stated in the report (unchanged).
7. The **pilot gate is unchanged**: stop unless there are at least 5
   judge-versus-key disagreements on the first 40 hard items.
8. The screening model must not be a judge; its `family_label` must differ from
   every judge's family label (the app warns, and blocks when identical).

**Staged execution (unchanged in substance, now explicit).** Experiment 003 runs
as confirmed stages — Screening, Review hard pool, Pilot (40), Gate decision,
Remaining items — each showing its worst-case cost first and requiring an
explicit confirmation; the owner may stop after any stage and resume later.
Real screening results commit item ids and correct/incorrect flags only, to
`results/experiment_003/screening.jsonl`; dry-run screening stays inside the run
directory and is labelled as a mock.

**What this amendment does not change.** The sample's size once screening
finishes (100 hard + 40 control, or less if the hard pool is smaller), the seed,
the exclusion of Experiment 002 items, the candidate builder, the judge prompt,
the pilot gate, the metrics, the flag rule, and the reporting language.

