# DECISIONS

Every non-obvious choice, why it was made, and what was rejected. Decisions are
numbered so later versions can cite them. "Conservative" here means: prefer the
option that cannot be mistaken for a stronger claim than the data supports.

---

**D-001 — Build location and final copy.**
The session environment permits writes only inside its workspace, so the project
was built at `<workspace>\medeval-lab` and copied to `<repo>`
at the end. The copy excludes `.venv`, caches and the large call log; `run_all.ps1`
recreates them. *Rejected:* writing into `%USERPROFILE%` file-by-file, which
needed an approval per file.

**D-002 — Python 3.14.6 with a clean virtual environment.**
A fresh `python -m venv` (not `--system-site-packages`) was created and every
dependency installed from wheels. All packages in `requirements.txt` had cp314
wheels, so no fallback interpreter was needed. Versions are pinned to what actually
ran. *Rejected:* inheriting the system site-packages, which would have hidden
version drift and made the build unreproducible.

**D-003 — One distribution, six packages.**
All six `medeval_*` packages plus the `medeval` CLI ship as a single distribution
(`medeval-lab`) with one `pyproject.toml`. *Why:* the packages are versioned
together and share the same criteria/metrics versions; six separate distributions
would add release overhead with no benefit at v0.1.

**D-004 — The harness is consumed read-only, registered by `.pth` when pip fails.**
`pip install -e` on the upstream checkout failed (setuptools/editable-wheel
incompatibility), so the venv gets a `.pth` file pointing at the checkout. The
upstream tree is never edited. `run_all.ps1` retries the pip path first, so a
normal machine may end up with a true editable install. *Rejected:* vendoring the
harness or patching its packaging metadata, both of which would break the
read-only rule.

**D-005 — Harness tasks use pinned local JSONL snapshots.**
`data/tasks/*.yaml` point at `data/cache/*.jsonl` written by the audit instead of
the live Hugging Face dataset. *Why:* the audit records a content fingerprint, so
the exact items behind a score are identifiable, and runs are not silently changed
by upstream dataset edits. Prompts, choices and 0-based targets are identical to
the upstream tasks, so accuracy is comparable. *Rejected:* running the upstream
task directly, which in this environment required a writable global HF cache and
would have left the evaluated items unidentified.

**D-006 — Mock backend is the default, and every mock result is stamped.**
No API key, no local server and no `torch` existed, so the client resolves to the
deterministic mock judge. Every verdict and result file carries
`pipeline_test_only` and the phrase "PIPELINE TEST ONLY". *Rejected:* silently
reporting simulated accuracy as if it were a model measurement, or skipping
Experiment 001 entirely (which would have left the analysis code untested).

**D-007 — PubMedQA skipped, not patched.**
`datasets` 5.2 cannot run its legacy loading script. Isolating a `datasets<4`
environment would add a second Python environment for one non-multiple-choice
benchmark. The skip is recorded in every audit report and in `VERIFY_ME.md`.
*Rejected:* pinning the whole project to `datasets<4`, which would have broken
MedMCQA loading.

**D-008 — Rule judge scope is deliberately small and its accuracy is an upper bound.**
10 contraindication pairs and 8 dose ceilings, with a 55-character negation guard.
The rules were written with the seed cases in view, so the measured 96% is
optimistic and is labelled as such everywhere. *Rejected:* writing a rule per case,
which would have measured the rule file's redundancy rather than its coverage.

**D-009 — Two judges' statistics: kappa *and* AC1 *and* percent agreement.**
Experiment 001 produced 76% agreement with kappa ≈ 0 — the prevalence paradox.
Reporting kappa alone would have supported a false "the judges disagree" claim.
All three are reported and the paradox is called out in the report.
*Rejected:* switching to AC1 alone, which would hide the sensitivity of kappa.

**D-010 — BCa bootstrap for primary intervals.**
BCa corrects for bias and skew at small n; percentile intervals are reported as a
sensitivity check in code. 10 000 resamples with a fixed seed, recorded in every
interval object. *Rejected:* normal-approximation intervals, which are poor for
proportions near 0 or 1 (several judges sit at 0.96).

**D-011 — No composite score in any conclusion.**
A weighted rubric total exists because the function is useful for inspection, but
it is labelled exploratory and no ranking or conclusion uses it. The per-criterion
scores are the reported unit. *Rejected:* a single "safety score", which would
imply an interpretation nobody has validated.

