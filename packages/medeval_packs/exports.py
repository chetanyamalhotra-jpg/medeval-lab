"""Publication-ready exports for a matrix run.

Every run writes:

* ``report.md`` — notice header, methodology card, CI summary, pairwise tests,
  judge agreement, cost/latency and limitations;
* ``results_table.tex`` — a booktabs snippet with a ``% MEDICAL_NOTICE`` header;
* ``citations.bib`` — the medeval-lab citation plus the pack's own BibTeX;
* ``items.csv`` — per-item scores and hashes (never raw model output text);
* ``bundle.zip`` — a byte-reproducible bundle of the above.

The notice is written by this module from the single ``MEDICAL_NOTICE`` constant,
so no caller can produce an export without it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from medeval_study.report import MEDICAL_NOTICE

from .schema import PaperPack

__all__ = [
    "BUNDLE_FILES",
    "citations_bib",
    "latex_table",
    "refresh_bundle",
    "render_report",
    "write_exports",
]

BUNDLE_FILES = (
    "README.txt",
    "manifest.json",
    "run.json",
    "metrics.json",
    "items.csv",
    "report.md",
    "results_table.tex",
    "citations.bib",
)
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_BIB_KEY = "medeval_lab_v060"


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:  # NaN
        return "—"
    return f"{number:.{digits}f}"


def _ci(block: Mapping[str, Any] | None) -> str:
    if not block:
        return "—"
    return f"[{_fmt(block.get('low'))}, {_fmt(block.get('high'))}]"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _short_hash(value: str) -> str:
    return value[:12] if value else "—"


def render_report(
    *,
    pack: PaperPack,
    metrics: Mapping[str, Any],
    config: Mapping[str, Any],
    run_id: str,
    pipeline_test_only: bool,
) -> str:
    lines: list[str] = []
    lines.append(f"# {pack.title}")
    lines.append("")
    lines.append(f"> {MEDICAL_NOTICE}")
    lines.append("")
    if pipeline_test_only:
        lines.append("> **PIPELINE TEST ONLY.** Mock targets and/or mock judges produced this run. "
                     "These numbers say nothing about any real model.")
        lines.append("")
    lines.append("## Methodology card")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Paper Pack | `{pack.id}` v{pack.version} |")
    lines.append(f"| Task type | `{pack.task_type}` |")
    lines.append(f"| Methodology | `{pack.methodology.kind}` — {_cell(pack.methodology.summary)} |")
    lines.append(f"| Citation | {_cell(pack.venue)}, {pack.year}; {_cell(pack.doi_or_url)} |")
    lines.append(f"| Licence | {_cell(pack.license)} |")
    lines.append(f"| Pack hash | `{_short_hash(pack.content_hash())}` |")
    lines.append(f"| Prompt hash | `{_short_hash(pack.prompt_hash())}` |")
    targets = config.get("targets") or []
    judges = config.get("judges") or []
    lines.append(f"| Targets | {', '.join(_cell(str(t.get('label') or t.get('id'))) for t in targets) or '—'} |")
    lines.append(
        "| Judges | "
        + (
            ", ".join(
                f"{_cell(str(j.get('label') or j.get('id')))} ({_cell(str(j.get('family') or 'family not set'))})"
                for j in judges
            )
            or "none (deterministic graders only)"
        )
        + " |"
    )
    lines.append(f"| Items | {metrics.get('n_items')} |")
    lines.append(f"| Stage | `{config.get('stage', 'full')}` |")
    lines.append(f"| Seed | {config.get('seed')} |")
    lines.append(f"| Run id | `{run_id}` |")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"Primary metric: `{metrics.get('primary_metric')}`. Intervals are "
        f"{config.get('bootstrap_resamples', 'the pack-declared')}-resample percentile bootstrap "
        f"95% CIs with seed {config.get('bootstrap_seed')}."
    )
    lines.append("")
    lines.append("| Target | Model | Primary | 95% CI | n | Abstention | Format | p50 ms | p95 ms | Est. USD |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for target_id, row in (metrics.get("targets") or {}).items():
        primary = row.get("primary") or {}
        lines.append(
            "| {label} | {model} | {estimate} | {ci} | {n} | {abstention} | {format_rate} | {p50} | {p95} | {usd} |".format(
                label=_cell(row.get("label") or target_id),
                model=_cell(row.get("model") or ""),
                estimate=_fmt(primary.get("estimate")),
                ci=_ci(primary.get("ci95")),
                n=row.get("n_items"),
                abstention=_fmt((row.get("abstention") or {}).get("rate")),
                format_rate=_fmt((row.get("format_compliance") or {}).get("rate")),
                p50=_fmt((row.get("latency") or {}).get("p50_ms"), 1),
                p95=_fmt((row.get("latency") or {}).get("p95_ms"), 1),
                usd=_fmt(row.get("estimated_usd"), 4),
            )
        )
    lines.append("")

    secondary_rows = [
        (target_id, row, name, payload)
        for target_id, row in (metrics.get("targets") or {}).items()
        for name, payload in (row.get("secondary") or {}).items()
    ]
    if secondary_rows:
        lines.append("### Secondary metrics")
        lines.append("")
        lines.append("| Target | Metric | Estimate | 95% CI | n |")
        lines.append("|---|---|---|---|---|")
        for target_id, row, name, payload in secondary_rows:
            lines.append(
                f"| {_cell(row.get('label') or target_id)} | {_cell(name)} | "
                f"{_fmt((payload or {}).get('estimate'))} | {_ci((payload or {}).get('ci95'))} | "
                f"{(payload or {}).get('n')} |"
            )
        lines.append("")

    lines.append("## Pairwise comparisons")
    lines.append("")
    pairwise = metrics.get("pairwise") or []
    if not pairwise:
        lines.append("No pairwise comparison was configured or only one target ran.")
    else:
        lines.append("| Left | Right | Method | n | Difference | p / 95% CI | Significant (α=0.05) |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in pairwise:
            if row.get("method") == "mcnemar":
                test = f"p = {_fmt(row.get('p_value'), 4)} (b01={row.get('b01')}, b10={row.get('b10')})"
            elif row.get("method") == "paired_bootstrap":
                test = _ci(row.get("ci95_difference"))
            else:
                test = row.get("note", "—")
            lines.append(
                f"| {_cell(row.get('left'))} | {_cell(row.get('right'))} | {_cell(row.get('method'))} | "
                f"{row.get('n')} | {_fmt(row.get('mean_difference'))} | {_cell(test)} | "
                f"{'yes' if row.get('significant') else 'no'} |"
            )
    lines.append("")

    lines.append("## Judge agreement and error correlation")
    lines.append("")
    reliability = metrics.get("judge_reliability") or {}
    if reliability.get("available"):
        lines.append(
            f"Judged items: {reliability.get('n_judged_items')}; Fleiss' κ {_fmt(reliability.get('fleiss_kappa'))}; "
            f"Krippendorff's α {_fmt(reliability.get('krippendorff_alpha'))}; "
            f"Cohen's κ {_fmt(reliability.get('cohen_kappa'))}."
        )
        lines.append("")
        lines.append("| Judge A | Judge B | n | Disagreements | Disagreement rate |")
        lines.append("|---|---|---|---|---|")
        for pair in (reliability.get("disagreement") or {}).get("pairs") or []:
            lines.append(
                f"| {_cell(pair.get('left'))} | {_cell(pair.get('right'))} | {pair.get('n')} | "
                f"{pair.get('disagreements')} | {_fmt(pair.get('disagreement_rate'))} |"
            )
        correlations = reliability.get("cross_family") or []
        if correlations:
            lines.append("")
            lines.append("Cross-family error correlation (positive φ = shared blind spots):")
            lines.append("")
            lines.append("| Judge A | Judge B | Families | φ | Both wrong | Excess over independence |")
            lines.append("|---|---|---|---|---|---|")
            for row in correlations:
                lines.append(
                    f"| {_cell(row.get('left'))} | {_cell(row.get('right'))} | "
                    f"{_cell(row.get('left_family'))} / {_cell(row.get('right_family'))} | "
                    f"{_fmt(row.get('phi'))} | {_fmt(row.get('both_wrong_rate'))} | "
                    f"{_fmt(row.get('excess_both_wrong_rate'))} |"
                )
        else:
            lines.append("")
            lines.append(
                "No cross-family pair could be estimated (one family, fewer than three shared items, or no "
                "binary correctness signal)."
            )
    else:
        lines.append(reliability.get("note") or "No LLM judge panel was configured.")

    lines.append("")
    lines.append("## Cost and latency")
    lines.append("")
    lines.append("| Target | Calls | Cached | Errors | Est. USD | USD per correct item | p50 ms | p95 ms |")
    lines.append("|---|---|---|---|---|---|---|---|")
    usage = config.get("usage") or {}
    for target_id, row in (metrics.get("targets") or {}).items():
        lines.append(
            f"| {_cell(row.get('label') or target_id)} | {usage.get('target_calls') or usage.get('calls_made') or '—'} | "
            f"{usage.get('cache_hits') or 0} | {row.get('errors')} | {_fmt(row.get('estimated_usd'), 4)} | "
            f"{_fmt(row.get('cost_per_correct_item'), 4)} | {_fmt((row.get('latency') or {}).get('p50_ms'), 1)} | "
            f"{_fmt((row.get('latency') or {}).get('p95_ms'), 1)} |"
        )
    if not metrics.get("overall", {}).get("total_estimated_usd"):
        lines.append("")
        lines.append(
            "Cost is unavailable: at least one target connection declared no price. The call cap, not a "
            "dollar estimate, is the binding limit for this run."
        )

    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for limitation in metrics.get("limitations") or []:
        lines.append(f"- {limitation}")

    lines.append("")
    lines.append("## Reproduction")
    lines.append("")
    lines.append("```powershell")
    lines.append(f"python -m medeval paper validate {pack.id}")
    lines.append(f"python -m medeval matrix run --paper {pack.id} --dry-run")
    lines.append(f"python -m medeval matrix show {run_id}")
    lines.append(f"python -m medeval regrade {run_id} --paper {pack.id}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def latex_table(*, pack: PaperPack, metrics: Mapping[str, Any], run_id: str) -> str:
    lines = [
        f"% {MEDICAL_NOTICE}",
        f"% Paper Pack: {pack.id} v{pack.version}; run {run_id}",
        "% Research output; not a clinical result.",
        "\\begin{table}[htbp]",
        "\\centering",
        f"\\caption{{{_latex_escape(pack.title)} --- primary metric with 95\\% bootstrap CI.}}",
        "\\label{tab:" + _latex_escape(pack.id.replace("-", "_")) + "}",
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        f"Target & {_latex_escape(str(metrics.get('primary_metric')))} & 95\\% CI & $n$ & Est.\\ USD \\\\",
        "\\midrule",
    ]
    for target_id, row in (metrics.get("targets") or {}).items():
        primary = row.get("primary") or {}
        interval = primary.get("ci95") or {}
        lines.append(
            "{} & {} & [{}, {}] & {} & {} \\\\".format(
                _latex_escape(str(row.get("label") or target_id)),
                _fmt(primary.get("estimate")),
                _fmt(interval.get("low")),
                _fmt(interval.get("high")),
                row.get("n_items"),
                _fmt(row.get("estimated_usd"), 4),
            )
        )
    lines.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
    ])
    return "\n".join(lines)


def citations_bib(*, pack: PaperPack, run_id: str) -> str:
    entries = [
        "@misc{" + _BIB_KEY + ",",
        "  title = {medeval-lab: an independent medical AI evaluation harness},",
        "  author = {medeval-lab contributors},",
        "  year = {2026},",
        "  note = {MIT-licensed research tool; version 0.6.0; output of run " + run_id + "},",
        "  howpublished = {\\url{https://github.com/medeval-lab/medeval-lab}}",
        "}",
    ]
    pack_bib = str((pack.citation or {}).get("bibtex") or "").strip()
    if pack_bib:
        entries.extend(["", pack_bib])
    return "\n".join(entries) + "\n"


def _items_csv(metrics: Mapping[str, Any], outputs: Sequence[Mapping[str, Any]], scores: Sequence[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    metrics_by_metric: dict[tuple[str, str], dict[str, float]] = {}
    for score in scores:
        if score.get("judge_id") not in (None, ""):
            continue
        key = (str(score.get("target_id")), str(score.get("item_id")))
        metrics_by_metric.setdefault(key, {})[str(score.get("metric"))] = float(score.get("value") or 0.0)
    writer.writerow(
        [
            "target_id", "item_id", "item_hash", "output_sha256", "latency_ms",
            "input_tokens", "output_tokens", "error", "primary_value",
        ]
    )
    for output in outputs:
        target_id = str(output.get("target_id"))
        item_id = str(output.get("item_id"))
        primary = (metrics.get("targets", {}).get(target_id, {}).get("values") or {}).get(item_id)
        writer.writerow([
            target_id,
            item_id,
            output.get("item_hash") or "",
            hashlib.sha256(str(output.get("text") or "").encode("utf-8")).hexdigest(),
            _fmt(output.get("latency_ms"), 3),
            output.get("input_tokens") or 0,
            output.get("output_tokens") or 0,
            output.get("error") or "",
            _fmt(primary),
        ])
    return buffer.getvalue()


def _zip_bytes(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue()


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def refresh_bundle(run_dir: Path) -> Path:
    """Rewrite ``bundle.zip`` from the files already on disk.

    Used after the final ``run.json`` write so the bundle carries the terminal
    status rather than the interim ``exporting`` state.
    """
    entries = {name: _read_bytes(run_dir / name) for name in BUNDLE_FILES if (run_dir / name).is_file()}
    bundle_path = run_dir / "bundle.zip"
    bundle_path.write_bytes(_zip_bytes(entries))
    return bundle_path


def write_exports(
    *,
    run_dir: Path,
    pack: PaperPack,
    metrics: Mapping[str, Any],
    config: Mapping[str, Any],
    run_id: str,
    outputs: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
    pipeline_test_only: bool,
) -> dict[str, str]:
    """Write all export files and return their paths."""
    run_dir.mkdir(parents=True, exist_ok=True)
    report = render_report(
        pack=pack, metrics=metrics, config=config, run_id=run_id, pipeline_test_only=pipeline_test_only
    )
    (run_dir / "report.md").write_text(report, encoding="utf-8", newline="\n")
    (run_dir / "results_table.tex").write_text(
        latex_table(pack=pack, metrics=metrics, run_id=run_id), encoding="utf-8", newline="\n"
    )
    (run_dir / "citations.bib").write_text(
        citations_bib(pack=pack, run_id=run_id), encoding="utf-8", newline="\n"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8", newline="\n"
    )
    (run_dir / "items.csv").write_text(_items_csv(metrics, outputs, scores), encoding="utf-8", newline="\n")

    readme = (
        f"medeval-lab v0.6.0 export for run {run_id}\n"
        f"Paper Pack: {pack.id} v{pack.version} ({pack.task_type})\n\n"
        f"{MEDICAL_NOTICE}\n\n"
        + ("PIPELINE TEST ONLY: mock targets/judges produced these numbers.\n\n" if pipeline_test_only else "")
        + "Contents: manifest.json, run.json, metrics.json, items.csv (scores and hashes only, no raw model text),\n"
        "report.md, results_table.tex, citations.bib. No API keys, prompts or dataset text are included.\n"
    )
    (run_dir / "README.txt").write_text(readme, encoding="utf-8", newline="\n")
    entries = {
        "README.txt": readme.encode("utf-8"),
        "manifest.json": _read_bytes(run_dir / "manifest.json"),
        "run.json": _read_bytes(run_dir / "run.json"),
        "metrics.json": _read_bytes(run_dir / "metrics.json"),
        "items.csv": (run_dir / "items.csv").read_bytes(),
        "report.md": report.encode("utf-8"),
        "results_table.tex": (run_dir / "results_table.tex").read_bytes(),
        "citations.bib": (run_dir / "citations.bib").read_bytes(),
    }
    bundle_path = run_dir / "bundle.zip"
    bundle_path.write_bytes(_zip_bytes(entries))
    return {
        "report": str(run_dir / "report.md"),
        "latex": str(run_dir / "results_table.tex"),
        "bib": str(run_dir / "citations.bib"),
        "metrics": str(run_dir / "metrics.json"),
        "items_csv": str(run_dir / "items.csv"),
        "bundle": str(bundle_path),
    }
