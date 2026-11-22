# Experiment 002 - judge error correlation and confidence (exploratory)

**Status: exploratory.** This report describes a small, single-dataset measurement of two LLM judges grading multiple-choice medical answers. It is not a confirmatory study and it makes no clinical claim.

- Run id: `exp002-smoke`
- Dataset: `medqa_4options/dev` (1272 usable items, 1272 raw rows)
- Licence: cc-by-sa-4.0 (GBaker/MedQA-USMLE-4-options-hf); item text is not committed
- Sample: 5 items, seed 20260101, 0 duplicate stems discarded
- Candidates: 3 built to name the key option, 2 built to name a plausible distractor
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
| accuracy vs key | 100.0% | [0.566, 1.000] Wilson | 5 |
| false accept (wrong candidate judged CORRECT) | 0.0% | [0.000, 0.658] Wilson | 2 |
| false reject (correct candidate judged INCORRECT) | 0.0% | [0.000, 0.561] Wilson | 3 |
| Cohen's kappa vs key | 1.000 | not computed bootstrap | 5 |
| ECE (5 bins) | 0.008 | [0.004, 0.010] bootstrap | 5 |
| Brier score | 0.000 | [0.000, 0.000] bootstrap | 5 |
| AUROC of confidence for own correctness | not computed | not computed bootstrap | 5 |
| Gwet AC1 vs key | 1.000 | not computed | 5 |
| same verdict on all repeats | 100.0% | [0.566, 1.000] Wilson | 5 |

Intervals: Wilson score for proportions; percentile bootstrap over items with 1000 resamples for kappa, correlation, ECE, Brier and AUROC. Paired comparisons resample items jointly.

## Judge versus judge

No cross-judge statistics were computed: this study needs at least two available judges for any of them. With one judge, error correlation across families is **not measured**, and no proxy for it is reported.

## Possible key errors (manual review only)

Rule applied: **single_judge_fallback** - one judge disagrees at confidence >= 90; reported for manual review only, never as a key error

No item met the two-judge rule. No key was changed; nothing here proves a key is correct.

## Effect of the flagged items

0 item(s) flagged. Excluding them changed nothing that is reported here.


## Benchmark health

- Items: 5
- Key letter distribution: {'B': 2, 'C': 3}
- j1_deepseek ceiling rate: 100.0% [0.566, 1.000] (5/5 items right on every repeat)

Score distribution (primary repeat):

| judge | said CORRECT | said INCORRECT | mean confidence |
| --- | --- | --- | --- |
| j1_deepseek | 3 | 2 | 99.2 |
- j1_deepseek confidence bins: 0-20: 0, 20-40: 0, 40-60: 0, 60-80: 0, 80-100: 5

## Cost and calls

- Network calls: 0; logged judge calls: 15; cache hits: 0
- Tokens: 5316 in, 2200 out, 1969 reasoning
- Estimated cost: US$0.0066 (price source: deepseek-api-docs-peak-2026-11-17)
- Caps: 500 calls, US$5.0
- Wall time: 0.0 s

## Limitations

1. **Small sample.** 5 items, one candidate answer each. Every interval is wide, and a difference of a few percentage points between judges is not interpretable at this size.
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
python -m medeval run exp002 --n-items 5 --seed 20260101 --repeats 3
```