**D-012 — Every safety criterion must carry the caveat, enforced by code.**
The criteria loader rejects any `safety_related: true` entry whose `limitations`
does not contain "literature-derived, not clinician-validated". *Why:* the phrase
is the difference between a method and a clinical claim.

**D-013 — `human_reference` is present and null everywhere.**
Every item and case schema carries the field, and the validator warns if a human
reference has fewer than two raters (agreement cannot be computed from one). No
clinician workflow is built. This lets a later version add labels without
redesigning the schema. *Rejected:* omitting the field until needed, which would
force a schema migration later.

**D-014 — Two extra item fields (`task_type`, `notes`).**
The required schema fields are exactly those in the brief; `task_type` and `notes`
were added as optional because the criteria catalogue needs applicability and
because planted-error items need to state what was planted. `additionalProperties`
is false, so the extension is explicit rather than accidental.

**D-015 — Answer-key audit checks structure, never correctness.**
Missing keys, out-of-range indices, conflicting key columns, duplicate options,
key distribution and shortcut baselines are all mechanical. Whether the keyed
answer is medically right requires a clinician and is out of scope. Stated in the
audit report header and in `VERIFY_ME.md` (B8).

**D-016 — Option-index base is detected, not assumed.**
MedMCQA's `cop` and MedQA's `label` are 0-based; the audit verifies the observed
range rather than trusting the documentation, because a 1-based key fed to a
positional choice list silently shifts every answer by one.

**D-017 — Experiment 001 target side is randomised deterministically.**
Half the cases put the planted error in slot A, decided by
`SHA-256(case_id)`. Without this, a position-biased judge is indistinguishable
from an accurate one, and the position-bias probe has no control.

**D-018 — Two-member jury kept and reported as a negative result.**
The pre-registration named a jury of the two mock judges. It ties whenever the
members disagree, which scored as a miss and dropped accuracy to 62%. The finding
is reported rather than fixed by adding a third member, because the point of the
experiment is to surface design consequences.

**D-019 — `HF_DATASETS_CACHE` is relative.**
The datasets builder derives lock filenames from the cache path; an absolute path
under a deep directory exceeds the Windows path limit. `run_all.ps1` sets
`HF_DATASETS_CACHE=hf` and runs from the repo root. Documented in `VERIFY_ME.md`
(A4) so a Linux or macOS user is not surprised.

**D-020 — Wording policy.**
The repository never says "clinically validated". It says "reference-label
validated" or "judge-reliability tested", and "literature-derived, not
clinician-validated" for safety criteria. This is enforced by review, not by code,
except where D-012 applies.

---

## Smaller calls, recorded for completeness

- **D-021** — Criteria and metrics are versioned separately from the toolkit
  (`CRITERIA_VERSION`, `METRICS_VERSION`, `RUBRIC_VERSION`, `ITEMS_VERSION`), so a
  score can be tied to the exact definitions that produced it.
- **D-022** — `verified` in `data/literature.csv` is set by an automated
  Crossref/DataCite title-match script, not by hand, so the evidence is
  reproducible (`data/literature_verification.json`).
- **D-023** — Tests override pytest's `tmp_path` because the sandbox's restricted
  token cannot read owner-only temp directories; the override is harmless on
  normal machines.
- **D-024** — Figures are PNG at 160 dpi, written by matplotlib with the Agg
  backend; no interactive plotting dependency is implied.
- **D-025** — The call log is git-ignored (16 MB of prompts); verdicts with prompt
  hashes are kept. Full prompts are regenerable from seeds and template versions.

## v0.2.0 decisions

- **D-026 — Do not run an uncredentialed real-judge experiment.** The environment
  had no API key, endpoint or model identifier. Experiment 002 is dry-run only;
  reporting mock substitutions would violate the real-judge objective.
- **D-027 — Small hard defaults before paid calls.** The default cap is 1,500
  cache-miss calls and US$5 estimated cost. If provider price fields are not
  verified, the USD estimate is unavailable and the call cap remains binding.
- **D-028 — Cache successes by model, prompt hash, temperature and seed.** This is
  the conservative key that prevents a rerun paying for the same request while
  preserving distinct stochastic conditions.
- **D-029 — Hold rule changes until after held-out scoring.** Negation and
  weight-based dosing are known extensions, not retroactive repairs. The v0.1
  rule file remains the pre-held-out rule set and must be tagged before scoring.
