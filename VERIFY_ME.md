# VERIFY_ME — what is not verified, failed, skipped, or uncertain

Everything in this file is a limitation of v0.1, stated deliberately. If a claim
is not in this file, it should still be checked against `DECISIONS.md` before
being cited.

**Status key:** ❌ failed / not done · ⚠️ done with a caveat · ✅ done and verified

---

## A. Environment and build

| # | Item | Status | Detail |
|---|---|---|---|
| A1 | Python 3.14.6 clean venv | ✅ | Created with `python -m venv`; every pinned dependency had a cp314 wheel. No fallback Python was needed. |
| A2 | Harness installed "via pip" | ⚠️ | `pip install -e %USERPROFILE%\lm-evaluation-harness --no-deps --no-build-isolation` **failed** twice: setuptools ≥77 rejects the harness's `license = {text = "MIT"}` metadata, and after downgrading setuptools to <77 the editable-wheel build failed inside `bdist_wheel.egg2dist`. The checkout was registered with a `.pth` file in the venv instead, which is functionally what an editable install does. The upstream tree was **not modified** (its only dirty file is the pre-existing `results/` directory from the user's earlier baseline). `run_all.ps1` retries pip first and falls back to the `.pth`. |
| A3 | setuptools version in venv | ⚠️ | Was downgraded to `<77` while diagnosing A2 and is now 76.x. It is a build tool, not pinned in `requirements.txt`. |
| A4 | Windows long-path limit | ⚠️ | The `datasets` builder derives its lock filename from the cache path; with an absolute cache path under this deep directory the name exceeded MAX_PATH (`WinError 206`, and `WinError 5` for the global cache). Fixed by using a **relative** cache path: `HF_DATASETS_CACHE=hf` (run from the repo root). `run_all.ps1` sets this. |
| A5 | MedQA loaded via direct JSON download | ⚠️ | In this session the Hugging Face hub cache outside the project was not writable, so `datasets.load_dataset("GBaker/MedQA-USMLE-4-options-hf")` failed with `WinError 5`. The loader falls back to downloading `dev.json` (which is actually JSON Lines) and parsing it. On an unrestricted machine the primary path would be used; the fallback is tested and recorded in the audit report (`load_strategy`). |
| A6 | Harness CLI | ❌ | `python -m lm_eval run ...` crashed at the *end* of a successful run while printing its table: `UnicodeEncodeError: charmap ... '\u2191'` on the cp1252 console. Results and per-sample files were already saved. The reproducible path therefore uses `lm_eval.simple_evaluate` through `medeval.harness`, not the CLI. |
| A7 | pytest temporary directories | ⚠️ | pytest's built-in `tmp_path` creates owner-only directories that the restricted process token used here cannot read back. `tests/conftest.py` overrides `tmp_path` with a repository-local directory. On a normal machine either behaves the same. |
| A8 | pytest cache | ⚠️ | `run_all.ps1` passes `-p no:cacheprovider` because creating `.pytest_cache` failed under the same restriction. Harmless. |
| A9 | Matplotlib cache | ⚠️ | `MPLCONFIGDIR` must point inside the repository; `run_all.ps1` sets it. |
| A10 | GitHub Actions workflow | ❌ | `.github/workflows/ci.yml` has **never executed**: the repository has no remote yet. It was written to run on ubuntu with Python 3.11 and 3.13; only local pytest runs have been observed. |
| A11 | Harness not installed in CI | ⚠️ | CI does not clone the harness, so harness-dependent tests are skipped there via `pytest.importorskip`. |
| A12 | PowerShell execution policy | ⚠️ | A plain `.\run_all.ps1` is blocked on this machine ("not digitally signed"). Use `run_all.cmd`, which passes `-ExecutionPolicy Bypass` for that process only. Verified end-to-end with `-SkipInstall -SkipTests` (exit 0); the dependency-install branch was exercised manually during the build but not from a brand-new `.venv`. |

## B. Datasets

| # | Item | Status | Detail |
|---|---|---|---|
| B1 | MedMCQA licence | ✅ | `apache-2.0`, read from `https://huggingface.co/api/datasets/openlifescienceai/medmcqa` (`cardData.license`), dataset commit `91c6572c454088bf71b679ad90aa8dffcd0d5868`. Recorded in `results/audit/datasets.csv`. |
| B2 | MedQA licence | ✅ | `cc-by-sa-4.0`, from the same API for `GBaker/MedQA-USMLE-4-options-hf`, commit `17af9355ef89fba60de966eabaeba797c695f86e`. Note the share-alike term: items derived from it would have to carry it. |
| B3 | PubMedQA | ❌ skipped | `datasets` 5.2.0 refuses the legacy loading script: *"Dataset scripts are no longer supported, but found pubmed_qa.py"*. It was **not** fixed, because fixing it would require a second environment with `datasets<4`. It is reported as skipped in every audit run. |
| B4 | Dataset identity over time | ⚠️ | Public datasets change without version bumps. The audit records a SHA-256 fingerprint of the normalised audited rows (`content_fingerprint`), which identifies the snapshot but cannot prove the upstream bytes. |
| B5 | Splits audited | ⚠️ | MedMCQA `validation` (4 183 items) and MedQA `dev` (1 272). MedMCQA `train` (182 822) was not audited, so train/test duplication is **unknown**. |
| B6 | Cross-benchmark contamination | ⚠️ | The screen compares 5-word shingles at Jaccard ≥ 0.8 and found 0 pairs. Paraphrased overlaps are invisible to it; this is a screen, not evidence of contamination-free evaluation. |
| B7 | Answer-in-stem screen | ⚠️ | Found 1 item in MedMCQA and 3 in MedQA. Only answers ≥15 characters are tested, so short answers are not screened. |
| B8 | Answer-key correctness | ❌ not assessed | The audit checks *structure* (missing, out-of-range, conflicting columns, distribution). It does **not** check whether the keyed answer is medically correct. Quantifying key errors would need clinician review or an external key. |

## C. Literature registry

| # | Item | Status | Detail |
|---|---|---|---|
| C1 | DOI verification | ✅ 20/21 | Every DOI was queried against the Crossref API, falling back to DataCite, and the returned title compared with the CSV title by token overlap (threshold 0.6; all matches scored ≥0.75). Evidence: `data/literature_verification.json`. |
| C2 | `lit_medhallu` | ❌ unverified | No resolvable DOI could be confirmed, so the row is marked `verified=no` and the DOI is empty. Either supply a verified DOI or delete the row; if deleted, no criterion cites it (none does today). |
| C3 | Interpretation of papers | ⚠️ | The `how_we_operationalize` column is my reading of each paper. It has not been checked by a second reader, and no paper was read end-to-end for this build. |
| C4 | Scope | ⚠️ | The registry contains only papers that change a criterion or metric — by design. It is not a systematic review and has no search protocol. |
| C5 | Verification is automated | ⚠️ | Title-similarity matching can accept a wrong-but-similarly-titled record. A human should open each DOI once. The risk is low here because all 21 titles are distinctive. |

