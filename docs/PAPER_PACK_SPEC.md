# Paper Pack specification (v1)

A **Paper Pack** turns a published medical-AI evaluation method into a
reproducible local protocol. It is data, not code: a `paper.yaml`, a dataset in
the pack directory, prompt templates, graders, a statistical plan and citation
metadata. medeval-lab v0.6 discovers packs under `papers/<pack_id>/` and runs
them as a matrix of **N target models × M graders/judges**.

> **Research tool, not a medical device.** A pack is a benchmarking protocol. It
> is not clinically validated, no clinician reviews its outputs, and it must not
> be used for patient care or clinical decisions. A pack that claims clinical
> validation or clinician review is rejected by the validator.

## 1. Folder layout

```
papers/my-pack/
  paper.yaml          # the protocol (required)
  data/dataset.jsonl  # local dataset (required; JSONL or CSV)
  smoke.jsonl         # optional synthetic smoke rows (presets use this as dataset.path)
  loader.py           # optional on-demand upstream loader (presets)
  README.md           # optional notes for humans
```

Packs are self-contained: the dataset must live inside the pack directory
(`dataset.path` is resolved relative to it, and a `..` escape is an error). Packs
never fetch data at run time.

## 2. Minimal example

```yaml
schema_version: 1
id: my-clinical-mcq
version: 0.1.0
title: "A published MCQ protocol, reproduced"
authors: ["A. Author", "B. Author"]
year: 2025
venue: "Journal of Synthetic Examples"
doi_or_url: "https://doi.org/10.0000/example"
license: "CC-BY-4.0"

methodology_notes:
  kind: inspired_adaptation       # or faithful_protocol_reproduction / synthetic_pipeline_template
  summary: "Reproduces the paper's accuracy comparison; prompts are re-written to avoid copyrighted text."
  limitations: "Synthetic subset; no clinical validation; small n."

task_type: mcq
dataset:
  path: data/dataset.jsonl
  format: jsonl
  seed: 20260101
  limit: 100
  screening_size: 0
  fields: {}                      # canonical field -> column, only when names differ

prompt_template:
  system: "Answer the multiple-choice question. This is not patient care."
  user: |
    Question: $question
    Options:
    $options_block
    Reply as: ANSWER: <letter or ABSTAIN>

graders:
  - id: accuracy
    type: deterministic
    metric: mcq_accuracy
  - id: abstention
    type: deterministic
    metric: abstention
  - id: format
    type: deterministic
    metric: format_compliance

metrics:
  primary: accuracy
  secondary: [abstention, format_compliance]
  comparison:
    method: mcnemar
    alpha: 0.05
    bootstrap_resamples: 2000
    bootstrap_seed: 20260101

citation:
  bibtex: |
    @article{key,
      title = {A published MCQ protocol, reproduced},
      author = {Author, A. and Author, B.},
      year = {2025}
    }
```

Scaffold one and edit it:

```powershell
python -m medeval paper init my-clinical-mcq --task mcq
python -m medeval paper validate my-clinical-mcq
python -m medeval matrix run --paper my-clinical-mcq --dry-run
```

## 3. Field reference

### Identity and citation

| Field | Rule |
|---|---|
| `schema_version` | must be `1` |
| `id` | lowercase letters, digits and hyphens, 2–64 chars; must match the directory name by convention |
| `version` | three-part semver (`0.1.0`) |
| `title`, `venue`, `doi_or_url`, `license` | non-empty strings; use the paper's real metadata |
| `authors` | non-empty list |
| `year` | 1900–2100 |
| `methodology_notes.kind` | `faithful_protocol_reproduction`, `inspired_adaptation` or `synthetic_pipeline_template` |
| `methodology_notes.summary` | what was kept and what changed |
| `methodology_notes.limitations` | sample, licensing, construct validity, absence of clinical validation |
| `citation.bibtex` | a BibTeX entry; exported beside the medeval-lab citation |
| `preset` | optional `true` for a built-in preset; presets must declare `source_dataset` and `adaptation_status` |
| `source_dataset` | mapping: `name`, `url`, `huggingface`, `license`, `notes`; at least `url` or `huggingface` |
| `adaptation_status` | free-text note such as `synthetic_smoke_only; no upstream rows bundled` |
| `loader` | optional path to a `loader.py` inside the pack; validated to exist |

The validator rejects the phrases "clinically validated", "clinician reviewed",
"clinician-reviewed", "clinically proven" and "medical device certified" in pack
metadata. Say what you did not do instead.

### Task types

| `task_type` | Typical dataset fields | Built-in deterministic graders |
|---|---|---|
| `mcq` | `id`, `question`, `options`, `answer` | `mcq_accuracy`, `abstention`, `format_compliance` |
| `open_ended_rubric` | `id`, `question`, `reference_answer`, `rubric` | `format_compliance` + `llm_rubric` |
| `structured_extraction` | `id`, `input`/`abstract`, `gold` (JSON object) | `json_schema`, `field_f1`, `field_exact_match` |
| `triage_classification` | `id`, `input`, `label` | `label_accuracy`, `abstention` |
| `multi_judge_comparison` | `id`, `question`, `reference_answer`, `candidate_a`, `candidate_b` | `format_compliance` + `llm_rubric` |

