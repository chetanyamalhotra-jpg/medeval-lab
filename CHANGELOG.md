# CHANGELOG

Versions are tracked separately for the toolkit and for each artefact, because a
score is only interpretable together with the criteria, rubric and item versions
that produced it.

## 0.6.0 — 2026-11-22

The next-generation harness: published evaluation methods become declarative
**Paper Packs**, and a **matrix runner** benchmarks N target models against M
decoupled graders/judges with content-addressed caching, offline re-grading and
paper-grade exports. **No live paid call was made** during development or tests;
every run used a mock transport or a local fake server.

### Added

1. **Paper Packs** (`packages/medeval_packs`, `papers/`). A strict, versioned
   `paper.yaml` schema covers identity/citation, methodology tag, task type,
   local dataset adapter, strict `$variable` prompt templates, composable
   deterministic + LLM-rubric graders and a statistical protocol. Unknown keys,
   dataset path escapes and clinical-validation claims are rejected with every
   error listed. Three synthetic starter packs ship:
   `template-clinical-mcq`, `template-rubric-panel` and
   `template-structured-extraction`.
2. **Five built-in presets** representing distinct medical-AI evaluation
   primitives: `healthbench-rubric`, `medcalc-bench`, `medhallu-detect`,
   `medxpertqa-text` and `medsafety-bench`. Each ships full bibliographic metadata,
   BibTeX, an explicit `source_dataset` URL/Hugging Face reference,
   `adaptation_status`, an optional on-demand `loader.py` (writes only to the
   git-ignored `.medeval_data/` after schema verification) and 3-5 strictly
   synthetic `smoke.jsonl` rows tagged
   `PIPELINE TEST / SYNTHETIC SMOKE DATA`. **No upstream dataset is bundled.**
   New deterministic graders cover exact and ±5% numeric tolerance, structured
   parameter extraction, three-class macro F1/precision and coverage.
3. **Matrix runner.** 1 Paper Pack × N target models × M graders/judges, with
   targets and evaluators as separate roles; deterministic graders for MCQ,
   label, abstention, format, JSON schema, field F1/exact match, exact match and
   refusal; an LLM rubric panel with per-judge scores and rationales.
4. **Crash-safe execution.** Append-only fsynced `events.jsonl`,
   `outputs.jsonl` and `scores.jsonl`; an atomic `run.json` (with a Windows
   sharing-violation retry); stop/resume that never repeats a completed
   evaluation; a `.medeval_cache/` keyed by
   `sha256(model + params + rendered_prompt + item_hash)` so identical requests
   are free.
5. **Budget guardrails.** A pre-flight plan (calls, token upper bounds, optional
   USD ceiling) plus a hard per-call check across the run cap and each
   connection's caps; exceeding one stops the run as `budget_stopped` with all
   completed items saved and exported.
6. **Offline re-grade.** `medeval regrade <run_id> --paper <pack>` re-scores
   cached target outputs with a new rubric/grader/judge family and records
   `target_calls: 0`.
7. **Statistics** (`medeval_core/stats.py`). Seeded percentile and stratified
   bootstrap CIs, exact/asymptotic McNemar, paired bootstrap, Cohen/Fleiss/
   Krippendorff, cross-family error correlation, disagreement matrix, latency
   percentiles, abstention/format compliance and cost-per-correct-item.
8. **Publication exports.** Every matrix run writes `report.md` (notice header,
   methodology card, CI summary, pairwise tests, judge agreement, cost/latency,
   limitations), `results_table.tex` (`% MEDICAL_NOTICE`), `citations.bib`,
   `metrics.json`, `items.csv` and a deterministic `bundle.zip`.
9. **Local UI.** A Paper Registry page (packs + read-only archived studies), a
   matrix builder with a live pre-flight plan card and cross-family indicator,
   and a run page with Stop/Resume, a head-to-head/disagreement/error explorer,
   export and offline re-grade.
10. **CLI.** `paper list|validate|init`, `matrix run|list|show`, and `regrade`.
   `medeval run exp002/exp003` is unchanged.
