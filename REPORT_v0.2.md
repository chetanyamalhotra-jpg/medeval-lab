# REPORT v0.2.0 — Experiment 002 and the local app

**Release status: exploratory. Not peer reviewed. No clinician reviewed any item,
candidate answer, verdict or flagged case. Nothing here is clinically validated.**

Run ids referenced below live in `results/study_runs/`:

| run | what it is | status |
| --- | --- | --- |
| `exp002-main` | the reported Experiment 002 | completed, 180/180 judgements |
| `exp002-smoke` | 5-item stage-2 check of the API path | completed, 15 calls |
| `exp002-full` | first attempt, aborted (truncated replies) | `status: aborted` |
| (dry runs) | mock judges, pipeline tests only | not evidence about any model |

## Plain-language headline

The measurement machinery now runs end to end against a real model. One judge was
available — DeepSeek — and it agreed with the official MedQA key on **58 of 59
scorable items (98.3%, 95% CI 91.0–99.7%)**, with a Cohen's kappa of 0.966.

The question this experiment was designed to answer — *do two judges from
different model families make correlated errors?* — **was not answered.** The
second judge needs a local model, Ollama is not installed on this machine, and the
specification forbids substituting a different judge. That is a blocked
measurement, not a negative finding.

A second result matters more than the accuracy number: **the sample is at
ceiling.** With 98.3% accuracy almost every item is judged correctly on every
repeat, so this 60-item task has almost no power to discriminate between judges or
to populate the both-wrong cell the cross-judge question depends on. Reporting a
correlation here would have produced a number that looks like an answer and is not
one.

## A. What was built

**Experiment 002 as specified** (`experiments/002_judge_families/`, pre-registered
in `PLAN.md` before the first real call):

* MedQA-USMLE 4-option (`GBaker/MedQA-USMLE-4-options-hf`, split `dev`, 1,272
  rows, **cc-by-sa-4.0**), sampled to 60 items with **distinct stems only**, seed
  `20260101`.
* One candidate answer per item: 30 name the key option, 30 name the most
  lexically similar distractor. Six shared templates, chosen by a hash of the item
  id so phrasing cannot leak the condition. **No model generated the candidates** —
  J1 is a DeepSeek model and would otherwise grade its own family's prose.
* Two judges: J1 DeepSeek via API (`deepseek-flash`, which is what the API returned
  for the configured name), J2 a local CPU model via Ollama (`llama3.2:3b`).
* Strict JSON contract (`{"verdict": "CORRECT"|"INCORRECT", "confidence": 0-100}`),
  one retry on a malformed reply, then an honest parse failure. Three repeats per
  item at temperature 0.3.
* Disk cache keyed by model + prompt hash + temperature + seed, and an
  append-only judgement log, so an interrupted run resumes without repeating
  calls. Verified: re-running `exp002-smoke` made **0** network calls.
* Hard caps: `--max-api-calls` (500) and `US$5`, checked before the first call and
  before every call after that.
* Manifest with git commit, package versions, model ids, seeds, prompt hashes and
  timestamps.
* Markdown, JSON and CSV reports with a mandatory Limitations section.

**The local app** (`packages/medeval_web/`, `python -m medeval ui`): FastAPI on
`127.0.0.1` plus hand-written HTML/CSS/JS with no build step and no CDN. Runs list,
new run form with a live token/cost estimate, run detail with server-sent progress
and a streaming log, results with metric cards, confusion matrices and a
reliability diagram, a filterable item explorer with a detail drawer, and a
settings page that reports whether a key is present without ever showing it.

Every page was later rendered in a real browser against the `exp002-main` data,
which is how four defects were found that no API test could see — a command
palette covering the whole screen, an aborted run advertising 100% accuracy,
`j2_ollama` listed as a judge where it never ran, and a cached stylesheet masking
the first fix. All four are fixed and pinned by tests; see `CHANGELOG.md`.

## B. Real-judge findings (J1 only)

Sample: 60 items, 30 candidates correct and 30 incorrect by the official key.
Primary repeat, scored items only (59 of 60; see data quality below).

| metric | estimate | 95% interval | n |
| --- | --- | --- | --- |
| Accuracy vs key | 98.3% | Wilson [91.0%, 99.7%] | 58/59 |
| Cohen's kappa vs key | 0.966 | bootstrap [0.894, 1.000] | 59 |
| Gwet AC1 vs key | 0.966 | not computed | 59 |
| False accept (wrong candidate judged CORRECT) | 0.0% | Wilson [0.0%, 11.7%] | 0/29 |
| False reject (correct candidate judged INCORRECT) | 3.3% | Wilson [0.6%, 16.7%] | 1/30 |
| ECE, 5 bins | 0.001 | bootstrap [0.000, 0.037] | 59 |
| Brier score | 0.017 | bootstrap [0.000, 0.050] | 59 |
| AUROC, confidence predicts own correctness | 0.733 | not computable (1 error) | 59 |
| AUROC, raw verdict predicts truth | 0.983 | not computed | 59 |
| Same verdict on all three repeats | 100.0% | Wilson [93.9%, 100%] | 59/59 |
| Ceiling rate (right on every repeat) | 98.3% | Wilson [91.0%, 99.7%] | 58/59 |