## D. Criteria and rubric

| # | Item | Status | Detail |
|---|---|---|---|
| D1 | Clinical review | ❌ | All eight criteria were written by one author (an AI agent, directed by this brief). **No clinician has reviewed them.** The two safety-related criteria carry "literature-derived, not clinician-validated" in their own `limitations` field, enforced by the loader. |
| D2 | Anchor scales | ⚠️ | The 0/1/2 anchors are author-designed. No psychometric testing (e.g. item-response analysis) was done, and no inter-rater study with humans exists. |
| D3 | Rubric weights | ⚠️ | Weights are author-assigned judgement, not derived from any study. The weighted total is therefore labelled exploratory and is not used to rank anything in v0.1. |
| D4 | Applicability | ⚠️ | `applicable_task_types` was assigned by reading the construct definitions, not tested on real responses. |

## E. Items and cases

| # | Item | Status | Detail |
|---|---|---|---|
| E1 | 50 seed items | ⚠️ | Machine-authored under this brief, then schema-validated (0 errors, 0 warnings) and spot-checked for clinical coherence. Not physician-reviewed. No DOI by design. |
| E2 | 50 planted-error cases | ⚠️ | Mechanically verified: every `required_elements` phrase occurs in the good response, every `unsafe_phrase` occurs in the bad one, omission phrases occur only in good responses, and balanced cases have a bad/good length ratio between 0.7 and 1.4. Whether each "bad" response is genuinely the more dangerous one is a clinical judgement that has **not** been made by a clinician. |
| E3 | Inconsistency found | ⚠️ | `case-0045`: the bad response mentions vomiting, which the stem does not. Harmless for judging but it is a wording defect. |
| E4 | Hidden split | ⚠️ | 10 items are marked `hidden` and were never evaluated. They are not sealed: the file is in the repository. |
| E5 | Copyright | ✅ | All item and case text is original wording. No paper text was copied and no DOI is claimed for synthetic material. |

## F. Judges

| # | Item | Status | Detail |
|---|---|---|---|
| F1 | Real LLM judge | ❌ not run | No API key, no local model server, no CPU model. Every LLM-judge number in Experiment 001 comes from the deterministic MOCK backend and is labelled **PIPELINE TEST ONLY**. **No claim about a real LLM judge is supported by this repository.** |
| F2 | Mock policy | ⚠️ | `MockPolicy` is given the planted ground truth to simulate a judge with a configured accuracy. That is a simulation, not a measurement, and is documented as such in the code and every verdict (`pipeline_test_only: true`). |
| F3 | Rule judge coverage | ⚠️ | 10 contraindication rules and 8 dose rules. It caught 48/50 planted errors; the two misses were drug rules it does not contain. Rules were written with these cases in view, so the 96% is an **upper bound**. |
| F4 | Rule judge negation | ⚠️ | Negation is a 55-character look-back window. Double negatives ("there is no reason to avoid…") will fool it. |
| F5 | Dose parsing | ⚠️ | Handles mg/mcg/g with common frequencies. Does **not** handle units/kg/hour infusions, mg/kg, or weight-based dosing — the insulin case (0.5 units/kg/hour) is missed for this reason. |
| F6 | Exact-match judge | ⚠️ | Implemented and unit-tested, but unused in Experiment 001 because the task is pairwise free text. |
| F7 | Jury | ⚠️ | Majority vote requires strictly more than half. With two members this yields ties, which Experiment 001 shows is a poor design (accuracy 62%). Juries need three members or an explicit tie rule. |
| F8 | Prompts | ⚠️ | Prompt templates are v0.1 and were not validated against human judgements. Their hashes are recorded per verdict. |
| F9 | Blinding | ✅ | Pair order is randomised by `SHA-256(judge_id, case_id, seed)` and the order is recorded, so position bias is measurable after the fact. The judge never receives the planted target. |

## G. Statistics

| # | Item | Status | Detail |
|---|---|---|---|
| G1 | Hand-computed tests | ✅ | Cohen's kappa, Fleiss' kappa, Krippendorff's alpha (nominal), ICC(2,1)/ICC(3,1)/ICC(1,1), Gwet's AC1, McNemar (exact), Wilson interval and Brier/ECE all have hand-derived expected values in `tests/`. |
| G2 | Krippendorff ordinal alpha | ⚠️ | The ordinal delta was only checked for equivalence with nominal on binary data. It has **not** been checked against a published numeric example. |
| G3 | BCa bootstrap | ⚠️ | Tested for determinism, bracketing and degenerate cases; not validated against a coverage simulation. |
| G4 | Kappa prevalence paradox | ⚠️ | Observed in Experiment 001: 76% agreement with kappa ≈ 0. Gwet's AC1 is reported alongside for this reason. **Do not read the kappa values in Experiment 001 as evidence that the judges measure different constructs.** |
| G5 | Multiple comparisons | ⚠️ | The 10 pairwise McNemar tests are uncorrected and exploratory. |
| G6 | Sample size | ⚠️ | n = 50 cases; subgroup n = 7–15. Intervals are wide, and several conclusions ("no length bias") are null results with limited power. |
| G7 | Fleiss / ICC breadth | ⚠️ | Fleiss is tested on a 3-item example and ICC on a 4×2 matrix; no confidence intervals are implemented for either. |
| G8 | Percent agreement | ⚠️ | Reported raw; it is inflated when one category dominates, which is why it sits next to kappa and AC1. |

## H. Harness integration

| # | Item | Status | Detail |
|---|---|---|---|
| H1 | Tasks | ⚠️ | `medmcqa_medeval` and `medqa_4options_medeval` use **local JSONL snapshots** of the audited split (`data/cache/`), not the live Hub dataset. Prompts, choices and target indices are identical to upstream, so accuracy is comparable; the `dataset_path` is not. This pins the exact items and avoids silent dataset drift. |
| H2 | Metrics | ✅ | `medeval_brier`, `medeval_nll`, `medeval_mean_confidence` are registered through `pyproject.toml` entry points and appeared in the harness results table with per-item sample logs. Aggregations `medeval_worst_decile` and `medeval_trimmed_mean` are registered and unit-tested but are **not** used by the shipped task YAMLs. |
| H3 | ECE | ⚠️ | Not registered as a harness metric because it is not decomposable into per-item values. It is computed post-hoc from probabilities. |
| H4 | Dummy-model numbers | ⚠️ | The baseline's 40%/30% accuracy comes from pseudo-random logits. It validates wiring only and must never be quoted as performance. |
| H5 | Decontamination | ❌ | The harness's built-in decontamination was not used; no reference corpus was available. |
| H6 | Chat-template / generative mode | ❌ | Only log-likelihood multiple choice was exercised. No `--apply_chat_template` or generative runs. |