11. **Security/reliability hardening.** `no-store` on every response,
    `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
    `X-Frame-Options: DENY`, `Permissions-Policy` and a same-origin CSP, on top
    of the token/Host/Origin checks. `connections.yaml` accepts the
    `api_key_env` alias for `key_env` and a `role: target`; a plaintext key is
    still refused without echoing it.
12. **Tests and CI.** New unit, statistics, cache/resume, budget, re-grade,
    export, CLI, web-API and Playwright tests; the three v0.5 skipped browser
    flows are now deterministic. CI targets `ubuntu-latest` + `windows-latest`
    (Python 3.12/3.13) and an offline Playwright job — **added but unrun**, as
    nothing was pushed.

### Protected artefacts

`results/study_runs/exp002-main`, `exp002-full`, `exp002-smoke` and
`experiments/002_*` are byte-identical; the archived studies remain readable
(and are listed as read-only in the Paper Registry).

## 0.5.1 — 2026-11-21

Three follow-ups to the bring-your-own-key release, each pinned by regression
tests. **No live (paid) API call was made** and no `.env` file was opened.

### Fixed

1. **The Models page preselected a provider.** The provider preset dropdown
   started on "DeepSeek", so the first thing a stranger saw was a named provider
   they had not chosen. It now starts on an empty **"Choose a provider"** option
   with the presets still listed below it. Until a preset is chosen, the base URL,
   model, family and price fields stay empty, **Save connection is disabled**, and
   a hint says why. Choosing a preset fills the **base URL only** (editable);
   it never selects a model or a price, and returning to the empty option clears
   the URL and disables Save again.
2. **The archived Experiment 002 path still resolved one provider for new runs.**
   `packages/medeval_study/runner.py` no longer resolves `j1_deepseek` to a
   "deepseek" family, an environment-configured endpoint or a default model, and
   no longer probes for the local Ollama judge. New runs — Experiment 002 and 003
   alike — take their judges from the saved connections or from
   `--connections connections.yaml`; a live run with no connection stops with the
   "add a connection on the Models page / pass --connections" message. The old
   identifiers are kept only to **read archived runs** and are labelled as the
   original run's configuration; their dry run still maps to mock judges and stays
   `PIPELINE TEST ONLY`. `available_judge_summary` (a legacy provider status that
   probed Ollama) and the `/api/settings.judges` payload are gone; the Settings
   page now reports connection counts, and the run page no longer reads a
   built-in provider. `rebuild`/report reading of `exp002-main` is unchanged.
3. **The "not a medical device" notice was missing in the app.** It now appears:
   - as a short notice on the **start screen** and inside the **first-run card**
     (research tool, not a medical device, not for patient care or clinical
     decisions, no clinical validation, small samples exploratory);
   - as a **one-line notice in the footer of every page** (the app shell);
   - as the **header of every generated report** (`report.md`, and therefore in an
     exported bundle), from a single `MEDICAL_NOTICE` constant in the report
     writer so no caller can produce a report without it.

### Tests added

- `tests/test_v051_fixes.py` (9 tests): the Models form has no preselected
  provider and a preset fills the base URL only; a live run with no connection
  stops with the add-a-connection message (and writes it to `run.json`); a live
  run with **two fake connections** completes with both judges; the archived ids
  are read-only for a live run but still work for their dry run (mock judges,
  `PIPELINE TEST ONLY`); the runner carries no built-in model/family/env defaults
  and exports no legacy provider summary; reading `exp002-main` works and leaves
  the files byte-identical; the generated report header carries the notice before
  the run content; the app shell and start screen carry it too.
- `tests/browser/test_v051_fixes.py` (2 tests): a fresh Models page has the empty
  option selected and Save disabled, choosing a preset enables Save and fills only
  the base URL (model, family and prices stay empty); the notice is present on the
  start screen, in the shell footer of every page, and in a report read through
  the real report endpoint.
- `tests/test_web_api.py::test_settings_never_exposes_a_key` now asserts the
  settings payload carries connection counts instead of a named provider status.

## 0.5.0 — 2026-11-21

**Open-source release with bring-your-own-key.** A stranger can clone the
repository, install it, start the app, add their own provider and key, run a dry
run and then a small live run with caps — and nothing in the tracked tree contains
the owner's key, personal paths or details they have not chosen to publish.
**No live (paid) API call was made** while preparing or verifying this release,
and no `.env` file was opened.

### Security and privacy (M1, M3)

- New `scripts/oss_audit.py` (with 12 tests) fails on key-like strings, tracked
  `.env` files other than `.env.example`, personal paths (drive-letter user
  directories, `/home/<name>`, `/Users/<name>`), emails outside
  `.github/allowed-emails.txt`, tracked `calls.jsonl`/`cache.json`/`items.jsonl`/
  `candidates.jsonl`/reviewer notes, files over 5 MB, and any string from the
  git-ignored personal deny-list. Findings print the path, line and rule only.
- `OSS_AUDIT.md`: the full audit. Tracked tree is clean (`0 finding(s)`, 12
  documented exceptions). 19 occurrences of the build machine's path were found in
  17 tracked files: 15 in 13 files were neutralised (`<repo>`, `%USERPROFILE%`,
  `MEDEVAL_HARNESS_PATH`), 4 historical v0.1 records were kept byte-identical and
  are listed with reasons in `.github/oss_audit_allowlist.txt`. History (38
  commits, 590 blobs) reports 261 findings — old paths, the ten formerly hidden
  seed items, and 13 historical test fixtures that are not credentials. The two
  history options for the owner (publish as-is vs. clean squashed copy) are
  written down with their trade-off; nothing was rewritten.
- **Redirects are refused.** The API key goes only to the exact configured host:
  a `3xx` is a terminal error and is never followed or retried, so the
  `Authorization` header cannot be replayed to another host. A fake-server pair
  proves the second host receives nothing.
- `base_url` rejects embedded credentials (`user:pass@host`) and non-http(s)
  schemes; plain `http` to a non-loopback host warns. The app binds to
  `127.0.0.1` only: a non-loopback `--host` is refused unless `--allow-remote`,
  which prints a loud warning and keeps the token, Host and Origin checks active.
- `medeval doctor` now warns about local `.env*` files (names only, never
  contents), an off-loopback bind and `--use-env`.
- `SECURITY.md`: supported versions, private reporting (owner placeholder), the
  threat model, the key-handling guarantees and — equally explicit — their limits.

### Bring your own key (M2, M4)

- **No default provider, judge, model or price.** A fresh install has zero
  connections and the app says how to add one. `StudyConfig.judges` defaults to
  empty; the CLI and the app refuse a live run without a connection instead of
  silently using a provider. The archived Experiment 002 ids
  (`j1_deepseek`/`j2_ollama`) remain only when named explicitly, documented as the
  original run's configuration; their `DEEPSEEK_API_KEY` fallback and
  `deepseek-chat` default are removed (a missing model is now an error, never a
  guess).
- **Headless BYOK:** `connections.yaml` + `connections.example.yaml`. Each entry
  names an environment variable (`key_env`) instead of holding a key; a key field
  is refused outright, as are unknown fields and credential-bearing URLs. A live
  CLI run needs `--connections <file>` **and** `--confirm-live`: it prints every
  connection (host, model, family, no key), the call count, the per-connection cap
  check, the worst-case cost and the run caps, then calls nothing without
  confirmation. There is no `--api-key` argument, and a test walks every subparser
  to keep it that way.
- **Presets are convenience, not defaults:** base URL, API style, documentation
  link, pricing link and a `last_verified` date, all editable, and **no price is
  ever shipped**. With a price unset the estimate says "unavailable, call cap is
  binding"; per-connection and run-wide caps still stop the run. `Test connection`
  lists the provider's models and the user picks one (nothing is auto-selected).
- **Dry runs use neutral mock judges** (`medeval_study.mock_bindings`) instead of
  mapping provider ids to mocks, so no provider appears in a key-free path.
- `.env.example` contains placeholder names only, stays git-ignored as `.env`, and
  is not loaded by the app unless `--use-env` is passed.
- First-run card in the app and the README say plainly: you pay your own provider,
  set a spend limit in the provider's dashboard too, and the maintainers cannot
  see, stop or refund your usage.

### Open-source scaffolding (M5) and CI (M6)

- New: `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `SECURITY.md`,
  `NOTICE`, `CITATION.cff` (placeholder authors), `.github/ISSUE_TEMPLATE/`
  (bug, feature, benchmark quality), `.github/PULL_REQUEST_TEMPLATE.md`,
  `docs/BYOK_QUICKSTART.md`, `docs/ADD_A_PROVIDER.md`, `docs/ADD_A_BENCHMARK.md`,
  `docs/REPRODUCE_EXPERIMENT_002.md`.