- **D-030 — One provider is not an independent jury.** Two DeepSeek prompt modes
  plus rules form a three-member majority operationally, but model-family error
  correlation and self-preference are not identifiable.
- **D-031 — Preserve history; retire the original hidden split.** The ten hidden
  items exist in early commits. Rewriting would alter the pre-registration commit
  and freeze tag, so history is retained. The items are not hidden; a replacement
  hidden set must be kept outside Git from its first draft.

## v0.2.0 implementation decisions (Experiment 002 as specified)

- **D-032 — The pairwise protocol moved to `002a`; `002` now means the specified
  grading study.** The earlier protocol was pre-registered at commit `ba490f0`
  and produced no real-judge result. Rather than delete that record, or silently
  reuse its number for a different experiment, the directory was renamed with
  `git mv` to `experiments/002a_pairwise_blocked/` and a note was added. Its
  commit history and its frozen-rule result (13/20) are intact.
- **D-033 — MedQA-USMLE via the `-hf` mirror, cc-by-sa-4.0, never redistributed.**
  `GBaker/MedQA-USMLE-4-options-hf` is the 4-option split the v0.1 audit already
  loaded; its licence was re-read from the Hugging Face API for this release. The
  licence is share-alike, so the sampled item text, candidate text and full model
  output stay in git-ignored run files. Only item ids, verdicts, confidences,
  prompt hashes and aggregate metrics are committed.
- **D-034 — Candidate answers are built from fixed templates, never by a model.**
  DeepSeek is J1, and a judge favours its own family's text. Six templates are
  shared between the correct and incorrect conditions and selected by a hash of
  the item id, so phrasing cannot leak the condition; the wrong option is the
  distractor with the highest content-word overlap with the key, because that
  produces an adjacent differential rather than an unrelated one. The rationales
  are template filler and every report says so.
- **D-035 — The key cannot reach a judge.** The judge receives a `JudgeView`,
  a type with no key field, and the prompt renderer only accepts that type. This
  is checked two ways in the tests: the dataclass has exactly three fields, and
  changing the key leaves the rendered prompt byte-identical.
- **D-036 — Strict JSON, one retry, then an honest failure.** Only
  `CORRECT`/`INCORRECT` plus an integer 0-100 is accepted; a numeric string or
  lower-case verdict is normalised, everything else is a parse failure. The retry
  re-asks the identical prompt with a different cache salt, so both attempts are
  logged under the same prompt hash and the second is a real call, not a cache hit.
- **D-037 — Confidence is `P(own verdict is right)`.** ECE (5 bins) and Brier
  therefore compare stated confidence with the observed frequency of agreement
  with the key, and AUROC asks whether confidence separates right verdicts from
  wrong ones. A second AUROC of the raw verdict against the truth is reported
  separately for transparency.
- **D-038 — Percentile bootstrap over items, 1,000 resamples, pairs resampled
  jointly.** The specification asks for bootstrap intervals here, not the BCa
  intervals used in Experiment 001. Proportions use Wilson intervals. Nothing uses
  a normal approximation at n=60.
- **D-039 — A blocked judge is reported, not substituted.** Ollama is unavailable
  on the execution machine, so J2 could not run. The specification's instruction
  is to fail with clear instructions rather than pick a different judge, and the
  pre-registration declares the consequence in advance: the cross-family question
  is reported as *not measured*, and a weaker single-judge flag replaces the
  two-judge possible-key-error rule without ever being called a key error.
- **D-040 — Token budget raised from 512 to 2048 after a truncation defect.**
  The first full run returned 42 empty replies in which the provider's thinking
  mode had consumed the entire 512-token allowance (`output_tokens ==
  reasoning_tokens == 512`), losing 7 of 180 judgements. The run is retained with
  `status: aborted`, the change is recorded in the pre-registration's deviations
  log, and a fresh run was executed. No prompt, sample, seed, temperature, metric
  or decision rule changed, and no result from the aborted run is reported.
- **D-041 — The web app is loopback-only and build-free.** FastAPI on
  `127.0.0.1` with hand-written HTML/CSS/JS served from `packages/medeval_web/static`.
  No CDN, no bundler, no telemetry, no authentication (there is no remote
  surface). Progress is streamed by tailing the run directory, so a page reload,
  a second tab, or a run started from the terminal all show the same state, and
  nothing is lost when the browser closes.