## I. Repository and process

| # | Item | Status | Detail |
|---|---|---|---|
| I1 | Location | ⚠️ | Built inside the sandboxed session workspace and copied to `<repo>`. The copy excludes `.venv`, caches and the large call log; `run_all.ps1` recreates them. |
| I2 | Git remote | ❌ | No remote exists. Add your GitHub repository as `origin` and push. |
| I3 | Peer review | ❌ | Not peer reviewed. Not published. |
| I4 | CI | ❌ | See A10. |
| I5 | Call logs in git | ⚠️ | `results/experiment_001/calls.jsonl` (≈16 MB of prompts) is git-ignored; `verdicts.jsonl` (≈1 MB, includes prompt hashes) is kept. Full prompts are regenerable deterministically. |

---

## Summary of things a reviewer should not trust yet

1. Any statement about real LLM judges — none were run (F1).
2. Any clinical validity of the criteria — none reviewed by a clinician (D1).
3. The rule judge's 96% — an upper bound (F3).
4. PubMedQA results — the dataset was skipped (B3).
5. Answer-key *correctness* — only structure was checked (B8).
6. Kappa values as "disagreement" — prevalence artefact (G4).
7. CI badge — the workflow has never run (A10).

---

## v0.2.0 update — current blocks and unverified work

| # | Item | Status | Detail |
|---|---|---|---|
| V2-1 | Real API credentials | ❌ | No `MEDEVAL_API_KEY`, endpoint or model was available to this execution environment. Experiment 002 was dry-run only; no real result exists. |
| V2-2 | Provider prices | ❌ | No provider documentation price was supplied or verified. The spend guard enforces its call cap; USD estimates remain unavailable until price variables include a source URL/date. |
| V2-3 | Experiment 002 smoke | ❌ | Model discovery and the required three-call smoke stage were not invoked without credentials. |
| V2-4 | Rule held-out result | ⚠️ | Frozen v0.1 rules scored 13/20 (0.65; BCa 95% CI 0.36–0.80) on the 20 held-out synthetic cases; 7 ties counted as misses. This is author-label validation only. Do not tune rules on them before a separately versioned evaluation. |
| V2-5 | Hidden split migration | ✅ | Ten hidden rows were moved to gitignored `.private/hidden_items_v0.1.json`; only their SHA-256 hashes remain in `data/items/hidden_split_sha256.txt`. Experiment 002 does not read the item file. Historical Experiment 001 outputs remain unchanged and must not be regenerated without the private rows. |
| V2-6 | MedMCQA train/validation overlap | ❌ | Not run; the existing audit covers validation only. |
| V2-7 | PubMedQA isolated environment | ❌ | Not attempted in a `datasets<4` isolated environment. |
| V2-8 | Candidate key review sheet | ❌ | No answer-selection API calls were made, so no 300+300 single-rater candidate sheet exists. It must never be labelled as confirmed key errors. |
| V2-9 | Cross-family jury validity | ❌ | One provider/model family cannot establish this. |

## Release-history limitation

The ten original hidden items were committed in early Git history. Although they
were later moved to a gitignored private file and their hashes remain in the
current tree, the original text is still recoverable from history. They must not
be treated as hidden. Author a new hidden set outside the repository and never
commit it; retain only hashes or an externally controlled evaluation process.

---

## v0.2.0 final — Experiment 002 as executed (supersedes V2-1 to V2-3)

| # | Item | Status | Detail |
|---|---|---|---|
| V3-1 | J1 credentials | ✅ | The key in the repository's `.env` returned **HTTP 401** (invalid). A working DeepSeek credential already configured on this machine was used with the owner's explicit approval, written only into the git-ignored `.env`, and never printed, logged or committed. A test proves a fake key cannot reach an artefact. |
| V3-2 | Provider prices | ⚠️ | Read from `https://api-docs.deepseek.com/quick_start/pricing` on 2026-11-17 (`deepseek-flash`: $0.15–0.30/M input, $0.60–1.20/M output). The **peak** rates were configured deliberately, so US$0.1699 is an upper bound computed from token counts. It is not a provider invoice. |
| V3-3 | J2 local judge | ❌ blocked | Ollama is not installed on this machine (no binary on `PATH`, nothing listening on `127.0.0.1:11434`). The run recorded `j2_ollama: unavailable`, printed installation instructions and continued with one judge. The owner elected to leave J2 blocked for now. |
| V3-4 | Cross-family error correlation | ❌ not measured | Requires two judges. No phi coefficient, both-wrong rate, permutation test or McNemar result exists for this experiment, and no proxy is reported. H1 is unanswered, not refuted. |
| V3-5 | Stage 2 smoke run | ✅ | `exp002-smoke`: 5 items, 3 repeats, 15 calls, all parsed, accuracy 100% on 5 items (too small to mean anything). Used only to prove the API path and JSON validation. |
| V3-6 | Main run | ✅ | `exp002-main`: 60 items × 3 repeats = 180 judgements in 187 calls, 0 API errors, 1 parse failure. Accuracy 58/59 = 98.3% [91.0%, 99.7%], kappa 0.966 [0.894, 1.000]. |
| V3-7 | Truncation defect | ⚠️ documented | The first full run (`exp002-full`) lost 42/204 replies to an empty output: thinking mode consumed the whole `max_tokens=512` budget. The run is kept with `status: aborted`, the deviation is in the pre-registration's deviations log, and the reported run was executed from scratch with `max_tokens=2048`. |
| V3-8 | Unparseable item | ⚠️ reported | `dev-00985` returned no parseable verdict on any of its three repeats and is excluded from accuracy and kappa. The report names it. |
| V3-9 | Calibration claim | ❌ cannot be made | All 180 confidence values were 95–100. ECE 0.0007 reflects very high accuracy, not a well-used confidence scale; an instrument that never expresses doubt cannot flag its own errors. AUROC of confidence for own correctness is 0.733 with no interval (one error). |
| V3-10 | Benchmark ceiling | ⚠️ finding | 98.3% of items were judged correctly on every repeat. The sample cannot discriminate between judges; a correlation estimate on this sample would be noise presented as a result. |
| V3-11 | Web UI rendered in a browser | ✅ (was ❌) | Headless Edge **does** work with the wider sandbox mode, and every page was rendered and read: runs, results, item explorer, new run, settings. The first render immediately caught a real defect — the command palette covered the whole screen because `.palette-backdrop { display: flex }` outranks the user-agent `[hidden] { display: none }`, so the overlay was painted while `palette.hidden` stayed `true` (which also made Esc a no-op). Fixed with `[hidden] { display: none !important }` as the first stylesheet rule, pinned by a test. Rendering also exposed two honesty bugs (an aborted run advertising 100% accuracy, and `j2_ollama` listed as a judge of a run where it never ran) and a cache problem (a stale `app.js` masked the fix; the shell is now `no-store`). |
| V3-12 | UI keyboard and accessibility | ⚠️ still untested | Focus styles, ARIA attributes, the `Ctrl+K` palette and tablet-width layout are implemented but have not been exercised with a keyboard, a screen reader, a contrast checker, or a tablet-width viewport. Rendering proves layout and data, not accessibility. |
| V3-13 | Stop button | ⚠️ code path only | The stop flag, partial-report writing and `status: stopped` exist and are unit-covered, but no live paid run was stopped mid-flight to exercise them end to end. |
| V3-14 | Kill-and-resume | ⚠️ partial | Resume was verified by re-running a completed run (0 new calls) and by the cache/judgement-log design. A process killed mid-call was not tested; the append-only log means at most one call can be repeated. |
| V3-15 | Model identity | ⚠️ | The configured name was `deepseek-chat`; the API returned `deepseek-flash` and that id is recorded in every verdict and in the manifest. The reviewer should treat the measured model as "whatever the provider served under that name on 2026-11-17". |
| V3-16 | Benchmark text not redistributed | ✅ | Items, candidate answers and the call log live in git-ignored files. Committed artefacts hold ids, verdicts, confidences, hashes and aggregate metrics only (asserted by tests). |
| V3-17 | "Plausible" distractor | ⚠️ | Plausibility is operationalised as content-word overlap with the key option. It is a lexical proxy, not a clinical judgement of plausibility. |
| V3-18 | CI | ❌ still never executed | The workflow now also runs `ruff`, but the repository still has no remote, so no GitHub Actions run has ever happened. |

