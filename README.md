# medeval-lab

An independent, open-source **Medical AI Evaluation Lab** — a next-generation
workbench for plugging published medical-AI evaluation methods in as declarative
**Paper Packs**, benchmarking multiple target models head-to-head, and reporting
paper-grade statistics with decoupled evaluators and BYOK neutrality.

> ### Research tool, not a medical device
>
> - **Not for patient care, diagnosis, treatment or any clinical decision.**
> - **No clinical validation.** Nothing here is validated against a clinical
>   endpoint, and there is no clinician reference standard in this repository.
> - **No clinician reviewed any criterion, item, judgement or report.** Criteria
>   are traced to the literature, not to expert consensus.
> - **Small samples are exploratory.** Intervals are reported because the point
>   estimates alone are not trustworthy; treat them as a measurement exercise.
> - **Results depend on the model and on the provider's serving version.** A
>   different model, provider or version is a different measurement instrument.
> - **Mock and dry runs say nothing about a model.** They prove the pipeline only
>   and are labelled `PIPELINE TEST ONLY` in every artefact.

**Bring your own key.** There is no default provider, model or price. You add a
provider connection and your own key — in the local app, where the key stays in
that process's memory, or in a git-ignored `connections.yaml` that names an
environment variable instead of holding the key. **You pay your provider.** Set a
spend limit in the provider's dashboard as well: the maintainers cannot see, stop
or refund your usage. There is no telemetry; outbound requests go only to the
endpoint you configured, plus metadata lookups you start yourself.

**Version 0.6.0.** Release status: not peer reviewed. v0.6 adds the Paper Pack /
matrix runner, a content-addressed response cache with stop-and-resume and
offline re-grading, paper-grade statistics, and publication exports
(`report.md`, LaTeX, BibTeX, deterministic `bundle.zip`). Experiment 002 ran
against one real judge; Experiment 003 is **built but not run** — the owner runs
it live with their own keys. Ten former hidden items remain exposed in early Git
history and must not be treated as hidden. Read `VERIFY_ME.md` before citing
anything, `OSS_AUDIT.md` for what the open-source audit found,
`docs/PAPER_PACK_SPEC.md` for the pack format, and
`docs/BYOK_QUICKSTART.md` for the bring-your-own-key walkthrough.

---

## Quick start (Windows, PowerShell)

```powershell
cd <repo>
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m medeval doctor
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m medeval ui
```

That is the whole path: a pinned environment, an environment check, the test
suite, then the loopback app at `http://127.0.0.1:8765/` (its token is in the
address bar). `requirements.txt` is the reproducible environment; `pyproject.toml`
only declares floors. Optional: `.\.venv\Scripts\python.exe -m pip install -e .`
adds a `medeval` console script, and `run_all.cmd` runs the full local pipeline
including dry runs that spend nothing.