- `.pre-commit-config.yaml` runs the same ruff, audit and secret-scan checks as
  CI. `.github/dependabot.yml` proposes grouped Python and Actions updates.
  CI gains the release audit and an advisory (non-blocking) `pip-audit` job; the
  full-history audit is a separate `workflow_dispatch` job because it cannot be
  green until the owner chooses a history strategy. CI has still **never run**:
  nothing is pushed, and no document claims otherwise.
- `requirements.txt` now states that it *is* the reproducible environment
  (`pyproject.toml` keeps floors) and how to recreate it.

### Tests added

- `tests/test_v050_byok.py` (28 tests): the connections file (parse, key-field
  refusal, credential URLs, field rules, loopback key_env exemption, screening
  rules, worst-case cost), the CLI plan/refusals/mock bindings, no `--api-key`
  anywhere, redirect refusal, bind refusal and warning, doctor warnings,
  presets without prices, `/api/estimate` saying the call cap binds, the absence
  of provider ids in new-run code, and the first-run wording.
- `tests/test_oss_audit.py` (12 tests): key shapes vs. fixture markers, personal
  paths vs. placeholders, email allow-list, tracked `.env`/run artefacts, the
  5 MB limit, the deny-list, the allow-list matcher, and a throwaway git repo
  proving only tracked content is judged and that `--history` sees old blobs.
- One existing test (`tests/test_study_runner.py`) now names the archived
  Experiment 002 pair explicitly, because new runs no longer have a default.

## 0.4.2 — 2026-11-21

Two bug fixes, each pinned by regression tests. **No live (paid) API call was
made**; no dataset download is attempted by a run any more.

### Fixed

1. **Experiment 003 dry run failed without the dataset.** Two causes:
   - the synthetic fallback sample was only 60 items, and
   - `load_excluded_item_ids()` globbed `exp002-*`, so stray test run directories
     (whose items were the synthetic sample) excluded *every* synthetic id. The
     screening stage then reported "0 eligible", wrote an empty screening file and
     the next stage raised `FileNotFoundError`.

   The sample is now **420 items** with ids of the form `synth-<seed>-<index>`
   that cannot collide with MedQA's `dev-#####` ids, and the exclusion list is
   read only from the committed Experiment 002 run records named in the
   pre-registration (`exp002-main`, `exp002-full`, `exp002-smoke`). If screening
   still finds zero eligible items — from any data source — the run stops with
   status `blocked` and a plain message naming the cause and the exact fix
   (`medeval data fetch <dataset>`); no traceback, no empty screening file, no
   stage offered. The app shows the same words on the run page and the Stages
   card, and the blocked card offers no "Confirm & run" button.

2. **Offline dry runs looked stuck.** The loader retried the network for a minute
   or more (about five attempts with 8-second pauses) before the synthetic
   fallback started, and the run page sat on "queued" with no explanation.
   - A run now **never downloads**: a cheap filesystem check decides whether the
     dataset is cached, and if not, a dry run goes straight to the synthetic
     fallback and a real run stops at once with the fetch-command card.
   - Dataset reads run with `HF_HUB_OFFLINE`/`HF_DATASETS_OFFLINE` forced on, so
     no library retry can stall a run.
   - `medeval data fetch` — the only thing that downloads — now uses a short
     timeout (`HF_HUB_ETAG_TIMEOUT=5`, `HF_HUB_DOWNLOAD_TIMEOUT=20`) and at most
     one retry (`HF_HUB_MAX_RETRIES=1`).
   - A run that is reading its dataset reports status `loading` and the run page
     says **"loading dataset…"** instead of "queued".

Measured on this machine with the cache emptied and every socket blocked: the
full staged Experiment 003 dry run finishes in **8.9 s with 0 network attempts**
(420 eligible synthetic items, all four stages, PIPELINE TEST ONLY).