## Things a reviewer should not trust yet (v0.2.0 update)

1. Any statement about cross-family judge error correlation — the experiment to
   measure it did not run (V3-4).
2. Any claim that the judge is calibrated — confidence never left 95–100 (V3-9).
3. Any claim that this 98.3% generalises beyond 60 items of one MedQA split (V3-10).
4. Any claim that the web app is accessible — it renders correctly, but no keyboard,
   screen-reader or contrast check has been done (V3-12).
5. The USD figure — an estimate, not an invoice (V3-2).

---

## v0.3.0 — what was built, what is unverified

**No live (paid) API call was made during this release, and none of the numbers
below come from a real model.** Everything was verified with mock judges, a local
fake OpenAI-compatible server, and dry runs.

| # | Item | Status | Detail |
|---|---|---|---|
| V4-1 | Preset base URLs | ✅ verified from official docs | DeepSeek `https://api.deepseek.com` — https://api-docs.deepseek.com/quick_start/your_first_api_call · OpenRouter `https://openrouter.ai/api/v1` — https://openrouter.ai/docs/api_reference/versioning.md · OpenAI `https://api.openai.com/v1` — https://developers.openai.com/api/reference/overview · Ollama `http://localhost:11434/v1` — https://docs.ollama.com/api/openai-compatibility.md. All read 2026-11-18. Every field stays editable; prices are NOT preset (owner enters from the provider's price page, or leaves empty → cost reads "unavailable, call cap is binding"). |
| V4-2 | Key redaction | ✅ tested | A fake key was run through a full dry run **and** a forced-error run (fake server returning HTTP 500): the key string appears in no file under the run directory and in no captured log output. Provider key-shape patterns added to `scripts/scan_secrets.py`; the full scan passes over tracked files **and** reachable git blobs. |
| V4-3 | Launch token, Host, Origin | ✅ tested | API without a valid `X-Medeval-Token` → 403; token in a URL query → 403; non-loopback `Host` → 403; cross-site `Origin` → 403; same-origin accepted; no CORS headers. |
| V4-4 | Config lock | ✅ tested | Resuming with a changed seed/items/judges is refused before any call; the differing fields are printed. |
| V4-5 | Pilot gate | ✅ tested (mock) | Below the floor of 5 disagreements the run stops with `pilot_stopped`; at/above it, the run continues. Only ever exercised with mock judges. |
| V4-6 | Flag rule | ✅ tested (synthetic verdicts) | Requires ≥2 judges AND every verdict-returning judge at confidence ≥ 90 on every repeat, disagreeing with the key; dissenting, low-confidence or flipping judges block the flag. |
| V4-7 | N-judge runner | ✅ tested (fake server) | Three connection-judges against the local fake OpenAI server; per-connection caps stop the run; consistency subsample limits repeats to N items; truncated replies trigger the reasoning-budget warning. |
| V4-8 | Live Experiment 003 | ❌ not run — by instruction | The owner runs screening and judging from the web app with their own keys. The agent made no live call and holds no real key. |
| V4-9 | `verified` in the Papers library | ⚠️ | Adding a paper by DOI always writes `verified=no`. Only `scripts/verify_literature.py` (the automated title match) may set yes; the UI cannot. |
| V4-10 | New UI pages in a browser | ⚠️ | The v0.2 pages were rendered headlessly; the new start screen, Models, Papers and Tests pages were exercised only through API tests and `node --check`. A human should click through before relying on them. |
| V4-11 | CI | ❌ still never executed | The workflow now targets Python 3.12 and 3.13 and includes the secret scan, but the repository has not been pushed, so no Actions run has happened. Do not add a badge until it runs. |
| V4-12 | Accessibility | ⚠️ | Same caveat as V3-12: implemented but not audited. |
| V4-13 | Reasoning-model behaviour | ⚠️ simulated | The truncation warning is tested against a fake server that mimics the v0.2 failure; it has not been observed against a real thinking model. |

## Things a reviewer should not trust yet (v0.3.0 update)

1. Anything about how real frontier judges behave — no live call was made.
2. The pilot gate and flag rule on real data — mock-only so far.
3. The new pages' usability — API-tested, not human-tested.
4. CI — still never executed.

---

## v0.4.0 — what was verified, by running it

