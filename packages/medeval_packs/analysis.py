"""Turn a finished matrix run's per-item records into paper-grade metrics.

The input is only what the run persisted: ``outputs.jsonl`` (target text and
usage) and ``scores.jsonl`` (deterministic and judge scores).  Nothing here calls
a model, so the same code powers live analysis, resume, rebuild and offline
regrading.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from medeval_core import stats

from .schema import PaperPack

__all__ = ["analyse_matrix", "json_safe"]

_ACCURACY_METRICS = ("mcq_accuracy", "label_accuracy", "exact_match", "field_exact_match")
_BINARY_METRICS = {"mcq_accuracy", "label_accuracy", "exact_match", "field_exact_match", "json_schema",
                   "format_compliance", "abstention", "refusal"}


def json_safe(value: Any) -> Any:
    """Replace NaN/inf with ``None`` so the JSON is valid for other tools."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def _mean(values: Sequence[float]) -> float:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return sum(clean) / len(clean) if clean else float("nan")


def _item_index(items: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(item.get("item_id")): item for item in items if item.get("item_id")}


def _primary_metric(pack: PaperPack) -> str:
    declared = pack.metrics.primary
    if declared in {"mean_rubric_score", "rubric_score", "judge_score"}:
        return "rubric_score"
    if declared != "accuracy":
        return declared
    if declared == "accuracy":
        for candidate in _ACCURACY_METRICS:
            if any(grader.metric == candidate for grader in pack.graders):
                return candidate
        return "mcq_accuracy"
    if declared in {"mean_rubric_score", "rubric_score", "judge_score"}:
        return "rubric_score"
    return declared


