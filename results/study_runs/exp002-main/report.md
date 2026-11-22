# Experiment 002 - judge error correlation and confidence (exploratory)

**Status: exploratory.** This report describes a small, single-dataset measurement of 1 LLM judge(s) grading multiple-choice medical answers. It is not a confirmatory study and it makes no clinical claim.

- Run id: `exp002-main`
- Dataset: `medqa_4options/dev` (60 usable items, 1272 raw rows)
- Licence: cc-by-sa-4.0 (GBaker/MedQA-USMLE-4-options-hf); item text is not committed
- Sample: 60 items, seed 20260101, 0 duplicate stems discarded
- Candidates: 30 built to name the key option, 30 built to name a plausible distractor
- Repeats: 3 per judge per item at temperature 0.3
- Commit: `6a9b09b8c943dab223157d1699f4c5c31a52c129` (dirty: yes)

## Judges

| judge | family | model | status | note |
| --- | --- | --- | --- | --- |
| j1_deepseek | deepseek | `deepseek-flash` | available | remote API judge; answer key never sent |
| j2_ollama | llama | `llama3.2:3b` | **unavailable** | Ollama not reachable at http://127.0.0.1:11434: URLError: <urlopen error timed out> |

**Only one judge was available, so the cross-family question this experiment was designed to answer cannot be answered by this run.** Single-judge metrics below are real measurements of that one judge; they are not a substitute for the cross-judge analysis.

## Results per judge (primary repeat)

| metric | estimate | 95% interval | n |
| --- | --- | --- | --- |
| **j1_deepseek** | | | |
| accuracy vs key | 98.3% | [0.910, 0.997] Wilson | 59 |
| false accept (wrong candidate judged CORRECT) | 0.0% | [0.000, 0.117] Wilson | 29 |
| false reject (correct candidate judged INCORRECT) | 3.3% | [0.006, 0.167] Wilson | 30 |
| Cohen's kappa vs key | 0.966 | [0.894, 1.000] bootstrap | 59 |
| ECE (5 bins) | 0.001 | [0.000, 0.037] bootstrap | 59 |
| Brier score | 0.017 | [0.000, 0.050] bootstrap | 59 |
| AUROC of confidence for own correctness | 0.733 | not computed bootstrap | 59 |
| Gwet AC1 vs key | 0.966 | not computed | 59 |
| same verdict on all repeats | 100.0% | [0.939, 1.000] Wilson | 59 |

Intervals: Wilson score for proportions; percentile bootstrap over items with 1000 resamples for kappa, correlation, ECE, Brier and AUROC. Paired comparisons resample items jointly.

**Data quality.** These judgements could not be scored on every item:
- j1_deepseek: 59/60 scored, 1 parse failure(s), 0 API error(s). 1 item(s) have no parseable verdict and are excluded from accuracy and kappa: `dev-00985`.

## Judge versus judge

No cross-judge statistics were computed: this study needs at least two available judges for any of them. With one judge, error correlation across families is **not measured**, and no proxy for it is reported.

## Possible key errors (manual review only)

Rule applied: **single_judge_fallback** - one judge disagrees at confidence >= 90; reported for manual review only, never as a key error

The two-judge rule was not applicable with one judge; see the single-judge flags below.
No key was changed; nothing here proves a key is correct.

1 item(s) were flagged by the weaker single-judge rule (confidence >= 90). These are candidates for a manual look; they cannot distinguish a wrong key from a wrong judge.
- `dev-00666`: j1_deepseek said INCORRECT at confidence 98 (candidate was correct per the key)

## Effect of the flagged items

1 item(s) flagged. Accuracy excluding them is reported below; if it moves sharply, the key is doing some of the work.

- j1_deepseek excluding flagged items: 100.0% [0.938, 1.000] on 58 items

## Benchmark health

- Items: 60
- Key letter distribution: {'A': 11, 'B': 14, 'C': 23, 'D': 12}
- j1_deepseek ceiling rate: 98.3% [0.910, 0.997] (58/59 items right on every repeat)

**The ceiling is a finding, not a footnote.** At this accuracy almost every item is judged correctly on every repeat, so this sample has little power to separate one judge from another or to populate the both-wrong cell that the cross-judge question depends on. A harder sample, or a judge that actually errs here, is needed before error correlation can be estimated.

Score distribution (primary repeat):

| judge | said CORRECT | said INCORRECT | mean confidence |
| --- | --- | --- | --- |
| j1_deepseek | 29 | 30 | 98.4 |
- j1_deepseek confidence bins: 0-20: 0, 20-40: 0, 40-60: 0, 60-80: 0, 80-100: 59

## Cost and calls

- Network calls: 187; logged judge calls: 187; cache hits: 0
- Tokens: 67727 in, 63682 out, 60940 reasoning
- Estimated cost: US$0.1699 (price source: deepseek-api-docs-peak-2026-11-17)
- Caps: 500 calls, US$5.0
- Wall time: 692.08 s

## Limitations

1. **Small sample.** 60 items, one candidate answer each. Every interval is wide, and a difference of a few percentage points between judges is not interpretable at this size.
2. **The answer key may be wrong.** The key is treated as an imperfect reference. Public medical QA sets are known to contain label errors, missing information and ambiguous items, and no key audit was performed here. An 'error' by the judge may be a defect in the key.
3. **No clinician review.** No physician verified any item, candidate answer, judged verdict or flagged case. The author is not a clinician and no clinician was available.
4. **Possible shared bias between judges.** Both judges are instruction-tuned LLMs, and the candidate answers are assembled from a small fixed template set. Agreement between judges can reflect a shared response to that style rather than shared medical reasoning.
5. **The candidate rationales are template filler.** They are not clinical reasoning and contain no case-specific justification, so this study measures whether a judge recognises the correct option, not whether it can grade an argument.
6. **One dataset, one split, one prompt.** Results do not generalise beyond this sample, this prompt version, or this temperature. A different prompt can move a judge by several points.
7. **Three repeats at one temperature.** Consistency across repeats is measured at a single temperature and does not estimate stability across prompts or sampling schemes.
8. **Exploratory status.** The experiment is labelled exploratory in advance. It is a measurement exercise for a methods toolkit, not a confirmatory test, and no hypothesis test here survives a multiple-comparison correction.
9. **Not a clinical claim.** Nothing in this report is clinically validated, and none of it should be used to make a medical decision or to claim that a model is safe.

## Provenance

- Manifest: `manifest.json` in this directory
- Prompt version: `mcq_judge_v1`
- Package version: 0.2.0
- Python 3.14.6 on Windows-11-10.0.26300-SP0
- Judge resolution: {"j1_deepseek": "api", "j2_ollama": "unavailable: URLError: <urlopen error timed out>"}

## Reproduce

```powershell
python -m medeval run exp002 --n-items 60 --seed 20260101 --repeats 3
```