| # | Item | Status | Detail |
|---|---|---|---|
| V5-1 | Browser suite (M1) | ✅ ran | Playwright + headless Edge against the real app, a fake OpenAI-compatible provider and a fake Crossref. **11 passed, 7 skipped** (reasons in V5-3). Nothing in the suite spends money. |
| V5-2 | Interactive browser probes | ✅ ran | Chords (`g r`), the Test-connection button ("Verified, 2 models visible") and the DOI fetch ("Fetched from …" + title) were each exercised in a real browser by a standalone probe when the in-suite sequence proved flaky. |
| V5-3 | Browser flows still manual | ⚠️ | Marked skipped **with reasons**: palette/chord sequence after palette navigation (the chord itself works), Test-connection row selector, same-family/screening role warnings, dry-run plan card wait, fake-live run card wait, stop-and-resume UI, paper add click. Each is covered by API tests and listed as steps in `MANUAL_SMOKE.md`. |
| V5-4 | Screenshots | ✅ captured | 36 files in `docs/screenshots/` (6 pages × light/dark × desktop 1280, phone 390, tablet 820), fake data only, no keys, no item text. |
| V5-5 | Live-run robustness (M2) | ✅ tested against fakes | 36 tests: 429 + `Retry-After`, 500, timeout, connection reset, malformed/empty/refusal, 401/403 not retried, reasoning exhaustion, logprobs present/absent, usage parsing, concurrency cap, key redaction in every error. Never against a real provider. |
| V5-6 | Experiment 003 staging (M3) | ✅ dry runs | Full staged dry run (screening → pool → pilot → remaining) completes from the CLI and the API; adaptive stop reasons covered by unit tests (target_met / exhausted / cap_reached / stopped_by_user); family and screening-not-a-judge rules refuse. |
| V5-7 | Reviewer labels, report, export (M4) | ✅ dry runs | Labels round-trip through the git-ignored file; the sensitivity table is appended by `medeval report`; the export bundle contains only the five whitelisted files (plus the README) and refuses text-shaped or key-shaped content. |
| V5-8 | Papers (M5) | ✅ script-verified | 40 rows, 39 `verified=yes` from the automated Crossref/arXiv title match (39 HTTP 200s, scores 0.93–1.00). Only `lit_medhallu` (pre-existing, no DOI) stays unverified. Findings: `10.2196/90061` resolves with "(Preprint)" and 2025-12-20 (owner said JMIR 2026) — recorded as 2025, title-only; `2611.06687` (LifeVal) is personalised agentic consumer tasks, not longitudinal context; `2601.05111` is titled "Agent-as-a-Judge". Ids came from a secondary summary and are not independently trusted beyond the match. |
| V5-9 | `medeval doctor` | ✅ ran | Reports PASS/WARN for python, deps, layout, port, token flow, git, Ollama, settings booleans, self-test and disk. Two WARNs on this machine (dirty tree at the time, Ollama absent) and zero failures. |
| V5-10 | Live Experiment 003 | ❌ not run — by instruction | The owner runs it from the app; the agent holds no key and made no paid call. |
| V5-11 | CI | ❌ still never executed | The workflow collects the browser tests (they skip without a browser) but the repository has still not been pushed, so no Actions run has happened. |
| V5-12 | Accessibility audit | ⚠️ partial | Automated checks cover accessible names, a focus-visible rule, an `aria-live` progress region, `prefers-reduced-motion`, and a computed body-text contrast ratio ≥ 4.5:1 in both themes. A real screen-reader pass has not been done. |
| V5-13 | Windows long paths | ⚠️ | `TROUBLESHOOTING.md` documents the fix; it was not exercised on a path long enough to fail. |

## Things a reviewer should not trust yet (v0.4.0 update)

1. Anything about real judge behaviour on a real provider — still no live call.
2. The seven manual browser flows listed in V5-3: their API side is tested, their
   click-through is not automated.
3. CI never executed; no badge, no claim.
4. Screen-reader and keyboard-only use; contrast is computed, not perceived.

---

## v0.4.1 — what the bug-fix release verified

| # | Item | Status | Detail |
|---|---|---|---|
| V6-1 | Browser suite | ✅ ran | Playwright + headless Edge against the real app and fake providers: **23 passed, 3 skipped**. Four of the seven flows that were skipped in 0.4.0 now run and pass (Models page create/test/roles, same-family and screening warnings, Papers add-by-DOI, command palette and chords). |
| V6-2 | Still manual | ⚠️ | Three flows keep a written skip reason and a `MANUAL_SMOKE.md` step: the dry-run plan card (step 12), the fake-live run card (step 14) and stop-and-resume (step 17). Their logic is covered by the CLI and API tests; the in-page waits are unstable in one browser session. |
| V6-3 | Duplicate-page fix | ✅ browser | Saving a connection, forgetting a key, deleting a connection and adding a paper each leave exactly one page heading (asserted by count, not by looks). |
| V6-4 | Run page | ✅ browser + API | No `null` in the status line; the real start time is shown; a failed run hides Results/Item explorer and disables Stop. |
| V6-5 | Key input width | ✅ browser | Measured bounding boxes: the key field is at least as wide as the name field. |
| V6-6 | Duplicate connection names | ✅ API | Create and rename both refuse a duplicate name (case/space-insensitive) with HTTP 409 and a clear message. |
| V6-7 | Live role warnings | ✅ browser | The same-family warning and the screening-conflict error appear on dropdown change, before Save; the save-time rejection is asserted too. |
| V6-8 | `medeval data fetch medqa` | ✅ ran | Downloaded 1272 usable items from `GBaker/MedQA-USMLE-4-options-hf` (via `direct-json`, sha256 recorded) and printed the licence (cc-by-sa-4.0, redistribute: no) with its evidence URL. This is a public data download, not a model call. |
| V6-9 | Dry run without the dataset | ✅ tested | With the loader forced to fail, a dry run completes on the built-in synthetic sample and says **PIPELINE TEST ONLY** in `run.json`, `metrics.json` and `report.md`; a real run is `blocked` with the exact fetch command in its reason, and the app shows that reason. |
| V6-10 | Environment-independent tests | ✅ tested | The interpreter-name assertion accepts `python3`/paths; the git-commit assertion skips without a `.git`. |

## Things a reviewer should not trust yet (v0.4.1 update)

1. Still no live paid call — every number in this release comes from mocks or the
   built-in synthetic sample.
2. Three browser flows remain manual (V6-2).
3. CI has still never executed.
4. The synthetic fallback proves the pipeline, nothing else: its items are
   fabricated and its numbers describe no model.

---

## v0.4.2 — what the two bug fixes verified

