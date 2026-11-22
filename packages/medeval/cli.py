"""``python -m medeval`` command line interface.

One entry point for the whole pipeline. Every command prints which model backend
produced its numbers and exits non-zero if a check fails, so it can be used in CI
and in ``run_all.ps1``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from medeval_core.backends import select_backend
from medeval_core.config import load_settings
from medeval_core.paths import items_dir, literature_csv, repo_root, results_dir
from medeval_core.validator import validate_file
from medeval_core.version import (
    CRITERIA_VERSION,
    ITEMS_VERSION,
    METRICS_VERSION,
    RUBRIC_VERSION,
    __version__,
)

__all__ = ["main"]


def _print_backend() -> str:
    settings = load_settings()
    selection = select_backend(settings)
    print(f"[backend] {selection.label}")
    print(f"[backend] reason: {selection.reason}")
    if selection.pipeline_test_only:
        print("[backend] WARNING: results are PIPELINE TEST ONLY (mock judge)")
    return selection.label


def cmd_validate(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.paths] if args.paths else sorted(items_dir().glob("*.json")) + sorted(
        items_dir().glob("*.jsonl")
    )
    if not paths:
        print("no item files found")
        return 1
    failed = False
    for path in paths:
        try:
            report = validate_file(path)
        except Exception as exc:
            print(f"[validate] {path}: ERROR {type(exc).__name__}: {exc}")
            failed = True
            continue
        status = "ok" if report.ok else "FAILED"
        print(
            f"[validate] {path.name}: {report.n_items} items, {len(report.errors)} errors, "
            f"{len(report.warnings)} warnings -> {status}"
        )
        for issue in report.errors[:10]:
            print(f"    ERROR {issue.code} [{issue.item_id}] {issue.message}")
        if not report.ok:
            failed = True
    return 1 if failed else 0


def cmd_traceability(args: argparse.Namespace) -> int:
    from medeval_criteria import check_traceability, format_issues, load_catalogue, load_literature
    from medeval_metrics import METRICS

    catalogue = load_catalogue()
    literature = load_literature(literature_csv())
    issues = check_traceability(catalogue, METRICS, literature)
    print(
        f"[traceability] {len(catalogue.criteria)} criteria, {len(METRICS)} metrics, "
        f"{len(literature)} literature rows"
    )
    print(format_issues(issues))
    errors = [issue for issue in issues if issue.severity == "error"]
    unverified = [row for row in literature.values() if not row.is_verified]
    if unverified:
        print(f"[traceability] {len(unverified)} unverified citation(s): " + ", ".join(row.id for row in unverified))
    return 1 if errors else 0


def cmd_audit(args: argparse.Namespace) -> int:
    from medeval_audit import run_audit

    run = run_audit(
        datasets=tuple(args.datasets),
        near_dup_threshold=args.threshold,
        allow_download=not args.offline,
    )
    for audit in run.audits:
        print(
            f"[audit] {audit.dataset}/{audit.split}: n={audit.n_items}, "
            f"key_base={audit.answer_key.index_base}, exact_dups={audit.duplicates.n_exact_groups}, "
            f"near_dups={audit.duplicates.n_near_groups}, flagged={len(audit.flagged_item_ids)}"
        )
    for skipped in run.skipped:
        print(f"[audit] SKIPPED {skipped['dataset']}: {skipped['reason']}")
    print(f"[audit] report: {run.json_path}")
    print(f"[audit] report: {run.markdown_path}")
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    from medeval.harness import run_baseline

    payload = run_baseline(limit=args.limit, tasks=tuple(args.tasks))
    print(f"[harness] model={payload['model']} tasks={payload['tasks']} limit={payload['limit']}")
    for task, metrics in payload["results"].items():
        flat = ", ".join(f"{key}={value:.4f}" for key, value in metrics.items() if isinstance(value, (int, float)))
        print(f"[harness] {task}: {flat}")
    print(f"[harness] saved: {payload['path']}")
    print("[harness] NOTE: dummy model; these numbers validate wiring, not performance")
    return 0


def cmd_experiment001(args: argparse.Namespace) -> int:
    import runpy

    script = repo_root() / "experiments" / "001_judge_reliability" / "run.py"
    if not script.is_file():
        print(f"[experiment] missing {script}")
        return 1
    sys.argv = [str(script)]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:  # the experiment script exits explicitly
        return int(exc.code or 0)
    return 0


def cmd_experiment002a(args: argparse.Namespace) -> int:
    """Archived pairwise protocol (see experiments/002a_pairwise_blocked)."""
    import runpy

    script = repo_root() / "experiments" / "002a_pairwise_blocked" / "run.py"
    if not script.is_file():
        print(f"[exp002a] missing {script}")
        return 1
    sys.argv = [str(script), *(["--dry-run"] if args.dry_run else []), *(["--rule-only"] if args.rule_only else [])]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


# --------------------------------------------------------------------------- #
# Experiment 002: grading study
# --------------------------------------------------------------------------- #
def _progress_printer(total_hint: int | None = None, label: str = "run") -> Any:
    state = {"last": -1, "total": total_hint or 0}

    def _callback(event: dict[str, Any]) -> None:
        phase = event.get("phase")
        if phase in {"load", "sample", "plan", "budget_stop", "pilot_gate", "screening"}:
            print(f"[{label}] {event['message']}")
            if event.get("total"):
                state["total"] = int(event["total"])
            return
        total = int(event.get("total") or state["total"] or 0)
        progress = int(event.get("progress") or 0)
        if total <= 0:
            return
        percent = int(progress * 10 / total)
        if percent > state["last"] or event.get("error"):
            state["last"] = percent
            print(f"[{label}] {progress}/{total} calls ({percent * 10}%)")

    return _callback


def _dry_run_judge_bindings(repeats: int = 1) -> tuple[dict[str, Any], ...]:
    """Two labelled mock judges, so a dry run needs no provider, no key and no data."""
    from medeval_study.mock_bindings import dry_run_judge_bindings

    return dry_run_judge_bindings(repeats)


def _connections_from_args(args: argparse.Namespace) -> Any:
    """Load ``--connections`` if given; print and exit on an unusable file."""
    from medeval_study.connections_file import ConnectionsFileError, load_connections_file

    try:
        return load_connections_file(args.connections), None
    except ConnectionsFileError as exc:
        print(f"[run] connections file refused: {exc}")
        print("[run] nothing was called. Copy connections.example.yaml and fill in real placeholders.")
        return None, 2


def _print_live_plan(connections: Any, args: argparse.Namespace, settings: Any) -> int:
    """Print the plan and worst-case cost; return non-zero when the caps refuse it."""
    n_items = max(1, int(args.n_items))
    repeats = max(1, int(args.repeats)) if args.experiment == "exp002" else 1
    print("[run] provider connections from the file (no key, no key length and no key prefix is shown):")
    for connection in connections.connections:
        row = connection.summary()
        prices = (
            f"in ${connection.price_in_per_mtok}/Mtok out ${connection.price_out_per_mtok}/Mtok"
            if connection.prices_declared
            else "prices not declared"
        )
        print(
            f"[run]   {row['name']!r} role={row['role']} host={row['host']} model={row['model']!r} "
            f"family={row['family_label']!r} key_env={row['key_env']} repeats={row['repeats']} "
            f"call_cap={row['call_cap']} usd_cap={row['usd_cap']} ({prices})"
        )

    judge_calls = 0
    over_cap: list[str] = []
    for connection in connections.judges:
        needed = n_items * repeats * max(1, connection.repeats)
        judge_calls += needed
        if needed > connection.call_cap:
            over_cap.append(
                f"{connection.name!r} would need {needed} calls but its call_cap is {connection.call_cap}"
            )
    print(
        f"[run] plan: {n_items} item(s) x {repeats} repeat(s) x {len(connections.judges)} judge(s) "
        f"= {judge_calls} judge call(s)"
    )
    if args.experiment == "exp003":
        print(
            f"[run] Experiment 003 also screens up to {args.screening_size} item(s) and recalibrates after a "
            "40-item pilot; its own plan (printed before the pilot gate) is the authoritative call count"
        )
    worst = connections.worst_case_usd(n_items=n_items, repeats=repeats)
    if worst is None:
        print("[run] worst-case cost: unavailable, call cap is binding (a connection declares no prices)")
    else:
        print(f"[run] worst-case cost: US${worst:.4f} (upper bound from your declared prices and max_tokens)")
    print(f"[run] run-wide caps: {args.max_api_calls} call(s), US${settings.max_usd:.2f}")

    problems = list(over_cap)
    if judge_calls > args.max_api_calls:
        problems.append(
            f"the plan needs {judge_calls} calls, above the run cap of {args.max_api_calls} "
            "(raise --max-api-calls or lower --n-items/--repeats)"
        )
    if worst is not None and worst > settings.max_usd:
        problems.append(
            f"the worst case US${worst:.4f} exceeds the run cap of US${settings.max_usd:.2f} "
            "(raise --max-usd, lower --n-items, declare smaller prices or set smaller call caps)"
        )
    if problems:
        for problem in problems:
            print(f"[run] refused: {problem}")
        print("[run] nothing was called.")
        return 2
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from medeval_study import StudyConfig, StudyRun

    if args.experiment not in {"exp002", "exp003"}:
        print(f"[run] unknown experiment {args.experiment!r}; this release implements 'exp002' and 'exp003'")
        return 2
    settings = load_settings()
    if getattr(args, "max_usd", None) is not None:
        from dataclasses import replace as _replace

        settings = _replace(settings, max_usd=max(0.0, float(args.max_usd)))
    label = "exp002" if args.experiment == "exp002" else "exp003"

    connections: Any = None
    if getattr(args, "connections", None):
        connections, failure = _connections_from_args(args)
        if failure is not None:
            return failure

    if not args.dry_run:
        # A live run needs a key. There are exactly two ways to provide one: the
        # app's memory (Models page), or this file plus environment variables.
        if connections is None:
            print("[run] a live run needs provider connections. Two ways to give it one:")
            print("[run]   app : medeval ui -> Models page -> add a connection, paste the key, assign judge roles")
            print("[run]   CLI : copy connections.example.yaml to connections.yaml; each entry names the")
            print("[run]         environment variable that holds its key (key_env), never the key itself")
            print("[run]         then: medeval run exp002 --connections connections.yaml")
            print("[run] A dry run needs no key and no connection: medeval run exp002 --dry-run")
            return 2
        missing = connections.missing_keys()
        if missing:
            print(
                "[run] these environment variables are named by the connections file but are not set: "
                + ", ".join(missing)
            )
            print("[run] set them in this shell (never in the file) and try again")
            return 2
        refused = _print_live_plan(connections, args, settings)
        if refused:
            return refused
        if not getattr(args, "confirm_live", False):
            print("[run] nothing was called. Re-run the same command with --confirm-live to make these calls.")
            return 2

    archived_judges = tuple(getattr(args, "judges", None) or ())
    if args.experiment == "exp002":
        if connections is not None:
            judge_bindings = connections.judge_bindings()
        elif args.dry_run and not archived_judges:
            judge_bindings = _dry_run_judge_bindings(args.repeats)
        else:
            judge_bindings = ()
        config = StudyConfig(
            experiment="002_judge_families",
            dataset=args.dataset,
            split=args.split,
            n_items=args.n_items,
            seed=args.seed,
            repeats=args.repeats,
            temperature=args.temperature,
            max_api_calls=args.max_api_calls,
            allow_download=not args.no_download,
            dry_run=args.dry_run,
            judges=() if judge_bindings else archived_judges,
            connection_judges=judge_bindings,
            consistency_subsample=args.consistency_subsample,
            run_id=args.run_id or args.resume,
            resume=args.resume is not None or args.resume_latest,
        )
    else:
        # Experiment 003 needs judges and a screening model. A dry run uses mock
        # stand-ins; a live run uses the connections file (the app is the other
        # supported path and keeps keys in its own memory).
        if not args.dry_run:
            if connections is None or not connections.screeners:
                print(
                    "[run] Experiment 003 needs a screening connection as well as judges: add an entry with "
                    "role: screening to connections.yaml (or run it from the app, where the Models page holds "
                    "the screening role)"
                )
                print("[run] nothing was called.")
                return 2
            judge_bindings = connections.judge_bindings()
            screening_binding = connections.screening_binding()
        else:
            judge_bindings = _dry_run_judge_bindings()
            screening_binding = None
        config = StudyConfig(
            experiment="003_frontier_judges",
            dataset=args.dataset,
            split=args.split,
            n_items=args.n_items,
            seed=args.seed,
            repeats=1,
            temperature=args.temperature,
            max_api_calls=args.max_api_calls,
            allow_download=not args.no_download,
            dry_run=args.dry_run,
            judges=(),
            connection_judges=judge_bindings,
            screening_binding=screening_binding,
            consistency_subsample=args.consistency_subsample,
            pilot_items=40,
            pilot_min_disagreements=5,
            screening_size=args.screening_size,
            run_id=args.run_id or args.resume,
            resume=args.resume is not None or args.resume_latest,
        )
    print(f"[{label}] experiment {config.experiment} (EXPLORATORY) - medeval-lab {__version__}")
    if args.dry_run:
        print(f"[{label}] dry run: mock judges only. Results are PIPELINE TEST ONLY.")
    if args.experiment == "exp003":
        from medeval_study.exp003 import Exp003Study

        run = Exp003Study(config, settings, progress=_progress_printer(args.n_items * max(1, args.repeats), label))
    else:
        judge_count = len(config.judges) if config.judges else max(1, len(config.connection_judges))
        run = StudyRun(
            config,
            settings,
            progress=_progress_printer(args.n_items * args.repeats * judge_count, label),
        )
    result = run.execute()

    status = result.get("status")
    print(f"[{label}] status: {status}")
    print(f"[{label}] run directory: {result.get('run_dir')}")
    if status == "pilot_stopped":
        gate = result.get("pilot_gate") or {}
        print(f"[{label}] pilot gate: {gate.get('decision')}")
        print(f"[{label}] report: {Path(result['run_dir']) / 'report.md'}")
        return 0
    if status in {"blocked", "refused"}:
        run_state = result.get("run_dir")
        if run_state:
            state_path = Path(run_state) / "run.json"
            if state_path.is_file():
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                reason = payload.get("blocked_reason") or payload.get("refused_reason")
                if reason:
                    print(f"[{label}] reason: {reason}")
        return 2
    metrics = result.get("metrics") or {}
    for judge_id, payload in (metrics.get("judges") or {}).items():
        primary = payload["primary_repeat"]
        accuracy = primary["accuracy"]
        interval = accuracy.get("ci95_wilson") or [float("nan"), float("nan")]
        kappa = primary.get("cohen_kappa_vs_key")
        print(
            f"[{label}] {judge_id}: accuracy {accuracy['estimate']:.3f} "
            f"[{interval[0]:.3f}, {interval[1]:.3f}] over {accuracy['denominator']} items; "
            f"kappa vs key {'n/a' if kappa is None else f'{kappa:.3f}'}"
        )
        if primary.get("n_parse_failures") or primary.get("n_api_errors"):
            print(
                f"[{label}] {judge_id}: {primary['n_parse_failures']} parse failure(s), "
                f"{primary['n_api_errors']} API error(s)"
            )
    usage = result.get("usage") or {}
    amount = usage.get("estimated_usd")
    if usage.get("mock_run"):
        print(f"[{label}] mock run: {usage.get('calls_logged')} judge calls logged, no network calls, no spend")
    else:
        print(
            f"[{label}] network calls {usage.get('calls_made')} (cache hits {usage.get('cache_hits')}), "
            f"estimated cost {'n/a' if amount is None else f'${amount:.4f}'}"
        )
    print(f"[{label}] report: {Path(result['run_dir']) / 'report.md'}")
    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Recompute metrics and reports from a run directory, without any model call."""
    from medeval_study.runner import rebuild_run

    try:
        result = rebuild_run(args.run_id)
    except FileNotFoundError as exc:
        print(f"[rebuild] {exc}")
        return 1
    metrics = result["metrics"]
    print(f"[rebuild] {result['n_judgements']} judgement(s) reanalysed from {result['run_dir']}")
    for judge_id, payload in (metrics.get("judges") or {}).items():
        accuracy = payload["primary_repeat"]["accuracy"]
        print(f"[rebuild] {judge_id}: accuracy {accuracy['estimate']:.3f} over {accuracy['denominator']} items")
    print("[rebuild] report.md and metrics.json rewritten; no model was called")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Regenerate a run's report.md from the verdicts on disk (no model call).

    ``medeval report exp003`` targets the newest Experiment 003 run. When the
    single reviewer has labelled items, a clearly labelled sensitivity table is
    appended: the headline numbers recomputed with ``key looks wrong`` items
    removed. The labels themselves never enter the headline metrics.
    """
    from medeval_study.registry import list_runs
    from medeval_study.runner import rebuild_run

    run_id = args.run_id
    if run_id == "exp003":
        candidates = [run for run in list_runs() if run.experiment == "003_frontier_judges"]
        if not candidates:
            print("[report] no Experiment 003 run exists yet; the experiment is built and pre-registered "
                  "but no live run has been made")
            return 2
        run_id = candidates[0].run_id
    try:
        result = rebuild_run(run_id)
    except FileNotFoundError as exc:
        print(f"[report] {exc}")
        return 1
    print(f"[report] {result['n_judgements']} judgement(s) reanalysed from {result['run_dir']}")
    print(f"[report] wrote {Path(result['run_dir']) / 'report.md'}")
    sensitivity = _append_sensitivity(run_id, Path(result["run_dir"]))
    if sensitivity:
        print(f"[report] sensitivity table appended ({sensitivity} item(s) labelled 'key looks wrong' removed)")
    print("[report] no model was called")
    return 0


def _append_sensitivity(run_id: str, run_dir: Path) -> int:
    """Append the reviewer-label sensitivity table; return how many items were removed."""
    from medeval_web.review import SENSITIVITY_LABEL, ReviewStore

    store = ReviewStore()
    removed = store.labelled_item_ids(run_id, label=SENSITIVITY_LABEL)
    if not removed:
        return 0
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    sensitivity: dict[str, Any] = {"removed_item_ids": sorted(removed), "n_removed": len(removed), "judges": {}}
    verdicts = []
    judgements = run_dir / "judgements.jsonl"
    if judgements.is_file():
        for line in judgements.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if str(row.get("item_id")) not in removed:
                    verdicts.append(row)
    primary = metrics.get("judges") or {}
    for judge_id in primary:
        rows = [row for row in verdicts if row.get("judge_id") == judge_id and row.get("repeat") == 0]
        scored = [row for row in rows if row.get("parse_ok") and row.get("verdict") in {"CORRECT", "INCORRECT"}]
        correct = sum(1 for row in scored if row.get("verdict") == "CORRECT")
        sensitivity["judges"][judge_id] = {
            "n_scored": len(scored),
            "n_correct": correct,
            "accuracy": (correct / len(scored)) if scored else None,
        }
    metrics["sensitivity_excluding_key_looks_wrong"] = sensitivity
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path = run_dir / "report.md"
    table = [
        "",
        "## Sensitivity (single-reviewer labels, local only)",
        "",
        f"One reviewer labelled {len(removed)} item(s) **{SENSITIVITY_LABEL}** while reading the review "
        "queue. This table recomputes the headline numbers with those items removed. It is a sensitivity "
        "analysis, **not** the headline result, and the labels are one person's opinion, not a reference "
        "standard; no clinician is involved in this project.",
        "",
        "| judge | scored items | correct | accuracy (sensitivity) |",
        "| --- | --- | --- | --- |",
    ]
    for judge_id, row in sensitivity["judges"].items():
        accuracy = row["accuracy"]
        table.append(
            f"| {judge_id} | {row['n_scored']} | {row['n_correct']} | "
            f"{'n/a' if accuracy is None else f'{accuracy:.1%}'} |"
        )
    table += ["", f"Items removed (ids only): {', '.join(sorted(removed))}", ""]
    if report_path.is_file():
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\n".join(table), encoding="utf-8"
        )
    return len(removed)


def cmd_export(args: argparse.Namespace) -> int:
    """Write a zip report bundle: ids and aggregates only, no text and no keys."""
    from medeval_web.export import UnsafExport, build_export_bundle

    try:
        path = build_export_bundle(
            args.run_id,
            include_notes=bool(getattr(args, "include_notes", False)),
            out_dir=Path(args.out).parent if args.out else None,
        )
    except FileNotFoundError as exc:
        print(f"[export] {exc}")
        return 1
    except UnsafExport as exc:
        print(f"[export] refused: {exc}")
        return 1
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        path = target
    print(f"[export] bundle written to {path}")
    print("[export] contents: metrics.json, items.csv (ids only), review_queue.csv (ids only), "
          "manifest.json, report.md, README.txt")
    if getattr(args, "include_notes", False):
        print("[export] reviewer notes included at your request; they are one reviewer's opinion and may "
              "quote item text")
    else:
        print("[export] reviewer notes excluded")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the environment without printing or storing any secret."""
    from medeval_web.doctor import run_doctor

    return run_doctor(port=args.port, host=args.host, use_env=args.use_env)


