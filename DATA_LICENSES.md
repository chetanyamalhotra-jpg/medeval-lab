# Data licences and tracked artefacts

## Experiment 003 (v0.3)

Experiment 003 screens the MedQA dev split closed-book and builds a stratified
pool. The same CC-BY-SA-4.0 rule applies:

| Path | Tracked? | Why |
| --- | --- | --- |
| `results/experiment_003/screening.jsonl` | **yes** | item ids and correct/incorrect flags only — no benchmark text (the pre-registered committed file) |
| `results/study_runs/exp003-*/items.jsonl`, `candidates.jsonl`, `calls.jsonl`, `cache.json`, `screening.jsonl` (dry run) | no (git-ignored) | verbatim benchmark text, candidates and prompts |
| `results/study_runs/exp003-*/metrics.json`, `report.md`, `items.csv`, `review_queue.csv`, `manifest.json`, `run.json` | **yes** | ids, verdicts, confidences, hashes, aggregates |

The screening file is the same design as v0.2's judgement records: ids and
booleans carry no licensable text.

The MIT `LICENSE` applies to this repository's code and original synthetic
material only. It does not relicense third-party benchmark content.

| Dataset | Source | Licence | Audited snapshot fingerprint | Tracked verbatim text |
|---|---|---|---|---|
| MedMCQA validation | `openlifescienceai/medmcqa` | Apache-2.0 | `5266466ff15ffd33194917363c3e42516a93ec34aa73dce846bacb84b64b1918` | No; harness sample JSONL removed from tracking. |
| MedQA dev | `GBaker/MedQA-USMLE-4-options-hf` | CC-BY-SA-4.0 | `a75f2850856a176338b2fe5e0ea1ed229a41d0b31cd528a5a3c89df3c44a0521` | No; harness sample JSONL removed from tracking. |

Fingerprints and source/licence evidence are retained in
`results/audit/datasets.csv`. The two removed files were
`results/harness_runs/samples_medmcqa_medeval_20261115T144952Z.jsonl` and
`results/harness_runs/samples_medqa_4options_medeval_20261115T144952Z.jsonl`.
They contained benchmark item text and are ignored for future runs.

## v0.5.0 — what a clone contains (open-source release)

- **This repository redistributes no benchmark dataset.** A clone contains ids,
  verdicts, confidences, hashes and aggregate metrics only. Every dataset is
  downloaded by *you*, under *its* licence, with `medeval data fetch <dataset>`,
  and stays in a git-ignored cache (`data/cache/`, or `MEDEVAL_CACHE_DIR`).
- **The built-in synthetic sample is original.** It is generated in code
  (`packages/medeval_study/synthetic.py`, 420 fabricated items) and labelled
  `PIPELINE TEST ONLY`. It contains no third-party content and is covered by this
  project's MIT licence.
- **Third-party components** (Python packages, and the optional
  lm-evaluation-harness checkout consumed read-only from outside this repository)
  keep their own licences; see `NOTICE`.
- Do not add a dataset loader without recording its licence and the evidence for
  it in `packages/medeval_study/datasets.py` and in this file, and never commit
  item text.

## Experiment 002 (v0.2)

Experiment 002 samples 60 items from the MedQA dev split and builds one candidate
answer per item. Because that dataset is **CC-BY-SA-4.0**, none of the sampled item
text, candidate text or full model output is tracked:

| Path | Tracked? | Why |
| --- | --- | --- |
| `results/study_runs/*/items.jsonl` | no (git-ignored) | verbatim benchmark stems and options |
| `results/study_runs/*/candidates.jsonl` | no (git-ignored) | quoted option text |
| `results/study_runs/*/calls.jsonl` | no (git-ignored) | full prompts, which quote the item |
| `results/study_runs/*/cache.json` | no (git-ignored) | model output derived from the item |
| `results/study_runs/*/judgements.jsonl` | **yes** | item id, verdict, confidence, prompt hash only |
| `results/study_runs/*/metrics.json`, `report.md`, `items.csv`, `manifest.json`, `run.json` | **yes** | aggregate metrics, ids and hashes; no benchmark prose |

The web app reads the git-ignored files to display a question in the item explorer.
That is a local display of a local copy, not redistribution: the files are never
committed, never uploaded, and the app binds to `127.0.0.1` only. Anyone who wants
to reproduce the item view must download the dataset themselves under its own
licence.

## v0.4.0 additions

| Path | Tracked? | Why |
| --- | --- | --- |
| docs/screenshots/*.png | **yes** | rendered from fake data only: a synthetic literature row, a fabricated exp002 fixture run, no benchmark item text, no keys, no personal data |
| data/review_notes.json | no (git-ignored) | one reviewer's labels and notes; may quote item text |
| data/preferences.json | no (git-ignored) | first-run card dismissal |
| data/workspaces.json | no (git-ignored) | local workspace/session organisation |
| 
esults/exports/*.zip | no (git-ignored) | generated bundles (ids and aggregates only unless notes were explicitly requested) |
