"""Web API for Paper Packs and matrix runs.

This module is deliberately separate from :mod:`medeval_web.app`: the app already
owns sessions, models and the Archived Experiment 002 flow, and Paper Packs are a
new surface.  ``attach_pack_routes`` adds:

* the Paper Registry (packs + read-only archived studies);
* a pre-flight plan endpoint for the matrix builder;
* start / stop / resume / export / regrade for matrix runs;
* an item explorer with model-disagreement, judge-disagreement, format-failure
  and error filters.

Keys are read from the existing in-memory :class:`~medeval_web.connections.KeyStore`
and never serialized.  The server keeps the same single-active-run rule as the
rest of the app.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from medeval_packs.cache import ContentAddressedCache
from medeval_packs.dataset import load_items
from medeval_packs.registry import archived_studies, list_packs, resolve_pack
from medeval_packs.runner import (
    MatrixConfig,
    MatrixRun,
    ModelBinding,
    default_matrix_root,
    load_matrix_run,
    regrade_run,
)
from medeval_packs.schema import PackValidationError

__all__ = ["MatrixRunManager", "attach_pack_routes"]


class MatrixRunManager:
    """One active matrix run at a time, in a background thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, MatrixRun] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._errors: dict[str, str] = {}

    def start(self, run: MatrixRun) -> None:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                raise RuntimeError("another matrix run is already active; stop it first")
            self._active[run.run_id] = run
            thread = threading.Thread(target=self._execute, args=(run,), daemon=True, name=f"matrix-{run.run_id}")
            self._threads[run.run_id] = thread
        thread.start()

    def _execute(self, run: MatrixRun) -> None:
        try:
            run.execute()
        except Exception as exc:
            self._errors[run.run_id] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._active.pop(run.run_id, None)
                self._threads.pop(run.run_id, None)

    def stop(self, run_id: str) -> bool:
        with self._lock:
            run = self._active.get(run_id)
        if run is None:
            return False
        run.request_stop()
        return True

    def active_run(self) -> str | None:
        with self._lock:
            for run_id, thread in self._threads.items():
                if thread.is_alive():
                    return run_id
        return None

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
            return bool(thread and thread.is_alive())

    def error_for(self, run_id: str) -> str | None:
        return self._errors.get(run_id)


def _binding_from_connection(connection: Any, keys: Any, *, role: str) -> ModelBinding:
    from medeval_packs.runner import ModelBinding as Binding

    return Binding(
        id=f"{role}-{connection.id}",
        label=connection.name,
        model=connection.model,
        family=connection.family_label,
        connection_id=connection.id,
        base_url=connection.base_url,
        api_key=keys.get(connection.id),
        role=role,
        temperature=connection.temperature,
        max_tokens=connection.max_tokens,
        call_cap=connection.per_connection_call_cap,
        usd_cap=connection.per_connection_usd_cap,
        price_in_per_mtok=connection.price_in_per_mtok,
        price_out_per_mtok=connection.price_out_per_mtok,
        api_style=connection.api_style,
    )


def _mock_bindings(role: str, count: int) -> tuple[ModelBinding, ...]:
    return tuple(
        ModelBinding(
            id=f"{role}-mock-{chr(97 + index)}",
            label=f"{'Target' if role == 'target' else 'Judge'} {chr(65 + index)}",
            model=f"mock-{role}-{chr(97 + index)}",
            family=f"mock-family-{index + 1}",
            role=role,
        )
        for index in range(max(1, count))
    )


def _pack_payload(entry: Any) -> dict[str, Any]:
    return entry.as_dict()