def _dataset_alias(name: str) -> str:
    """Accept the shorthand people type (``medqa``) as well as the registry key."""
    aliases = {"medqa": "medqa_4options", "medqa4": "medqa_4options"}
    return aliases.get((name or "").strip().lower(), name)


def cmd_data_fetch(args: argparse.Namespace) -> int:
    """Download and cache a dataset once, and show the licence it is used under.

    This is a data download, not a model call: it costs nothing and touches no
    provider API. A dry run does not need it (it falls back to a labelled
    synthetic sample), but every real run does.
    """
    from medeval_study.datasets import DATASET_LICENCES, DatasetUnavailable, load_mcq_items

    dataset = _dataset_alias(args.dataset)
    split = args.split
    if dataset not in DATASET_LICENCES:
        print(f"[data] unknown dataset {dataset!r}; known: {', '.join(sorted(DATASET_LICENCES))}")
        return 2
    licence = DATASET_LICENCES[dataset]
    print(f"[data] fetching {dataset}/{split} from {licence.hf_repo}")
    print(f"[data] licence: {licence.licence} (redistribute: {'yes' if licence.redistribute else 'no'})")
    if licence.licence_evidence:
        print(f"[data] licence evidence: {licence.licence_evidence}")
    from medeval_study.datasets import download_dataset_access

    try:
        with download_dataset_access():
            bundle = load_mcq_items(dataset, split, allow_download=True)
    except DatasetUnavailable as exc:
        print(f"[data] could not fetch {dataset}/{split}: {exc}")
        print("[data] install the loader once with: .\\.venv\\Scripts\\python.exe -m pip install datasets")
        print("[data] then run this command again. Dry runs do not need the data.")
        return 1
    print(f"[data] cached {bundle.n} usable items ({bundle.n_dropped} dropped, via {bundle.strategy})")
    print(f"[data] source: {bundle.source_detail}")
    print("[data] item text stays in the git-ignored dataset cache; run records keep ids only")
    print("[data] next: .\\.venv\\Scripts\\python.exe -m medeval run exp003 --dry-run")
    return 0