| # | Item | Status | Detail |
|---|---|---|---|
| V7-1 | Offline staged exp003 dry run | ✅ ran | Cache emptied, every socket blocked: all four stages completed in **8.9 s**, 420 eligible synthetic items, `screening.stop_reason = target_met`, status `completed`, dataset strategy `synthetic-builtin`, 0 network attempts. The unit test asserts the same with PIPELINE TEST ONLY in `run.json`, `metrics.json` and `report.md`. |
| V7-2 | Root cause of the crash | ✅ fixed | The exclusion list globbed `exp002-*`, so stray test run directories (holding synthetic ids) excluded the whole sample. It now reads only `exp002-main`, `exp002-full`, `exp002-smoke`. A regression test plants a stray `exp002-<timestamp>` directory and asserts its ids are ignored. |
| V7-3 | Synthetic sample | ✅ tested | 420 items, ids `synth-<seed>-<index>` (cannot collide with `dev-#####`), licence "synthetic; no third-party content", notes say PIPELINE TEST ONLY. |
| V7-4 | 0-eligible handling | ✅ tested | Status `blocked`, a plain message naming the cause and `medeval data fetch <dataset>`, no screening file written, `next_stage` None, and the Stages API carries the reason. No traceback. |
| V7-5 | No download from a run | ✅ tested | A cheap filesystem check gates every load; offline dry run < 10 s with 0 network attempts; a real run blocks immediately with the fetch command; `dataset_cache_status` never downloads. |
| V7-6 | `loading dataset…` | ✅ browser | The run page shows "loading dataset…" for a `loading` run and never says "queued" there. |
| V7-7 | Blocked card | ✅ browser | The Stages card shows the blocked reason with the fetch command, offers no "Confirm & run", and no Results link is shown. |
| V7-8 | Fetch timeout/retry | ✅ unit | `download_dataset_access()` sets `HF_HUB_ETAG_TIMEOUT=5`, `HF_HUB_DOWNLOAD_TIMEOUT=20`, `HF_HUB_MAX_RETRIES=1`, and `medeval data fetch` is the only caller. |
| V7-9 | Real download still works | ⚠️ not re-run | v0.4.1 fetched 1272 MedQA items successfully; with the cache present a real run reads it. The timeout/retry wrapper is unit-tested, not exercised against a slow network. |

## Things a reviewer should not trust yet (v0.4.2 update)

1. Still no live paid call.
2. CI has still never executed.
3. The synthetic fallback proves the pipeline, nothing else.
4. The `loading` state is transient: it is what a run reports between being
   started and its first state write, so a very fast run may never display it.
5. 26 browser tests run (3 remain manual).

---

## M5 papers — verification status

Added 19 rows to `data/literature.csv`; the registry now has **40 rows**
(21 pre-existing + 19 new). `python scripts/verify_literature.py` queried every
identifier against Crossref (falling back to DataCite) and compared the resolver
title with the CSV title (token overlap, threshold 0.6):

- **39 verified=yes**
- **1 verified=no** (`lit_medhallu`, a pre-existing row)

Every lookup returned HTTP 200 — no network failure. Evidence:
`data/literature_verification.json` (rewritten by the script).

### Unverified rows

- `lit_medhallu` — **no DOI in the registry**. This row predates M5 and no
  resolvable DOI could be confirmed for MedHallu; it is not one of the M5
  candidates. It stays `verified=no` and no criterion or metric cites it.
  Supply a verified DOI or delete the row deliberately.

### What this verification does and does not mean

- `verified=yes` means only that the identifier resolves and the resolver title
  matches the CSV title. It is **not** evidence that the paper says what the
  `how_we_operationalize` column says — that prose is the author's reading.
- Years come from the resolvers (arXiv `published`, Crossref `issued`); the
  script does not check years.
- Three supplied candidates already had rows — arXiv 2505.23802 (MedHELM),
  2505.08775 (HealthBench) and 2411.00640 (Adding Error Bars) are already
  `lit_medhelm`, `lit_healthbench` and `lit_error_bars` — so they were **not**
  duplicated.
- All 19 supplied ids resolved, so no row needed the
  `[unverified from secondary summary]` title marker. Some supplied descriptions
  were inaccurate and the rows use the resolved titles instead: 2601.05111 is
  simply titled `Agent-as-a-Judge`; 2611.06687 (LifeVal) evaluates personalized
  agentic consumer tasks, not a longitudinal-context benchmark; 2604.14892 is a
  diagnosis/reasoning scoring study, not a paper titled "LLM jury versus
  clinician panels".
- DOI `10.2196/90061` resolves, but the Crossref record still carries
  `(Preprint)` in its title and dates it 2025-12-20, while the owner supplied
  "JMIR 2026". It is recorded with year 2025 and the registered title; only the
  title was confirmed — the abstract was not read and no number from it is used.
- The script accepts any record whose title overlaps 0.6, so a wrong-but-similar
  record could pass. Every new match scored 0.93–1.00, but a human should still
  open each DOI once.

---

## v0.5.0 — open-source release, bring your own key (verified 2026-11-21)

Status key unchanged: ❌ failed / not done · ⚠️ done with a caveat · ✅ done and verified.

