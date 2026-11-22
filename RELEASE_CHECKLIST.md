# Release checklist — v0.2.0 (historical)

> **Historical record.** This is the v0.2.0 release checklist, kept unchanged as
> evidence of what was actually run then. For the v0.5.0 open-source release see
> [`RELEASE_OSS_CHECKLIST.md`](RELEASE_OSS_CHECKLIST.md). The "not pushed / CI
> never executed" status described here is still true today; `PUSH_STATUS.md`
> records that.

Status: **committed locally, not pushed, CI never executed.** See `PUSH_STATUS.md`.

This checklist records what was actually run in the release environment (Windows
11, PowerShell, Python 3.14.6, CPU only, no local model server). Anything that was
not run says so rather than being ticked.

## Gate 1 — repository state: PASS

- Commits: `6a9b09b` (study machinery), `ef343f6` (real run + web app),
  `f5a9965` (aborted-run handling), plus the release documentation commit.
- Experiment 002 pre-registration committed **before** the first real call, with an
  append-only deviations log (`experiments/002_judge_families/PLAN.md`).
- Frozen-rule tag `rules-v0.1-freeze` still present; `experiments/001_*` unchanged
  except for lint-only edits.
- `git status` clean; 158 tracked files; no remote push performed.
- `main` is 3 commits ahead of `origin/main` (`c89d693`).

## Gate 2 — tests: PASS

- `python -m pytest -q`: **263 passed, 0 failed** in the delivery repository
  (`<repo>`) after the final sync.
- Includes hand-computed vectors for kappa, ECE, Brier, AUROC, phi and ceiling;
  key isolation (prompt byte-identical when the key changes); resume with zero
  repeated calls; cap refusal before any call; a fake key absent from every
  artefact; and the API endpoints including the SSE stream.

## Gate 3 — lint: PASS

- `ruff check .` → *All checks passed* using the configuration in `pyproject.toml`
  (E, F, W, I, B, UP, SIM, RUF, C4, PT; line length left to the formatter).
- Three genuine defects were fixed during the release: two template-version
  constants that silently duplicated their imports, an unused `primary` binding,
  and a loop variable that shadowed a used one.

## Gate 4 — secret scan: PASS

- `scripts/scan_secrets.py` scans tracked files and reachable Git blobs; its unit
  test uses a fake key.
- No API key appears in any tracked file, result, report or log. A test asserts a
  fake key cannot reach a run artefact.
- The working key lives only in the git-ignored `.env`. The previous `.env` in the
  delivery repository was preserved as `.env.previous-config` (also git-ignored),
  because its key returned HTTP 401 and its price fields did not parse.

## Gate 5 — licence and content audit: PASS

- `DATA_LICENSES.md` records source, licence and snapshot fingerprint per dataset.
- No benchmark text is tracked: `results/study_runs/*/items.jsonl`,
  `candidates.jsonl`, `calls.jsonl` and `cache.json` are git-ignored, and
  `data/cache/` is git-ignored. Committed: ids, verdicts, confidences, prompt
  hashes, aggregate metrics, reports.
- The two harness sample JSONL files that previously held verbatim item text stay
  untracked and are ignored for future runs.

## Gate 6 — real-judge run: PASS with a documented block

- Stage 1 dry run: `medeval run exp002 --dry-run` completes with mock judges, no
  network, no spend, and labels everything PIPELINE TEST ONLY.
- Stage 2 smoke run (`exp002-smoke`): 5 items, 15 calls, all parsed.
- Stage 3 main run (`exp002-main`): 60 items × 3 repeats = 180 judgements in 187
  calls under the 500-call cap; 0 API errors; 1 unparseable item; US$0.17.
- Resume verified: re-running a completed run made 0 network calls.
- **J2 never ran** (Ollama absent). The cross-family question is reported as not
  measured; no substitute judge was used.
- An earlier attempt (`exp002-full`) was aborted for truncated replies and is kept
  with `status: aborted` and its reason.

## Gate 7 — web app: PASS, rendered and exercised

- `medeval ui` served on `127.0.0.1`; `/api/health`, `/api/runs`,
  `/api/runs/{id}`, `/api/runs/{id}/items`, `/api/runs/{id}/report`,
  `/api/runs/{id}/events`, `/api/settings` and `/api/estimate` all answered 200
  against the real run data; the SSE stream emits snapshots and log events.
- Headless Edge rendered **every page** against the real `exp002-main` data:
  runs, results (metric cards, confusion-matrix data, reliability chart, one-judge
  banner), item explorer (60 items, filters, question preview), new run (live
  estimate), settings (no key shown).
- The first render found four defects that no API test could see, all fixed:
  1. the command palette covered every page (`display: flex` outranking `[hidden]`)
     and Esc did nothing;
  2. an aborted run advertised `100.0%` accuracy in the runs list;
  3. `j2_ollama` was listed as a judge of runs where it never started;
  4. a stale `app.js` masked fix (1) until the shell was served `no-store`.
- Still unverified: keyboard navigation, screen-reader output, contrast ratios and
  tablet-width layout (`VERIFY_ME.md` V3-12).

## Gate 8 — push: NOT DONE

- No push. `PUSH_STATUS.md` records the three unpublished commits, the fact that CI
  has never run, and the instruction to confirm repository visibility first.