- **D-042 — A live run needs an explicit confirmation.** The form defaults to a
  dry run; starting a run that can spend money requires `confirm_real_calls` in
  the API and a confirmation dialogue in the UI. `Stop` finishes the call in
  flight, then writes reports from the judgements already on disk.

## v0.3.0 decisions

- **D-043 — Keys live only in process memory; there is no "remember on disk".**
  The v0.3 specification is categorical. `KeyStore` holds keys in a dict behind a
  lock, the browser sends them in request bodies (never URLs), the stored
  connection file holds metadata only, and `serve()` forgets everything in a
  `finally` block. `.env` loading is off unless `medeval ui --use-env`, because
  an implicitly loaded file is a side channel around the memory rule.
- **D-044 — A local app that can spend money still needs a token.** Binding to
  127.0.0.1 stops the outside world but not other local users, other browser
  tabs, or a malicious web page driving the API cross-site (any web page can
  POST to localhost). A random per-launch token delivered in the URL fragment
  (never sent to servers), kept in `sessionStorage` and required on every API
  call closes that. Host and Origin checks stop DNS rebinding and cross-site
  pages; no CORS headers are sent at all.
- **D-045 — Preset base URLs are documentation-verified, never guessed.** Each
  preset URL was read from the provider's official documentation at build time
  (see `VERIFY_ME.md` for source URLs and the date) and every field stays
  editable, so a moved endpoint is a user edit rather than a code change.
- **D-046 — The Test center's allow-list is server-side and closed.** The
  browser sends only a command *key*; the argv, working directory and no-shell
  execution live in `testcenter.py`. Anything not in the dict does not exist as
  far as the API is concerned, and results keep counts and ids only, because
  command output can quote arbitrary repository content.
- **D-047 — Screening and judging are different roles by construction.** The
  screening model must not also be a judge (blocked in the UI, validated in the
  store): Experiment 003's strata would be circular if the model defining
  "hard" were one of the instruments being measured.
- **D-048 — The pilot gate stops the run, not the analysis.** When the floor of
  5 disagreements is not reached, the run ends with `status: pilot_stopped` and
  the partial analysis is still written, so the ceiling is reported honestly
  while every correlation claim is withheld.
- **D-049 — Accuracy is always reported two ways.** Failed items can be excluded
  (the v0.2 denominator) or counted as disagreements with the key. A judge with
  parse failures looks better under the first and worse under the second, so
  neither is quoted without the other beside it.
- **D-050 — The config lock refuses, it does not merge.** A resume that changes
  seed, sample, judges, repeats, temperature or pilot settings would mix two
  designs into one judgement file. The run is refused before any call and every
  differing field is printed; a fresh run id is the correct path.
- **D-051 — Known fixture strings are excluded from the secret scan instead of
  rewriting history.** The v0.3 scanner is stricter (provider key shapes), and
  some pre-v0.3 test fixtures do not carry a marker word. Since this project
  never rewrites git history, the exact fake strings are listed in the scanner
  with a comment; new fixtures must use a marker word ("fake", "test", …) and
  real configured keys are flagged wherever they appear, list or not.
- **D-052 — No `REPORT_v0.3.md` exists yet.** The specification forbids a
  results report before a real run; what was built and what is unverified is
  recorded in `README.md`, `CHANGELOG.md` and `VERIFY_ME.md` instead.

## v0.4.0 decisions

- **D-053 — The browser suite may skip, but never lie.** Playwright drives a real
  headless Edge/Chromium against the real app; when a driver or browser cannot
  start in an environment, the module skips with the exact error and points at
  `MANUAL_SMOKE.md`. A green suite therefore means "the flows that ran passed",
  never "every flow was exercised" — the report says which.
- **D-054 — Seven browser flows are manual, and the reason is written down.** The
  in-suite selectors for the Test-connection button, the role warnings, the
  dry-run/live session cards, stop-and-resume and the DOI fetch became flaky once
  several connections existed in one session. Each is covered by API tests and
  was exercised in an interactive probe, then marked skipped with its reason
  recorded so nobody mistakes it for coverage.
- **D-055 — Refusals are not API errors.** A provider refusal or a spend-guard
  refusal is recorded as `failure_type: refused`. Folding it into `api_error`
  would hide the difference between "the provider said no" and "we never reached
  the provider", which is exactly the distinction a reviewer needs.
