# Open-source release checklist — v0.5.0

The repository is ready to publish. Everything below is what only the **owner**
can do: revoke keys, choose an identity, choose a history strategy, flip GitHub
settings, tag and announce. Nothing here has been done by the agent, and nothing
here has been pushed.

Work top to bottom. Step 1 is not optional.

---

## 1. Revoke keys and delete key files (do this first)

1. **Delete the local key file** in the repository folder:

   ```powershell
   cd <repo>
   Remove-Item .env -Force        # and any .env.local / .env.previous-config you created
   Get-ChildItem -Force -Filter ".env*"    # only .env.example should remain
   ```

2. **Revoke every API key that has ever been used with this project**, at each
   provider's dashboard — including keys used for Experiment 002, any key pasted
   into the app's Models page, and any key that lived in a `.env`. Rotate rather
   than reuse. Only you can do this: the audit never saw the values, so it cannot
   confirm revocation.

3. Confirm the hidden item rows stay private: `.private/` must exist on your disk
   and must **not** be force-added to git (`git status --ignored` should list it).

4. Check the provider dashboards for a **spend limit** and set one lower than you
   would ever want to pay. The app's caps are a second line of defence, not the
   first.

## 2. Fill the identity placeholders

Nothing personal was invented. Review and verify the maintainer identity and
contact details you choose to publish in the files below:

| File | What to fill |
|---|---|
| `CITATION.cff` | author name (or keep "medeval-lab contributors"), ORCID, affiliation, email, release date — all placeholders are commented |
| `SECURITY.md` | private reporting contact and a response window you can actually keep |
| `CODE_OF_CONDUCT.md` | enforcement contact (and confirm whether the file says it is Contributor Covenant 2.1 or an original code) |
| `GOVERNANCE.md` | maintainer name/handle if you want it public |
| `.github/allowed-emails.txt` | any public contact address you use, so the audit keeps passing |

Re-run the audit after editing: a new public email triggers the email rule until
it is on the allow-list.

## 3. Choose the history strategy (no rewrite has been done)

`git log --oneline` here has 38 commits with one neutral author identity, but
history contains (a) old absolute build paths in earlier docs and result
manifests, (b) the ten formerly hidden seed items, and (c) 13 fake test keys
(not credentials). `OSS_AUDIT.md` section 5 has the details and the trade-off.

| Option | Do | Consequence |
|---|---|---|
| **A. Publish the existing history** | nothing; keep the commits | pre-registration timestamps and the commit trail stay verifiable; old paths and the ten hidden items become recoverable from old blobs |
| **B. Publish a clean squashed copy** | create a new repository from the current tree only (`git filter-repo --invert-paths --path data/items/seed_v0.1.json` is **not** a fix; make a fresh `git init`, commit the tracked tree, tag it) and push that | no old strings and no hidden item text; the per-commit pre-registration trail is lost and `experiments/003_frontier_judges/PLAN.md` timestamps can only be attested in prose |

If you choose B, do it on a **copy** of the folder, keep this repository intact,
and note the choice in `CHANGELOG.md`/`OSS_AUDIT.md`. Do not run a history rewrite
in the repository you have verified.

## 4. Repository settings (before the first push)

- [ ] Confirm the target repository is the intended one and its visibility is
      what you want (public).
- [ ] Enable **Settings → Code security → Secret scanning** and **Push
      protection**. This is the real defence against a future committed key.
- [ ] Enable **Dependabot alerts** and **security updates** (`.github/dependabot.yml`
      is already in the tree).
- [ ] Enable **private vulnerability reporting** so `SECURITY.md` has a working
      intake.
- [ ] Protect `main`: require a pull request, require the `test` job, disallow
      force-pushes and deletions. (CI has never run; the first run will tell you
      whether the required check name is `ci / test`.)
- [ ] Set Actions permissions to read-only by default.
- [ ] Do **not** add repository secrets for provider keys. CI is offline by design.

## 5. First push and watch CI

```powershell
cd <repo>
git status                      # expect a clean tree
git log --oneline -10
git push origin main
```

Then open the **Actions** tab and read the first run. Until it is green:

- do not add a CI badge;
- do not write "CI passing" anywhere;
- expect the first run to find something (this suite has only ever run locally on
  Windows).

If it fails, fix forward with a new commit. Do not rewrite history to hide it.

## 6. Tag and release

Only after CI is green:

```powershell
git tag -a v0.5.0 -m "medeval-lab 0.5.0: open-source release, bring your own key"
git push origin v0.5.0
```

Then create the GitHub release from the tag, with the 0.5.0 section of
`CHANGELOG.md` as the notes and the safety notice from the top of `README.md`
pasted into the release body. Attach the source zip GitHub generates; there is no
wheel or binary to publish.

## 7. Announce (last)

Announce only when steps 1–6 are done. In the announcement, keep the limits:
research tool, not a medical device, no clinical validation, no clinician review,
small samples are exploratory, you pay your provider. Do not claim any provider
endorses the project, and do not publish anyone's usage numbers as a benchmark
result. If you want a package index release, PyPI is a later, separate decision
(the project is installable from source, so it is not required).

## 8. Things not to do

- Do not commit `connections.yaml`, `.env*`, `.private/`, exported bundles or
  dataset caches. They are git-ignored; leave them that way.
- Do not commit benchmark item text, candidate text, reviewer notes or call logs.
- Do not force-push `main` or rewrite published history.
- Do not add a CI badge or a "validated" badge.
- Do not reuse the original Experiment 002 keys anywhere.

---

## Commands to verify the release yourself (before step 5)

```powershell
cd <repo>
$env:MEDEVAL_SKIP_DATASETS_BUILDER = "1"

# 1. Test suite (offline; no key, no network, no paid call)
.\.venv\Scripts\python.exe -m pytest

# 2. Lint
.\.venv\Scripts\ruff.exe check --no-cache .

# 3. Release audit over what a clone would receive
.\.venv\Scripts\python.exe scripts\oss_audit.py

# 4. Secret scan over tracked files and every reachable history blob
.\.venv\Scripts\python.exe scripts\scan_secrets.py

# 5. Full-history triage (expected to report, not be clean — read OSS_AUDIT.md)
.\.venv\Scripts\python.exe scripts\oss_audit.py --history

# 6. The app starts, works and its doctor is clean
.\.venv\Scripts\python.exe -m medeval doctor
.\.venv\Scripts\python.exe -m medeval ui          # Ctrl+C when done

# 7. The key-free path still works end to end
.\.venv\Scripts\python.exe -m medeval run exp003 --dry-run --n-items 140 --screening-size 60
```

Expected: 529 passed / 3 skipped, ruff "All checks passed", audit "PASS oss-audit
0 finding(s) … 12 documented exception(s)", secret scan two PASS lines, doctor 0
failures, and a completed dry run whose artefacts say `PIPELINE TEST ONLY`.

## v0.6.0 additions to the pre-publish checklist

```powershell
# Paper Packs: validate every declared pack, then run all three offline
.\.venv\Scripts\python.exe -m medeval paper validate
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-clinical-mcq --dry-run --n-items 6
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-rubric-panel --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-structured-extraction --dry-run --n-items 4
```

Expected (v0.6.0 local baseline): 603 passed, 0 skipped (the three former manual
browser flows are deterministic tests now), ruff clean, OSS audit 0 findings,
secret scan PASS, doctor 0 failures, every Paper Pack `VALID`, and dry-run
artefacts that say `PIPELINE TEST ONLY`. The CI workflow (ubuntu + windows +
offline Playwright) has never run because nothing has been pushed; no
CI-passing claim may be made until it does.

Preset-specific checks:

```powershell
.\.venv\Scripts\python.exe -m medeval paper list
.\.venv\Scripts\python.exe -m medeval matrix run --paper healthbench-rubric --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper medcalc-bench --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper medhallu-detect --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper medxpertqa-text --dry-run --n-items 4
.\.venv\Scripts\python.exe -m medeval matrix run --paper medsafety-bench --dry-run --n-items 4
.\.venv\Scripts\python.exe papers\medcalc-bench\loader.py --source papers\medcalc-bench\smoke.jsonl --out .tmp\loader-check
```

No upstream dataset may be committed; `.medeval_data/` is git-ignored.