### Tests added

- `tests/test_v042_fixes.py` (13 tests): the sample's size, licence and
  non-colliding ids; the exclusion list ignoring stray `exp002-*` test runs; a
  full staged offline exp003 dry run (all stages, PIPELINE TEST ONLY in state,
  metrics and report, 0 network attempts); the 0-eligible blocked message and
  that no screening file or next stage exists; the Stages API payload carrying
  the reason; an offline dry run finishing under 10 s with 0 attempts; a real run
  blocking immediately; the cache check never downloading; the offline/download
  environment contexts.
- `tests/browser/test_v042_fixes.py` (3 tests): the Stages card shows the blocked
  reason with the fetch command, a blocked run offers no "Confirm & run" and no
  Results link, and a loading run says "loading dataset…" rather than "queued".

### Changed

- Tests that faked the dataset now patch the cache gate
  (`runner.dataset_cache_status`) instead of the loader, matching what a run
  actually calls.

## 0.4.1 — 2026-11-20

Bug-fix release. **No live (paid) API call was made**; every fix is pinned by a
regression test, and the browser suite now runs 23 flows (3 remain manual).

### Fixed

1. **Duplicate page on re-render (serious).** `renderModels`, `renderPapers` and
   every other page renderer now start with `beginRender(view)`, which closes any
   live event stream and clears the view. Saving a connection, forgetting a key,
   deleting a connection, adding a paper and saving roles no longer stack a second
   copy of the page. Pinned by browser tests that assert exactly one `Models` /
   `Papers` page after each action.
2. **Run page.** The status line printed the literal text `nullnull…` because it
   appended `null` children through `Element.append` instead of the DOM helper
   that skips them; it now renders only the values that exist. A run shows its
   real start time (a queued run records `started_utc`, and a page without one
   says "starting…" instead of "started —"). Results and Item explorer are hidden
   for a failed, blocked, refused or aborted run, which offers "no results" text
   instead; Stop is disabled whenever the run is not active, including after the
   stream reports a terminal status.
3. **API key input width.** `input[type="password"]` was missing from the shared
   input rule, so the key field had no width, border or padding; it is styled
   like every other field and grows to fill its row.
4. **Duplicate connection names.** The store rejects a name that already exists
   (case- and whitespace-insensitive) on create and on rename, and the API
   returns HTTP 409 with "a connection named 'x' already exists".
5. **Live role warnings.** The same-family warning and the
   screening-is-a-judge error are recomputed on every role dropdown change, so
   they appear before Save. The save-time checks remain, and two judge roles are
   shown by default so the two-judge design is what you see first.
6. **Dry run without the dataset.** New `medeval data fetch <dataset>` downloads
   and caches a split and prints its licence (name, redistribute flag, evidence
   URL, source hash); `medeval data status` lists what is cached. A dry run that
   cannot read the dataset now falls back to a built-in synthetic sample labelled
   **PIPELINE TEST ONLY** (recorded in `run.json`, `metrics.json` and the report).
   A real run stops with status `blocked` and a reason naming the exact command
   to fetch the data.
7. **Tests.** The "interpreter name" assertion accepts `python3`/`python3.12` and
   any path, and the git-commit assertion skips when there is no `.git`
   (e.g. running from an extracted archive). The browser flows now use stable
   `data-testid` attributes: 4 of the 7 previously-skipped flows run and pass
   (Models page create/test/roles, same-family and screening warnings, Papers
   add-by-DOI, command palette and chords); 3 keep a written skip reason and their
   `MANUAL_SMOKE.md` step.

### Added

- `medeval_study/synthetic.py`: the built-in 40-item synthetic sample, its
  licence registration and the fetch-command helper.
- `medeval_study.datasets.DatasetUnavailable`: the "data cannot be read" state,
  distinguished from a crash so both the CLI and the runner can react sensibly.
- `create_app(metadata_base=…)`: the Papers fetch endpoint can be pointed at a
  fake Crossref server, so the browser suite never reaches the real one.

### Tests added

- `tests/test_v041_fixes.py` (11 tests): duplicate names (create, case/space,
  rename), synthetic bundle labelling, dry-run fallback end to end (bundle,
  state, metrics, report), real-run blocked reason with the fetch command, the
  API round-trip of that reason, the fetch command text, `medeval data status`,
  the missing-loader message of `medeval data fetch`, and a queued run's
  `started_utc`.
- `tests/browser/test_v041_fixes.py` (8 tests): duplicate-page regressions after
  save / forget-key / delete / add-paper, no `null` on the run page, real start
  time, hidden outputs and disabled Stop for a failed run, key-input width, and
  role warnings appearing before Save.

## 0.4.0 — 2026-11-19

Built from the v0.4 "fix and complete" prompt. **No live (paid) API call was made
at any point**; every path was exercised against mock judges, a local fake
OpenAI-compatible provider and dry runs. There is no `REPORT_v0.4.md` because no
real run exists yet.

### M1 — Browser-verified UI
- Playwright end-to-end suite (`tests/browser/`) driving the real app against a
  fake provider and a fake Crossref: start screen, first-run card, sessions and
  the composer, slash commands, theme control, Models page, token/Host/Origin
  refusals, accessibility basics and contrast in both themes. **11 pass, 7 skip
  with a written reason** (see `VERIFY_ME.md` V5-3 and `MANUAL_SMOKE.md`).