- **D-056 — A stage is a process; `run.json` is the record.** Staged Experiment
  003 runs as several processes over one run directory, so `StudyRun` now merges
  the stored state on resume. Status and `finished_utc` stay with the new stage;
  everything else (gate decision, strata, screening summary, stage history) is
  carried forward. Without this every stage forgot the previous one.
- **D-057 — The stage is not part of the config lock.** The lock protects the
  design (seed, sample, judges, repeats, temperature, pilot rules). Which stage
  runs next is execution, not design, so `stage` is deliberately excluded — and a
  test pins that advancing a stage does not trip the lock.
- **D-058 — Adaptive screening is a pre-registered amendment, not a tweak.** The
  plan fixed 600 items; a strong screener would have yielded 30–60 hard items
  instead of 100. The amendment was written into the deviations log and committed
  before the code existed, with the stop rules and control-stratum definition
  fixed in advance.
- **D-059 — Export refuses rather than redacts.** The bundle is assembled by
  whitelisting five files and then scanning each for key-shaped strings and long
  text fields; a hit aborts the export with an error. Silently stripping a field
  would hide that the run directory contained something that should not have been
  there.
- **D-060 — Reviewer labels live only in a git-ignored file.** They are one
  person's opinion, they can quote item text, and they must never enter a run
  record or the headline metrics. The only place they surface is a clearly
  labelled sensitivity table.
- **D-061 — `medeval doctor` reports booleans, never values.** It may say "an API
  key is present in the environment"; it never reads, prints or stores the key.
  It also runs a token-flow check with a throwaway token.
- **D-062 — A busy port must explain itself.** The v0.3 "token required" dead end
  happened because a stale server held the port. `medeval ui` now detects that the
  holder answers like medeval, says so, prints the exact PowerShell to stop it,
  and moves to the next free port with a fresh token.
- **D-063 — Papers are verified by script, never by hand.** The 19 added rows came
  from a secondary summary the owner supplied and may be wrong; each went through
  the Crossref/arXiv title match, which set `verified=yes` only on a match. The
  three findings that matter (a preprint date, a mislabelled topic, a
  "preprint" record) are written into `VERIFY_ME.md` rather than smoothed over.

## v0.5.0 decisions (open-source release, bring your own key)

- **D-064 — The release audit only judges what a clone would receive.** It reads
  tracked content (plus, on demand, every reachable blob), because untracked files
  are not published. It never prints a matched secret: findings carry the path,
  line and rule only. The one exception is personal paths and emails, which are
  what the owner has to fix and are not credentials.
- **D-065 — Historical results stay byte-identical; exceptions are visible.**
  Four v0.1 audit/baseline records contain the build machine's absolute path. They
  are evidence and were not edited. `.github/oss_audit_allowlist.txt` lists each
  path with a reason and is *rule-scoped*, so a key-like string in those files
  would still fail the audit, and the audit prints how many findings it suppressed.
- **D-066 — Git history is not rewritten; the owner chooses.** History contains
  the old build paths, historical fake test keys (no credential) and the ten
  formerly hidden seed items. `OSS_AUDIT.md` gives the two options — publish the
  existing history, or publish from a clean squashed copy — with the trade-off
  (pre-registration timestamps vs. old strings and hidden items). Nothing was
  rewritten.
- **D-067 — No default provider, judge, model or price anywhere in new-run code.**
  `StudyConfig.judges` defaults to empty; the CLI, the app and the composer refuse
  a live run without a connection and say how to add one. The archived Experiment
  002 pair (`j1_deepseek`/`j2_ollama`) survives only when named explicitly, for
  reproducing the original configuration, and its old `DEEPSEEK_API_KEY` fallback
  and `deepseek-chat` default are gone: the CLI never guesses a model id.
- **D-068 — Headless keys are named, not stored: `connections.yaml` + `key_env`.**
  The file is parsed and validated before anything is called, refuses any key
  field, embedded-credential URLs and unknown fields. A live CLI run needs
  `--connections` **and** `--confirm-live`, prints the plan and worst-case cost
  first, and enforces the same per-connection and run-wide caps as the app. There
  is deliberately no `--api-key` argument (shell history, process list), and a
  test walks every subparser to keep it that way.