def cmd_data_status(args: argparse.Namespace) -> int:
    """Show each known dataset, its licence, and whether it is already cached."""
    from medeval_study import synthetic  # noqa: F401 - registers the built-in sample's licence
    from medeval_study.datasets import DATASET_LICENCES, DatasetUnavailable, load_mcq_items

    failures = 0
    for key, licence in sorted(DATASET_LICENCES.items()):
        if key.startswith("synthetic"):
            print(f"{key:<22} built in        {licence.licence}")
            continue
        try:
            bundle = load_mcq_items(key, "dev", allow_download=False)
        except DatasetUnavailable:
            failures += 1
            print(f"{key:<22} NOT CACHED      {licence.licence}  ->  medeval data fetch {key}")
        except Exception as exc:
            failures += 1
            print(f"{key:<22} unavailable     {type(exc).__name__}: {exc}")
        else:
            print(f"{key:<22} cached {bundle.n:>5}   {licence.licence}")
    return 0  # a status listing is informational whatever it finds


def cmd_ui(args: argparse.Namespace) -> int:
    from medeval_web.server import serve

    return serve(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        use_env=args.use_env,
        allow_remote=args.allow_remote,
    )


def cmd_backend(args: argparse.Namespace) -> int:
    settings = load_settings()
    selection = select_backend(settings)
    print(
        json.dumps(
            {
                "settings": settings.safe_dict(),
                "backend": selection.as_dict(),
                "prices": {
                    "configured": bool(settings.input_usd_per_million is not None and settings.output_usd_per_million is not None),
                    "source_url": settings.price_source_url,
                    "source_date": settings.price_source_date,
                },
            },
            indent=2,
        )
    )
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    print(f"medeval-lab {__version__}")
    print(
        f"criteria {CRITERIA_VERSION} | metrics {METRICS_VERSION} | rubric {RUBRIC_VERSION} | items {ITEMS_VERSION}"
    )
    _print_backend()
    failures: list[str] = []

    print("\n== 1/6 validate seed items ==")
    if cmd_validate(argparse.Namespace(paths=[str(items_dir() / "seed_v0.1.json")])) != 0:
        failures.append("validate")

    print("\n== 2/6 traceability ==")
    if cmd_traceability(argparse.Namespace()) != 0:
        failures.append("traceability")

    if not args.skip_audit:
        print("\n== 3/6 benchmark audit ==")
        if cmd_audit(argparse.Namespace(datasets=["medmcqa", "medqa_4options", "pubmedqa"], threshold=0.85, offline=False)) != 0:
            failures.append("audit")
    else:
        print("\n== 3/6 benchmark audit skipped ==")

    if not args.skip_harness:
        print("\n== 4/6 harness baseline ==")
        try:
            if cmd_harness(argparse.Namespace(limit=args.limit, tasks=["medmcqa_medeval", "medqa_4options_medeval"])) != 0:
                failures.append("harness")
        except Exception as exc:
            print(f"[harness] FAILED: {type(exc).__name__}: {exc}")
            failures.append("harness")
    else:
        print("\n== 4/6 harness baseline skipped ==")

    print("\n== 5/6 experiment 001 ==")
    if cmd_experiment001(argparse.Namespace()) != 0:
        failures.append("experiment001")

    print("\n== 6/6 experiment 002 dry run (no paid calls) ==")
    dry_args = argparse.Namespace(
        experiment="exp002",
        n_items=args.exp002_items,
        seed=20260101,
        repeats=3,
        temperature=0.3,
        max_api_calls=500,
        dataset="medqa_4options",
        split="dev",
        no_download=False,
        dry_run=True,
        judges=[],
        connection_judges=(),
        connections=None,
        confirm_live=False,
        max_usd=None,
        consistency_subsample=0,
        screening_size=600,
        resume=None,
        resume_latest=False,
        json=False,
    )
    if cmd_run(dry_args) != 0:
        failures.append("experiment002-dry-run")

    print("\n== summary ==")
    if failures:
        print("FAILED stages: " + ", ".join(failures))
        return 1
    print("all stages completed")
    print(f"results directory: {results_dir()}")
    return 0


