# OSS release audit (v0.5.0)

What a stranger would receive if this repository were cloned, and what was done
about it. Findings are reported by **file path, rule and count only**. No secret
value is printed anywhere in this document; no local `.env` file was opened, read
or copied during the audit.

**Scope.** 253 tracked files and every one of the 668 blobs reachable from any
git ref (46 commits). Historical result files under `results/` are evidence and
are kept byte-identical (`DECISIONS.md`, "results are immutable"); where they
contain a personal string it is listed below rather than edited.

**Tools.** `scripts/scan_secrets.py` (pre-existing: key shapes plus an exact
match against the key the environment configures, over tracked files and every
history blob) and the new `scripts/oss_audit.py` (this release: key shapes,
personal paths, emails, tracked `.env` files, oversized files, run artefacts and
an optional git-ignored personal deny-list). Reproduce with:

```powershell
cd <repo>
.\.venv\Scripts\python.exe scripts\scan_secrets.py
.\.venv\Scripts\python.exe scripts\oss_audit.py
.\.venv\Scripts\python.exe scripts\oss_audit.py --history   # release preparation only
```

Verdict at HEAD: **`PASS oss-audit 0 finding(s) in 253 files, 12 documented
exception(s)`** and **`PASS secret-scan tracked-files-and-history`**.

## 1. Credentials and key-like strings

| Area | Result |
| --- | --- |
| Tracked working tree | 0 key-like strings. `scan_secrets.py` also checks the key held in the local environment against every tracked byte and every history blob: PASS. |
| Git history | 13 key-shaped hits, all inside historical versions of test files, all fake fixtures (`sk-test-…`, `sk-or-v1-…`): `tests/test_m2b.py`, `tests/test_web_security.py`, `tests/test_connection_run.py`. `scan_secrets.py` already knows them as fixtures and nothing else in history matches. No real credential was ever committed. |
| Tracked `.env` files | 0. `git log --all -- .env` returns nothing; `.env.example` is the only tracked env file, and in v0.5.0 it contains placeholder names only. |
| Command line | There is no `--api-key` option anywhere, by design (it would leak into shell history and process lists). A test fails the build if one is added. |

`.env`-style files that exist **on this machine but are git-ignored** (listed by
name only — contents were not opened):

| Path | Tracked? | Action for the owner |
| --- | --- | --- |
| `<repo>\.env` | no (git-ignored) | **Delete it, and revoke every key it may have held at the provider.** |
| `<repo>\.env.example` | yes | Safe: placeholder names only. |
| `<repo>\.tmp\pytest-of-*\...\.env` | no (git-ignored `.tmp/`) | Test artefact; deleted with `.tmp/`. |

`.gitignore` ignores `.env`, `.env.*` (with `!.env.example`), `.private/`, `hf/`,
`.hfhub/`, `data/cache/`, caches, local reviewer notes, exported bundles, the
headless `connections.yaml`, and run directories that tests or a local study can
create.

## 2. Personal paths

Fifteen occurrences of the build machine's absolute path
(`C:\Users\<owner>\…`) were found in 13 tracked files. Eleven files were
neutralised to documented placeholders — `<repo>`, `%USERPROFILE%`,
`$env:MEDEVAL_HARNESS_PATH` — with no change of meaning:

`CHANGELOG.md`, `DECISIONS.md`, `MANUAL_SMOKE.md`, `NEXT_STEPS.md`,
`README.md`, `RELEASE_CHECKLIST.md`, `REPORT.md`, `REPORT_v0.2.md`,
`USER_GUIDE.md`, `VERIFY_ME.md`, `run_all.ps1`.

`run_all.ps1` also had the owner's `lm-evaluation-harness` checkout path as a
**default parameter value**; it now uses `MEDEVAL_HARNESS_PATH` or a sibling
`lm-evaluation-harness` directory, and skips that optional stage when neither
exists.

Four historical records still contain the path and were **kept unchanged**; they
are enumerated in the tracked allow-list `.github/oss_audit_allowlist.txt` with a
reason, so the audit stays green while the decision stays visible:

| File | Rule | Why |
| --- | --- | --- |
| `results/audit/audit_report.json` | personal-path, deny-list | v0.1 audit evidence, byte-identical |
| `results/audit/audit_report.md` | personal-path, deny-list | v0.1 audit evidence, byte-identical |
| `results/audit/datasets.csv` | personal-path, deny-list | v0.1 audit evidence, byte-identical |
| `results/harness_runs/baseline_20261115T144952Z.json` | personal-path, deny-list | v0.1 harness baseline, byte-identical |

The exception list is rule-scoped on purpose: a key-like string in those same
files would still fail the audit. `TROUBLESHOOTING.md` mentions the OneDrive
product generically (a Windows sync problem, not a person) and was left alone.

## 3. Identities, emails, local strings

* **Email addresses in tracked content: 0.** `.github/allowed-emails.txt` exists
  and is empty apart from instructions; the audit fails on any address that is
  neither listed there nor an obviously non-routable example domain.
