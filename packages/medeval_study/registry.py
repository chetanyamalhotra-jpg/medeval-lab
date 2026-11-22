"""Discovery of completed and in-progress study runs, for the CLI and the web app.

Runs live in ``results/study_runs/<run_id>/``. Everything here is read-only and
tolerant of a half-written run: a browser that is opened while a run is still
going must not crash because ``metrics.json`` does not exist yet.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medeval_core.paths import results_dir

__all__ = [
    "RunSummary",
    "item_table",
    "list_runs",
    "load_json",
    "load_manifest",
    "load_metrics",
    "load_report",
    "load_run",
    "run_root",
]


def run_root() -> Path:
    return results_dir() / "study_runs"


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
    except OSError:
        return


@dataclass(frozen=True)
class RunSummary:
    """One row in the runs list."""

    run_id: str
    path: Path
    status: str
    experiment: str
    started_utc: str | None
    finished_utc: str | None
    n_items: int | None
    judges: tuple[str, ...]
    pipeline_test_only: bool
    headline: dict[str, Any]
    unavailable_judges: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "experiment": self.experiment,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "n_items": self.n_items,
            "judges": list(self.judges),
            "unavailable_judges": list(self.unavailable_judges),
            "pipeline_test_only": self.pipeline_test_only,
            "headline": self.headline,
        }


#: Only these outcomes produced complete evidence; a number from an aborted or
#: refused run must never be shown as if it were a result.
_REPORTABLE_STATUSES = {"completed", "stopped"}


def _headline(metrics: dict[str, Any] | None, status: str) -> dict[str, Any]:
    if not metrics or status not in _REPORTABLE_STATUSES:
        return {}
    out: dict[str, Any] = {}
    for judge_id, payload in (metrics.get("judges") or {}).items():
        accuracy = ((payload or {}).get("primary_repeat") or {}).get("accuracy") or {}
        out[judge_id] = {
            "accuracy": accuracy.get("estimate"),
            "ci95": accuracy.get("ci95_wilson"),
            "kappa": ((payload or {}).get("primary_repeat") or {}).get("cohen_kappa_vs_key"),
        }
    return out


def list_runs(root: Path | None = None) -> list[RunSummary]:
    """Newest first; tolerant of missing files."""
    base = root or run_root()
    if not base.is_dir():
        return []
    summaries: list[RunSummary] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        state = load_json(child / "run.json") or {}
        metrics = load_json(child / "metrics.json")
        config = state.get("config") or {}
        specs = state.get("judges") or []
        status = state.get("status", "unknown")
        judges = [row.get("judge_id") for row in specs if row.get("judge_id") and row.get("available", True)]
        unavailable = [row.get("judge_id") for row in specs if row.get("judge_id") and not row.get("available", True)]
        summaries.append(
            RunSummary(
                run_id=state.get("run_id", child.name),
                path=child,
                status=status,
                experiment=state.get("experiment", config.get("experiment", "unknown")),
                started_utc=state.get("started_utc"),
                finished_utc=state.get("finished_utc"),
                n_items=config.get("n_items"),
                judges=tuple(judges),
                unavailable_judges=tuple(unavailable),
                pipeline_test_only=bool((metrics or {}).get("pipeline_test_only") or config.get("dry_run")),
                headline=_headline(metrics, status),
            )
        )
    summaries.sort(key=lambda row: (row.started_utc or "", row.run_id), reverse=True)
    return summaries


def load_run(run_id: str, root: Path | None = None) -> dict[str, Any] | None:
    base = (root or run_root()) / run_id
    if not base.is_dir():
        return None
    return {
        "run": load_json(base / "run.json") or {},
        "manifest": load_json(base / "manifest.json") or {},
        "metrics": load_json(base / "metrics.json"),
        "path": str(base),
        "files": sorted(path.name for path in base.iterdir() if path.is_file()),
    }


def load_manifest(run_id: str, root: Path | None = None) -> dict[str, Any]:
    return load_json((root or run_root()) / run_id / "manifest.json", {}) or {}


def load_metrics(run_id: str, root: Path | None = None) -> dict[str, Any] | None:
    return load_json((root or run_root()) / run_id / "metrics.json")


def load_report(run_id: str, root: Path | None = None) -> str | None:
    path = (root or run_root()) / run_id / "report.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def item_table(run_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    """Join items, candidates and judgements into rows for the item explorer.

    The item text is read from the local run directory only; it is never part of
    the committed artefacts because the benchmark licence does not allow
    redistribution.
    """
    base = (root or run_root()) / run_id
    items = {row["item_id"]: row for row in _read_jsonl(base / "items.jsonl")}
    candidates = {row["item_id"]: row for row in _read_jsonl(base / "candidates.jsonl")}
    verdicts: dict[str, list[dict[str, Any]]] = {}
    for row in _read_jsonl(base / "judgements.jsonl"):
        verdicts.setdefault(row["item_id"], []).append(row)

    metrics = load_json(base / "metrics.json") or {}
    flagged = set((metrics.get("possible_key_errors") or {}).get("flagged_item_ids") or [])

    rows: list[dict[str, Any]] = []
    for item_id, item in items.items():
        candidate = candidates.get(item_id) or {}
        item_verdicts = sorted(verdicts.get(item_id, []), key=lambda row: (row["judge_id"], row["repeat"]))
        by_judge: dict[str, list[dict[str, Any]]] = {}
        for verdict in item_verdicts:
            by_judge.setdefault(verdict["judge_id"], []).append(verdict)
        rows.append(
            {
                "item_id": item_id,
                "question": item.get("question", ""),
                "options": item.get("options", {}),
                "key_letter": item.get("key_letter"),
                "key_text": item.get("key_text"),
                "condition": candidate.get("condition"),
                "chosen_letter": candidate.get("chosen_letter"),
                "chosen_text": candidate.get("chosen_text"),
                "candidate_text": candidate.get("text", ""),
                "candidate_correct": candidate.get("chosen_letter") == item.get("key_letter"),
                "judges": {
                    judge: {
                        "verdict": rows_[0].get("verdict"),
                        "confidence": rows_[0].get("confidence"),
                        "parse_ok": rows_[0].get("parse_ok"),
                        "repeats": [row.get("verdict") for row in rows_],
                        "confidences": [row.get("confidence") for row in rows_],
                    }
                    for judge, rows_ in by_judge.items()
                },
                "flagged": item_id in flagged,
            }
        )
    rows.sort(key=lambda row: row["item_id"])
    return rows