def cmd_paper_list(args: argparse.Namespace) -> int:
    """List runnable Paper Packs (and, optionally, archived study runs)."""
    from medeval_packs.registry import archived_studies, list_packs

    entries = list_packs()
    if getattr(args, "include_archived", False):
        entries = entries + archived_studies()
    if not entries:
        print("[paper] no Paper Packs found under papers/; create one with: medeval paper init my-pack --task mcq")
        return 0
    if getattr(args, "json", False):
        print(json.dumps([entry.as_dict() for entry in entries], indent=2, ensure_ascii=False))
        return 0
    for entry in entries:
        status = entry.validation.status if entry.runnable else "read-only" if entry.kind == "archived_study" else "invalid"
        kind = "preset" if entry.preset else "pack"
        print(
            f"[paper] {entry.id}  v{entry.version or '—'}  {entry.task_type:24s} "
            f"{status:9s} items={entry.dataset_count if entry.dataset_count is not None else '—'}  "
            f"[{kind}] {entry.title}"
        )
        if entry.validation.errors:
            for error in entry.validation.errors[:5]:
                print(f"[paper]   ERROR {error}")
        if entry.validation.warnings:
            for warning in entry.validation.warnings[:3]:
                print(f"[paper]   WARN  {warning}")
    return 0


def cmd_paper_validate(args: argparse.Namespace) -> int:
    """Validate one or more packs by id or path, printing every problem."""
    from medeval_packs.registry import list_packs, resolve_pack
    from medeval_packs.schema import PackValidationError

    identifiers = args.packs or [entry.id for entry in list_packs()]
    if not identifiers:
        print("[paper] nothing to validate: no packs found and no path given")
        return 1
    failures = 0
    for identifier in identifiers:
        try:
            pack = resolve_pack(identifier)
        except PackValidationError as exc:
            failures += 1
            print(f"[paper] {identifier}: INVALID")
            for error in exc.errors or [str(exc)]:
                print(f"[paper]   ERROR {error}")
            continue
        validation = pack.validate()
        print(f"[paper] {pack.id}: {validation.status.upper()} ({len(validation.errors)} errors, "
              f"{len(validation.warnings)} warnings)")
        for error in validation.errors:
            print(f"[paper]   ERROR {error}")
        for warning in validation.warnings:
            print(f"[paper]   WARN  {warning}")
        if not validation.ok:
            failures += 1
    return 1 if failures else 0


