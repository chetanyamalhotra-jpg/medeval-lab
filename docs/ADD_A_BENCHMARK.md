# Add a benchmark loader

Add a public or synthetic multiple-choice benchmark to the study runner, with its
licence recorded and its item text kept out of version control. medeval-lab is a
research tool, not a medical device: it has no clinical validation, no clinician has
reviewed any content, and small-sample results are exploratory.

## Rules before any code

- **Public or synthetic data only.** No private data, no data behind a login, no
  scraped text, no benchmark whose licence you cannot point to.
- **Record the licence and the evidence for it.** Every dataset is a licence record:
  the source repository, the licence id, an evidence URL (for example the Hugging
  Face dataset API response that reports the licence), the homepage, and whether
  redistribution is allowed.
- **Item text is never committed.** Only item ids, verdicts, confidences, hashes,
  aggregates and licence metadata are tracked. Downloaded item text lives in the
  git-ignored dataset cache. `DATA_LICENSES.md` is the tracked record of that policy
  per dataset.
- **Redistribution is off unless the licence really allows it.** The built-in path
  records `redistribute: False` for every dataset registered this way, and item text
  stays untracked regardless. Publishing item text needs a clearly permissive
  licence *and* a deliberate, separate change to `DATA_LICENSES.md` and the ignore
  rules.
- **Study runs never download.** A run reads the cache; a missing cache is an error
  for a live run and a labelled synthetic fallback for a dry run. `medeval data
  fetch <key>` is the supported downloader for study data and shows the licence
  first. The separate `medeval audit` command fetches the public data it audits and
  takes `--offline` to refuse; that is an unrelated path.

## Where the pieces live

| File | What it holds |
| --- | --- |
| `packages/medeval_audit/loaders.py` | `DatasetSpec`, the `DATASETS` registry, the row normalisers, and `load_split`, which tries the loading strategies in order: the `datasets` library builder, `hf-cache-arrow`, then a direct download (`direct-json` for MedQA, `direct-parquet` for MedMCQA). |
| `packages/medeval_study/datasets.py` | Builds `DATASET_LICENCES` from `DATASETS`, converts normalised rows into study items, validates them, and exposes `load_mcq_items`, `register_dataset`, the cache helpers and the offline guards. |
| [DATA_LICENSES.md](../DATA_LICENSES.md) | The human-readable licence and tracking record for every dataset and artefact. |

`DATASET_LICENCES` maps a dataset key to a `DatasetLicence`: `key`, `hf_repo`,
`licence`, `licence_evidence`, `redistribute`, `attribution` and `homepage`. The
study runner and `medeval data status`/`medeval data fetch` all read it.

## What an item must look like

After normalisation, an item must provide:

- a `question` string that is at least 40 characters after trimming;
- exactly four options, all non-empty, with four distinct texts (labels default to
  `A`–`D`);
- a `gold_index` in the range `0`–`3` (0-based).

Items that fail these checks are counted and dropped with a reason in the run
manifest (`not_four_options`, `empty_option`, `duplicate_option_text`,
`missing_key`, `question_too_short`); they are not silently repaired.

If you use the pluggable `register_dataset(licence, loader)` path, `loader(split,
allow_download)` must return a `LoadedSplit` whose records carry `question`, exactly
four `options` and a `gold_index`. It is registered at import time by the code that
owns it, and it appears in `DATASET_LICENCES` with the licence you pass.

## Steps

1. Pick a public or synthetic MCQ dataset and read its licence from the source
   itself (the dataset card or the Hugging Face dataset API, not a paper abstract).
   Write down the evidence URL and the date you read it.