**Calibration must not be over-read.** Every one of the 180 confidence values was
between 95 and 100 (mean 98.4, no value below 95). ECE is small because accuracy is
also very high, not because the judge used the confidence scale well. An instrument
that never expresses doubt cannot flag the cases where it is wrong, which is
exactly what H2 asked. The single error was given at confidence 98.

**Consistency is perfect but uninformative at this ceiling.** Three repeats agreed
on all 59 items; a judge that is right 98% of the time will do that. It does not
show that the judge is stable on questions it finds hard.

**One error, one flag.** `dev-00666` is the only item the judge got wrong: it said
INCORRECT at confidence 98 about a candidate that named the key option. Under the
pre-registered rule this is a *single-judge flag for manual review*, **not** a
possible key error — one judge disagreeing with the key cannot distinguish a wrong
key from a wrong judge. Accuracy excluding that item is 58/58 (100%, Wilson
[93.8%, 100%]), which is exactly why the with/without comparison is reported.

**Data quality.** `dev-00985` returned an empty reply on all three repeats (two
attempts each) and has no verdict. It is excluded from accuracy and kappa and named
in the report. Seven calls in total needed the single retry; six recovered.

**Cross-family error correlation: not measured.** J2 could not start — Ollama is
not installed and nothing answers on `127.0.0.1:11434`, so the probe failed and the
run correctly recorded `j2_ollama: unavailable` rather than quietly running two
prompts from one model family. No phi coefficient, no both-wrong rate and no
McNemar result exists for this experiment, and no proxy for them is offered.

## C. Why the first attempt was aborted

The first full run (`exp002-full`) made 204 calls and 42 of them came back empty.
The cause was mechanical: the provider's thinking mode spends the `max_tokens`
budget on hidden reasoning, so with `max_tokens=512` every one of those replies had
`output_tokens == reasoning_tokens == 512` and no visible JSON. Seven of 180
judgements were lost, and all 53 survivors agreed with the key — a subset biased
towards the items the model finished quickly.

`MEDEVAL_MAX_TOKENS` was raised to 2048, the deviation was written into the
pre-registration's deviations log, the aborted run was kept with
`status: aborted` and its reason, and a fresh run was executed from scratch.
Nothing else changed: same sample, seed, prompt version, temperature, repeats,
metrics and decision rules. No number from the aborted run is reported.

## D. Benchmark health

| item | value |
| --- | --- |
| Items | 60 (distinct stems, seed 20260101) |
| Key letters | A 11, B 14, C 23, D 12 |
| Candidates built | 30 correct, 30 incorrect |
| Ceiling rate | 98.3% — the sample cannot separate judges |
| Duplicate stems discarded before drawing | 0 |

This is the finding a benchmark paper should care about as much as the accuracy:
a 60-item MedQA slice that a current model passes at 98% is a poor instrument for
studying judge error correlation, because there are almost no errors to correlate.

## E. Cost

| item | value |
| --- | --- |
| Network calls | 187 (180 planned + 7 retries) |
| Cache hits | 0 (nothing was re-requested) |
| Input / output / reasoning tokens | 67,727 / 63,682 / 60,940 |
| Estimated spend | **US$0.1699** at DeepSeek's published peak rates |
| Price source | `https://api-docs.deepseek.com/quick_start/pricing`, read 2026-11-17 |
| Wall time | 692 s |

Peak prices were used deliberately (`$0.30`/M input, `$1.20`/M output for
`deepseek-flash`), so the estimate is an upper bound for that run's token counts.
The USD figure is an estimate from token counts and published prices; it is not a
provider invoice.

## F. What this does not support

* Any claim about cross-family error correlation (J2 did not run).
* Any claim that the judge is calibrated: its confidence never left 95–100.
* Any claim that a benchmark key is wrong. The one disagreement is a flag for a
  human, and no human has looked at it.
* Any claim that 98.3% generalises: one benchmark, one split, one prompt, one
  temperature, 60 items, and a ceiling that leaves no room for discrimination.
* Any clinical claim. No clinician was involved at any stage.

## G. How to reproduce

```powershell
cd <repo>
.\.venv\Scripts\python.exe -m medeval run exp002 --n-items 60 --seed 20260101 --repeats 3
.\.venv\Scripts\python.exe -m medeval rebuild exp002-main   # reanalyse, no model calls
.\.venv\Scripts\python.exe -m medeval run exp002 --dry-run   # mock judges, no network
.\.venv\Scripts\python.exe -m medeval ui                     # local app
```

The committed run directory contains `metrics.json`, `report.md`, `items.csv`,
`judgements.jsonl`, `manifest.json` and `run.json`. The benchmark item text, the
candidate responses and the full call log are deliberately **not** committed:
MedQA-USMLE is share-alike and this repository does not redistribute it.

**Provenance note.** `exp002-main` ran from a working tree at commit `6a9b09b` with
uncommitted reporting, test and UI changes, so its manifest records `dirty: true`
and lists the files. The modules that decide a verdict — prompt rendering,
sampling, candidate construction, the judge runner and the transport — differ
between that commit and the release commit only by import style, a ternary rewrite,
`.encode()` relying on its UTF-8 default, and `int(round(x))` becoming `round(x)`.
No prompt string, seed, template, threshold or parsing rule changed, so the stored
verdicts correspond exactly to the committed judge code. `medeval rebuild
exp002-main` re-derives every number from those stored verdicts with no model call.