def attach_pack_routes(
    app: Any,
    *,
    store: Any,
    keys: Any,
    settings: Any,
    cache_root: Path | None = None,
    matrix_root: Path | None = None,
) -> MatrixRunManager:
    """Register the Paper Pack routes on ``app`` and return the run manager."""
    root = matrix_root or default_matrix_root()
    cache = ContentAddressedCache(cache_root)
    manager = MatrixRunManager()
    router = APIRouter()

    def _resolve(paper_id: str) -> Any:
        try:
            return resolve_pack(paper_id)
        except PackValidationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _connections_for(ids: Sequence[str]) -> list[Any]:
        rows = []
        for connection_id in ids:
            connection = store.get(str(connection_id))
            if connection is None:
                raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
            if not connection.model:
                raise HTTPException(
                    status_code=400,
                    detail=f"connection {connection.name!r} has no model id; choose one on the Models page",
                )
            rows.append(connection)
        return rows

    def _build_bindings(body: Mapping[str, Any], pack: Any) -> tuple[tuple[ModelBinding, ...], tuple[ModelBinding, ...], bool]:
        dry_run = bool(body.get("dry_run", True))
        target_ids = [str(value) for value in body.get("targets") or []]
        judge_ids = [str(value) for value in body.get("judges") or []]
        if target_ids:
            targets = tuple(
                _binding_from_connection(row, keys, role="target") for row in _connections_for(target_ids)
            )
        elif dry_run:
            targets = _mock_bindings("target", int(body.get("mock_targets") or 2))
        else:
            raise HTTPException(
                status_code=400,
                detail="a live matrix run needs at least one target connection; nothing is preconfigured",
            )
        rubric = any(grader.type == "llm_rubric" for grader in pack.graders)
        if judge_ids:
            judges = tuple(
                _binding_from_connection(row, keys, role="judge") for row in _connections_for(judge_ids)
            )
        elif dry_run and rubric:
            judges = _mock_bindings("judge", int(body.get("mock_judges") or 2))
        elif rubric:
            raise HTTPException(
                status_code=400,
                detail="this pack has a rubric judge: select at least one judge connection",
            )
        else:
            judges = ()
        if not dry_run:
            for binding in (*targets, *judges):
                is_loopback = any(host in binding.base_url for host in ("127.0.0.1", "localhost", "::1"))
                if not is_loopback and not binding.api_key:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{binding.label!r} has no key in process memory; re-enter it on the Models page",
                    )
        return targets, judges, dry_run

    def _config_from_body(body: Mapping[str, Any], pack: Any) -> tuple[MatrixConfig, bool]:
        targets, judges, dry_run = _build_bindings(body, pack)
        config = MatrixConfig(
            targets=targets,
            judges=judges,
            dry_run=dry_run,
            stage=str(body.get("stage") or "full"),
            n_items=int(body["n_items"]) if body.get("n_items") not in (None, "", 0) else None,
            seed=int(body.get("seed") or 20260101),
            max_calls=int(body.get("max_calls") or getattr(settings, "max_calls", 500)),
            max_usd=float(body.get("max_usd") if body.get("max_usd") is not None else getattr(settings, "max_usd", 5.0)),
            run_id=str(body["run_id"]) if body.get("run_id") else None,
            resume=True,
        )
        return config, dry_run

    # ------------------------------------------------------------------ #
    # Paper Registry
    # ------------------------------------------------------------------ #
    @router.get("/api/packs")
    def get_packs(include_archived: bool = Query(default=False)) -> dict[str, Any]:
        entries = list_packs()
        archived = archived_studies() if include_archived else []
        return {
            "packs": [_pack_payload(entry) for entry in entries],
            "archived": [_pack_payload(entry) for entry in archived],
            "cache": cache.stats(),
            "note": (
                "Paper Packs are declarative protocols. Archived studies are read-only metadata and "
                "cannot be re-run as packs."
            ),
        }

    @router.get("/api/packs/{pack_id:path}")
    def get_pack(pack_id: str) -> dict[str, Any]:
        pack = _resolve(pack_id)
        items = load_items(pack)
        preview = [
            {
                "item_id": item.item_id,
                "fields": {
                    key: value
                    for key, value in item.prompt_fields.items()
                    if key in {"item_id", "question", "input", "options_block", "candidate_a", "candidate_b"}
                },
            }
            for item in items[:3]
        ]
        return {**pack.summary(dataset_count=len(items)), "preview": preview}

    @router.post("/api/packs/{pack_id:path}/validate")
    def validate_pack(pack_id: str) -> dict[str, Any]:
        pack = _resolve(pack_id)
        validation = pack.validate()
        return {
            "pack_id": pack.id,
            **validation.as_dict(),
            "pack_hash": pack.content_hash(),
            "prompt_hash": pack.prompt_hash(),
        }

    @router.get("/api/matrix/cache")
    def cache_stats() -> dict[str, Any]:
        return cache.stats()

    @router.delete("/api/matrix/cache")
    def clear_cache() -> dict[str, Any]:
        return cache.prune()

    # ------------------------------------------------------------------ #
    # Planning and runs
    # ------------------------------------------------------------------ #
    @router.post("/api/matrix/plan")
    async def plan(request: Request) -> dict[str, Any]:
        body = await request.json()
        pack = _resolve(str(body.get("paper") or ""))
        config, dry_run = _config_from_body(body, pack)
        run = MatrixRun(pack, config, run_root=root, cache_root=cache_root)
        plan_payload = run.plan()
        return {
            "plan": plan_payload,
            "dry_run": dry_run,
            "pipeline_test_only": dry_run,
            "judges": [binding.as_dict() for binding in config.judges],
            "targets": [binding.as_dict() for binding in config.targets],
            "note": (
                "PIPELINE TEST ONLY: a dry run uses mock targets/judges and makes no network calls."
                if dry_run
                else "Live run: caps are enforced before every dispatched call."
            ),
        }

    @router.post("/api/matrix/runs")
    async def start(request: Request) -> JSONResponse:
        body = await request.json()
        pack = _resolve(str(body.get("paper") or ""))
        config, dry_run = _config_from_body(body, pack)
        if not dry_run and not body.get("confirm_real_calls"):
            raise HTTPException(
                status_code=400,
                detail="a live matrix run requires confirm_real_calls=true; the plan card shows the cap first",
            )
        if manager.active_run():
            raise HTTPException(
                status_code=409,
                detail=f"matrix run {manager.active_run()} is already active; stop it first",
            )
        run = MatrixRun(pack, config, run_root=root, cache_root=cache_root)
        try:
            run.prepare()
            run._write_state(status="queued", plan=run.plan())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            manager.start(run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            {"run_id": run.run_id, "status": "queued", "pipeline_test_only": dry_run},
            status_code=202,
        )

    @router.get("/api/matrix/runs")
    def list_runs() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if root.is_dir():
            for directory in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
                state = _read_json(directory / "run.json")
                if state is None:
                    continue
                rows.append({
                    "run_id": state.get("run_id") or directory.name,
                    "status": state.get("status"),
                    "paper": state.get("paper"),
                    "started_utc": state.get("started_utc"),
                    "finished_utc": state.get("finished_utc"),
                    "pipeline_test_only": state.get("pipeline_test_only"),
                    "active": manager.is_active(str(state.get("run_id") or directory.name)),
                    "error": manager.error_for(str(state.get("run_id") or directory.name)),
                })
        return rows

    @router.get("/api/matrix/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        payload = load_matrix_run(run_id, root=root)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown matrix run {run_id!r}")
        return {
            **payload,
            "active": manager.is_active(run_id),
            "error": manager.error_for(run_id),
            "paths": {
                **payload["paths"],
                "bundle_download": f"/api/matrix/runs/{run_id}/bundle",
            },
        }

    @router.get("/api/matrix/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str) -> str:
        payload = load_matrix_run(run_id, root=root)
        if payload is None or not payload.get("report"):
            raise HTTPException(status_code=404, detail="no report for this run yet")
        return payload["report"]  # type: ignore[return-value]

    @router.get("/api/matrix/runs/{run_id}/bundle")
    def get_bundle(run_id: str) -> FileResponse:
        path = root / run_id / "bundle.zip"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no bundle for this run yet")
        return FileResponse(path, media_type="application/zip", filename=f"{run_id}-bundle.zip")

    @router.post("/api/matrix/runs/{run_id}/stop")
    def stop(run_id: str) -> dict[str, Any]:
        if manager.stop(run_id):
            return {"run_id": run_id, "stopping": True}
        raise HTTPException(status_code=409, detail="this matrix run is not active in the app")

    @router.post("/api/matrix/runs/{run_id}/resume")
    def resume(run_id: str) -> dict[str, Any]:
        payload = load_matrix_run(run_id, root=root)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown matrix run {run_id!r}")
        if manager.active_run():
            raise HTTPException(status_code=409, detail="another matrix run is active; stop it first")
        state = payload["run"]
        paper_id = (state.get("paper") or {}).get("id")
        pack = _resolve(str(paper_id))
        stored = state.get("config") or {}
        targets = tuple(ModelBinding.from_mapping(row, role="target") for row in stored.get("targets") or [])
        judges = tuple(ModelBinding.from_mapping(row, role="judge") for row in stored.get("judges") or [])
        # Re-attach keys held in process memory. A restarted app has none, and the
        # run is refused rather than silently becoming a mock run.
        def reattach(binding: ModelBinding) -> ModelBinding:
            if not binding.connection_id:
                return binding
            key = keys.get(binding.connection_id)
            return ModelBinding(**{**binding.__dict__, "api_key": key}) if key else binding

        targets = tuple(reattach(binding) for binding in targets)
        judges = tuple(reattach(binding) for binding in judges)
        if not stored.get("dry_run"):
            missing = [binding.label for binding in (*targets, *judges) if binding.connection_id and not binding.api_key]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "keys live in memory only and the app was restarted; re-enter keys for "
                        + ", ".join(missing)
                        + " on the Models page before resuming"
                    ),
                )
        config = MatrixConfig(
            targets=targets,
            judges=judges,
            dry_run=bool(stored.get("dry_run", True)),
            stage=str(stored.get("stage") or "full"),
            n_items=stored.get("n_items"),
            seed=int(stored.get("seed") or 20260101),
            max_calls=int(stored.get("max_calls") or getattr(settings, "max_calls", 500)),
            max_usd=float(stored.get("max_usd") or getattr(settings, "max_usd", 5.0)),
            run_id=run_id,
            resume=True,
        )
        run = MatrixRun(pack, config, run_root=root, cache_root=cache_root)
        try:
            run.prepare()
            run._write_state(status="queued")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            manager.start(run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "status": "queued"}

    @router.post("/api/matrix/runs/{run_id}/regrade")
    async def regrade(run_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        payload = load_matrix_run(run_id, root=root)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown matrix run {run_id!r}")
        paper_id = str(body.get("paper") or (payload["run"].get("paper") or {}).get("id"))
        pack = _resolve(paper_id)
        judge_rows = _connections_for([str(value) for value in body.get("judges") or []])
        judges = tuple(_binding_from_connection(row, keys, role="judge") for row in judge_rows)
        dry_run = bool(body.get("dry_run", True))
        if not dry_run:
            if not body.get("confirm_real_calls"):
                raise HTTPException(status_code=400, detail="live regrade requires confirm_real_calls=true")
            for binding in judges:
                if not binding.api_key and "127.0.0.1" not in binding.base_url:
                    raise HTTPException(
                        status_code=400,
                        detail=f"judge {binding.label!r} has no key in process memory",
                    )
        try:
            result = regrade_run(
                run_id,
                pack,
                judges=judges,
                dry_run=dry_run,
                root=root,
                cache_root=cache_root,
                run_root=root,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run_id": result["run_id"],
            "regrade_of": run_id,
            "status": result["status"],
            "metrics": result["metrics"],
            "paths": result["paths"],
        }

    # ------------------------------------------------------------------ #
    # Item explorer
    # ------------------------------------------------------------------ #
    @router.get("/api/matrix/runs/{run_id}/items")
    def items(run_id: str, filter: str = Query(default="all")) -> dict[str, Any]:
        payload = load_matrix_run(run_id, root=root)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown matrix run {run_id!r}")
        allowed = {"all", "model_disagreements", "judge_disagreements", "format_failures", "errors"}
        if filter not in allowed:
            raise HTTPException(status_code=400, detail=f"filter must be one of {sorted(allowed)}")
        metrics = payload.get("metrics") or {}
        targets = metrics.get("targets") or {}
        outputs = payload.get("outputs") or []
        scores = payload.get("scores") or []
        item_ids: list[str] = []
        for row in outputs:
            item_id = str(row.get("item_id"))
            if item_id and item_id not in item_ids:
                item_ids.append(item_id)
        rows: list[dict[str, Any]] = []
        for item_id in item_ids:
            target_rows = []
            primary_values = []
            judge_values: dict[str, dict[str, Any]] = {}
            format_failures = 0
            errors = 0
            for target_id, target in targets.items():
                output = next(
                    (row for row in reversed(outputs)
                     if row.get("target_id") == target_id and row.get("item_id") == item_id),
                    None,
                )
                checks = [
                    {
                        "grader_id": row.get("grader_id"),
                        "metric": row.get("metric"),
                        "value": row.get("value"),
                        "passed": row.get("passed"),
                        "detail": row.get("detail"),
                        "error": row.get("error"),
                    }
                    for row in scores
                    if row.get("target_id") == target_id
                    and row.get("item_id") == item_id
                    and row.get("judge_id") in (None, "")
                ]
                judge_rows = [
                    {
                        "judge_id": row.get("judge_id"),
                        "grader_id": row.get("grader_id"),
                        "value": row.get("value"),
                        "rationale": row.get("detail"),
                        "error": row.get("error"),
                    }
                    for row in scores
                    if row.get("target_id") == target_id
                    and row.get("item_id") == item_id
                    and row.get("judge_id") not in (None, "")
                ]
                for judge_row in judge_rows:
                    judge_values.setdefault(target_id, {})[str(judge_row["judge_id"])] = judge_row["value"]
                if any(
                    check["metric"] in {"format_compliance", "json_schema"}
                    and check["value"] is not None
                    and float(check["value"]) < 1
                    for check in checks
                ):
                    format_failures += 1
                if output and output.get("error"):
                    errors += 1
                value = (target.get("values") or {}).get(item_id)
                if value is not None:
                    primary_values.append(float(value))
                target_rows.append({
                    "target_id": target_id,
                    "label": target.get("label"),
                    "model": target.get("model"),
                    "text": (output or {}).get("text") or "",
                    "latency_ms": (output or {}).get("latency_ms"),
                    "cached": (output or {}).get("cached"),
                    "error": (output or {}).get("error"),
                    "primary_value": value,
                    "checks": checks,
                    "judges": judge_rows,
                })
            model_disagreement = len({round(value, 6) for value in primary_values}) > 1
            judge_disagreement = any(len({value for value in values.values() if value is not None}) > 1
                                     for values in judge_values.values())
            row = {
                "item_id": item_id,
                "targets": target_rows,
                "model_disagreement": model_disagreement,
                "judge_disagreement": judge_disagreement,
                "format_failures": format_failures,
                "errors": errors,
            }
            keep = {
                "all": True,
                "model_disagreements": model_disagreement,
                "judge_disagreements": judge_disagreement,
                "format_failures": format_failures > 0,
                "errors": errors > 0,
            }[filter]
            if keep:
                rows.append(row)
        return {"run_id": run_id, "filter": filter, "total_items": len(item_ids), "items": rows[:500]}

    app.include_router(router)
    app.state.matrix_manager = manager
    return manager


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
