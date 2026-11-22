# Experiment 002a — archived pairwise protocol (no real-judge result)

This directory holds the protocol that was numbered **002** in the v0.2 work of
16 November 2026. It is preserved unchanged, with one addition: this note, which
records that the identifier was reassigned.

## Why the number changed

The v0.2 master specification defines Experiment 002 as a *pointwise*
multiple-choice grading study: MedQA-USMLE items, template-built candidate
answers, a DeepSeek judge and a local cross-family judge, three repeats, verdict
plus confidence. This directory is a *different* experiment: pairwise comparison
of a planted-bad against a planted-good response, judged by two DeepSeek prompt
configurations and a frozen rule judge.

The pre-registration in `PLAN.md` was committed at `ba490f0` before any model
call. Rather than delete that record or reuse its number for a different
protocol, the directory was moved here and Experiment 002 was re-assigned to the
master-specification study in `../002_judge_families/`. Commit history for this
protocol is intact; `git log --follow` on this path shows it.

## What this protocol produced

* No real-judge result. The run was blocked: no API credentials were available
  in the execution environment at the time, so nothing was measured about a real
  model. This is a blocked measurement, not a negative finding.
* One auxiliary result that needs no model: the **frozen** v0.1 rule judge
  scored 13/20 (0.65, 95% BCa CI 0.36-0.80) on the post-freeze held-out case
  set, counting ties as misses. It is stored in
  `results/experiment_002/rule_heldout_summary.json` and is reported in
  `REPORT_v0.2.md` as Experiment 002a. It is deliberately not combined with the
  Experiment 001 numbers, because those rules were written with the 001 cases in
  view.
* A sealed hidden item split: the ten historical hidden rows now live outside
  version control; the repository keeps only SHA-256 hashes.

## Status of the code here

`run.py` still runs and its tests still pass (`tests/test_experiment_002a.py`),
including `--rule-only` and `--dry-run`. It is not part of the default pipeline
and is not wired into `medeval all`.