- **D-069 — HTTP redirects are never followed.** `requests` would re-send the
  `Authorization` header to whatever host a `3xx` names. A redirect is a terminal,
  never-retried error that names the target host only. A fake server pair asserts
  the second host is never contacted and no retry happens.
- **D-070 — The app is loopback-only, with an explicit escape hatch.** A
  non-loopback `--host` is refused unless `--allow-remote` is passed, which prints
  a loud warning; the token, Host and Origin checks stay active and the Host
  allow-list covers the bound interface. Plain `http` to a non-loopback provider
  host is a warning, not a block, because some private gateways only speak http.
- **D-071 — Prices are never shipped; a missing price is said out loud.** Presets
  carry a pricing URL, a docs URL and a `last_verified` date but no price. With any
  connection unpriced the estimate says "unavailable, call cap is binding" and the
  call cap is what stops the run. In the app a price field left empty is a
  deliberate choice, never a silently assumed number.
- **D-072 — Dry runs use neutral mock bindings.** Previously a dry run mapped the
  archived provider ids to `j1_mock`/`j2_mock`, which meant every install had two
  providers baked into the code path. `medeval_study.mock_bindings` supplies two
  labelled mock judges instead; every artefact still says `PIPELINE TEST ONLY`.
- **D-073 — Provider presets are convenience only.** Base URL, API style, a docs
  link, a pricing link and a date. No model id is pre-filled: `Test connection`
  lists the provider's models and the user clicks one, so a model is never
  auto-selected.
- **D-074 — Pre-commit mirrors the cheap CI checks.** ruff, the release audit and
  the secret scan run locally with the same scripts CI uses, so a local failure is
  the same failure. The full-history audit is not a pre-commit hook (it is slow and
  its findings are triaged, not fixed by editing history).
- **D-075 — The history audit is a separate, manual CI job.** It cannot be green
  until the owner chooses a history strategy (D-066), so making it run on every
  push would either fail forever or have to be ignored. It is `workflow_dispatch`
  only, and `OSS_AUDIT.md` records what triage means.
- **D-076 — 0.5.0 is the public-release version, and it is still unpushed.** The
  repository is ready to publish: nothing has been tagged, pushed, or claimed as
  passing CI. `RELEASE_OSS_CHECKLIST.md` holds the owner's steps, including the
  key revocations that only the owner can do.

## v0.5.1 decisions (byok follow-ups)

- **D-077 — A fresh Models page names no provider.** The preset dropdown opens on
  an empty "Choose a provider" option, Save stays disabled until one is chosen, and
  choosing one fills the base URL only — never a model, a family or a price. The
  first screen a stranger sees must not imply a recommended provider, and "which
  model" is always a deliberate owner action (D-067, D-073).
- **D-078 — The archived Experiment 002 ids are read-only.** `j1_deepseek` and
  `j2_ollama` remain in the runner because the archived run records them and its
  dry run still uses mock stand-ins, but a *live* run that names them is refused
  with the add-a-connection message. No non-archive code path resolves a provider,
  family, model or environment variable from them: the family default, the
  environment-configured endpoint and the Ollama probe are gone, and the legacy
  `available_judge_summary` / `/api/settings.judges` status (which named J1/J2) was
  removed. Reading `exp002-main` and rebuilding reports is unchanged.
- **D-079 — The "not a medical device" notice is part of the product, not docs.**
  It is a short notice on the start screen and in the first-run card, a one-line
  footer in the shared app shell (so it is on every page), and the header of every
  generated `report.md` — produced by a single `MEDICAL_NOTICE` constant inside the
  report writer so no caller can omit it, and therefore present in exported
  bundles. The app must not be mistaken for a clinical tool even if someone never
  reads the README.

## v0.6.0 decisions (Paper Packs, matrix runs and paper-grade exports)

- **D-080 — A Paper Pack is data, not code.** `papers/<id>/paper.yaml` is a strict,
  versioned declaration of task type, dataset adapter, prompts, graders,
  statistical protocol and citation. Unknown keys are errors, so a typo cannot
  silently change a plan. A pack is self-contained: its dataset must live inside
  the pack directory and a `..` escape is rejected. The alternative (Python
  plugins per paper) would put arbitrary code on the run path for no benefit.
