"""Harness baseline runner (Python API, not the CLI).

Using ``lm_eval.simple_evaluate`` avoids the CLI's console-encoding and exit-code
issues on Windows, and gives us the raw per-sample metrics to store ourselves.
The upstream checkout is never modified: tasks come from ``data/tasks`` via
``--include_path`` semantics (``TaskManager(include_path=...)``) and metrics from
the installed entry points.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medeval_core.paths import repo_root, results_dir

__all__ = ["DEFAULT_TASKS", "run_baseline"]

DEFAULT_TASKS = ("medmcqa_medeval", "medqa_4options_medeval")


def run_baseline(
    tasks: Sequence[str] = DEFAULT_TASKS,
    *,
    limit: int | None = 20,
    out_dir: Path | None = None,
    include_path: Path | None = None,
    model: str = "dummy",
) -> dict[str, Any]:
    """Evaluate a model on the medeval task variants and save the results.

    ``model="dummy"`` produces deterministic pseudo-logits; it is a smoke test of
    the task/metric wiring, not a performance measurement of anything.
    """
    import lm_eval
    from lm_eval.tasks import TaskManager

    root = repo_root()
    out_dir = out_dir or (results_dir() / "harness_runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    include = include_path or (root / "data" / "tasks")

    for task in tasks:
        yaml_path = include / f"{task}.yaml"
        if not yaml_path.is_file():
            raise FileNotFoundError(
                f"task file not found: {yaml_path}. Run from the repository root so the "
                "include path resolves."
            )
        text = yaml_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("test: data/cache/"):
                data_file = root / line.split("test:", 1)[1].strip()
                if not data_file.is_file():
                    raise FileNotFoundError(
                        f"dataset snapshot missing: {data_file}. Run `python -m medeval audit` "
                        "first: it writes the pinned JSONL snapshots the tasks use."
                    )
    task_manager = TaskManager(include_path=str(include))
    results = lm_eval.simple_evaluate(
        model=model,
        tasks=list(tasks),
        limit=limit,
        log_samples=True,
        task_manager=task_manager,
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload: dict[str, Any] = {
        "timestamp_utc": stamp,
        "model": model,
        "model_args": {},
        "tasks": list(tasks),
        "limit": limit,
        "include_path": str(include),
        "harness_version": getattr(lm_eval, "__version__", "unknown"),
        "results": results.get("results", {}),
        "n_samples": {task: len(samples) for task, samples in (results.get("samples") or {}).items()},
        "note": (
            "dummy model: deterministic pseudo-logits. These numbers test the "
            "evaluation wiring, not model performance."
        ),
    }
    json_path = out_dir / f"baseline_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    for task, samples in (results.get("samples") or {}).items():
        sample_path = out_dir / f"samples_{task}_{stamp}.jsonl"
        with sample_path.open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(json.dumps(sample, default=str, ensure_ascii=False) + "\n")

    payload["path"] = str(json_path)
    return payload