def cmd_paper_init(args: argparse.Namespace) -> int:
    """Scaffold a documented starter pack (never overwrites a non-empty directory)."""
    from pathlib import Path as _Path

    from medeval_packs.registry import init_pack
    from medeval_packs.schema import PackValidationError

    try:
        destination = init_pack(
            args.slug,
            args.task,
            _Path(args.dest) if getattr(args, "dest", None) else None,
        )
    except PackValidationError as exc:
        print(f"[paper] could not create pack: {exc}")
        return 2
    print(f"[paper] created {destination}")
    print("[paper] next: edit paper.yaml and data/items.jsonl, then run:")
    print(f"[paper]   python -m medeval paper validate {args.slug}")
    print(f"[paper]   python -m medeval matrix run --paper {args.slug} --dry-run")
    print("[paper] template data is PIPELINE TEST / TEMPLATE DATA, not clinical evidence")
    return 0


def _mock_targets(count: int = 2) -> tuple[Any, ...]:
    from medeval_packs.runner import ModelBinding

    labels = ["Target A", "Target B", "Target C", "Target D"]
    return tuple(
        ModelBinding(
            id=f"target-{chr(97 + index)}",
            label=labels[index] if index < len(labels) else f"Target {index + 1}",
            model=f"mock-target-{chr(97 + index)}",
            role="target",
        )
        for index in range(max(1, count))
    )


def _mock_judges(count: int = 2) -> tuple[Any, ...]:
    from medeval_packs.runner import ModelBinding

    return tuple(
        ModelBinding(
            id=f"judge-{chr(97 + index)}",
            label=f"Judge {chr(65 + index)}",
            model=f"mock-judge-{chr(97 + index)}",
            family=f"mock-family-{index + 1}",
            role="judge",
        )
        for index in range(max(1, count))
    )


def _bindings_from_connections(connections: Any, role: str, wanted: Sequence[str]) -> tuple[Any, ...]:
    from medeval_packs.runner import ModelBinding

    rows = connections.targets if role == "target" else connections.judges
    if wanted:
        by_name = {row.name: row for row in rows}
        by_id = {row.judge_id: row for row in rows}
        selected = []
        for name in wanted:
            row = by_name.get(name) or by_id.get(name)
            if row is None:
                raise ValueError(f"no connection named {name!r} with role {role!r}")
            selected.append(row)
        rows = tuple(selected)
    return tuple(ModelBinding.from_mapping(row.as_binding(), role=role) for row in rows)