- `scripts/capture_screenshots.py` writes 36 screenshots (6 pages × light/dark ×
  desktop/phone/tablet) into `docs/screenshots/`, fake data only.
- Accessibility: labelled controls, visible focus ring, `aria-live` run progress,
  and a `prefers-reduced-motion` block.
- Bugs found and fixed by the browser pass: the boot health check ran before the
  launch token was read, so the first 403 latched a lock screen onto every later
  route; the token URL redirected to `#/runs` instead of the start screen; the
  command palette had no entries for Models, Papers or Tests; the stylesheet had
  no `prefers-reduced-motion` rule.

### M2 — Live-run robustness (tested against the fake server)
- Retries on 429/5xx with exponential backoff plus jitter, honouring
  `Retry-After`; never retries 400/401/403/404/422 or a refusal.
- 401/403 raise a distinct `AuthError` that is not retried and is recorded as
  `error_type: auth`; provider and spend-guard refusals now classify as
  `failure_type: refused` rather than `api_error`.
- Per-call timeout (default 120 s, configurable), per-connection concurrency
  limit (default 2), connection-reset and timeout surfacing.
- Usage parsed from provider fields including reasoning-token variants; optional
  `verdict_logprob` captured only when the provider returns it; empty replies
  whose reasoning tokens exhausted `max_tokens` are flagged so the runner warns
  with the exact advice instead of failing silently.
- Fake-server modes now cover 429 + Retry-After, 500, slow, timeout, reset,
  malformed JSON, empty reply, refusal, 401, 403, reasoning exhaustion and
  logprobs; 36 tests assert the retry counts, delays and failure types.

