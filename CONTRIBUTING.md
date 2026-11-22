# Contributing to medeval-lab

medeval-lab is an independent, open-source Medical AI Evaluation Lab. It is a
research methods toolkit for auditing medical benchmark item integrity, defining
literature-traced evaluation criteria and metrics, testing LLM judges as
measurement instruments, and reporting reliability statistics honestly. It is
**not** a medical device, it has no clinical validation, and no clinician has
reviewed its content.

Contributions are welcome under the MIT licence (`LICENSE`) and the code of
conduct (`CODE_OF_CONDUCT.md`). Keep changes small, testable and honestly
scoped. If you are unsure whether a change fits, open an issue first.

## Development setup

Python 3.11+ is required. The project is built and tested on CPython 3.14 on
Windows; CI targets CPython 3.12 and 3.13.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest ruff httpx playwright
python -m pip install -e .   # optional: editable install
```

POSIX shell:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install pytest ruff httpx playwright
python -m pip install -e .   # optional: editable install
```

`requirements.txt` is the pinned, reproducible environment; prefer it when
reproducing a recorded run. Installing the package editable is optional and only
needed if you want the `medeval` entry point on your `PATH`.

## Running the checks

Run these before opening a pull request:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe scripts\oss_audit.py
.\.venv\Scripts\python.exe scripts\scan_secrets.py
```

POSIX equivalents:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/python scripts/oss_audit.py
.venv/bin/python scripts/scan_secrets.py
```

The suite currently has 529 tests (3 are skipped: the manual browser flows). `scripts/oss_audit.py` is the release audit
(secret-like strings, tracked `.env` files, personal paths, forbidden
artefacts); `scripts/scan_secrets.py` scans tracked files and history.

If the optional Hugging Face datasets-builder check is unavailable or slow:

```powershell
$env:MEDEVAL_SKIP_DATASETS_BUILDER = '1'
```

```bash
export MEDEVAL_SKIP_DATASETS_BUILDER=1
```

For behaviour changes, also run the relevant dry run. `python -m medeval run
exp002 --dry-run` uses labelled mock judges and needs no API key and no network.
The local app starts with `medeval ui` on `http://127.0.0.1:8765` (loopback
only).

## Repository layout

| Path | Contents |
| --- | --- |
| `packages/` | Library code: `medeval_core`, `medeval_criteria`, `medeval_judges`, `medeval_metrics`, `medeval_audit`, `medeval_reliability`, `medeval_study`, `medeval_web` and the `medeval` CLI. |
| `tests/` | pytest suite. |
| `scripts/` | Release audit, secret scan, literature verification and screenshot capture. |
| `results/` | Recorded evidence for released experiments. **Do not edit.** |
| `experiments/` | Pre-registrations for studies. **Do not edit.** |
| `data/` | Seed items, literature records and local caches (most caches are git-ignored). |
| `docs/` | Architecture, statistics and release notes. |

## How to add a provider preset

Provider presets live in the `PRESETS` dict in
`packages/medeval_web/connections.py`. A preset only fills an editable base URL
and documentation links; the model is chosen from the list that "Test
connection" returns. The dropdown starts on an empty "Choose a provider" option
and Save is disabled until a preset is chosen, so adding a preset must not
preselect anything. Each preset has `label`, `base_url`, `api_style`,
`requires_key`, `docs_url`, `pricing_url` and `last_verified`.

Never ship a price. Price fields start empty and the app links to `pricing_url`;
with no price set, the estimate says "unavailable, call cap is binding". Add or
adjust a test: the v0.5 test suite checks that presets carry no prices and have
the required metadata.

## How to add a benchmark loader

`packages/medeval_study/datasets.py` holds `DATASET_LICENCES` (dataset key to a
licence record: HF repo, licence id, whether redistribution is allowed, and the
evidence) and the `load_split` strategies (`hf-cache-arrow`, `direct-json`,
builder). When adding one:

- use only public or synthetic data;
- never commit item text: downloaded items stay in the git-ignored `data/cache/`;
- record the licence and its evidence in `DATASET_LICENCES` and `DATA_LICENSES.md`;
- note that the repository redistributes no dataset;
- keep tests on fake data.

## How to add a judge

Judges live in `packages/medeval_judges/` (`base.py`, `llm.py`, `rule.py` and
the prompt files). A new judge needs tests, and a criteria/metric traceability
entry where applicable (`medeval traceability` checks the links).

A mock or stand-in judge must label its output `PIPELINE TEST ONLY`. Judges must
never receive the answer key when the study design forbids it; check the design
before adding a field to a prompt.

## Contribution rules

- **No secrets.** No API keys, tokens or `.env` contents in code, tests, issues
  or pull requests. `.env*` files are git-ignored; keys come from the environment
  variable named by `key_env` in a git-ignored `connections.yaml`.
- **No benchmark text.** Do not commit benchmark item text, candidate text or
  reviewer notes. Only ids, verdicts, hashes and aggregates are tracked.
- **Add tests** for behaviour changes and keep the existing tests passing.
- **Run the checks**: dry run plus `scripts/oss_audit.py` before opening a PR.
- **Leave evidence alone.** Do not edit `results/` or `experiments/` and do not
  rewrite git history.
- **Trace your claims.** A new criterion or metric cites its literature; if it
  does not, `medeval traceability` fails.
- **Keep claims proportional.** Small-sample results are exploratory, not
  clinical evidence.

## What we will not merge

- Clinical claims, medical advice, or statements of clinical utility, safety or
  accuracy for patient care.
- Endorsement claims by any provider, hospital, university or institution.
- Redistributed benchmark item text, candidate text or reviewer notes.
- API keys, secrets, `.env` files or credentials.
- Telemetry or any phone-home behaviour. Outbound requests are limited to
  configured provider endpoints and user-started dataset or metadata fetches.
- Tests that require a paid API call or a live provider endpoint; use mocks,
  labelled fake judges or a local fake server instead.
- Edits to `results/` or `experiments/` evidence, or a rewritten git history.

## Pull requests

- Open an issue for a large change; small fixes can go straight to a pull
  request.
- Fill in `.github/PULL_REQUEST_TEMPLATE.md`, including the commands you ran.
- CI runs lint, the secret scan, the tests and the traceability check when it is
  enabled; green CI does not replace the local checks.
- The maintainer reviews and decides. See `GOVERNANCE.md` for how disagreements
  are handled.
- Report security problems privately per `SECURITY.md`, never in a public issue.
- Maintainer identity: Chetanya Malhotra (`@chetanyamalhotra-jpg`), contact:
  `301152462+chetanyamalhotra-jpg@users.noreply.github.com`.

By contributing you agree that your contribution is licensed under the MIT
Licence. There is no CLA.
