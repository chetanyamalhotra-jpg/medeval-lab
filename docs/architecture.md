# Architecture

```
                       data/items/*.json ──► medeval_core.validator ──► issues (never edits)
                                                    │
data/literature.csv ──► medeval_criteria.traceability ──► CI failure if a citation is missing
                                                    │
packages/medeval_criteria/catalogue/v0.1/*.yaml ──► medeval_criteria.loader ──► Criterion objects
                                                    │
                                          medeval_judges (prompts use criteria)
                                                    │
public datasets ──► medeval_audit.loaders ──► duplicates / answer_key / contamination ──► results/audit/
                                                    │
                              data/tasks/*.yaml ──► lm-evaluation-harness ──► results/harness_runs/
                                                    │
experiments/001_judge_reliability/run.py ──► verdicts + medeval_reliability ──► results/experiment_001/

Experiment 002 (v0.2) ─────────────────────────────────────────────────────────────────────────────

  MedQA dev (cc-by-sa-4.0, item text git-ignored)
        └─► medeval_study.datasets ──► medeval_study.sampling (dedupe stems, seeded draw, balanced
                                      conditions) ──► medeval_study.responses (fixed templates,
                                      lexical-overlap distractor; no model involved)
                                                        │
                              JudgeView (question + options only; no key field)
                                                        │
                       medeval_study.prompts ──► medeval_core.client (cache, guard, JSONL call log)
                                                        │
                        judge connections (bring your own key: remote API or local
                        model server; any provider, any model you choose)
                                                        │
                       strict JSON parse, one retry ──► judgements.jsonl (append-only, resumable)
                                                        │
        medeval_study.analysis (Wilson + bootstrap CIs, kappa, phi, ECE, Brier, AUROC, consistency)
                                                        │
        medeval_study.report ──► report.md / metrics.json / items.csv + manifest.json

  medeval_web (FastAPI, loopback only) reads the same run directory:
    GET /api/runs, /api/runs/{id}, /api/runs/{id}/items, /api/runs/{id}/report,
    GET /api/runs/{id}/events (SSE tail of run.json + judgements.jsonl), POST /api/runs
```

## Package responsibilities

| Package | May import | Must not import |
|---|---|---|
| `medeval_core` | stdlib, numpy, requests | any other `medeval_*` package |
| `medeval_criteria` | `medeval_core`, yaml | `medeval_judges`, `medeval_audit` |
| `medeval_metrics` | `medeval_core`, numpy | `medeval_judges` |
| `medeval_audit` | `medeval_core`, datasets/scipy | `medeval_judges` |
| `medeval_judges` | `medeval_core`, `medeval_criteria` | `medeval_audit` |
| `medeval_reliability` | numpy, scipy | everything else (pure statistics) |
| `medeval_study` | `medeval_core`, `medeval_audit` (loader), `medeval_reliability` | `medeval_web` |
| `medeval_web` | `medeval_core`, `medeval_study` | `medeval_judges` (it only reads run directories) |
| `medeval` (CLI) | all of the above | — |

`medeval_reliability` is deliberately dependency-free of the domain packages so the
statistics can be tested in isolation and reused elsewhere.

## The judge protocol

Any object with `judge_id`, `backend`, `pipeline_test_only`, `score(case, criterion_id)`
and/or `compare(case, seed, swapped)` is a judge. Four implementations ship:
exact-match, rules, LLM rubric, jury. The experiment only uses `compare`.

Cases carry the ground truth (`target`) for analysis; the judge never receives it.
For LLM judges the presentation order is derived from
`SHA-256(judge_id, case_id, seed)`, so order is reproducible but independent of the
answer, and the order is written into the verdict.

## Where to extend

- **New criterion:** add YAML to the catalogue, add a literature row, run
  `python -m medeval traceability`, add weights to the rubric.
- **New metric:** add a function and a `MetricDefinition` with literature IDs to
  `medeval_metrics/metrics.py`; register it in `metrics.py`'s `METRICS` dict. If it
  is a mean of per-item values, it can also be a harness plugin.
- **New judge:** implement `compare`/`score` returning `PairwiseVerdict`/
  `PointwiseVerdict`; reuse `LLMRubricJudge` for anything that is a prompt.
- **New dataset:** add a `DatasetSpec` to `medeval_audit.loaders.DATASETS` with a
  verified licence, a normaliser, and (optionally) a download strategy.

## Data flow guarantees

1. Validation and auditing never modify input files; they emit issues and reports.
2. Every model call is logged before its text is used.
3. Every verdict records backend, model, prompt hash, template version, seed and
   timestamp.
4. Every result file names the artefact versions (toolkit, criteria, rubric, items)
   that produced it.
5. A benchmark answer key is not representable in a judge prompt: the judge view
   type has no key field, and the renderer only reads question and options.
6. Benchmark item text stays out of version control (share-alike licence); only
   ids, verdicts, confidences, hashes and aggregate metrics are committed.