### M3 — Experiment 003 live readiness (pre-registration amendment 1)
- Dated amendment committed **before** the code: adaptive screening in batches of
  50 that stops on `target_met` (100 hard items), `exhausted` or `cap_reached`
  (the lesser of 1 000 items and the screening connection's call cap), recording
  the reason; controls are 40 screened-correct items.
- Staged controller: Screening → Review hard pool → Pilot → Gate → Remaining,
  each a confirmed process with its own worst-case call count and USD estimate,
  run status `awaiting_confirmation`, and resumable after any stage.
- Blocking role rules: the screening model cannot also be a judge, its family
  label must differ from every judge's, and at least two judge connections are
  required. Dry-run screening stays in the run directory and is labelled mock;
  real screening commits ids and correct/incorrect flags only.
- Fixed a real bug: `StudyRun` never reloaded `run.json`, so each stage of a
  resumed or staged run forgot the previous one.

### M4 — Review, report and export
- Single-reviewer queue with the four pre-registered labels
  (`key looks correct`, `key looks wrong`, `ambiguous`, `unsure`), keyboard
  n/p/1–4, notes in a git-ignored `data/review_notes.json`; labels never enter
  the headline metrics.
- `medeval report <run_id|exp003>` regenerates `report.md` from stored verdicts
  and appends a labelled sensitivity table with `key looks wrong` items removed.
- `medeval export <run_id>` and a Results-page button produce a zip of
  `metrics.json`, `items.csv` (ids only), `review_queue.csv` (ids only),
  `manifest.json`, `report.md` and a README; notes only on an explicit,
  warning-confirmed ask; the exporter refuses key-shaped or long-text files.

### M5 — Papers library
- 19 candidate papers added from the owner's secondary-summary list, verified
  through the automated Crossref/arXiv title-match script (40 rows, 39 verified;
  only the pre-existing `lit_medhallu` stays unverified and is listed in
  `VERIFY_ME.md`). Ids, titles and honesty findings are recorded there.

### M6 — First-run experience and packaging
- First-run card (add a connection → test it → dry run → plan card), dismissible
  and reopenable from Settings via `/api/preferences`.
- `medeval doctor`: Python, dependencies, layout, port, token flow, git, Ollama,
  settings booleans (never values), a fast self-test and disk space; it never
  prints or stores a secret.
- Port handling: a busy port is explained — a stale medeval server gets the exact
  PowerShell command to stop it — then the next free port is used and the browser
  is opened with a fresh token.
- `USER_GUIDE.md`, `TROUBLESHOOTING.md`, `MANUAL_SMOKE.md`; README quickstart.

### M7 — Release hygiene
- Version 0.4.0 everywhere, with a test that pins `pyproject.toml`,
  `__version__` and the CHANGELOG section together.
- CI (Python 3.12/3.13: ruff, secret scan, pytest, validation, traceability) now
  collects the browser tests, which skip when no browser can start.
- Secret scan passes over the working tree and reachable git blobs.

## 0.3.0 — 2026-11-18

Built from the v0.3 master prompt. **No live (paid) API call was made at any
point during this release**, and no `REPORT_v0.3.md` exists: per the prompt, a
results report is created only after a real run.

### Added — M2/M2b: Models screen, keys in memory, local-site protection
- Connections with provider presets (DeepSeek, OpenRouter, OpenAI, Ollama,
  Custom OpenAI-compatible). Preset base URLs were read from each provider's
  official documentation at build time (sources and date in `VERIFY_ME.md`);
  every preset field stays editable.
- **Keys live only in app-process memory** (KeyStore): sent from the browser over
  loopback in a request body, shown only as "key set ····last4", forgotten on
  demand and when the app stops. No disk persistence option exists; `.env`
  loading is off by default (`medeval ui --use-env` opts in).
- Local-site protection: a random launch token per app start (`#token=...` in the
  URL fragment → `sessionStorage` → `X-Medeval-Token` header; 403 without);
  non-loopback `Host` headers and cross-site `Origin` headers are refused; no
  CORS headers. Loopback binding unchanged.
- Models page: connection CRUD, masked key with show/hide, **Test connection**
  (list models, no spend; one-token call behind explicit confirm), roles for
  Judge 1..N plus a Screening model that must not also be a judge, and a
  same-family warning ("cross-family error correlation cannot be measured
  between these judges").
- Redaction: every key in play is stripped from logs, caches, run.json,
  manifests and reports; `scan_secrets.py` gained provider key-shape patterns,
  a fixture-marker allow-list, and a documented known-fixture exclusion list
  for pre-v0.3 history (history is never rewritten).

### Added — M2b: harness-style UI, Papers library, Test center
- Shell with a collapsible left sidebar: brand + LAB tag, New session,
  **workspaces as folders of sessions** (v0.2 runs import automatically under
  "Experiment 002"), Settings pinned at the bottom.
- Start screen: "Describe an evaluation" with a Preview badge; Workspace, Mode
  (Dry run / Pilot / Full) and Experiment chips; a composer that takes plain
  notes (saved as the session's hypothesis, no model call) or slash commands
  (`/run`, `/dryrun`, `/test`, `/papers`, `/report`) with a command list on `/`;
  a Spend policy dropdown and a Judges selector; sessions render as a thread of
  cards (plan, progress, results, flagged items, report).
- **Papers library** over `data/literature.csv`: add by DOI or arXiv id
  (user-initiated Crossref/arXiv metadata fetch, announced before it runs;
  tests use a local fake metadata server), status tags and local notes in a
  git-ignored file, a reverse "what cites this paper" view, and `verified`
  settable only by the automated title-match script.
- **Test center**: an allow-listed command set (pytest, ruff check, secret scan,
  traceability, key-isolation tests, Experiment 002/003 dry runs). The browser
  selects an item and never sends a command string; commands run with fixed
  argv, fixed working directory and no shell; results keep counts, ids and
  durations only (last 20). A pre-flight checklist is shown on run plans.

### Added — M3: N-judge runner
- `JudgeBinding`: any number of judges bound to connections, with per-judge
  repeats, per-connection call and USD caps (enforced by a chained guard before
  every call), and refused calls recorded as `failure_type: refused`.
- `--consistency-subsample N`: three repeats on N items only (default off,
  suggested 20), so consistency does not triple cost.
- Reasoning-budget warning: when a call spends its whole `max_tokens` on hidden
  reasoning (the mechanism behind the v0.2 abort), the run warns with the exact
  advice per judge. Reasoning tokens are recorded per call.
- Tests run against a **local fake OpenAI-compatible server** (verdicts,
  malformed, truncated and HTTP-500 modes) plus the mock transport — the whole
  judge path works with no network. Ollama connections keep working without a key.

### Added — M1: fixes and reporting
- The `[hidden] { display: none !important }` rule stays the first stylesheet
  rule (regression-tested); aborted runs show status and reason with **no
  headline metric** anywhere; "Possible key errors" is renamed **"Flagged for
  review"** with the producing rule and the not-a-key-error caveat; 390px
  overflow fixed; `g r`/`g n`/`g s`/`t` chords implemented and ignored while
  typing; the dry-run estimate reads "Dry run: 0 network calls, US$0" with the
  live plan under "If run live"; the theme control is a three-state
  Auto/Light/Dark segmented control.
- Accuracy against the key is reported two labelled ways (failures excluded;
  failures counted as wrong); a **config lock** refuses a resumed run whose
  settings differ from the stored config, printing each differing field before
  any call; every judgement carries a `failure_type` with per-judge counts;
  v0.2 run directories still read.

### Added — M4: Experiment 003 (built, not run)
- Pre-registration committed **before** the implementation
  (`experiments/003_frontier_judges/PLAN.md`): difficulty enrichment by a
  screening model, stratified 100-hard + 40-control sample on a new seed with
  Experiment 002's items excluded, a pilot gate (stop unless ≥ 5 judge-vs-key
  disagreements on the first 40 hard items), the unanimous-dispute flag rule
  writing `review_queue.csv` (ids only), stratified metrics, and the
  single-reviewer caveat (local notes, one opinion, not a reference standard).
- `medeval run exp003 --dry-run` completes with mock judges and the mock
  screener and writes every output, including the review queue and strata.
  A live CLI run refuses honestly and points at the web app.

### Release hygiene (M5)
- CI runs `ruff` + `pytest` on Python 3.12 and 3.13 from tracked files only,
  plus the secret scan; it still has never executed (no push yet).
- No live API call was made; Experiment 002 artefacts are untouched.

## 0.2.0 — 2026-11-17

### Added — Experiment 002 (real-judge study, `experiments/002_judge_families/`)
- Pre-registration (`PLAN.md`) committed before the first real model call, with an
  append-only deviations log.
- `medeval_study` package: licence-checked MedQA-USMLE loading, stem-deduplicated
  seeded sampling with balanced conditions, template-built candidate answers (six
  shared templates, lexically-similar distractor, no model in the loop), a strict
  JSON judge layer with one retry, and a resumable runner.
- Two judges by design: J1 remote API (DeepSeek) and J2 local CPU (Llama via
  Ollama). J2 unavailability is a recorded, instructed failure — never a silent
  substitution.
- Metrics with intervals: accuracy, false accept, false reject, Cohen's kappa vs
  key, Gwet AC1, ECE (5 bins), Brier, two AUROCs, self-consistency across repeats,
  ceiling rate and score distribution; Wilson for proportions and a 1,000-resample
  percentile bootstrap over items for the rest.
- Cross-judge panel (percent agreement, kappa, phi between error indicators,
  both-wrong rate versus independence with binomial **and** permutation tests,
  McNemar) — computed only when two judges actually ran.
- Possible-key-error flagging: the pre-registered two-judge rule, plus a clearly
  weaker single-judge fallback that is never promoted to a key error.
- Reports in Markdown, JSON and CSV with a mandatory Limitations section; run
  manifest with git commit, versions, model ids, seeds, prompt hashes and timestamps.
- Cost controls: `--max-api-calls`, worst-case token/cost printed before the first
  call, `--dry-run` with mock judges.
- `medeval rebuild <run_id>`: reanalyse and rewrite a report from stored evidence
  with no model call.

### Added — local web app (`packages/medeval_web/`)
- FastAPI backend on `127.0.0.1` with a hand-written static frontend: no build
  step, no CDN, no telemetry.
- Pages: runs list, new-run form with live call/token/cost estimate, run detail
  with server-sent progress and a streaming log, results (metric cards with
  intervals, confusion matrices, reliability diagram, agreement and
  error-correlation panels, persistent limitations banner), filterable item
  explorer with a detail drawer, and settings (key present/absent, Ollama status,
  paths, caps, prices, dataset licences).
- Command palette on `Ctrl+K`, light/dark/system themes with a manual toggle,
  keyboard-accessible controls, responsive to tablet width.
- Live runs require explicit confirmation; a stop button finishes the call in
  flight and writes reports from the judgements already on disk.
- `medeval ui` launches it and opens the browser.

### Added — core
- `medeval_core.manifest` (run provenance), `medeval_core.cost` (token/cost
  preview), `medeval_core.ollama` (reachability probe with installation
  instructions), cache salt for deliberate retries, UTF-8 console output on Windows.

### Results
- **`exp002-main`**: 60 MedQA items, 3 repeats, J1 only. Accuracy 58/59 = 98.3%
  (Wilson 91.0–99.7%), Cohen's kappa 0.966 (bootstrap 0.894–1.000), 0 false
  accepts, 1 false reject, self-consistency 59/59. 187 calls, 192k tokens, **US$0.17**.
- **Cross-family error correlation: not measured** (J2 blocked; Ollama absent).
- **Benchmark ceiling 98.3%**: the sample cannot discriminate between judges, which
  is reported as a finding rather than buried.
- **Confidence was uninformative**: all 180 values were 95–100, so ECE ≈ 0 reflects
  high accuracy, not a well-used confidence scale.
- **`exp002-full` aborted** after 42/204 empty replies: thinking mode consumed the
  entire `max_tokens=512` budget. `MEDEVAL_MAX_TOKENS` raised to 2048, deviation
  recorded, run re-executed from scratch; no result from the aborted run is used.

### Retained from the earlier v0.2 work (2026-11-16)
- Recursive secret redaction in JSONL outputs plus a test that a fake key string is
  absent from every output field; offline tracked-file/history secret scanner and
  local pre-push hook instructions.
- Explicit v0.2 overlay correcting case `case-0045` (the bad response no longer
  mentions vomiting, which was absent from the stem). The v0.1 file and historical
  outputs were not edited.
- MedMCQA raw-string exact duplicate groups are 0; the historical category is named
  **identical after normalisation**. Option D share is 19.7%. MedQA p=0.017 is
  described as mildly uneven with a multiple-comparison caveat.
- The ten historical hidden item rows moved out of version control; only SHA-256
  hashes remain. They are **not** hidden: the original text is still in early Git
  history and must be treated as public.
- Tracked harness sample JSONL files containing verbatim dataset text were removed;
  audit fingerprints and licence evidence were kept.

### Fixed after the first browser render (2026-11-17)
- **Command palette covered every page.** `.palette-backdrop { display: flex }` is
  an author rule and outranks the user-agent `[hidden] { display: none }`, so the
  overlay was painted while `palette.hidden` stayed `true` — which also made the
  Esc handler believe it was already closed. `[hidden] { display: none !important }`
  is now the first rule in the stylesheet, with a regression test.
- **An aborted run advertised accuracy.** `exp002-full` showed `100.0%
  [0.932, 1.000]` in the runs list, a number from the subset biased by the
  truncation defect that caused the abort. Headline metrics now come only from
  completed or stopped runs.
- **Unavailable judges were listed as judges.** Every run showed `j2_ollama` even
  where it never started; available and unavailable judges are now separate, and
  the unavailable one is marked.
- **A stale `app.js` masked the palette fix.** The shell and its assets are served
  with `Cache-Control: no-store` and versioned asset URLs.
- Single-judge results use a two-column grid, wide accuracy bars carry their label
  inside the bar, short cards no longer stretch to the tallest in their row, the
  item table gained a truncated question preview, and the Ollama instructions were
  reflowed so the browser wraps them.

### Quality
- `ruff` clean under a documented configuration; CI now runs `ruff check .`.
- 263 tests, including hand-computed vectors for every study statistic, key
  isolation (prompt invariance under a key change), resume (zero repeated calls),
  cap refusal, no-secret-in-artefacts, and the API endpoints.
- `pyproject.toml` gains the web dependencies, ruff configuration and package data.

### Changed
- The earlier pairwise protocol moved to `experiments/002a_pairwise_blocked/`
  (`git mv`, history intact) and `medeval experiment002` became `medeval exp002a`.
  Its frozen-rule held-out result (13/20, 0.65, BCa 0.36–0.80) is unchanged and is
  reported separately as Experiment 002a.
- `run_all.ps1` now ends with an Experiment 002 dry run instead of the removed
  preflight, and pytest runs without the cache provider (unusable under the
  restricted Windows token).

### Corrected during the build
- `prompts.py` silently re-declared two template-version constants that it already
  imported; the duplicates were removed so the single source of truth is
  `prompts_versions.py`.
- The mock judge's oracle is keyed by prompt digest, so it can imitate a given
  accuracy without the answer key ever appearing in a prompt.

## Artefact versions in v0.1

| Artefact | Version | Location |
|---|---|---|
| toolkit `medeval-lab` | 0.1.0 | `pyproject.toml` |
| criteria catalogue | v0.1 | `packages/medeval_criteria/catalogue/v0.1/criteria.yaml` |
| metrics definitions | v0.1 | `packages/medeval_metrics/metrics.py` |
| rubric | v0.1 | `data/rubrics/rubric_v0.1.yaml` |
| seed items | v0.1 | `data/items/seed_v0.1.json` |
| experiment case specs | v0.1 | `experiments/001_judge_reliability/data/case_specs_v0.1.json` |
| judge prompt templates | pairwise-v0.1.0, pointwise-v0.1.0, rules-v0.1.0, exact-match-v0.1.0, jury-v0.1.0 | `packages/medeval_judges/prompts_versions.py` |
| rule file | v0.1 | `packages/medeval_judges/rules/contraindications.yaml` |
| literature registry | 21 rows (20 DOI-verified) | `data/literature.csv` |
| benchmark audit snapshot | MedMCQA validation `5266466f…`, MedQA dev `a75f2850…` | `results/audit/datasets.csv` |
| harness | 0.4.14.dev0 (read-only checkout) | `%USERPROFILE%\lm-evaluation-harness` |
| Python | 3.14.6 | built and tested |

## 0.1.0 — 2026-11-15

### Added
- Item JSON Schema (draft 2020-12) plus a semantic validator that rejects items
  without provenance, label source or licence, and enforces DOI rules per label
  source, contiguous anchors, and the safety caveat on criteria.
- Benchmark audit: exact/near duplicate detection (word-shingle Jaccard), answer-key
  checks (missing, out-of-range, conflicting columns, duplicate options), 0-based /
  1-based index detection, shortcut baselines (always-first, longest-option), key
  distribution test, contamination screens, per-dataset JSON + Markdown report and
  a licence/fingerprint registry.
- Criterion catalogue v0.1: 8 constructs with anchored scales, failure examples,
  applicable task types, literature IDs and limitations.
- Metrics v0.1: critical error rate, unsafe recommendation rate, omission rate,
  rubric-weighted score with per-criterion breakdown, Brier, NLL, ECE/MCE,
  perturbation consistency, subgroup performance, accuracy — each with formula,
  unit, interpretation, assumptions and "when not to use".
- lm-evaluation-harness plugins (entry points): metrics `medeval_brier`,
  `medeval_nll`, `medeval_mean_confidence`, `medeval_acc`; aggregations
  `medeval_worst_decile`, `medeval_trimmed_mean`; two task variants with
  `process_results` that preserve per-item calibration information.
- Judges: exact-match, deterministic rule judge (contraindications + dose ceilings
  + omission checklist), single LLM rubric judge with blinded randomised order and
  reference-guided/free variants, and a majority-vote jury.
- Reliability: Cohen's kappa, Fleiss' kappa, Krippendorff's alpha (nominal and
  ordinal), ICC(1,1)/ICC(2,1)/ICC(3,1), Gwet's AC1, percent agreement, percentile
  and BCa bootstrap intervals, cluster bootstrap, paired bootstrap, McNemar,
  Wilson intervals, position/length/self-preference probes, self-consistency.
- Experiment 001: pre-registered plan, 50 planted-error cases, five judge
  configurations, 1 250 verdicts, four figures, results report.
- 174 unit tests, including hand-computed expected values for every statistic.
- `run_all.ps1` one-command reproduction and `python -m medeval all`.
- CI workflow (not yet executed — see VERIFY_ME A10).

### Known limitations (also in README and VERIFY_ME)
- No clinician reference standard; criteria are literature-derived, not
  clinician-validated.
- LLM judges ran on the deterministic MOCK backend only.
- PubMedQA skipped (legacy loading script).
- No answer-key correctness assessment; structure only.
- Kappa is unreliable under high accuracy/prevalence; AC1 is reported beside it.

### Fixed during the build
- MedQA's hub file is JSON Lines despite the `.json` suffix; the loader now falls
  back to line-delimited parsing.
- Windows MAX_PATH broke the datasets builder's path-derived lock files; the cache
  is now relative (`HF_DATASETS_CACHE=hf`).
- The harness CLI crashed on its own Unicode table output under cp1252; the
  reproducible path uses the Python API instead.
- The two-member jury ties on disagreement; kept and reported as a negative result.

## Planned beyond 0.2.0
- A second judge that actually runs: install Ollama, pull `llama3.2:3b`, then re-run
  `medeval run exp002` — the cross-family question becomes answerable only then.
- A harder sample or a weaker judge, because a 98% ceiling leaves no room to observe
  error correlation.
- Human reference labels and human–judge agreement.
- Three-member jury with an explicit tie rule.
- Criteria v0.2 after clinician review (v0.1 results stay interpretable).
- PubMedQA in an isolated `datasets<4` environment; MedMCQA train/validation overlap;
  the answer-key correctness screen that needs clinician review.