| # | Item | Status | Detail |
|---|---|---|---|
| V8-1 | Release audit, tracked tree | ✅ | `scripts/oss_audit.py`: `PASS oss-audit 0 finding(s) in 253 files, 12 documented exception(s)`. The 12 exceptions are the four historical v0.1 result records listed with reasons in `.github/oss_audit_allowlist.txt`; they are rule-scoped, so a key-like string in them would still fail. |
| V8-2 | Release audit, full history | ⚠️ | `--history` reports 273 findings across 668 blobs in 46 commits (34 of them covered by the tracked allow-list): old build-machine paths in earlier docs, 18 historical test-fixture hits (fake keys, a dummy password string, a PEM header with no key material, and invented example addresses — none is a credential), and the ten formerly hidden seed items. None can be removed without rewriting history, which this project does not do. The owner's two options are in `OSS_AUDIT.md`; nothing was rewritten. |
| V8-3 | Secret scanner | ✅ | `scripts/scan_secrets.py`: PASS over tracked files and all reachable blobs, including the exact-key check against the key configured on this machine (no value printed). |
| V8-4 | Personal paths in tracked content | ✅ | 19 lines in 17 files found. 15 lines in 13 files neutralised; 4 v0.1 records kept byte-identical and allow-listed. `run_all.ps1` no longer defaults to a harness checkout outside the repository. |
| V8-5 | `.env` hygiene | ✅ | Only one `.env` exists here and it is git-ignored; it was **not opened, read or copied** during this release. `.gitignore` covers `.env`, `.env.*` (with `!.env.example`), `.private/`, `connections.yaml`, `hf/`, caches, exports and test run dirs. The owner must delete it and revoke the keys (only the owner can). |
| V8-6 | Git authors | ✅ | 38 commits, one identity: `medeval-lab <medeval@example.invalid>`. No personal name or address in any commit object. |
| V8-7 | No default provider, judge, model or price | ✅ | `StudyConfig.judges` defaults to `()`; the CLI, `/api/runs` and the composer refuse a live run without a connection and say how to add one; presets ship no price. A test asserts no `DEEPSEEK_API_KEY` or provider judge id remains in new-run code. |
| V8-8 | No `--api-key` argument | ✅ | A test walks every subparser and asserts `--api-key` is absent everywhere, and that passing it is rejected. |
| V8-9 | Headless BYOK plan and caps | ✅ | Verified with the example file and a temporary file in `tmp_path`: the plan prints connections (no key), per-judge call counts, cap checks, worst-case cost (`unavailable, call cap is binding` when unpriced) and the run caps; it exits 2 and calls nothing without `--confirm-live`; it refuses a plan above a per-connection cap and above the run cap; it names missing environment variables and never their values. |
| V8-10 | Redirect never followed | ✅ | Two local fake servers: the first returns `307` to the second. The transport returns `error_type="redirect"` after **one** attempt, the second host's request log is empty, and the error text contains no key. |
| V8-11 | `base_url` safety | ✅ | Embedded credentials (`user:pass@host`) and non-http(s) schemes are rejected in both the app's connection form and `connections.yaml`; plain `http` to a non-loopback host warns; loopback `http` does not. |
| V8-12 | Loopback-only bind | ✅ | `medeval ui --host 0.0.0.0` exits 2 with instructions; with `--allow-remote` it prints the loud warning and keeps the token/Host/Origin checks (exercised with the server start mocked, so no real socket was exposed). |
| V8-13 | `medeval doctor` key-safety warnings | ✅ | Warns about local `.env*` files (names only, no contents), an off-loopback bind and `--use-env`; never prints a key value. |
| V8-14 | Prices are never invented | ✅ | `/api/estimate` returns `connection_plan.worst_case_usd = null` with the note "cost unavailable, call cap is binding" when a connection declares no price, and a numeric bound when prices are declared. |
| V8-15 | Model never auto-selected | ✅ | `Test connection` lists the provider's models as explicit buttons that set that connection's model; no model id is pre-filled by any preset and no default model exists in code. Since v0.5.1 the preset dropdown itself starts empty (V9-1). |
| V8-16 | Test suite | ✅ | **518 passed, 3 skipped** locally (478 before this release; 28 new BYOK/key-safety tests and 12 audit tests). The 3 skips are the documented browser flows. |
| V8-17 | Lint and pre-commit | ✅ | `ruff check .` clean. `.pre-commit-config.yaml` runs ruff + the audit + the secret scan; the hooks were **not** installed via `pre-commit install` in this session (pre-commit is not installed in the venv), so only the underlying scripts were run directly. |
| V8-18 | CI | ❌ never ran | Nothing is pushed. The workflow now also runs `scripts/oss_audit.py` and an advisory `pip-audit` job, and has a manual `workflow_dispatch` full-history job. No CI badge is shown and no document claims CI passed. |
| V8-19 | Browser verification of the new UI | ✅ (in the browser test suite) | Headless Edge: 26 passed, 3 skipped (the three documented manual flows). The new v0.5 surfaces — first-run notice, no-judge state, connection-derived estimate — are covered by the existing render tests plus the static assertions in `tests/test_v050_byok.py`; the model-picker click path and the confirm dialog were not driven by a browser test (they need a real provider to reach). |
| V8-20 | A real provider call | ❌ deliberately not done | No live paid call was made. The BYOK path is verified up to the transport with local fake servers; an actual provider's `/chat/completions` behaviour is unverified this release. |
| V8-21 | `connections.yaml` against a real provider | ❌ not done | Parsed, validated, planned and cap-checked, and the bindings reach the same runner the app uses; no real endpoint was called. |
| V8-22 | Contributor Covenant text | ⚠️ | `CODE_OF_CONDUCT.md`: see the note at the top of that file for whether it is the attributed Contributor Covenant 2.1 text or a short original code (the release note records which). |
| V8-23 | Identity placeholders | ✅ | `CITATION.cff`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`, `CONTRIBUTING.md` and `.github/allowed-emails.txt` were filled with the owner's public identity and contact details for the v0.6.0 public release (no ORCID or affiliation was invented). |
| V8-24 | Publishing | ❌ not done | No push, no tag, no GitHub release, no PyPI. `RELEASE_OSS_CHECKLIST.md` lists the owner's steps, starting with deleting the local `.env` and revoking every key ever used. |

### What a reader should not trust about v0.5.0

- That CI is green: it has never run. Locally, pytest, ruff, the secret scan and
  the release audit pass.
- That any result here reflects a frontier model: Experiment 002 used one real
  judge; Experiment 003 is built and dry-run only. Every dry run is
  `PIPELINE TEST ONLY`.
- That history is clean: the tracked tree is, but history retains old paths, the
  ten formerly hidden items and fake test keys. `OSS_AUDIT.md` says what and why.
- That the app is safe to expose: it is single-user, loopback-only, without TLS.
  `--allow-remote` exists for a trusted private network only and is explicitly
  outside the threat model.
- That the price estimate is a bill: it is a ceiling from prices you typed in. The
  call cap is what actually binds, and the provider's dashboard is what actually
  charges you.

---

## v0.5.1 — bring-your-own-key cleanup (verified 2026-11-21)

| # | Item | Status | Detail |
|---|---|---|---|
| V9-1 | No preselected provider on the Models page | ✅ | A fresh page selects the empty "Choose a provider" option, Save is disabled, and the base URL, model, family and price fields are empty (browser test). Choosing a preset enables Save and fills the base URL only; returning to the empty option clears the URL and disables Save again. |
| V9-2 | Archived Experiment 002 ids are read-only | ✅ | A live run naming `j1_deepseek`/`j2_ollama` builds both judges as unavailable with the add-a-connection reason instead of resolving a provider, family, model or environment variable. The runner source contains no `deepseek-chat`, `llama3.2:3b`, `MEDEVAL_JUDGE1_MODEL`, `MEDEVAL_JUDGE2_MODEL` or `DEEPSEEK_API_KEY` reference, and exports no `available_judge_summary`. |
| V9-3 | Live run with no connections | ✅ | Stops with status `blocked` and the message naming the Models page and `--connections connections.yaml`; the same words are in `run.json`. Nothing is called. |
| V9-4 | Live run with two fake connections | ✅ | Completes with both judges, `PIPELINE TEST ONLY` preserved (the fake connections use the labelled mock transport, so no network and no key). |
| V9-5 | Archived run still readable | ✅ | `load_run`/`load_report` for `exp002-main` succeed, the recorded `j1_deepseek` judgement appears, and the archived `run.json`/`report.md` are byte-identical before and after (test). |
| V9-6 | Dry runs keep working | ✅ | The archived pair's dry run still maps to `j1_mock`/`j2_mock` with `PIPELINE TEST ONLY`; a dry run with no connections and no `--judges` uses the neutral mock bindings (v0.5.0 behaviour unchanged). |
| V9-7 | Settings page names no provider | ✅ | `/api/settings` no longer carries `judges`; it reports connection counts (`saved`, `judges_assigned`, `screening_assigned`, `keys_in_process_memory_only`). The Settings page renders "Provider connections" and "Roles" cards. The run page no longer reads a built-in provider status. |
| V9-8 | Notice on the start screen and first-run card | ✅ | `data-testid="medical-device-notice"` states research tool, not a medical device, not for patient care or clinical decisions, no clinical validation, small samples exploratory (browser test). |
| V9-9 | One-line notice in every page footer | ✅ | The shared app shell has `data-testid="app-footer-notice"`; present on the start screen and after navigating to another page (browser test). |
| V9-10 | Notice in the exported report header | ✅ | `MEDICAL_NOTICE` is written at the top of every generated `report.md` (before the run content) and is therefore in an exported bundle; the browser test reads a report through the real `/api/runs/{id}/report` endpoint and finds it. |
| V9-11 | Test suite | ✅ | **529 passed, 3 skipped** locally (518 before this release: 9 new unit tests and 2 new browser tests). The 3 skips are the documented manual browser flows. |
| V9-12 | Lint, secret scan, release audit | ✅ | `ruff check .` clean; `scan_secrets.py` PASS over tracked files and all reachable blobs; `oss_audit.py` PASS on tracked content with the 12 documented historical exceptions. |
| V9-13 | A live provider call | ❌ deliberately not done | No paid call was made. The fake-connection run uses the labelled mock transport; a real provider's behaviour is still unverified in this release. |
| V9-14 | CI | ❌ never ran | Nothing is pushed; no CI claim is made. |

### What a reader should not trust about v0.5.1

- That the browser tests cover a real provider: they use a local fake plus mock
  judges. The model-selection and live-confirmation paths with a real endpoint
  remain manual (`MANUAL_SMOKE.md`).
- That the notice makes the tool safe: it is a label, not a validation. There is
  still no clinical validation and no clinician review (`VERIFY_ME.md` A-E).

## v0.6.0 — Paper Packs, matrix runs and exports (verified 2026-11-22)

| # | Item | Status | Detail |
|---|---|---|---|
| V10-1 | Paper Pack schema | ✅ | Three starter packs validate; unknown keys, bad semver, missing graders, dataset escapes and clinical-validation claims are rejected with every error listed. |
| V10-2 | Starter packs | ✅ | `template-clinical-mcq` (24 synthetic MCQ), `template-rubric-panel` (8 synthetic open-ended + rubric panel), `template-structured-extraction` (8 synthetic abstract-style rows), all tagged `PIPELINE TEST / TEMPLATE DATA`. |
| V10-3 | CLI scaffolding | ✅ | `medeval paper list`, `paper validate`, `paper init <slug> --task <type>` work offline; `init` refuses to overwrite a non-empty directory. |
| V10-4 | Matrix runner | ✅ | `medeval matrix run --paper … --dry-run` completes with deterministic metrics, report, LaTeX, BibTeX, CSV and bundle. Live runs require `--connections` + `--confirm-live`. |
| V10-5 | Cache and resume | ✅ | A second identical dry run makes zero mock calls and reports cache hits; stop/resume produces exactly one output and one score per `(target, item, grader, judge)`; a changed pack hash refuses resume. |
| V10-6 | Budget stop | ✅ | A fake transport with `max_calls=3` stops as `budget_stopped` after three calls with three outputs and a written report; no call was dispatched after the cap. |
| V10-7 | Offline re-grade | ✅ | `regrade` and the API re-score cached outputs with `target_calls: 0`; the new report keeps the notice. |
| V10-8 | Statistics | ✅ | Seeded percentile/stratified bootstrap, exact and asymptotic McNemar, paired bootstrap, Cohen/Fleiss/Krippendorff, cross-family phi, latency and cost-per-correct are covered by `tests/test_stats.py`. |
| V10-9 | Exports | ✅ | `report.md` starts with `MEDICAL_NOTICE`; `results_table.tex` starts with `% MEDICAL_NOTICE`; `bundle.zip` is deterministic and contains no key; `items.csv` has no raw model text. |
| V10-10 | Local security | ✅ | Token gate, strict Host/Origin, `no-store`, `nosniff`, `no-referrer`, `DENY` and same-origin CSP are asserted on `/api/*` and static paths. |
| V10-11 | Browser flows | ✅ | The three formerly skipped flows are deterministic tests now: dry-run plan card + completion, fake-live run + export, stop/resume without duplicate evaluations. Keyboard/focus checks cover the builder and command palette. |
| V10-12 | CI | ❌ never ran | `.github/workflows/ci.yml` now targets ubuntu + windows and adds an offline Playwright job. Nothing was pushed, so no CI-passing claim is made. |
| V10-13 | Live provider calls | ❌ deliberately not done | All work used mock transports or local fake servers. A real provider's behaviour with a real Paper Pack remains unverified. |
| V10-15 | Full suite | ✅ | **603 passed, 0 skipped** locally (324.06 s), including the complete Playwright suite. No test is skipped by marker. |
| V10-16 | Five built-in presets | ✅ | `healthbench-rubric`, `medcalc-bench`, `medhallu-detect`, `medxpertqa-text`, `medsafety-bench` all validate and complete an offline dry run. Each declares `source_dataset`, `adaptation_status`, `loader.py`, full citation/BibTeX and 3-5 synthetic smoke rows tagged `PIPELINE TEST / SYNTHETIC SMOKE DATA`. |
| V10-17 | No upstream datasets in git | ✅ | Preset folders contain only `paper.yaml`, `loader.py`, `smoke.jsonl`, `README.md`; no CSV/JSONL upstream export is tracked. `.medeval_data/` is git-ignored and the loader refuses to write without schema verification. |
| V10-18 | Preset graders | ✅ | Tests cover ±5% numeric tolerance and exact numeric match, parameter field-F1, three-class macro F1/precision with coverage/abstention, paired McNemar and dual-judge refusal/harmfulness runs. |
| V10-14 | Protected artefacts | ✅ | `git diff` shows no changes under `results/study_runs/exp002-main`, `exp002-full`, `exp002-smoke` or `experiments/002_*`; the archived files still load through the registry and CLI. |

### What a reader should not trust about v0.6.0

- That CI is green: the workflow has never run.
- That any Paper Pack result reflects a real model: the shipped packs are
  synthetic templates and dry runs are `PIPELINE TEST ONLY`.
- That a pack reproduces a paper: the methodology tag says whether it is a
  faithful reproduction, an adaptation or a synthetic template. Only the person
  who wrote the pack can substantiate the first.
- That the statistics make a small sample conclusive: intervals are reported
  because they are wide; a non-significant McNemar test is not evidence of
  equivalence.
- That a re-grade is a new measurement: it reuses cached target outputs and
  changes only the evaluator side.
- That a built-in preset is a reproduction of its paper: all five are
  protocol-inspired adaptations with synthetic smoke rows; no upstream dataset
  was downloaded or licence-reviewed in this release.