def cmd_matrix_run(args: argparse.Namespace) -> int:
    """Run 1 Paper Pack x N target models x M graders/judges."""
    from medeval_packs.registry import resolve_pack
    from medeval_packs.runner import MatrixConfig, MatrixRun
    from medeval_packs.schema import PackValidationError

    try:
        pack = resolve_pack(args.paper)
    except PackValidationError as exc:
        print(f"[matrix] {exc}")
        return 2
    settings = load_settings()
    connections: Any = None
    if getattr(args, "connections", None):
        connections, failure = _connections_from_args(args)
        if failure is not None:
            return failure
    dry_run = bool(args.dry_run or not args.confirm_live)
    try:
        if connections is not None:
            targets = _bindings_from_connections(connections, "target", args.target or ())
            judges = _bindings_from_connections(connections, "judge", args.judge or ())
            missing = connections.missing_keys()
            if missing:
                print("[matrix] these environment variables are named by the connections file but are not set: "
                      + ", ".join(missing))
                return 2
        else:
            if not dry_run:
                print("[matrix] a live run needs --connections (see connections.example.yaml); nothing was called.")
                return 2
            targets = _mock_targets(args.mock_targets)
            rubric_pack = any(grader.type == "llm_rubric" for grader in pack.graders)
            judges = _mock_judges(args.mock_judges) if args.mock_judges and rubric_pack else ()
        if pack.graders and any(grader.type == "llm_rubric" for grader in pack.graders) and not judges:
            print("[matrix] this pack has a rubric judge but no judge connection was selected; add one or use "
                  "--mock-judges for a dry run")
            return 2
        config = MatrixConfig(
            targets=tuple(targets),
            judges=tuple(judges),
            dry_run=dry_run,
            stage=args.stage,
            n_items=args.n_items,
            seed=args.seed,
            max_calls=args.max_calls or settings.max_calls,
            max_usd=args.max_usd if args.max_usd is not None else settings.max_usd,
            run_id=args.run_id,
            resume=not args.no_resume,
        )
        run = MatrixRun(pack, config)
        plan = run.plan()
    except (PackValidationError, ValueError) as exc:
        print(f"[matrix] {exc}")
        return 2
    print(f"[matrix] paper {pack.id} v{pack.version} ({pack.task_type})")
    print(f"[matrix] mode: {'DRY RUN - PIPELINE TEST ONLY' if dry_run else 'LIVE'}")
    print(f"[matrix] items {plan['n_items']} x targets {len(targets)} x judges {len(judges)}")
    print(f"[matrix] planned calls: {plan['total_calls']} "
          f"({plan['target_calls']} target, {plan['judge_calls']} judge); cached target outputs: "
          f"{plan['n_cached_target_outputs']}")
    estimate = plan.get("usd_upper_bound")
    print(f"[matrix] worst-case cost: {'unavailable (a connection declares no prices; call cap is binding)' if estimate is None else f'US${estimate:.4f}'}")
    print(f"[matrix] caps: {config.max_calls} calls, US${config.max_usd:.2f}")
    if not dry_run and not args.confirm_live:
        print("[matrix] nothing was called. Re-run with --confirm-live to make these calls.")
        return 2
    result = run.execute()
    print(f"[matrix] status: {result['status']}")
    print(f"[matrix] run directory: {result['run_dir']}")
    if result.get("reason"):
        print(f"[matrix] reason: {result['reason']}")
    for target_id, row in (result["metrics"].get("targets") or {}).items():
        interval = (row.get("primary") or {}).get("ci95") or {}
        print(
            f"[matrix] {target_id}: {row['primary_metric']} {row['primary']['estimate']:.3f} "
            f"[{interval.get('low', float('nan')):.3f}, {interval.get('high', float('nan')):.3f}] "
            f"over {row['n_items']} items"
        )
    print(f"[matrix] report: {result['paths']['report']}")
    print(f"[matrix] bundle: {result['paths']['bundle']}")
    if args.json:
        print(json.dumps(result["metrics"], indent=2, ensure_ascii=False))
    return 0 if result["status"] in {"completed", "stopped", "budget_stopped"} else 1


def cmd_matrix_list(args: argparse.Namespace) -> int:
    """List matrix runs with status and headline metric."""
    from medeval_packs.runner import default_matrix_root

    root = default_matrix_root()
    rows = []
    if root.is_dir():
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            state_path = directory / "run.json"
            if not state_path.is_file():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append(state)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("[matrix] no matrix runs yet; start with: medeval matrix run --paper template-clinical-mcq --dry-run")
        return 0
    for state in rows:
        paper = state.get("paper") or {}
        print(f"[matrix] {state.get('run_id')}  {state.get('status'):14s}  "
              f"{paper.get('id', '?')} v{paper.get('version', '?')}  "
              f"{'PIPELINE TEST ONLY' if state.get('pipeline_test_only') else 'live'}")
    return 0


def cmd_matrix_show(args: argparse.Namespace) -> int:
    """Print a matrix run's manifest, metrics and export paths."""
    from medeval_packs.runner import load_matrix_run

    payload = load_matrix_run(args.run_id)
    if payload is None:
        print(f"[matrix] unknown run {args.run_id!r}")
        return 1
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    run = payload["run"]
    paper = run.get("paper") or {}
    print(f"[matrix] {run.get('run_id')} status={run.get('status')}")
    print(f"[matrix] paper {paper.get('id')} v{paper.get('version')} ({paper.get('task_type')})")
    print(f"[matrix] pack hash {paper.get('pack_hash')}")
    print(f"[matrix] pipeline test only: {run.get('pipeline_test_only')}"
          + (" (PIPELINE TEST ONLY)" if run.get("pipeline_test_only") else ""))
    metrics = payload.get("metrics") or {}
    for target_id, row in (metrics.get("targets") or {}).items():
        interval = (row.get("primary") or {}).get("ci95") or {}
        print(f"[matrix] {target_id}: {row['primary_metric']} {row['primary']['estimate']:.3f} "
              f"[{interval.get('low', float('nan')):.3f}, {interval.get('high', float('nan')):.3f}] "
              f"n={row['n_items']}")
    print(f"[matrix] report: {payload['paths']['report']}")
    print(f"[matrix] bundle: {payload['paths']['bundle']}")
    return 0