- **D-081 — Three methodology labels, and no clinical claims.** Every pack must
  say whether it is a `faithful_protocol_reproduction`, an `inspired_adaptation`
  or a `synthetic_pipeline_template`. The validator rejects "clinically
  validated", "clinician reviewed" and similar phrases in pack metadata. The
  single `MEDICAL_NOTICE` is still written by the report/export layer, so a pack
  cannot omit it.
- **D-082 — Targets and evaluators are separate roles.** A matrix run is
  1 Paper Pack × N target models × M graders/judges. Target bindings and judge
  bindings are distinct types of record; the runner never treats a judge as a
  target or vice versa. Cross-family error correlation is only estimated for
  judges that declare different `family_label`s; same-family panels are reported
  as unable to answer that question.
- **D-083 — Cache keys are content-addressed, not run-addressed.**
  `.medeval_cache/<prefix>/<sha256>.json` is keyed by
  `sha256(model_id + params + rendered_prompt + item_hash)`. The same request from
  a different run id, target label or process maps to the same entry, which is
  what makes stop/resume and repeated experiments free of duplicate paid calls.
  The key deliberately does not include the run id: that would defeat the point.
- **D-084 — Append-only logs are the source of truth; run.json is a snapshot.**
  `outputs.jsonl` and `scores.jsonl` are fsynced per record and replayed on
  resume. `run.json` is written atomically (with a Windows sharing-violation
  retry) and only published with a terminal status after the report and bundle
  exist, so a poller never sees `completed` with no `report.md`.
- **D-085 — A budget stop is a normal terminal state, not an exception that
  loses work.** The call and USD caps are checked before every dispatched call;
  when the next call would cross one, the run records `budget_stopped`, writes the
  report from completed items and exits 0. Pre-flight shows the upper bound, but
  the per-call guard is what actually binds.
- **D-086 — Re-grade is target-free by construction.** `medeval regrade` reads the
  original `outputs.jsonl` and applies graders/judges to cached text. It never
  constructs a target transport, and the run records `target_calls: 0`. A new
  rubric or a different judge family is therefore cheap to try without
  re-querying models under test.
- **D-087 — Statistics are seeded and undefined is `nan`/`None`.** Percentile and
  stratified bootstrap intervals, McNemar (exact/small, continuity-corrected
  otherwise), paired bootstrap, Cohen/Fleiss/Krippendorff, cross-family phi,
  latency percentiles and cost-per-correct are computed in
  `medeval_core/stats.py` with numpy/scipy only. An undefined statistic is never
  replaced by a fabricated zero.
- **D-088 — Exports are deterministic and notice-first.** `bundle.zip` uses fixed
  ZIP timestamps and sorted entries; `report.md` starts with `MEDICAL_NOTICE`;
  `results_table.tex` starts with `% MEDICAL_NOTICE`; `items.csv` carries scores
  and hashes, never raw model text. No export contains a key, a prompt or dataset
  text.
- **D-089 — The local server's hard perimeter is explicit.** The launch token,
  strict Host and Origin checks, `no-store` on every response, `nosniff`,
  `no-referrer`, `X-Frame-Options: DENY` and a same-origin CSP are part of the
  product, not deployment advice. The CSP allows inline *styles* only because the
  lightweight UI sets a few style attributes; scripts remain `'self'`.
- **D-090 — CI now covers two operating systems and a browser job, and is still
  unrun.** The workflow matrix is `ubuntu-latest` + `windows-latest` for Python
  3.12/3.13, plus an offline Playwright job. It was added in this release and has
  not been observed to run: the repository has not been pushed, so no "CI passed"
  claim is made (`VERIFY_ME.md` V10-*).

- **D-091 — Built-in presets ship synthetic smoke data, not upstream datasets.**
  The five presets (`healthbench-rubric`, `medcalc-bench`, `medhallu-detect`,
  `medxpertqa-text`, `medsafety-bench`) model published evaluation primitives but
  bundle only 3-5 synthetic `smoke.jsonl` rows tagged
  `PIPELINE TEST / SYNTHETIC SMOKE DATA`. `paper.yaml` declares the upstream
  `source_dataset` URL/Hugging Face reference, licence and `adaptation_status`.
  `loader.py` is explicit and on-demand: it verifies every row against the same
  normaliser the runner uses, writes only under git-ignored `.medeval_data/`, and
  refuses `--download` without `--accept-license`. This keeps the repository
  lightweight, avoids redistributing benchmark text, and prevents a preset from
  silently becoming a licence or contamination problem.