2. Register the dataset. The standard in-repo path is:
   - add a `DatasetSpec` to `DATASETS` in `packages/medeval_audit/loaders.py` with
     `key`, `repo_id`, `licence`, `licence_evidence`, `splits`, `homepage` and a
     short `notes`; and
   - add a normaliser for its row shape and wire it into `normalise_records`.

   Add a download strategy only if the existing strategies cannot read the dataset;
   it must respect `allow_download`, follow the same short-timeout rules as the
   existing ones, and record the source URL and content hash through
   `medeval_core.net.fetch_url` so the run manifest carries a fingerprint. A
   self-contained loader can instead use `register_dataset(DatasetLicence(...),
   loader)` without touching the runner.

3. Set the licence honestly: `redistribute=False` unless the licence clearly allows
   redistribution, and record the evidence URL, not just the licence name.
4. Update `DATA_LICENSES.md`: the dataset row (source, licence, evidence, content
   fingerprint), and whether any new path is tracked or git-ignored. If you add a
   tracked artefact, justify why it contains no licensable text.
5. Keep the cache git-ignored. The cache directory is `data/cache/` by default and
   is overridden by `MEDEVAL_CACHE_DIR`; it is already in `.gitignore` and is read
   with the Hugging Face offline flags forced on. Do not add a second downloader to
   the study path: `medeval data fetch <key>` is the only supported one there, and
   it prints the licence and its evidence before fetching.
6. Add tests with fake data and no network. The suite's autouse fixture disables
   network access for the test session (only a marked local fake server on loopback
   is allowed), so a test that needs a real endpoint fails by design. Two workable
   patterns are already used in the repository:
   - monkeypatch `runner.dataset_cache_status` to return a `DatasetBundle` built
     from synthetic items, as `tests/test_study_runner.py` does; or
   - call `register_dataset` with a loader that returns a small in-memory
     `LoadedSplit` and assert that `load_mcq_items` validates it and that the licence
     reaches the run manifest.

   Also cover the failure path you care about (not cached: dry run falls back and
   labels the sample; live run raises with the fetch command) and never commit real
   benchmark text as a test fixture.

7. Check it from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m medeval data status
   .\.venv\Scripts\python.exe -m pytest
   .\.venv\Scripts\ruff.exe check .
   .\.venv\Scripts\python.exe scripts\oss_audit.py
   .\.venv\Scripts\python.exe scripts\scan_secrets.py
   ```

   `data status` should list the new key with its licence and a `medeval data fetch
   <key>` hint. The release audit fails on tracked benchmark artefacts and on
   personal paths, so it is part of validating a new loader.

8. Fetch once (a public download, no API key and no cost) and run the pipeline
   test, which still makes no model calls:

   ```powershell
   .\.venv\Scripts\python.exe -m medeval data fetch <dataset-key>
   .\.venv\Scripts\python.exe -m medeval run exp002 --dry-run
   ```

   Then use the normal path: a live run reads the cache, keeps item text in the
   run's git-ignored files, and records the licence, strategy and source detail in
   `manifest.json`.

## What can go wrong

- Treating a licence on a paper as the dataset licence. Read the dataset's own
  record.
- Guessing a source URL or revision. Record the evidence and the content
  fingerprint; public datasets change without version bumps.
- Committing item text in a fixture, a sample file or a README. Only ids, verdicts,
  hashes and aggregates belong in tracked files.
- Adding a download inside a run or a test. Runs must stay offline; tests must use
  fake data.
- Assuming A–D labels or 0-based keys. Check the row shape, then assert it in a
  normaliser test.
- Silently dropping malformed rows. Keep the drop reasons and counts visible in the
  manifest.

## Related documentation

- [DATA_LICENSES.md](../DATA_LICENSES.md) — the tracked licence and artefact record.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — setup, checks and contribution rules.
- [USER_GUIDE.md](../USER_GUIDE.md) — how datasets are fetched and used in a run.
- [VERIFY_ME.md](../VERIFY_ME.md) — what is verified and what remains uncertain.
- [docs/architecture.md](architecture.md) — where the audit and study layers sit.
- [BYOK_QUICKSTART.md](BYOK_QUICKSTART.md) — setup and a first dry run.