def _score_value(score: Mapping[str, Any]) -> float | None:
    value = score.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _per_item_values(
    scores: Sequence[Mapping[str, Any]],
    *,
    target_id: str,
    metric: str,
    judge_id: str | None = None,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for score in scores:
        if score.get("target_id") != target_id or score.get("metric") != metric:
            continue
        if judge_id is not None and score.get("judge_id") != judge_id:
            continue
        if judge_id is None and score.get("judge_id") not in (None, ""):
            continue
        value = _score_value(score)
        item_id = str(score.get("item_id"))
        if value is None or item_id in values:
            continue
        values[item_id] = value
    return values


def _judge_scores(
    scores: Sequence[Mapping[str, Any]],
    *,
    target_id: str,
    grader_id: str,
    judge_id: str,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for score in scores:
        if (
            score.get("target_id") == target_id
            and score.get("grader_id") == grader_id
            and score.get("judge_id") == judge_id
        ):
            value = _score_value(score)
            item_id = str(score.get("item_id"))
            if value is not None and item_id not in values:
                values[item_id] = value
    return values


def _cost_for_target(
    outputs: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
    *,
    target_id: str,
) -> float | None:
    price_in = target.get("price_in_per_mtok")
    price_out = target.get("price_out_per_mtok")
    if price_in is None and price_out is None:
        return None
    total = 0.0
    for output in outputs:
        if output.get("target_id") != target_id:
            continue
        total += (
            int(output.get("input_tokens") or 0) * float(price_in or 0.0)
            + int(output.get("output_tokens") or 0) * float(price_out or 0.0)
        ) / 1_000_000
    return total


def _macro_pairs(
    scores: Sequence[Mapping[str, Any]],
    *,
    target_id: str,
    metric: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Item ids, predicted labels, gold labels and declared label set."""
    item_ids: list[str] = []
    predicted: list[str] = []
    gold: list[str] = []
    labels: list[str] = []
    metric_names = {metric, "macro_f1", "multiclass_f1", "macro_precision"}
    for score in scores:
        if score.get("target_id") != target_id or score.get("metric") not in metric_names:
            continue
        if score.get("judge_id") not in (None, ""):
            continue
        parsed = score.get("parsed") or {}
        prediction = parsed.get("predicted") if "predicted" in parsed else parsed.get("label")
        truth = parsed.get("gold")
        item_id = str(score.get("item_id"))
        if prediction is None or truth is None or item_id in item_ids:
            continue
        if isinstance(parsed.get("labels"), list) and not labels:
            labels = [str(label) for label in parsed["labels"]]
        item_ids.append(item_id)
        predicted.append(str(prediction))
        gold.append(str(truth))
    return item_ids, predicted, gold, labels


def _rubric_values(
    scores: Sequence[Mapping[str, Any]],
    *,
    target_id: str,
    rubric_graders: Sequence[str],
    judges: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    combined: dict[str, list[float]] = {}
    for grader_id in rubric_graders:
        for judge in judges:
            judge_values = _judge_scores(
                scores, target_id=target_id, grader_id=grader_id, judge_id=str(judge.get("id"))
            )
            for item_id, value in judge_values.items():
                combined.setdefault(item_id, []).append(value)
    return {item_id: sum(rows) / len(rows) for item_id, rows in combined.items() if rows}


def analyse_matrix(
    *,
    pack: PaperPack,
    items: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
    judges: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    pipeline_test_only: bool,
) -> dict[str, Any]:
    """Compute every number the report, LaTeX table and UI need."""
    primary_metric = _primary_metric(pack)
    rubric_graders = [grader.id for grader in pack.graders if grader.type == "llm_rubric"]
    item_by_id = _item_index(items)
    comparison = pack.metrics.comparison
    error_outputs = [row for row in outputs if row.get("error")]
    target_metrics: dict[str, Any] = {}

    for target in targets:
        target_id = str(target.get("id") or target.get("binding_id"))
        values = _per_item_values(scores, target_id=target_id, metric=primary_metric)
        macro_report: dict[str, Any] | None = None
        interval = None
        if primary_metric in {"macro_f1", "multiclass_f1", "macro_precision"}:
            macro_ids, macro_predicted, macro_gold, macro_labels = _macro_pairs(
                scores, target_id=target_id, metric=primary_metric
            )
            if macro_predicted:
                macro_report = stats.classification_report(macro_predicted, macro_gold, labels=macro_labels or None)
                values = {
                    item_id: 1.0 if predicted == truth else 0.0
                    for item_id, predicted, truth in zip(macro_ids, macro_predicted, macro_gold, strict=True)
                }
                interval = stats.bootstrap_macro_metric(
                    macro_predicted,
                    macro_gold,
                    metric="precision" if primary_metric == "macro_precision" else "f1",
                    labels=macro_labels or None,
                    n_resamples=comparison.bootstrap_resamples,
                    seed=comparison.bootstrap_seed,
                    alpha=comparison.alpha,
                )
        if primary_metric == "rubric_score":
            values = _rubric_values(
                scores, target_id=target_id, rubric_graders=rubric_graders, judges=judges
            )

        ordered_ids = [item_id for item_id in item_by_id if item_id in values]
        ordered_values = [values[item_id] for item_id in ordered_ids]
        strata = [str(item_by_id[item_id].get("stratum") or "all") for item_id in ordered_ids]
        if interval is None and ordered_values:
            interval = stats.bootstrap_ci(
                ordered_values,
                n_resamples=comparison.bootstrap_resamples,
                seed=comparison.bootstrap_seed,
                alpha=comparison.alpha,
                strata=strata if len(set(strata)) > 1 else None,
            )
        binary = primary_metric in _BINARY_METRICS or macro_report is not None

        def metric_values(metric: str, target_id: str = target_id) -> list[float]:
            rows = _per_item_values(scores, target_id=target_id, metric=metric)
            return [rows[item_id] for item_id in item_by_id if item_id in rows]

        abstention_values = metric_values("abstention")
        format_values = metric_values("format_compliance")
        latency_rows = [
            float(row.get("latency_ms") or 0.0)
            for row in outputs
            if row.get("target_id") == target_id and not row.get("error")
        ]
        correct = sum(1 for value in ordered_values if value >= 0.5) if binary else None
        cost = _cost_for_target(outputs, target, target_id=target_id)
        secondary: dict[str, Any] = {}
        for metric_name in pack.metrics.secondary:
            metric_interval = None
            metric_values: list[float] = []
            if metric_name in {"rubric_score", "mean_rubric_score", "judge_score"}:
                metric_map = _rubric_values(
                    scores, target_id=target_id, rubric_graders=rubric_graders, judges=judges
                )
                metric_values = [metric_map[item_id] for item_id in item_by_id if item_id in metric_map]
            elif metric_name in {"macro_f1", "multiclass_f1", "macro_precision"}:
                _ids, predicted, gold, labels = _macro_pairs(scores, target_id=target_id, metric=metric_name)
                if predicted:
                    metric_interval = stats.bootstrap_macro_metric(
                        predicted,
                        gold,
                        metric="precision" if metric_name == "macro_precision" else "f1",
                        labels=labels or None,
                        n_resamples=comparison.bootstrap_resamples,
                        seed=comparison.bootstrap_seed,
                        alpha=comparison.alpha,
                    )
                    metric_values = [metric_interval.estimate]
            else:
                metric_map = _per_item_values(scores, target_id=target_id, metric=metric_name)
                metric_values = [metric_map[item_id] for item_id in item_by_id if item_id in metric_map]
            if metric_interval is None and metric_values:
                metric_interval = stats.bootstrap_ci(
                    metric_values,
                    n_resamples=comparison.bootstrap_resamples,
                    seed=comparison.bootstrap_seed,
                    alpha=comparison.alpha,
                )
            secondary[metric_name] = {
                "estimate": metric_interval.estimate if metric_interval else float("nan"),
                "ci95": metric_interval.as_dict() if metric_interval else None,
                "n": len(metric_values),
            }

        target_metrics[target_id] = {
            "label": target.get("label") or target_id,
            "family": target.get("family") or "",
            "model": target.get("model") or "",
            "n_items": len(ordered_values),
            "primary_metric": primary_metric,
            "primary": {
                "estimate": interval.estimate if interval else float("nan"),
                "ci95": interval.as_dict() if interval else None,
                "n": len(ordered_values),
            },
            "correct": correct,
            "errors": sum(1 for row in outputs if row.get("target_id") == target_id and row.get("error")),
            "abstention": stats.abstention_rate([value >= 0.5 for value in abstention_values])
            if abstention_values else {"n": 0, "rate": float("nan")},
            "format_compliance": stats.format_compliance_rate([value >= 0.5 for value in format_values])
            if format_values else {"n": 0, "rate": float("nan")},
            "latency": stats.latency_summary(latency_rows),
            "estimated_usd": cost,
            "cost_per_correct_item": stats.cost_per_correct_item(cost, correct or 0),
            "values": {item_id: values[item_id] for item_id in ordered_ids},
            "secondary": secondary,
            "classification": macro_report,
        }

    # -- pairwise comparisons -------------------------------------------- #
    pairwise: list[dict[str, Any]] = []
    target_ids = list(target_metrics)
    for index, left_id in enumerate(target_ids):
        for right_id in target_ids[index + 1 :]:
            left_values = target_metrics[left_id]["values"]
            right_values = target_metrics[right_id]["values"]
            shared = [item_id for item_id in item_by_id if item_id in left_values and item_id in right_values]
            if len(shared) < 2:
                pairwise.append({
                    "left": left_id, "right": right_id, "n": len(shared),
                    "method": comparison.method, "note": "fewer than two paired items",
                })
                continue
            a = [left_values[item_id] for item_id in shared]
            b = [right_values[item_id] for item_id in shared]
            entry: dict[str, Any] = {"left": left_id, "right": right_id, "n": len(shared),
                                     "mean_left": _mean(a), "mean_right": _mean(b),
                                     "mean_difference": _mean(a) - _mean(b)}
            if comparison.method == "mcnemar":
                result = stats.mcnemar_test([value >= 0.5 for value in a], [value >= 0.5 for value in b])
                entry.update(result)
                entry["test"] = result["method"]
                entry["method"] = "mcnemar"
                entry["significant"] = bool(result["p_value"] < comparison.alpha)
            elif comparison.method == "paired_bootstrap":
                interval = stats.paired_bootstrap_diff(
                    a, b,
                    n_resamples=comparison.bootstrap_resamples,
                    seed=comparison.bootstrap_seed,
                    alpha=comparison.alpha,
                )
                entry.update({
                    "method": "paired_bootstrap",
                    "ci95_difference": interval.as_dict(),
                    "significant": bool(interval.low > 0 or interval.high < 0),
                })
            else:
                entry.update({"method": "none"})
            pairwise.append(entry)

    # -- judge reliability ------------------------------------------------- #
    judge_ids = [str(judge.get("id")) for judge in judges]
    judge_labels: dict[str, list[Any]] = {judge_id: [] for judge_id in judge_ids}
    judge_values_by_target: dict[str, dict[str, float]] = {}
    for target in targets:
        target_id = str(target.get("id") or target.get("binding_id"))
        for judge_id in judge_ids:
            values: dict[str, float] = {}
            for grader_id in rubric_graders:
                values.update(_judge_scores(scores, target_id=target_id, grader_id=grader_id, judge_id=judge_id))
            judge_values_by_target.setdefault(judge_id, {}).update(values)
    reliability: dict[str, Any] = {"available": bool(judges and rubric_graders), "judges": judge_ids}
    if reliability["available"]:
        matrix: list[list[Any]] = []
        for item_id in item_by_id:
            row = []
            for judge_id in judge_ids:
                value = judge_values_by_target.get(judge_id, {}).get(item_id)
                row.append(round(value) if value is not None else None)
            if any(value is not None for value in row):
                matrix.append(row)
        for row in matrix:
            for index, judge_id in enumerate(judge_ids):
                if row[index] is not None:
                    judge_labels[judge_id].append(row[index])
        reliability.update({
            "n_judged_items": len(matrix),
            "disagreement": stats.disagreement_matrix({judge_id: [row[index] for row in matrix] for index, judge_id in enumerate(judge_ids)}),
            "fleiss_kappa": stats.fleiss_kappa(matrix),
            "krippendorff_alpha": stats.krippendorff_alpha(matrix),
        })
        if len(judge_ids) == 2:
            shared = [(row[0], row[1]) for row in matrix if row[0] is not None and row[1] is not None]
            if shared:
                left, right = zip(*shared, strict=True)
                reliability["cohen_kappa"] = stats.cohen_kappa(left, right)
                reliability["raw_agreement"] = stats.raw_agreement(left, right)
        # Cross-family error correlation uses a binary signal. For an MCQ pack
        # that signal is correctness against the key; for a rubric pack it is a
        # median split of the judge's own scores (explicitly labelled as such).
        families = {str(judge.get("id")): str(judge.get("family") or "") for judge in judges}
        correlations: list[dict[str, Any]] = []
        for index, left in enumerate(judge_ids):
            for right in judge_ids[index + 1 :]:
                if families.get(left) and families.get(right) and families[left].casefold() == families[right].casefold():
                    continue
                left_values = judge_values_by_target.get(left, {})
                right_values = judge_values_by_target.get(right, {})
                shared = sorted(set(left_values) & set(right_values))
                if len(shared) < 3:
                    continue
                if pack.task_type == "mcq":
                    left_errors = [left_values[item_id] < 3 for item_id in shared]
                    right_errors = [right_values[item_id] < 3 for item_id in shared]
                    basis = "judge score below the middle of a 1-5 rubric"
                else:
                    left_errors = [left_values[item_id] < 3 for item_id in shared]
                    right_errors = [right_values[item_id] < 3 for item_id in shared]
                    basis = "score below the 3-point midpoint of the declared scale"
                correlations.append({
                    "left": left,
                    "right": right,
                    "left_family": families.get(left),
                    "right_family": families.get(right),
                    "basis": basis,
                    **stats.cross_family_error_correlation(left_errors, right_errors),
                })
        reliability["cross_family"] = correlations
    else:
        reliability["note"] = "no LLM judge panel was configured for this run"

    # -- run-level summary ------------------------------------------------ #
    all_primary = [row["primary"]["estimate"] for row in target_metrics.values() if math.isfinite(row["primary"]["estimate"])]
    total_calls = int(config.get("usage", {}).get("calls_made") or 0) if isinstance(config.get("usage"), Mapping) else 0
    total_usd = sum(
        row["estimated_usd"] for row in target_metrics.values() if row["estimated_usd"] is not None
    )
    priced_targets = sum(1 for row in target_metrics.values() if row["estimated_usd"] is not None)
    return json_safe({
        "schema_version": 1,
        "paper": {
            "id": pack.id,
            "version": pack.version,
            "title": pack.title,
            "task_type": pack.task_type,
            "methodology": pack.methodology.as_dict(),
            "pack_hash": pack.content_hash(),
            "prompt_hash": pack.prompt_hash(),
        },
        "pipeline_test_only": bool(pipeline_test_only),
        "primary_metric": primary_metric,
        "n_items": len(item_by_id),
        "n_outputs": len(outputs),
        "n_errors": len(error_outputs),
        "targets": target_metrics,
        "pairwise": pairwise,
        "judge_reliability": reliability,
        "overall": {
            "mean_primary": _mean(all_primary),
            "total_estimated_usd": total_usd if priced_targets == len(target_metrics) and target_metrics else None,
            "priced_targets": priced_targets,
            "targets": len(target_metrics),
            "judges": len(judge_ids),
            "calls_made": total_calls,
            "errors": len(error_outputs),
        },
        "limitations": _limitations(pack, target_metrics, judges, pipeline_test_only),
    })


def _limitations(
    pack: PaperPack,
    target_metrics: Mapping[str, Mapping[str, Any]],
    judges: Sequence[Mapping[str, Any]],
    pipeline_test_only: bool,
) -> list[str]:
    limitations = [
        "Research benchmark only: this is not a medical device, has no clinical validation and no "
        "clinician reviewed any output.",
        pack.methodology.limitations
        or "No pack-specific limitations were declared; treat that as a documentation gap, not as absence of limitations.",
    ]
    if pipeline_test_only:
        limitations.append(
            "PIPELINE TEST ONLY: mock targets/judges produced these outputs; the numbers say nothing "
            "about any real model."
        )
    small = [target_id for target_id, row in target_metrics.items() if row.get("n_items", 0) < 30]
    if small:
        limitations.append(
            f"Small samples for {', '.join(small)} (<30 items): confidence intervals are wide and the "
            "analysis is exploratory."
        )
    families = {str(judge.get("family") or "") for judge in judges}
    if len(judges) >= 2 and len(families) < 2:
        limitations.append(
            "All judges declare one family, so cross-family error correlation cannot be estimated; "
            "same-family judges share training data and failure modes."
        )
    if not judges:
        limitations.append("No LLM judge panel was configured; only deterministic graders were applied.")
    return limitations
