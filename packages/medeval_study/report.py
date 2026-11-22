"""Markdown, JSON and CSV reports for a grading study.

The Limitations section is mandatory and generated unconditionally: it is part of
the report writer, not something a caller can forget to ask for. Every number is
printed with its interval, and anything that could not be computed says so in
words rather than being omitted.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from medeval_study.judging import JudgeSpec, Verdict
from medeval_study.models import CandidateResponse, McqItem
from medeval_study.sampling import SamplingPlan

__all__ = ["LIMITATIONS", "MEDICAL_NOTICE", "write_reports"]

#: Shown at the top of every report, including exported bundles. It is the same
#: wording as the app's start-screen notice, because a reader may meet the report
#: first and must not mistake it for a clinical result. Deliberately part of the
#: writer, so no caller can produce a report without it.
MEDICAL_NOTICE = (
    "**Research tool, not a medical device.** Not for patient care or clinical decisions. "
    "No clinical validation (no clinician reviewed this content), and small samples are "
    "exploratory; mock and dry runs say nothing about any model."
)

LIMITATIONS: tuple[str, ...] = (
    "**Small sample.** {n_items} items, one candidate answer each. Every interval is wide, and a "
    "difference of a few percentage points between judges is not interpretable at this size.",
    "**The answer key may be wrong.** The key is treated as an imperfect reference. Public medical "
    "QA sets are known to contain label errors, missing information and ambiguous items, and no key "
    "audit was performed here. An 'error' by the judge may be a defect in the key.",
    "**No clinician review.** No physician verified any item, candidate answer, judged verdict or "
    "flagged case. The author is not a clinician and no clinician was available.",
    "**Possible shared bias between judges.** Both judges are instruction-tuned LLMs, and the "
    "candidate answers are assembled from a small fixed template set. Agreement between judges can "
    "reflect a shared response to that style rather than shared medical reasoning.",
    "**The candidate rationales are template filler.** They are not clinical reasoning and contain no "
    "case-specific justification, so this study measures whether a judge recognises the correct "
    "option, not whether it can grade an argument.",
    "**One dataset, one split, one prompt.** Results do not generalise beyond this sample, this "
    "prompt version, or this temperature. A different prompt can move a judge by several points.",
    "**Three repeats at one temperature.** Consistency across repeats is measured at a single "
    "temperature and does not estimate stability across prompts or sampling schemes.",
    "**Exploratory status.** The experiment is labelled exploratory in advance. It is a measurement "
    "exercise for a methods toolkit, not a confirmatory test, and no hypothesis test here survives a "
    "multiple-comparison correction.",
    "**Not a clinical claim.** Nothing in this report is clinically validated, and none of it should "
    "be used to make a medical decision or to claim that a model is safe.",
)


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "not computed"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "undefined"
        if value in (float("inf"), float("-inf")):
            return "infinite"
        return f"{value:.{digits}f}"
    return str(value)


def _ci(interval: Mapping[str, Any] | None, digits: int = 3) -> str:
    if not interval:
        return "not computed"
    low = interval.get("low")
    high = interval.get("high")
    if low is None or high is None:
        return "not computed"
    return f"[{_fmt(float(low), digits)}, {_fmt(float(high), digits)}]"


def _wilson_ci(block: Mapping[str, Any] | None, digits: int = 3) -> str:
    if not block:
        return "not computed"
    interval = block.get("ci95_wilson")
    if not interval:
        return "not computed"
    return f"[{_fmt(interval[0], digits)}, {_fmt(interval[1], digits)}]"


def _bootstrap_ci(interval: Mapping[str, Any] | None, digits: int = 3) -> str:
    if not interval:
        return "not computed"
    return f"[{_fmt(interval.get('low'), digits)}, {_fmt(interval.get('high'), digits)}]"


def _pct(value: Any, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "not computed"
    return f"{float(value) * 100:.{digits}f}%"


def _judge_table(metrics: Mapping[str, Any]) -> list[str]:
    lines = [
        "| metric | estimate | 95% interval | n |",
        "| --- | --- | --- | --- |",
    ]
    per_judge = metrics.get("judges", {})
    for judge_id, payload in per_judge.items():
        primary = payload["primary_repeat"]
        lines.append(f"| **{judge_id}** | | | |")
        accuracy = primary["accuracy"]
        lines.append(
            f"| accuracy vs key (failed items excluded) | {_pct(accuracy['estimate'])} | {_wilson_ci(accuracy)} Wilson | {accuracy['denominator']} |"
        )
        alt = primary.get("accuracy_counting_failures_as_wrong") or accuracy
        lines.append(
            f"| accuracy vs key (failed items counted as wrong) | {_pct(alt['estimate'])} | {_wilson_ci(alt)} Wilson | {alt['denominator']} |"
        )
        fa = primary["false_accept_rate"]
        lines.append(
            f"| false accept (wrong candidate judged CORRECT) | {_pct(fa['estimate'])} | {_wilson_ci(fa)} Wilson | {fa['denominator']} |"
        )
        fr = primary["false_reject_rate"]
        lines.append(
            f"| false reject (correct candidate judged INCORRECT) | {_pct(fr['estimate'])} | {_wilson_ci(fr)} Wilson | {fr['denominator']} |"
        )
        lines.append(
            f"| Cohen's kappa vs key | {_fmt(primary['cohen_kappa_vs_key'])} | {_bootstrap_ci(primary.get('cohen_kappa_vs_key_ci95'))} bootstrap | {primary['n_scored']} |"
        )
        calibration = primary["calibration"]
        lines.append(
            f"| ECE (5 bins) | {_fmt(calibration['ece_5_bins'])} | {_bootstrap_ci(calibration.get('ece_5_bins_ci95'))} bootstrap | {primary['n_scored']} |"
        )
        lines.append(
            f"| Brier score | {_fmt(calibration['brier'])} | {_bootstrap_ci(calibration.get('brier_ci95'))} bootstrap | {primary['n_scored']} |"
        )
        lines.append(
            f"| AUROC of confidence for own correctness | {_fmt(calibration['auroc_confidence_predicts_own_correctness'])} | "
            f"{_bootstrap_ci(calibration.get('auroc_confidence_predicts_own_correctness_ci95'))} bootstrap | {primary['n_scored']} |"
        )
        lines.append(
            f"| Gwet AC1 vs key | {_fmt(primary['gwet_ac1_vs_key'])} | not computed | {primary['n_scored']} |"
        )
        consistency = payload["across_repeats"]
        lines.append(
            f"| same verdict on all repeats | {_pct(consistency.get('consistent_fraction'))} | "
            f"{_fmt_ci_list(consistency.get('consistent_fraction_ci95_wilson'))} Wilson | {consistency.get('n_items_all_repeats')} |"
        )
    return lines


def _fmt_ci_list(interval: Sequence[float] | None, digits: int = 3) -> str:
    if not interval:
        return "not computed"
    return f"[{_fmt(interval[0], digits)}, {_fmt(interval[1], digits)}]"


def _cross_judge_section(metrics: Mapping[str, Any]) -> list[str]:
    pairs = metrics.get("cross_judge", {})
    if not pairs:
        return [
            "No cross-judge statistics were computed: this study needs at least two available judges "
            "for any of them. With one judge, error correlation across families is **not measured**, "
            "and no proxy for it is reported.",
        ]
    lines: list[str] = []
    for pair, payload in pairs.items():
        lines.append(f"### {pair}")
        lines.append("")
        if "verdict_agreement" not in payload:
            # Too few common items to compute anything; the reason is in "note".
            lines.append(f"- {payload.get('note', 'no statistics could be computed')}")
            lines.append("")
            continue
        agreement = payload["verdict_agreement"]
        errors = payload["error_correlation"]
        lines.append(f"- Common items: {payload['n_common_items']}")
        lines.append(
            f"- Verdict agreement: {_pct(agreement['percent_agreement'])} raw; "
            f"Cohen's kappa {_fmt(agreement['cohen_kappa'])} (95% {_bootstrap_ci(agreement.get('cohen_kappa_ci95'))})"
        )
        lines.append(
            f"- Phi between error indicators: {_fmt(errors['phi_coefficient'])} "
            f"(95% {_bootstrap_ci(errors.get('phi_coefficient_ci95'))})"
        )
        lines.append(
            f"- Both wrong: {errors['both_wrong_count']} of {payload['n_common_items']} "
            f"({_pct(errors['both_wrong_rate'])})"
        )
        binomial = errors.get("both_wrong_vs_independence_binomial") or {}
        permutation = errors.get("both_wrong_vs_independence_permutation") or {}
        if binomial:
            lines.append(
                f"- Expected if errors were independent: {_fmt(binomial.get('expected'))}; "
                f"ratio {_fmt(binomial.get('excess_ratio'))}; binomial p {_fmt(binomial.get('p_value_binomial'))}"
            )
        if permutation:
            lines.append(
                f"- Permutation test of both-wrong co-occurrence: p {_fmt(permutation.get('p_value_two_sided'))} "
                f"({permutation.get('n_permutations')} permutations)"
            )
        mcnemar = errors.get("mcnemar_errors") or {}
        if mcnemar:
            lines.append(
                f"- McNemar on error patterns: b01 {_fmt(mcnemar.get('b01'))}, b10 {_fmt(mcnemar.get('b10'))}, "
                f"p {_fmt(mcnemar.get('p_value'))} ({mcnemar.get('method')})"
            )
        lines.append("")
    return lines


def _judge_status_lines(judges: Sequence[JudgeSpec]) -> list[str]:
    lines = ["| judge | family | model | status | note |", "| --- | --- | --- | --- | --- |"]
    for judge in judges:
        status = "available" if judge.available else "**unavailable**"
        note = judge.unavailable_reason or judge.notes
        lines.append(
            f"| {judge.judge_id} | {judge.family} | `{judge.model}` | {status} | {note or ''} |"
        )
    return lines


def write_reports(
    *,
    run_dir: Path,
    run_id: str,
    config: Any,
    bundle: Any,
    sampling: SamplingPlan,
    responses: Mapping[str, CandidateResponse],
    judges: Sequence[JudgeSpec],
    verdicts: Sequence[Verdict],
    metrics: Mapping[str, Any],
    manifest: Mapping[str, Any],
    usage: Mapping[str, Any],
    judge_detail: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Write ``report.md``, ``metrics.json`` and ``items.csv`` into the run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = run_dir / "items.csv"
    _write_items_csv(csv_path, sampling, responses, verdicts, judges, metrics)

    md_path = run_dir / "report.md"
    md_path.write_text(
        _render_markdown(
            run_id=run_id,
            config=config,
            bundle=bundle,
            sampling=sampling,
            judges=judges,
            metrics=metrics,
            manifest=manifest,
            usage=usage,
            judge_detail=judge_detail or {},
        ),
        encoding="utf-8",
    )
    return {"report": md_path, "metrics": metrics_path, "items_csv": csv_path}


def _write_items_csv(
    path: Path,
    sampling: SamplingPlan,
    responses: Mapping[str, CandidateResponse],
    verdicts: Sequence[Verdict],
    judges: Sequence[JudgeSpec],
    metrics: Mapping[str, Any],
) -> None:
    primary = getattr(metrics, "get", lambda key, default=None: default)("_primary_repeat", 0)
    flagged = set((metrics.get("possible_key_errors") or {}).get("flagged_item_ids") or [])
    judge_ids = [judge.judge_id for judge in judges if judge.available]
    lookup: dict[tuple[str, str, int], Verdict] = {}
    for verdict in verdicts:
        lookup[(verdict.item_id, verdict.judge_id, verdict.repeat)] = verdict

    fieldnames = ["item_id", "condition", "chosen_letter", "candidate_correct"]
    for judge_id in judge_ids:
        fieldnames.extend([f"{judge_id}_verdict", f"{judge_id}_confidence"])
    fieldnames.append("flagged_for_manual_review")

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sampled in sampling.items:
            item: McqItem = sampled.item
            response = responses[item.item_id]
            row: dict[str, Any] = {
                "item_id": item.item_id,
                "condition": sampled.condition,
                "chosen_letter": response.chosen_letter,
                "candidate_correct": response.chosen_letter == item.key_letter,
            }
            for judge_id in judge_ids:
                verdict = lookup.get((item.item_id, judge_id, primary))
                row[f"{judge_id}_verdict"] = (verdict.verdict if verdict else "") or ""
                row[f"{judge_id}_confidence"] = (verdict.confidence if verdict else "") or ""
            row["flagged_for_manual_review"] = item.item_id in flagged
            writer.writerow(row)


def _render_markdown(
    *,
    run_id: str,
    config: Any,
    bundle: Any,
    sampling: SamplingPlan,
    judges: Sequence[JudgeSpec],
    metrics: Mapping[str, Any],
    manifest: Mapping[str, Any],
    usage: Mapping[str, Any],
    judge_detail: Mapping[str, Any],
) -> str:
    pipeline_only = bool(metrics.get("pipeline_test_only"))
    banner = (
        "> **PIPELINE TEST ONLY.** These numbers come from a deterministic mock, not from a model. "
        "They validate that the pipeline runs; they say nothing about any real judge.\n"
        if pipeline_only
        else ""
    )
    lines: list[str] = []
    lines.append("# Experiment 002 - judge error correlation and confidence (exploratory)")
    lines.append("")
    lines.append(f"> {MEDICAL_NOTICE}")
    lines.append("")
    if banner:
        lines.append(banner)
    lines.append(
        "**Status: exploratory.** This report describes a small, single-dataset measurement of "
        f"{len([judge for judge in judges if judge.available])} LLM judge(s) grading multiple-choice medical "
        "answers. It is not a confirmatory study and it makes no clinical claim."
    )
    lines.append("")
    lines.append(f"- Run id: `{run_id}`")
    lines.append(f"- Dataset: `{bundle.dataset}/{bundle.split}` ({bundle.n} usable items, {bundle.n_raw_rows} raw rows)")
    lines.append(f"- Licence: {bundle.licence.licence} ({bundle.licence.hf_repo}); item text is not committed")
    lines.append(f"- Sample: {sampling.n} items, seed {sampling.seed}, {sampling.discarded_duplicate_stems} duplicate stems discarded")
    lines.append(
        f"- Candidates: {sampling.condition_counts.get('correct', 0)} built to name the key option, "
        f"{sampling.condition_counts.get('incorrect', 0)} built to name a plausible distractor"
    )
    lines.append(f"- Repeats: {config.repeats} per judge per item at temperature {config.temperature}")
    lines.append(f"- Commit: `{(manifest.get('git') or {}).get('commit')}` (dirty: {_fmt((manifest.get('git') or {}).get('dirty'))})")
    lines.append("")
    lines.append("## Judges")
    lines.append("")
    lines.extend(_judge_status_lines(judges))
    lines.append("")
    available = [judge for judge in judges if judge.available]
    if len(available) < 2:
        lines.append(
            "**Only one judge was available, so the cross-family question this experiment was designed "
            "to answer cannot be answered by this run.** Single-judge metrics below are real measurements "
            "of that one judge; they are not a substitute for the cross-judge analysis."
        )
        lines.append("")
    lines.append("## Results per judge (primary repeat)")
    lines.append("")
    lines.extend(_judge_table(metrics))
    lines.append("")
    lines.append(
        "Intervals: Wilson score for proportions; percentile bootstrap over items with "
        f"{metrics.get('methods', {}).get('n_resamples', 1000)} resamples for kappa, correlation, ECE, Brier and AUROC. "
        "Paired comparisons resample items jointly."
    )
    missing = [
        judge_id
        for judge_id, payload in (metrics.get("judges") or {}).items()
        if (payload.get("primary_repeat") or {}).get("n_scored") != (payload.get("primary_repeat") or {}).get("n_items")
    ]
    if missing:
        lines.append("")
        lines.append("**Data quality.** These judgements could not be scored on every item:")
        for judge_id in missing:
            primary = metrics["judges"][judge_id]["primary_repeat"]
            counts = primary.get("failure_counts") or {}
            breakdown = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()) if count)
            lost = primary["n_items"] - primary["n_scored"]
            ids = ", ".join(f"`{item_id}`" for item_id in (primary.get("unscored_item_ids") or [])[:5]) or "none recorded"
            lines.append(
                f"- {judge_id}: {primary['n_scored']}/{primary['n_items']} scored; failures: {breakdown or 'none'}. "
                f"{lost} item(s) have no parseable verdict and are excluded from accuracy and kappa: {ids}."
            )
    lines.append("")
    lines.append("## Judge versus judge")
    lines.append("")
    lines.extend(_cross_judge_section(metrics))
    lines.append("")
    lines.append("## Flagged for review (manual review only)")
    lines.append("")
    flags = metrics.get("possible_key_errors") or {}
    rule = flags.get("rule") or {}
    applied = rule.get("applied")
    rule_text = rule.get("two_judge") if applied == "two_judge" else rule.get("single_judge_fallback")
    lines.append(f"Rule that produced these flags: **{applied}** - {rule_text}")
    lines.append("")
    lines.append(
        "A flag means confident judges disagreed with the key. It is a prompt for a human to look, "
        "**not** a finding that the key is wrong: the judges may be the ones who are wrong."
    )
    lines.append("")
    if flags.get("possible_key_errors"):
        lines.append("| item | candidate was correct | judges |")
        lines.append("| --- | --- | --- |")
        for row in flags["possible_key_errors"]:
            judge_text = ", ".join(
                f"{name}: {payload['verdict']} ({payload['confidence']})"
                for name, payload in row["judges"].items()
            )
            lines.append(f"| `{row['item_id']}` | {_fmt(row['candidate_was_correct'])} | {judge_text} |")
    else:
        lines.append(
            "No item met the two-judge rule."
            if rule.get("applied") == "two_judge"
            else "The two-judge rule was not applicable with one judge; see the single-judge flags below."
        )
        lines.append("No key was changed; nothing here proves a key is correct.")
    if flags.get("single_judge_flags"):
        lines.append("")
        lines.append(
            f"{len(flags['single_judge_flags'])} item(s) were flagged by the weaker single-judge rule "
            f"(confidence >= {flags.get('single_judge_flags') and 90}). These are candidates for a manual "
            "look; they cannot distinguish a wrong key from a wrong judge."
        )
        for row in flags["single_judge_flags"][:10]:
            lines.append(
                f"- `{row['item_id']}`: {row['judge']} said {row['verdict']} at confidence {row['confidence']} "
                f"(candidate was {'correct' if row['candidate_was_correct'] else 'incorrect'} per the key)"
            )
    lines.append("")
    with_without = metrics.get("with_and_without_flagged_items") or {}
    lines.append("## Effect of the flagged items")
    lines.append("")
    lines.append(
        f"{with_without.get('n_flagged', 0)} item(s) flagged. "
        + ("Accuracy excluding them is reported below; if it moves sharply, the key is doing some of the work."
           if with_without.get("excluding_flagged") else "Excluding them changed nothing that is reported here.")
    )
    lines.append("")
    for judge_id, payload in (with_without.get("excluding_flagged") or {}).items():
        accuracy = payload["accuracy"]
        lines.append(
            f"- {judge_id} excluding flagged items: {_pct(accuracy['estimate'])} {_wilson_ci(accuracy)} "
            f"on {accuracy['denominator']} items"
        )
    lines.append("")
    lines.append("## Benchmark health")
    lines.append("")
    health = metrics.get("benchmark_health") or {}
    lines.append(f"- Items: {health.get('n_items')}")
    lines.append(f"- Key letter distribution: {health.get('key_letter_distribution')}")
    for judge_id, payload in (health.get("ceiling_per_judge") or {}).items():
        lines.append(
            f"- {judge_id} ceiling rate: {_pct(payload['ceiling_rate'])} "
            f"{_fmt_ci_list(payload.get('ceiling_rate_ci95_wilson'))} "
            f"({payload['n_all_repeats_correct']}/{payload['n_items_with_all_repeats']} items right on every repeat)"
        )
    ceilings = [payload.get("ceiling_rate") for payload in (health.get("ceiling_per_judge") or {}).values()]
    if any(value is not None and value >= 0.9 for value in ceilings):
        lines.append("")
        lines.append(
            "**The ceiling is a finding, not a footnote.** At this accuracy almost every item is judged "
            "correctly on every repeat, so this sample has little power to separate one judge from another or "
            "to populate the both-wrong cell that the cross-judge question depends on. A harder sample, or a "
            "judge that actually errs here, is needed before error correlation can be estimated."
        )
    lines.append("")
    lines.append("Score distribution (primary repeat):")
    lines.append("")
    lines.append("| judge | said CORRECT | said INCORRECT | mean confidence |")
    lines.append("| --- | --- | --- | --- |")
    for judge_id, payload in (metrics.get("judges") or {}).items():
        primary = payload["primary_repeat"]
        distribution = primary.get("verdict_distribution") or {}
        calibration = primary.get("calibration") or {}
        lines.append(
            f"| {judge_id} | {distribution.get('correct')} | {distribution.get('incorrect')} | "
            f"{_fmt(calibration.get('mean_confidence'), 1)} |"
        )
    for judge_id, payload in (metrics.get("judges") or {}).items():
        bins = (payload["primary_repeat"].get("calibration") or {}).get("confidence_histogram_5_bins") or []
        if not bins:
            continue
        rendered = ", ".join(
            f"{int(bin_row['low'])}-{int(bin_row['high'])}: {bin_row['count']}" for bin_row in bins
        )
        lines.append(f"- {judge_id} confidence bins: {rendered}")
    lines.append("")
    lines.append("## Cost and calls")
    lines.append("")
    if usage.get("spend_note"):
        lines.append(f"- **{usage['spend_note']}**")
    lines.append(f"- Network calls: {usage.get('calls_made')}; logged judge calls: {usage.get('calls_logged')}; cache hits: {usage.get('cache_hits')}")
    lines.append(f"- Tokens: {usage.get('input_tokens')} in, {usage.get('output_tokens')} out, {usage.get('reasoning_tokens')} reasoning")
    amount = usage.get("estimated_usd")
    if amount is None and usage.get("mock_run"):
        lines.append("- Estimated cost: nothing was spent (mock judges, no network calls)")
    else:
        lines.append(
            f"- Estimated cost: {'not computable (prices unverified)' if amount is None else f'US${amount:.4f}'} "
            f"(price source: {usage.get('price_source')})"
        )
    lines.append(f"- Caps: {usage.get('max_api_calls')} calls, US${usage.get('max_usd')}")
    lines.append(f"- Wall time: {usage.get('elapsed_seconds')} s")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for index, template in enumerate(LIMITATIONS, start=1):
        lines.append(f"{index}. " + template.format(n_items=sampling.n))
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append("- Manifest: `manifest.json` in this directory")
    lines.append(f"- Prompt version: `{(manifest.get('prompt_hashes') or {}).get('prompt_version')}`")
    lines.append(f"- Package version: {manifest.get('package_version')}")
    environment = manifest.get("environment") or {}
    lines.append(f"- Python {environment.get('python')} on {environment.get('platform')}")
    lines.append(f"- Judge resolution: {json.dumps(judge_detail.get('resolved', {}), ensure_ascii=False)}")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```powershell")
    lines.append(f"python -m medeval run exp002 --n-items {sampling.n} --seed {sampling.seed} --repeats {config.repeats}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