* **Git authors: one identity, 46 commits:** `medeval-lab <medeval@example.invalid>`.
  No personal name or address appears in any commit object. Nothing needs to
  change; the owner may switch to a GitHub noreply address for future commits.
* **Machine name: not found** in tracked content or history.
* **Personal handles:** the repository URL in `CITATION.cff` (and in the git
  remote, which is not tracked content) contains the owner's GitHub handle. That
  is the intended publish target, so it was **kept**; it appears nowhere else
  after `PUSH_STATUS.md` was rewritten as a neutral status note.
* **Local deny-list.** `.private/oss_deny_list.txt` holds the owner's private
  strings (2 lines: a local user name and a workspace name). It is git-ignored;
  the audit reads it and fails if any of those strings appears in tracked
  content. The strings are deliberately not repeated here.

## 4. Large files and run artefacts

* Tracked files over 5 MB: **0**. Largest tracked file: `results/experiment_001/`
  `verdicts.jsonl` at 994,969 bytes. Total tracked content: 4.5 MB.
* Tracked `calls.jsonl` / `cache.json` / `items.jsonl` / `candidates.jsonl` /
  `screening_cache.json` / reviewer-note files: **0**. The audit fails if one is
  added.
* Benchmark item text is not redistributed: `data/cache/` and `results/**`
  call/item dumps are git-ignored; only ids, verdicts, hashes and aggregates are
  tracked. Third-party licences are in `DATA_LICENSES.md` and `NOTICE`.

## 5. What remains in history and cannot be changed without rewriting it

`scripts/oss_audit.py --history` reports **273 findings** across 668 history blobs
(34 of them are covered by the tracked allow-list and are counted as documented
exceptions). All 273 are content already fixed at HEAD:

| Rule | Count | What it is |
| --- | --- | --- |
| `personal-path` | 126 | the build machine's absolute path in earlier versions of the 13 files listed above |
| `deny-list` | 129 | the same, seen through the private deny-list |
| `openai-style-key` | 12 | historical fake keys inside `tests/test_m2b.py`, `tests/test_web_security.py`, `tests/test_connection_run.py` |
| `email-address` | 3 | the first committed version of `tests/test_oss_audit.py` quoted invented addresses to test the email rule; they use reserved example domains at HEAD |
| `quoted-credential` | 2 | a dummy password string that test fed to the audit, in that same first version |
| `private-key-block` | 1 | a PEM header with no key material, in that same first version |

The last three rules come from one commit (`34b1a95`, the audit's own tests) whose
later versions build those strings at run time so the tracked source carries no
key-shaped literal; the two fixture strings are recorded in
`scripts/scan_secrets.py`'s `KNOWN_FIXTURE_STRINGS`. None of them is a credential
and none is a personal address.

History also exposes two things the current tree does not:

1. the absolute paths of the build machine in old docs and old result manifests;
2. **the ten formerly hidden seed items** (`data/items/seed_v0.1.json` in commits
   up to `d686d45` "Seal hidden split outside version control"). Since v0.2 those
   rows live only in the git-ignored `.private/hidden_items_v0.1.json`, and only
   their SHA-256 hashes are tracked.

This project never rewrites git history, so the audit did not, and the choice is
the owner's:

| Option | Gains | Costs |
| --- | --- | --- |
| **A. Publish the existing history** | Pre-registration timestamps and the full commit trail stay verifiable, which is the point of pre-registration (`experiments/003_frontier_judges/PLAN.md` amendment 1 predates its code). | The old absolute build paths become public, and the ten formerly hidden items become recoverable from old blobs, which weakens the "hidden split" claim in `VERIFY_ME.md`. No credential is exposed either way. |
| **B. Publish a clean squashed copy** (`git filter-repo` to strip old blobs, or a fresh `git init` + one commit) | No old personal strings and no hidden item text. | The per-commit pre-registration trail is lost; the plan timestamps would have to be attested by the release notes instead of by commit order. |

Option A is defensible if the owner accepts that the hidden split was sealing,
not secrecy from history; option B is cleaner for strangers. Neither is executed
here, and `RELEASE_OSS_CHECKLIST.md` requires the owner to choose.

## 6. What the owner must do before publishing

1. **Delete `<repo>\.env`** and revoke every API key that has ever been placed in
   it, at the provider's dashboard. The audit cannot see the keys, so it cannot
   confirm the revocation; only the owner can.
2. Keep `.private/` (holds the hidden item rows and the deny-list) out of the
   published tree — it is already git-ignored; do not force-add it.
3. Fill the identity placeholders: `CITATION.cff`, `SECURITY.md` (private
   reporting contact), `CODE_OF_CONDUCT.md` (enforcement contact),
   `.github/allowed-emails.txt` if a public address is chosen.
4. Choose option A or B in section 5.
5. Follow `RELEASE_OSS_CHECKLIST.md` (secret scanning, push protection,
   Dependabot, branch protection, first tag, announcement).