`dataset.fields` maps a canonical name to a column when the column is not named
the same way. For example, to read the `abstract` column as the canonical
`input`:

```yaml
dataset:
  fields:
    input: abstract
```

### Prompts

Templates use strict `$variable` / `${variable}` placeholders (`string.Template`
semantics), not `str.format`, so JSON braces in a prompt are literal. Available
variables for a target prompt:

- `mcq`: `item_id`, `question`, `options`, `options_block`
- `open_ended_rubric` / `multi_judge_comparison`: `item_id`, `question`, `reference_answer`, `rubric`, `candidate_a`, `candidate_b`
- `structured_extraction`: `item_id`, `input`, `abstract`
- `triage_classification`: `item_id`, `input`, `text`

The **answer key is never rendered into a target prompt**. `gold` is used only by
graders and the report.

### Graders

Deterministic grader:

```yaml
- id: fields
  type: deterministic
  metric: field_f1
  options:
    fields: [diagnosis, intervention, population]
```

LLM rubric grader:

```yaml
- id: panel
  type: llm_rubric
  prompt: |
    Question: $question
    Reference: $reference_answer
    Candidate: $target_answer
    Rubric: $rubric
    Return JSON only: {"score": <number $scale_min-$scale_max>, "rationale": "..."}
  scale_min: 1
  scale_max: 5
```

The rubric prompt may use `$item_id`, `$question`, `$target_answer`,
`$reference_answer`, `$rubric`, `$scale_min` and `$scale_max`. A judge reply that
is not parseable, or whose score is outside the declared scale, is recorded as a
parse failure — not as a zero.

### Metrics and statistics

`metrics.primary` may be a built-in metric name, `accuracy` (resolved from the
graders) or `mean_rubric_score`/`rubric_score`.

`metrics.comparison.method` is one of:

- `mcnemar` — exact binomial for ≤25 discordant pairs, otherwise continuity-corrected χ²; use for binary correctness;
- `paired_bootstrap` — paired bootstrap of the mean difference; use for scores/F1;
- `none`.

Every interval is seeded (`bootstrap_seed`), so it is reproducible. A
stratified bootstrap is used automatically when items carry more than one
`stratum` value.

## 4. Dataset rules

- UTF-8, JSONL (one object per line) or CSV (with a header).
- `id` must be unique and non-empty.
- MCQ: `options` is a mapping (`{"A": "...", "B": "..."}`), a list, or a
  pipe-delimited string; `answer` is a letter or the exact option text.
- Extraction: `gold` is a JSON object (or a JSON string containing one).
- Sampling is deterministic: rows are ordered by
  `sha256("<seed>:<item_id>")`, then `limit` is applied. File order does not
  matter.
- `screening_size > 0` enables a two-stage design: `--stage screening` runs the
  first N sampled items, `--stage full` runs the rest as well. The matrix runner
  records which stage produced a result.

## 5. What a run writes

`results/matrix_runs/<run_id>/` contains:

| File | Purpose |
|---|---|
| `run.json` | status, pack hash, prompt hash, model params, caps, usage, timestamps, `pipeline_test_only` |
| `events.jsonl` | append-only, fsynced event log (`run_started`, `target_output`, `item_scored`, `judge_score`, `budget_stopped`, …) |
| `outputs.jsonl` | one target output per (target, item), with latency and token usage |
| `scores.jsonl` | one grader/judge score per (target, item, grader, judge) |
| `metrics.json` | CIs, pairwise tests, judge reliability, cost/latency, limitations |
| `report.md` | publication-style report prefixed with `MEDICAL_NOTICE` |
| `results_table.tex` | booktabs table snippet with `% MEDICAL_NOTICE` |
| `citations.bib` | medeval-lab + pack BibTeX |
| `items.csv` | per-item scores and hashes (no raw model text) |
| `bundle.zip` | deterministic export of the above |
| `manifest.json` | git state, package versions, seeds, hashes |

**Cache key.** Successful completions are stored in `.medeval_cache/` keyed by
`sha256(model_id + params + rendered_prompt + item_hash)`. `params` includes
temperature, max tokens, seed, system prompt and API style. A crash or a repeat
run therefore reuses exact requests and never re-bills them.

**Stop and resume.** Every output and score is appended and fsynced. Stopping
saves completed items; resuming the same run id skips every completed
`(target, item)` and grader. A resume is refused if the pack hash changed, so a
protocol edit cannot be presented as a continuation.