def cmd_regrade(args: argparse.Namespace) -> int:
    """Re-score cached target outputs with a new pack/grader/judge set."""
    from medeval_packs.registry import resolve_pack
    from medeval_packs.runner import load_matrix_run, regrade_run
    from medeval_packs.schema import PackValidationError

    payload = load_matrix_run(args.run_id)
    if payload is None:
        print(f"[regrade] unknown matrix run {args.run_id!r}")
        return 1
    paper_id = args.paper or (payload["run"].get("paper") or {}).get("id")
    try:
        pack = resolve_pack(paper_id)
    except PackValidationError as exc:
        print(f"[regrade] {exc}")
        return 2
    judges: tuple[Any, ...] = ()
    dry_run = True
    if getattr(args, "connections", None):
        connections, failure = _connections_from_args(args)
        if failure is not None:
            return failure
        try:
            judges = _bindings_from_connections(connections, "judge", args.judge or ())
        except ValueError as exc:
            print(f"[regrade] {exc}")
            return 2
        dry_run = not args.confirm_live
        if not dry_run and connections.missing_keys():
            print("[regrade] these environment variables are named by the connections file but are not set: "
                  + ", ".join(connections.missing_keys()))
            return 2
    elif any(grader.type == "llm_rubric" for grader in pack.graders):
        judges = _mock_judges(2)
        print("[regrade] no --connections given: rubric judges use labelled mock stand-ins (PIPELINE TEST ONLY)")
    if not dry_run and not args.confirm_live:
        print("[regrade] nothing was called. Re-run with --confirm-live to call live judges.")
        return 2
    result = regrade_run(
        args.run_id,
        pack,
        judges=judges,
        dry_run=dry_run,
        root=None,
    )
    print(f"[regrade] {args.run_id} re-scored as {result['run_id']} (target calls: 0)")
    print(f"[regrade] status: {result['status']}")
    for target_id, row in (result["metrics"].get("targets") or {}).items():
        print(f"[regrade] {target_id}: {row['primary_metric']} {row['primary']['estimate']:.3f} over {row['n_items']} items")
    print(f"[regrade] report: {result['paths']['report']}")
    print(f"[regrade] bundle: {result['paths']['bundle']}")
    if args.json:
        print(json.dumps(result["metrics"], indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medeval", description="medeval-lab: Medical AI Evaluation Lab")
    parser.add_argument(
        "--version",
        action="version",
        version=f"medeval-lab {__version__}",
        help="print the version and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_all = sub.add_parser("all", help="run the full pipeline")
    p_all.add_argument("--limit", type=int, default=20, help="items per task in the harness baseline")
    p_all.add_argument("--exp002-items", type=int, default=8, help="items in the experiment 002 dry run")
    p_all.add_argument("--skip-audit", action="store_true")
    p_all.add_argument("--skip-harness", action="store_true")
    p_all.set_defaults(func=cmd_all)

    p_validate = sub.add_parser("validate", help="validate item files")
    p_validate.add_argument("paths", nargs="*")
    p_validate.set_defaults(func=cmd_validate)

    p_trace = sub.add_parser("traceability", help="check paper -> criterion -> metric links")
    p_trace.set_defaults(func=cmd_traceability)

    p_audit = sub.add_parser("audit", help="audit public benchmark items")
    p_audit.add_argument("--datasets", nargs="+", default=["medmcqa", "medqa_4options", "pubmedqa"])
    p_audit.add_argument("--threshold", type=float, default=0.85)
    p_audit.add_argument("--offline", action="store_true", help="do not download missing datasets")
    p_audit.set_defaults(func=cmd_audit)

    p_harness = sub.add_parser("harness", help="run the lm-evaluation-harness baseline")
    p_harness.add_argument("--limit", type=int, default=20)
    p_harness.add_argument("--tasks", nargs="+", default=["medmcqa_medeval", "medqa_4options_medeval"])
    p_harness.set_defaults(func=cmd_harness)

    p_exp = sub.add_parser("experiment001", help="run experiment 001")
    p_exp.set_defaults(func=cmd_experiment001)

    p_exp2 = sub.add_parser("exp002a", help="archived pairwise protocol (no real-judge result)")
    p_exp2.add_argument("--dry-run", action="store_true", help="show planned calls/tokens without contacting an API")
    p_exp2.add_argument("--rule-only", action="store_true", help="score the frozen rule judge without an API")
    p_exp2.set_defaults(func=cmd_experiment002a)

    p_run = sub.add_parser("run", help="run an experiment end to end")
    p_run.add_argument("experiment", nargs="?", default="exp002", choices=["exp002", "exp003"])
    p_run.add_argument("--dry-run", action="store_true", help="mock judges, no network calls")
    p_run.add_argument("--n-items", type=int, default=60, help="items to sample (default 60)")
    p_run.add_argument("--seed", type=int, default=20260101)
    p_run.add_argument("--repeats", type=int, default=3, help="repeats per judge per item")
    p_run.add_argument("--temperature", type=float, default=0.3)
    p_run.add_argument("--max-api-calls", type=int, default=500, help="hard cap on paid/local network calls")
    p_run.add_argument("--dataset", default="medqa_4options")
    p_run.add_argument("--split", default="dev")
    p_run.add_argument("--no-download", action="store_true")
    p_run.add_argument(
        "--connections",
        default=None,
        metavar="FILE",
        help=(
            "headless bring-your-own-key file (see connections.example.yaml). Each entry names the "
            "environment variable that holds its key; the key is never in the file or on the command line"
        ),
    )
    p_run.add_argument(
        "--confirm-live",
        action="store_true",
        help="required for a live run with --connections: the plan and worst-case cost are printed first",
    )
    p_run.add_argument(
        "--max-usd",
        type=float,
        default=None,
        metavar="USD",
        help="run-wide spend cap, in USD (default: MEDEVAL_MAX_USD or 5)",
    )
    p_run.add_argument(
        "--judges",
        nargs="+",
        default=None,
        metavar="ID",
        help=(
            "ARCHIVED Experiment 002 configuration only: the original judge ids "
            "(j1_deepseek j2_ollama). New runs use --connections or the app's Models page; "
            "a dry run without either uses two labelled mock judges"
        ),
    )
    p_run.add_argument(
        "--consistency-subsample",
        type=int,
        default=0,
        help="run 3 repeats on N items only, for consistency without tripling cost (0 = off; suggested 20)",
    )
    p_run.add_argument(
        "--screening-size",
        type=int,
        default=600,
        help="Experiment 003: items answered closed-book by the screening model",
    )
    p_run.add_argument("--resume", default=None, help="resume the given run id")
    p_run.add_argument("--run-id", default=None, help="explicit run id (default: generated)")
    p_run.add_argument("--resume-latest", action="store_true", help="resume the newest incomplete run")
    p_run.add_argument("--json", action="store_true", help="print the metrics payload")
    p_run.set_defaults(func=cmd_run)

    p_paper = sub.add_parser("paper", help="inspect, validate and scaffold Paper Packs")
    paper_sub = p_paper.add_subparsers(dest="paper_action", required=True)
    p_paper_list = paper_sub.add_parser("list", help="list discovered Paper Packs")
    p_paper_list.add_argument("--include-archived", action="store_true", help="also show archived study runs (read-only)")
    p_paper_list.add_argument("--json", action="store_true")
    p_paper_list.set_defaults(func=cmd_paper_list)
    p_paper_validate = paper_sub.add_parser("validate", help="validate one or more packs by id or path")
    p_paper_validate.add_argument("packs", nargs="*", help="pack ids or paths (default: every pack)")
    p_paper_validate.set_defaults(func=cmd_paper_validate)
    p_paper_init = paper_sub.add_parser("init", help="scaffold a new Paper Pack")
    p_paper_init.add_argument("slug")
    p_paper_init.add_argument(
        "--task",
        required=True,
        choices=["mcq", "open_ended_rubric", "structured_extraction", "triage_classification",
                 "multi_judge_comparison"],
    )
    p_paper_init.add_argument("--dest", default=None, help="destination directory (default: papers/<slug>)")
    p_paper_init.set_defaults(func=cmd_paper_init)

    p_matrix = sub.add_parser("matrix", help="run 1 Paper Pack x N target models x M graders")
    matrix_sub = p_matrix.add_subparsers(dest="matrix_action", required=True)
    p_matrix_run = matrix_sub.add_parser("run", help="run a Paper Pack")
    p_matrix_run.add_argument("--paper", required=True, help="Paper Pack id or path")
    p_matrix_run.add_argument("--dry-run", action="store_true", help="mock targets/judges; no network calls")
    p_matrix_run.add_argument("--confirm-live", action="store_true", help="required to dispatch real provider calls")
    p_matrix_run.add_argument("--connections", default=None, metavar="FILE")
    p_matrix_run.add_argument("--target", nargs="+", default=None, help="connection name(s) with role: target")
    p_matrix_run.add_argument("--judge", "--judges", dest="judge", nargs="+", default=None, help="connection name(s) with role: judge")
    p_matrix_run.add_argument("--mock-targets", type=int, default=2, help="dry-run target stand-ins (default 2)")
    p_matrix_run.add_argument("--mock-judges", type=int, default=2, help="dry-run judge stand-ins (default 2)")
    p_matrix_run.add_argument("--n-items", type=int, default=None, help="limit items (after pack sampling)")
    p_matrix_run.add_argument("--stage", default="full", choices=["screening", "full"])
    p_matrix_run.add_argument("--seed", type=int, default=20260101)
    p_matrix_run.add_argument("--max-calls", type=int, default=None)
    p_matrix_run.add_argument("--max-usd", type=float, default=None)
    p_matrix_run.add_argument("--run-id", default=None)
    p_matrix_run.add_argument("--no-resume", action="store_true", help="refuse to reuse an existing run directory")
    p_matrix_run.add_argument("--json", action="store_true")
    p_matrix_run.set_defaults(func=cmd_matrix_run)
    p_matrix_list = matrix_sub.add_parser("list", help="list local matrix runs")
    p_matrix_list.add_argument("--json", action="store_true")
    p_matrix_list.set_defaults(func=cmd_matrix_list)
    p_matrix_show = matrix_sub.add_parser("show", help="show one matrix run")
    p_matrix_show.add_argument("run_id")
    p_matrix_show.add_argument("--json", action="store_true")
    p_matrix_show.set_defaults(func=cmd_matrix_show)

    p_regrade = sub.add_parser(
        "regrade", help="re-score cached target outputs with a new rubric/grader/judge (no target calls)"
    )
    p_regrade.add_argument("run_id")
    p_regrade.add_argument("--paper", default=None, help="Paper Pack id or path (default: the run's pack)")
    p_regrade.add_argument("--connections", default=None, metavar="FILE")
    p_regrade.add_argument("--judge", "--judges", dest="judge", nargs="+", default=None)
    p_regrade.add_argument("--confirm-live", action="store_true", help="allow live judge calls")
    p_regrade.add_argument("--json", action="store_true")
    p_regrade.set_defaults(func=cmd_regrade)

    p_ui = sub.add_parser("ui", help="launch the local web app")
    p_ui.add_argument("--host", default="127.0.0.1", help="bind address; loopback only unless --allow-remote")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--no-browser", action="store_true")
    p_ui.add_argument(
        "--use-env",
        action="store_true",
        help="load the git-ignored .env (off by default; keys normally come from the Models page, in memory)",
    )
    p_ui.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit a non-loopback --host; prints a loud warning. The app is single-user and has no TLS",
    )
    p_ui.set_defaults(func=cmd_ui)

    p_rebuild = sub.add_parser("rebuild", help="reanalyse a run without calling a model")
    p_rebuild.add_argument("run_id")
    p_rebuild.set_defaults(func=cmd_rebuild)

    p_report = sub.add_parser(
        "report", help="regenerate a run's report.md from stored verdicts (no model call)"
    )
    p_report.add_argument("run_id", nargs="?", default="exp003", help="run id, or 'exp003' for the newest one")
    p_report.set_defaults(func=cmd_report)

    p_export = sub.add_parser("export", help="write a shareable zip bundle for a run")
    p_export.add_argument("run_id")
    p_export.add_argument("--out", default=None, help="zip path (default: results/exports/<run>-bundle.zip)")
    p_export.add_argument(
        "--include-notes",
        action="store_true",
        help="include reviewer notes (one reviewer's opinion; may quote item text)",
    )
    p_export.set_defaults(func=cmd_export)

    p_data = sub.add_parser("data", help="inspect and fetch evaluation datasets")
    data_sub = p_data.add_subparsers(dest="data_action")
    p_fetch = data_sub.add_parser("fetch", help="download and cache a dataset, showing its licence")
    p_fetch.add_argument("dataset", nargs="?", default="medqa_4options")
    p_fetch.add_argument("--split", default="dev")
    p_fetch.set_defaults(func=cmd_data_fetch)
    p_status = data_sub.add_parser("status", help="show which datasets are cached")
    p_status.set_defaults(func=cmd_data_status)

    p_doctor = sub.add_parser("doctor", help="check the environment without touching any secret")
    p_doctor.add_argument("--port", type=int, default=8765)
    p_doctor.add_argument("--host", default="127.0.0.1", help="the bind address you intend to use (for the safety check)")
    p_doctor.add_argument("--use-env", action="store_true", help="check as if medeval ui ran with --use-env")
    p_doctor.set_defaults(func=cmd_doctor)

    p_backend = sub.add_parser("backend", help="show resolved model backend")
    p_backend.set_defaults(func=cmd_backend)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "data" and getattr(args, "data_action", None) is None:
        args.func = cmd_data_status
    return int(args.func(args))


def _configure_stdio() -> None:
    """Make Windows consoles print UTF-8 instead of crashing on ``α`` or ``→``."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover - non-standard streams
            continue


if __name__ == "__main__":
    raise SystemExit(main())