Next: **Models page → add a connection → paste your key → test it → pick a listed
model → assign a judge role → dry run → small live run with caps**, or read
[docs/BYOK_QUICKSTART.md](docs/BYOK_QUICKSTART.md). Full walkthrough:
[USER_GUIDE.md](USER_GUIDE.md). Failures:
[TROUBLESHOOTING.md](TROUBLESHOOTING.md). Security model:
[SECURITY.md](SECURITY.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## What it does

| Package | Job |
|---|---|
| `medeval_core` | item JSON Schema + validator, one OpenAI-compatible client, JSONL call logging, backend detection, response cache, spend guard, run manifests, Ollama probe |
| `medeval_audit` | duplicate detection, answer-key and option-index-base checks, shortcut baselines, contamination screens, per-dataset report |
| `medeval_criteria` | versioned YAML catalogue of 8 constructs with anchored scales, failure examples and literature IDs |
| `medeval_metrics` | critical-error / unsafe / omission rates, rubric-weighted score, Brier, ECE, perturbation consistency, subgroups — plus lm-evaluation-harness plugins |
| `medeval_judges` | exact-match, deterministic rule judge, single LLM rubric judge (blinded, randomised order, guided/free), jury |
| `medeval_reliability` | Cohen / Fleiss / Krippendorff / ICC / AC1, bootstrap CIs, McNemar, position and length bias probes, self-consistency |
| `medeval_study` | Experiment machinery: licensed MCQ loading, stem-deduplicated sampling, template-built candidates, strict-JSON judge layer (N judges via connections), resumable runner, metrics and reports |
| `medeval_packs` | **v0.6** Paper Packs (declarative protocol + dataset + prompts + graders + statistics), matrix runner (N target models × M graders), content-addressed cache, offline re-grade, Markdown/LaTeX/BibTeX/bundle exports |
| `medeval_web` | local-only FastAPI app: launch-token protected, memory-only keys, Models/Papers/Tests pages, workspaces and sessions, **Paper Registry + matrix builder + disagreement inspector** |

## Paper Packs and matrix runs (v0.6)

A Paper Pack is data: `papers/<id>/paper.yaml` plus a local dataset. It declares
the task type, prompt templates, composable deterministic/rubric graders, the
statistical comparison and citation metadata. The answer key is never rendered
into a target prompt.

**Three neutral templates** ship for scaffolding, all synthetic and tagged
`PIPELINE TEST / TEMPLATE DATA`:

| Pack | Task | What it demonstrates |
|---|---|---|
| `template-clinical-mcq` | `mcq` | accuracy + abstention/format + paired McNemar |
| `template-rubric-panel` | `open_ended_rubric` | cross-family 2+ judge panel, agreement and error correlation |
| `template-structured-extraction` | `structured_extraction` | JSON-schema validity + field-level F1/exact match |

**Five built-in presets** model published evaluation primitives. Each ships only
3-5 strictly synthetic `smoke.jsonl` rows (`PIPELINE TEST / SYNTHETIC SMOKE
DATA`), full bibliographic metadata, an explicit `source_dataset` URL/HF
reference and an `adaptation_status` note. **No upstream dataset is bundled.**
An optional `loader.py` materialises a licensed dataset on demand into the
git-ignored `.medeval_data/` after schema verification.

| Preset | Inspired by | Primitive |
|---|---|---|
| `healthbench-rubric` | Arora et al., 2025 | rubric-scored open-ended QA + cross-family judge panel + disagreement/error correlation |
| `medcalc-bench` | Khandekar et al., NeurIPS 2024 | structured parameter extraction + exact and ±5% numeric grading |
| `medhallu-detect` | Pandit et al., EMNLP 2025 | 3-class hallucination detection; macro F1/precision, abstention and coverage |
| `medxpertqa-text` | Zuo et al., ICML 2025 | hard clinical-reasoning MCQ + paired McNemar |
| `medsafety-bench` | Han et al., NeurIPS 2024 | harmful-prompt refusal rate + dual-judge harmfulness scoring |

```powershell
# List, validate, scaffold (no network, no key):
.\.venv\Scripts\python.exe -m medeval paper list
.\.venv\Scripts\python.exe -m medeval paper validate
.\.venv\Scripts\python.exe -m medeval paper init my-pack --task mcq

# 1 Paper Pack x N targets x M judges; a dry run is PIPELINE TEST ONLY:
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-clinical-mcq --dry-run --n-items 12
.\.venv\Scripts\python.exe -m medeval matrix run --paper healthbench-rubric --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper medcalc-bench --dry-run --n-items 4

# Verify and load a licensed upstream export on demand (writes .medeval_data/):
.\.venv\Scripts\python.exe papers\medcalc-bench\loader.py --source path\to\licensed-export.jsonl --force

# Inspect, export, and re-score cached outputs without re-querying any target:
.\.venv\Scripts\python.exe -m medeval matrix show <run_id>
.\.venv\Scripts\python.exe -m medeval regrade <run_id> --paper template-rubric-panel
```

Every matrix run writes `report.md` (with the `MEDICAL_NOTICE` header),
`results_table.tex`, `citations.bib`, `metrics.json`, `items.csv` and a
deterministic `bundle.zip` under `results/matrix_runs/<run_id>/`. In the app,
**Paper Packs** is a new sidebar page: registry → matrix builder with a live
pre-flight plan card → run page with Stop/Resume, head-to-head output view,
disagreement/error filters, export and offline re-grade. Full format reference:
[docs/PAPER_PACK_SPEC.md](docs/PAPER_PACK_SPEC.md).

## Experiment 003 — frontier judges on a difficulty-enriched sample (built, not run)

Experiment 002 hit the ceiling: a strong judge on a random MedQA sample leaves
almost no errors to correlate. Experiment 003 enriches for difficulty by a rule
fixed in its pre-registration (`experiments/003_frontier_judges/PLAN.md`,
committed before the code): a **screening model** (a separate connection that is
never a judge) answers items closed-book **adaptively** — shuffled by the
pre-registered seed, screened in batches of 50, stopping when 100 hard items are
found, when every eligible item has been screened, or at the cap (the lesser of
1,000 items and the screening connection's own call cap), with the stop reason
recorded. The pool takes 100 items the screener got wrong plus 40 seeded
controls, with every Experiment 002 item excluded.

The run is **staged and confirmed**: Screening → Review hard pool → Pilot (first
40 hard items) → Gate decision → Remaining items. Each stage shows its worst-case
call count and cost before it runs and needs an explicit confirmation; you can
stop after any stage and resume later. The pilot gate stops the run unless there
are at least 5 judge-versus-key disagreements on the first 40 hard items. Flags
require **every** verdict-returning judge to dispute the key at confidence ≥ 90
on every repeat, and land in `review_queue.csv` (ids only). Single-reviewer
labels and notes stay local, git-ignored, and are labelled everywhere as one
person's opinion; the report adds a sensitivity table with `key looks wrong`
items removed.

```powershell
# Pipeline test with mock judges and a mock screener; no network, no spend:
.\.venv\Scripts\python.exe -m medeval run exp003 --dry-run --n-items 140 --screening-size 60

# Regenerate a report from stored verdicts (no model call); export ids-only bundle:
.\.venv\Scripts\python.exe -m medeval report exp003
.\.venv\Scripts\python.exe -m medeval export <run_id>
```

The live run is yours: start the app, add a provider connection and paste your
key (memory only), assign judge connections (and the screening connection) on the
Models page, then launch Experiment 003 from the start screen and confirm each
stage. With a key in an environment variable you can also run it headlessly:

```powershell
# Prints the plan and worst-case cost, calls nothing:
.\.venv\Scripts\python.exe -m medeval run exp003 --connections connections.yaml
# Actually runs it (needs a role: screening entry too):
.\.venv\Scripts\python.exe -m medeval run exp003 --connections connections.yaml --confirm-live
```

**State: built and dry-run only.** No live call has been made; no result exists;
nothing here supports any claim about how frontier judges behave.

## Experiment 002 — the real-judge study

```powershell
# 1. Dry run first. Mock judges, no network, no spend.
.\.venv\Scripts\python.exe -m medeval run exp002 --dry-run --n-items 8

# 2. A live run. Needs a connections file whose entries name an env var per key;
#    without --confirm-live it prints the plan and the worst-case cost and stops.
$env:MEDEVAL_JUDGE_A_KEY = "<your key>"
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml --confirm-live --n-items 60 --repeats 3 --max-api-calls 500

# 3. Interrupted? Resume the same run id. Completed calls are never repeated.
.\.venv\Scripts\python.exe -m medeval run exp002 --resume exp002-main

# 4. Fix a report or re-score without spending anything.
.\.venv\Scripts\python.exe -m medeval rebuild exp002-main
```

What it does, in one paragraph: it loads a public four-option medical QA split,
samples 60 items with no duplicate stems, builds one candidate answer per item
(half naming the key option, half naming the most lexically similar distractor,
from six shared templates — never generated by the judge's own model family), asks
each judge three times for `{"verdict": "CORRECT"|"INCORRECT", "confidence": 0-100}`,
and then reports accuracy, false-accept and false-reject rates, Cohen's kappa
against the key, ECE, Brier score, AUROC, self-consistency and benchmark ceiling —
each with an interval. The official key never enters a judge prompt; that is
enforced by the type of the object the prompt is rendered from and checked by a
test that the prompt is byte-identical when the key changes.

**Judges.** J1 is a remote API model (DeepSeek by default) and J2 is a local CPU
model from a different family via Ollama (`llama3.2:3b` by default). If Ollama is
not installed or not reachable, the run records J2 as unavailable, prints
installation instructions, and reports the cross-family question as **not measured**.
It never substitutes a second prompt from the same family.

**What it costs.** A 60-item, 3-repeat, one-judge run is 180 calls. The reported
run used 187 calls and 192k tokens: **US$0.17** at DeepSeek's published peak
rates. Caps default to 500 calls and US$5, and are checked before the first call
and before every call after that.

## Models, keys and the local app

```powershell
.\.venv\Scripts\python.exe -m medeval ui          # opens the app with a per-launch token
```

Loopback only, no telemetry, no CDN, no build step. **Keys live only in the app's
process memory**: you paste a key on the Models page, it is sent over loopback in
a request body, shown only as "key set ····last4", and forgotten when the app
stops. There is no remember-on-disk option; `.env` loading is off unless the app
is started with `--use-env`.

The Models page is where you bring your own providers: the preset dropdown starts
empty ("Choose a provider") and Save stays disabled until you pick one; choosing a
preset fills only the base URL (DeepSeek, OpenRouter, OpenAI, Ollama, Custom — base
URLs from official documentation, editable). Then set model, family label,
per-connection call/USD caps and optional prices, and **Test connection** (lists
models without spending; a one-token call is available behind explicit confirmation
for endpoints without a model list). Assign connections to Judge 1, 2, 3… and to
the optional Screening model — which must not also be a judge — and the app warns
when two judges share a family.

Every API call the app makes requires the per-launch token (`X-Medeval-Token`);
non-loopback `Host` headers and cross-site `Origin` headers are refused.

Pages: the **start screen** ("Describe an evaluation" — plain notes become a
session's hypothesis with no model call, or slash commands `/run`, `/dryrun`,
`/test`, `/papers`, `/report`), **workspaces** as folders of sessions (v0.2 runs
appear under "Experiment 002"), **Runs / Results / Item explorer** (as in v0.2),
**Papers** (the literature registry: add by DOI or arXiv, status tags, local
notes, reverse citation view), **Tests** (allow-listed server-side commands:
pytest, ruff, secret scan, traceability, key-isolation tests, experiment dry
runs) and **Settings** (paths, caps, prices, licences, key status without values).

## Where the results are

| File | Contents |
|---|---|
| `REPORT_v0.2.md` | Experiment 002 results and the app (v0.2 numbers) |
| `REPORT.md` | plain-language summary of the whole project |
| `results/study_runs/exp002-main/report.md` | auto-generated Experiment 002 report |
| `results/study_runs/exp002-main/metrics.json` | machine-readable metrics with intervals |
| `results/study_runs/exp002-main/items.csv` | per-item verdicts (no benchmark text) |
| `results/study_runs/exp002-main/manifest.json` | git commit, versions, models, seeds, prompt hashes |
| `results/audit/audit_report.md` | benchmark audit findings |
| `results/audit/datasets.csv` | licence, evidence URL, row count, content fingerprint per dataset |
| `results/experiment_001/report.md` | Experiment 001 tables + figures |
| `experiments/003_frontier_judges/PLAN.md` | Experiment 003 pre-registration |
| `VERIFY_ME.md` | everything not verified, failed, skipped or uncertain |
| `DECISIONS.md` | every design choice and why (D-001 to D-052) |
| `NEXT_STEPS.md` | what to review first, and how to run things |

## Model backends (one client, environment variables only)

Both evaluation models and judges go through a single OpenAI-compatible client.
Resolution order:

1. explicitly configured endpoint (`MEDEVAL_BASE_URL` + `MEDEVAL_MODEL` + `MEDEVAL_API_KEY`);
2. a local OpenAI-compatible server detected on `127.0.0.1` (Ollama, LM Studio, vLLM);
3. a small local Hugging Face model on CPU, if `torch`/`transformers` are installed and `MEDEVAL_HF_MODEL` is set;
4. the deterministic **MOCK** judge.

From v0.3 the preferred path is **connections** on the Models page: they bypass
the environment entirely (keys in memory), and Experiment 003 requires them.
Experiment 002's CLI still reads `MEDEVAL_*` variables, plus
`MEDEVAL_JUDGE1_MODEL`, `MEDEVAL_JUDGE2_BASE_URL` and `MEDEVAL_JUDGE2_MODEL`.
Every result file records which backend produced it. Mock results are labelled
**PIPELINE TEST ONLY**, and no key is ever written to a log, result or report (a
test enforces this).

## Data and licences

| Dataset | Licence | Used for |
|---|---|---|
| `GBaker/MedQA-USMLE-4-options-hf` | cc-by-sa-4.0 | Experiment 002 items |
| `openlifescienceai/medmcqa` | apache-2.0 | item-integrity audit (no Experiment 002 use) |
| `bigbio/pubmed_qa` | mit | audit, skipped (legacy loader script unsupported) |

**No benchmark text is redistributed.** The sampled items, candidate answers and
full call logs stay in git-ignored files; only ids, verdicts, confidences, hashes
and aggregate metrics are committed. `results/audit/datasets.csv` holds the licence
evidence and a content fingerprint per dataset.

## The 40 public seed items

Historical v0.1 results used 50 synthetic, original-wording items. The ten rows
that were marked hidden have been moved to the gitignored `.private/` folder; the
tracked `data/items/seed_v0.1.json` now contains the 40 public rows and
`hidden_split_sha256.txt` retains only the ten hashes. They are illustrative, not a
curated examination. See `data/items/HOWTO.md` to add items from papers.

## Literature traceability

`data/literature.csv` holds 21 rows with the columns
`id, doi, title, year, task, reference_standard, metric_proposed,
how_we_operationalize, limitations, verified`.
Every criterion and metric cites at least one row, and CI fails if a citation has
no row. DOIs were checked against Crossref/DataCite; **20 of 21 resolved to a
title-matching record** — the exception is MedHallu, left unverified on purpose.
See `VERIFY_ME.md`.

## What this deliberately does not do

- No leaderboards, no composite score in any conclusion, no hosted service.
- No paid API calls by default and no network calls unless a command asks for them.
- No radiology, dermatology, images or multi-modal input.
- No clinician workflows; every schema carries an empty `human_reference` field so
  they can be added later without redesigning anything.
- No editing of the upstream `lm-evaluation-harness` checkout.
- No MedMCQA item-integrity analysis (that is reserved for a separate OSF-registered
  audit) and no MedMCQA use in Experiment 002.

## Repository layout

```
packages/            medeval_core, medeval_audit, medeval_criteria, medeval_metrics,
                     medeval_judges, medeval_reliability, medeval_study, medeval_web,
                     medeval (CLI)
data/items/          seed items, template, HOWTO
data/literature.csv  literature registry
data/rubrics/        rubric v0.1 (weights + judge instructions)
data/tasks/          lm-evaluation-harness task variants (via --include_path)
experiments/001_judge_reliability/   PLAN.md, data/, run.py
experiments/002_judge_families/      PLAN.md (pre-registration + deviations)
experiments/002a_pairwise_blocked/   archived earlier protocol, no real-judge result
results/             generated outputs (audit, harness runs, experiment 001, study runs)
tests/               unit tests, including hand-computed statistics
docs/                architecture, statistics, harness-integration notes
run_all.ps1          one-command reproduction (use run_all.cmd on Windows)
```

## Requirements

- Windows 11 with PowerShell 5.1+ (tested), Python 3.11+ (built on CPython 3.14.6).
- The EleutherAI `lm-evaluation-harness` checkout for the baseline stage; without
  it everything except `medeval harness` still runs.
- Optional: a remote OpenAI-compatible endpoint (J1) and Ollama with a small model (J2).
- For the web app: `fastapi` and `uvicorn`, both in `requirements.txt`.

Pinned versions are in `requirements.txt`.

## Limitations (read these before citing anything)

1. **No clinician reference standard.** Ground truth is an official benchmark answer
   key, treated as an imperfect reference. No key audit was performed.
2. **Experiment 002 ran with one judge.** Cross-family error correlation is
   unmeasured; the single-judge results describe one model on one sample.
3. **The sample is at ceiling.** 98.3% accuracy on 60 items means the task cannot
   discriminate between judges or populate the both-wrong cell.
4. **Confidence was uninformative.** Every value was 95–100, so calibration metrics
   are near-perfect by construction and cannot show that the judge knows when it is
   wrong.
5. **The candidate rationales are template filler.** This measures whether a judge
   recognises the correct option, not whether it can grade an argument.
6. **The rule judge is narrow and optimistic.** Its 96% in Experiment 001 is an
   upper bound; on post-freeze held-out cases the frozen rules scored 65%.
7. **Contamination risk is unquantified.** The audit's screens are cheap screens,
   not evidence of contamination-free evaluation.
8. **Benchmarks drift.** Public datasets change without version bumps; the audit
   records a content fingerprint so at least the audited snapshot is identifiable.
9. **Statistically small.** 60 items; every interval is wide and no result survives
   a multiple-comparison correction.

## Licence

MIT (see `LICENSE`). Dataset licences are recorded in `results/audit/datasets.csv`
and `data/literature.csv`; the seed items are released as `synthetic-original`
(CC0-compatible).
