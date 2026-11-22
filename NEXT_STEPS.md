# NEXT_STEPS

Ordered by what most improves the trustworthiness of the project per hour spent.

## v0.5.0 status and the next moves

The repository is ready for a public v0.5.0 release: bring-your-own-key makes it
provider-neutral, the release audit is clean on tracked content, and the owner's
remaining steps are in `RELEASE_OSS_CHECKLIST.md` (revoke keys, delete the local
`.env`, fill identity placeholders, choose a history strategy, enable secret
scanning and branch protection, push, watch CI, tag).

Things the release does **not** establish, in rough order of how much they would
improve trust per hour spent:

1. **A live run with a real frontier model.** Experiment 003 is built and only
   dry-run tested. One live staged run with a screening model and two judges from
   different families would turn the pre-registration into evidence. This costs
   the owner real money and needs their own key; no agent can do it.
2. **CI actually running.** Nothing is pushed, so `.github/workflows/ci.yml` has
   never executed. The first push will very likely find a Windows-only assumption.
3. **A clinician reading the criteria.** Every criterion is literature-traced, but
   no clinician has reviewed any of them; that is the largest single gap in the
   project's usefulness and cannot be closed by code.
4. **The manual smoke checklist** (`MANUAL_SMOKE.md`) for the flows the browser
   suite skips, including the v0.5 model-picker click path and the live-confirm
   dialog, which need a real provider to reach.
5. **A second dataset.** Everything currently rests on MedQA `dev`; a second
   licensed loader with its own audit would test whether the pipeline generalises.
6. **PyPI publishing** (`pip install medeval-lab`) — optional, and only after the
   first tagged GitHub release is green.

## v0.4.0 status and the next moves

Experiment 003 is **built, staged and dry-run tested but not run**. No live paid
call has been made by the agent; that is deliberate.

1. **Run the manual smoke checklist once** (MANUAL_SMOKE.md, 25 steps, about 15
   minutes). It covers the browser flows the automated suite marks skipped: the
   Test-connection button, role warnings, the dry-run and live run cards,
   stop-and-resume and the DOI fetch. The automated suite runs 11 browser flows
   and skips 7 with written reasons (VERIFY_ME.md V5-3).

2. **Run medeval doctor** on the machine that will do the live run. It checks
   Python, dependencies, the port, the token flow, git, Ollama and disk, and it
   never prints a key.

3. **Do the live Experiment 003** from the app: Models page (two or more judge
   connections from different families plus a screening connection from a third
   family), then the start screen, then confirm each stage. Adaptive screening
   stops at 100 hard items, at exhaustion, or at the cap; the gate decides whether
   the remaining items run. Expect roughly 100-350 screening calls, 120 pilot
   judge calls and about 400 for the remainder at one repeat.

4. **Publish the results only after that run**: medeval report exp003, then
   medeval export <run_id>, and only then create a top-level results report.

## 1. Push (when you are ready - the agent never pushes)

18 commits are ahead of origin/main. git push origin main, then watch CI run
for the first time: ruff, the secret scan, pytest (Python 3.12 + 3.13), item
validation and traceability. It has never executed - no badge until it has.

## 1. Put it under version control at its final home (10 minutes)

The repository has **no remote yet**, so CI has never executed.

```powershell
cd <repo>
git status                     # should show a clean tree
git remote add origin https://github.com/<your-user>/medeval-lab.git
git branch -M main
git push -u origin main
```

Then update `CITATION.cff` (`REPLACE-WITH-YOUR-USERNAME`) and watch the CI workflow
run for the first time — it now runs `ruff`, the test suite, item validation and
literature traceability (`VERIFY_ME.md` A10, V3-18).

## 2. Have a clinician read three files (highest scientific value)

Nothing else in this repository substitutes for this. In order:

1. `packages/medeval_criteria/catalogue/v0.1/criteria.yaml` — are the eight
   constructs and their 0/1/2 anchors what you would actually mark against?
2. `experiments/001_judge_reliability/data/case_specs_v0.1.json` — are all 50
   planted errors genuinely the more dangerous response? Any arguable case is a
   finding to record, not to fix silently.
3. The single flagged Experiment 002 item (`dev-00666`) — is the key right, or is
   the judge right? Note the answer in a new file; do not edit the run.

Record the outcome in a new `review_status` value (e.g.
`two-rater, clinician-reviewed` plus a second rater id) rather than editing
`review_status` in place; the schema already supports `human_reference.rater_ids`
with a warning when fewer than two raters exist.

## 3. A real second-rater study (the actual missing piece)

The toolkit's reliability layer is ready; what is missing is a second human.
The cheapest useful design:

- 100 responses from one model on the 50 seed items (or the case pairs);
- two raters (you and one colleague) scoring 30–50 of them on two criteria;
- compute Cohen's kappa / Krippendorff's alpha **between the humans**, then
  between each human and the judge.

`medeval_reliability` already implements everything needed;
`human_reference.rater_ids` is where the labels go.

## 4. Calibration on a judge that actually hedges

The reported run cannot support any calibration claim: every confidence value was
95–100, so ECE is near zero because accuracy is near one, not because the scale is
used well. A judge that produces a spread of confidences (a weaker local model, or a
prompt that asks for a probability rather than an integer) would make the ECE,
Brier and AUROC results meaningful. Change the prompt **version** if you change the
prompt, so old runs stay interpretable.

## 5. Deal with PubMedQA (1 hour, isolated)

It was skipped because `datasets` 5.x cannot run its legacy loading script. Two
acceptable fixes, both documented in `VERIFY_ME.md` B3:

- create a second environment with `datasets<4` purely to export PubMedQA to
  JSONL, then load that JSONL like the other tasks; or
- use the dataset's `pqaa.zip` / `pqal.zip` / `pqau.zip` archives directly.

Do **not** downgrade the main environment; MedMCQA loading depends on
`datasets>=5`.

## 6. Close the audit's known gaps (1–2 days)

- Audit MedMCQA's **train** split for train/validation duplication (182,822 rows;
  memory is the only obstacle).
- Add a sensitivity analysis that re-scores with the flagged items excluded, so a
  reviewer can see how much the duplicates and skewed key matter.
- Consider a paraphrase-robust contamination screen; the current 5-gram screen
  cannot see rewording.

## 7. Review the literature registry (2 hours)

Open each DOI once and check that the `how_we_operationalize` column is fair to the
paper. The automated title match proves the DOI resolves; it does not prove the
interpretation. `lit_medhallu` has no verified DOI and should be either fixed or
removed.

## 8. Decide what v0.3 should add (only after 1–3)

Candidates, in order of value:

- human reference labels and human–judge agreement (the real point of the lab);
- a second judge family that actually runs, and a harder item sample;
- a three-member jury and a documented tie rule;
- criteria v0.2 with clinician input, versioned so v0.1 results stay interpretable.

---

## What not to do yet

- Do not turn the rubric weights into a published composite score.
- Do not describe any result as clinical validation.
- Do not add a leaderboard. The app shows one run's measurement, not a ranking.
- Do not raise the spend caps to get past a guard; understand the plan first.
- Do not edit the upstream harness checkout to make an install work — the `.pth`
  fallback exists for that.