**Budget.** The run-wide call cap and USD cap (plus each connection's own caps)
are checked before every dispatched call. When the next call would exceed a cap
the run stops as `budget_stopped`; everything completed is still reported and
exported. Dollar caps only bind when every called connection declares prices.

## 6. Offline re-grading

```powershell
python -m medeval regrade <run_id> --paper my-pack
```

Re-grading reads the original `outputs.jsonl` and applies the new pack's graders
and/or judges. **Target models are never called** (the regrade records
`target_calls: 0`). Judge calls are cached like any other request. Use it to try
a stricter rubric, a deterministic check added after the fact, or a different
judge family without paying to re-query the models under test.

## 7. Matrix runs and judges

- **Target models** are the models under test (1..N).
- **Graders** are deterministic checks (0..N) plus an optional LLM judge panel
  (0..M).
- A judge may be one of the target connections, but the runner keeps the roles
  separate in every record and report.
- Cross-family error correlation is only estimated when at least two judges
  declare different `family_label` values. Same-family judges share training
  data and failure modes; the report says so rather than pretending otherwise.

## 8. Fifteen-minute checklist for a new paper

1. **Extract the protocol** (5 min): what is one item, what does the model see,
   what is scored, and against what reference?
2. **Write the dataset** (5 min): export a small licensed sample to JSONL and map
   the columns with `dataset.fields`. Do not commit text you cannot redistribute.
3. **Write the prompts and graders** (3 min): use `paper init` as the skeleton;
   keep the answer key out of the target prompt; add a `format_compliance`
   grader so malformed output is separable from poor quality.
4. **Declare the statistics** (1 min): McNemar for binary correctness, paired
   bootstrap for scores, and a fixed seed.
5. **Validate and dry-run** (1 min): `paper validate` then `matrix run --dry-run`.
   A dry run is `PIPELINE TEST ONLY`; it proves the wiring, not the science.
6. **Label the methodology honestly**: faithful reproduction, inspired
   adaptation, or synthetic template. State what you changed and what you could
   not verify.

## 9. Licensing and safety

- A pack's `license` field describes the dataset/protocol it ships. The
  repository is MIT; third-party datasets keep their own licences.
- Do not put copyrighted paper text into prompts, datasets or fixtures. Use
  synthetic or public-domain text for templates.
- Do not put a key in `paper.yaml`, a dataset, a result, an export or a bundle.
  Keys live in process memory (app) or an environment variable named by
  `key_env`/`api_key_env` (CLI).
- The report always carries the "research tool, not a medical device" notice.
  A pack cannot remove it.

## 10. Built-in presets and on-demand loaders

Five presets model published evaluation primitives. They are *protocol-inspired
adaptations*, not faithful reproductions, and each says so in
`methodology_notes` and `adaptation_status`. They ship only 3-5 synthetic
`smoke.jsonl` rows so the repository stays lightweight and licence-clean.

| Preset | Source (declared in `paper.yaml`) | Grader/statistics primitive |
|---|---|---|
| `healthbench-rubric` | HealthBench, Arora et al. 2025; OpenAI page + arXiv 2505.08775 | rubric panel, cross-family disagreement/error phi |
| `medcalc-bench` | MedCalc-Bench, Khandekar et al. NeurIPS 2024; GitHub `ncbi-nlp/MedCalc-Bench`, HF `nsk7153/MedCalc-Bench` | parameter field-F1, exact numeric and ±5% numeric match |
| `medhallu-detect` | MedHallu, Pandit et al. EMNLP 2025; HF `UTAustin-AIHealth/MedHallu` | 3-class macro F1/precision, coverage, abstention |
| `medxpertqa-text` | MedXpertQA, Zuo et al. ICML 2025; GitHub `TsinghuaC3I/MedXpertQA`, HF `TsinghuaC3I/MedXpertQA` | hard MCQ accuracy, paired McNemar |
| `medsafety-bench` | MedSafetyBench, Han et al. NeurIPS 2024; GitHub `AI4LIFE-GROUP/med-safety-bench` | refusal rate + dual-judge harmfulness |

Each preset declares:

```yaml
preset: true
source_dataset:
  name: "..."
  url: "https://..."          # or huggingface: "org/dataset"
  huggingface: "org/dataset"
  license: "..."
  notes: "reference only; no rows bundled"
adaptation_status: "synthetic_smoke_only; no upstream rows bundled"
loader: loader.py
dataset:
  path: smoke.jsonl
```

### Loading a real dataset (optional)

```powershell
# Verify a licensed local export, write .medeval_data/<id>/dataset.jsonl:
python papers/medcalc-bench/loader.py --source path/to/export.jsonl --force

# Or explicitly download the declared Hugging Face dataset after reading its licence:
python papers/medcalc-bench/loader.py --download --accept-license --limit 200

# Check an already-materialised dataset:
python papers/medcalc-bench/loader.py --check
```

The loader verifies every row with the same normaliser the runner uses; one bad
row aborts the whole load. The destination is always under `.medeval_data/`,
which is git-ignored. `--download` without `--accept-license` exits 2 and makes
no network call. A downloaded dataset is never written into `papers/`.
