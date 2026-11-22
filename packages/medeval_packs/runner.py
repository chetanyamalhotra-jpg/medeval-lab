"""Matrix runner: 1 Paper Pack x N target models x M graders/judges.

Execution model
---------------
* Targets (models under test) are separate from evaluators (deterministic
  graders plus an optional LLM judge panel), so a judge can never silently be a
  target and vice versa.
* Every target output and every judge score is appended to ``outputs.jsonl`` /
  ``scores.jsonl`` with an fsync.  Resume reads those files and never repeats a
  completed call.
* Successful completions are cached under ``.medeval_cache`` keyed by
  ``sha256(model + params + rendered_prompt + item_hash)``.  A repeated run with
  identical prompts therefore costs nothing.
* A hard call/USD budget is checked before every dispatched call.  When it would
  be exceeded the run stops as ``budget_stopped`` with everything already
  completed saved and exported.

Nothing in this module chooses a provider, model, family or price.  Bindings are
plain data built by the caller (the app, the CLI, or a test).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medeval_core.calllog import new_run_id, utc_now_iso
from medeval_core.client import OpenAICompatibleTransport
from medeval_core.cost import estimate_tokens
from medeval_core.manifest import build_manifest
from medeval_core.paths import results_dir
from medeval_study.report import MEDICAL_NOTICE

from .analysis import analyse_matrix, json_safe
from .cache import ContentAddressedCache, cache_key
from .dataset import PackItem, load_items
from .exports import refresh_bundle, write_exports
from .graders import (
    GraderOutcome,
    parse_rubric_score,
    run_deterministic_grader,
    run_rubric_grader_prompt,
)
from .mock import MockJudge, MockTarget
from .render import render_template
from .schema import GraderSpec, PaperPack

__all__ = [
    "BudgetExceeded",
    "MatrixConfig",
    "MatrixRun",
    "ModelBinding",
    "RunAborted",
    "default_matrix_root",
    "load_matrix_run",
    "regrade_run",
]

ProgressCallback = Callable[[dict[str, Any]], None]

TERMINAL_STATUSES = ("completed", "stopped", "budget_stopped", "failed", "refused")


def default_matrix_root() -> Path:
    return results_dir() / "matrix_runs"


class BudgetExceeded(RuntimeError):
    """A hard call/USD ceiling would be crossed; the run stops gracefully."""


class RunAborted(RuntimeError):
    """A terminal provider error (for example authentication) stopped the run."""


@dataclass(frozen=True)
class ModelBinding:
    """One target model or judge, with its key held in memory only."""

    id: str
    label: str
    model: str
    family: str = ""
    connection_id: str = ""
    base_url: str = ""
    api_key: str | None = None
    role: str = "target"
    temperature: float = 0.0
    max_tokens: int = 1024
    call_cap: int = 500
    usd_cap: float = 5.0
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    api_style: str = "chat"

    def as_dict(self, *, include_key: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "family": self.family,
            "connection_id": self.connection_id,
            "base_url": self.base_url,
            "role": self.role,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "call_cap": self.call_cap,
            "usd_cap": self.usd_cap,
            "price_in_per_mtok": self.price_in_per_mtok,
            "price_out_per_mtok": self.price_out_per_mtok,
            "api_style": self.api_style,
        }
        if include_key:
            payload["api_key"] = self.api_key
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, role: str = "target") -> ModelBinding:
        binding_id = str(payload.get("id") or payload.get("judge_id") or payload.get("binding_id") or "").strip()
        if not binding_id:
            raise ValueError("a model binding needs an id")
        return cls(
            id=binding_id,
            label=str(payload.get("label") or payload.get("name") or binding_id),
            model=str(payload.get("model") or "").strip(),
            family=str(payload.get("family") or payload.get("family_label") or "").strip(),
            connection_id=str(payload.get("connection_id") or "").strip(),
            base_url=str(payload.get("base_url") or "").strip(),
            api_key=(str(payload["api_key"]) if payload.get("api_key") else None),
            role=str(payload.get("role") or role),
            temperature=float(payload.get("temperature") or 0.0),
            max_tokens=max(1, int(payload.get("max_tokens") or 1024)),
            call_cap=max(1, int(payload.get("call_cap") or 500)),
            usd_cap=max(0.0, float(payload.get("usd_cap") if payload.get("usd_cap") is not None else 5.0)),
            price_in_per_mtok=_optional_float(payload.get("price_in_per_mtok")),
            price_out_per_mtok=_optional_float(payload.get("price_out_per_mtok")),
            api_style=str(payload.get("api_style") or "chat"),
        )

    @property
    def prices_verified(self) -> bool:
        return self.price_in_per_mtok is not None and self.price_out_per_mtok is not None

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.id:
            problems.append("id is required")
        if not self.model:
            problems.append(f"{self.id or 'binding'}: model is required (nothing is chosen for you)")
        if self.role not in {"target", "judge"}:
            problems.append(f"{self.id}: role must be 'target' or 'judge'")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            problems.append(f"{self.id}: base_url must start with http:// or https://")
        if self.base_url and "://" in self.base_url:
            from urllib.parse import urlsplit

            parts = urlsplit(self.base_url)
            if not parts.hostname:
                problems.append(f"{self.id}: base_url must name a host")
            if parts.username or parts.password:
                problems.append(f"{self.id}: base_url must not embed credentials")
        if not 0 <= self.temperature <= 2:
            problems.append(f"{self.id}: temperature must be between 0 and 2")
        if self.max_tokens < 16:
            problems.append(f"{self.id}: max_tokens must be at least 16")
        return problems

    def estimate_usd(self, input_tokens: int, output_tokens: int) -> float | None:
        if not self.prices_verified:
            return None
        return (
            input_tokens * float(self.price_in_per_mtok or 0.0)
            + output_tokens * float(self.price_out_per_mtok or 0.0)
        ) / 1_000_000


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MatrixConfig:
    """Everything a matrix run needs to be reproducible."""

    targets: tuple[ModelBinding, ...]
    judges: tuple[ModelBinding, ...] = ()
    dry_run: bool = True
    stage: str = "full"
    n_items: int | None = None
    seed: int = 20260101
    max_calls: int = 500
    max_usd: float = 5.0
    pace_seconds: float = 0.0
    run_id: str | None = None
    resume: bool = True
    regrade_of: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "targets": [binding.as_dict() for binding in self.targets],
            "judges": [binding.as_dict() for binding in self.judges],
            "dry_run": self.dry_run,
            "stage": self.stage,
            "n_items": self.n_items,
            "seed": self.seed,
            "max_calls": self.max_calls,
            "max_usd": self.max_usd,
            "pace_seconds": self.pace_seconds,
            "regrade_of": self.regrade_of,
        }


@dataclass
class _Budget:
    max_calls: int
    max_usd: float
    calls: int = 0
    usd_known: float = 0.0
    usd_unknown_calls: int = 0
    per_binding_calls: dict[str, int] = field(default_factory=dict)
    per_binding_usd: dict[str, float] = field(default_factory=dict)

    def check(self, binding: ModelBinding, input_tokens: int, max_output_tokens: int) -> float | None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"run call cap reached ({self.max_calls})")
        used = self.per_binding_calls.get(binding.id, 0)
        if used >= binding.call_cap:
            raise BudgetExceeded(f"{binding.id} call cap reached ({binding.call_cap})")
        estimate = binding.estimate_usd(input_tokens, max_output_tokens)
        if estimate is None:
            return None
        if self.usd_known + self.per_binding_usd.get(binding.id, 0.0) + estimate > self.max_usd + 1e-12:
            raise BudgetExceeded(
                f"estimated USD cap would be exceeded (run cap ${self.max_usd:.2f})"
            )
        if self.per_binding_usd.get(binding.id, 0.0) + estimate > binding.usd_cap + 1e-12:
            raise BudgetExceeded(
                f"{binding.id} estimated USD cap would be exceeded (${binding.usd_cap:.2f})"
            )
        return estimate

    def record(self, binding: ModelBinding, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.per_binding_calls[binding.id] = self.per_binding_calls.get(binding.id, 0) + 1
        amount = binding.estimate_usd(input_tokens, output_tokens)
        if amount is None:
            self.usd_unknown_calls += 1
            return
        self.usd_known += amount
        self.per_binding_usd[binding.id] = self.per_binding_usd.get(binding.id, 0.0) + amount

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "max_usd": self.max_usd,
            "estimated_usd": None if self.usd_unknown_calls else round(self.usd_known, 6),
            "unpriced_calls": self.usd_unknown_calls,
            "per_binding_calls": dict(self.per_binding_calls),
            "per_binding_usd": {key: round(value, 6) for key, value in self.per_binding_usd.items()},
        }


class MatrixRun:
    """One executable pack evaluation."""

    def __init__(
        self,
        pack: PaperPack,
        config: MatrixConfig,
        *,
        run_root: Path | None = None,
        cache_root: Path | None = None,
        progress: ProgressCallback | None = None,
        stop_event: threading.Event | None = None,
        transport_factory: Callable[[ModelBinding], Any] | None = None,
    ) -> None:
        self.pack = pack
        self.config = config
        self.root = run_root or default_matrix_root()
        self.cache = ContentAddressedCache(cache_root)
        self.progress = progress
        self.stop_event = stop_event or threading.Event()
        self.transport_factory = transport_factory
        self.run_id = config.run_id or new_run_id(f"matrix-{pack.id[:24]}")
        self.run_dir = self.root / self.run_id
        self.items: list[PackItem] = []
        self.outputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.scores: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.budget = _Budget(config.max_calls, config.max_usd)
        self.status = "queued"
        self.reason: str | None = None
        self.usage: dict[str, Any] = {
            "calls_made": 0,
            "target_calls": 0,
            "judge_calls": 0,
            "cache_hits": 0,
            "mock_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_usd": None,
        }
        self._seq = 0
        self._started = utc_now_iso()

    # ------------------------------------------------------------------ #
    # Validation, planning and persistence
    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self.stop_event.set()

    def prepare(self) -> None:
        validation = self.pack.validate()
        if not validation.ok:
            raise ValueError("pack is invalid: " + "; ".join(validation.errors))
        if not self.config.targets:
            raise ValueError("a matrix run needs at least one target model")
        problems = [problem for binding in (*self.config.targets, *self.config.judges) for problem in binding.problems()]
        if problems:
            raise ValueError("; ".join(problems))
        if not self.config.dry_run:
            if not self.config.targets:
                raise ValueError("a live run needs target connections")
            for binding in self.config.targets:
                if not binding.base_url:
                    raise ValueError(f"target {binding.id} has no base_url; live runs need an endpoint")
        self.items = load_items(self.pack, stage=self.config.stage)
        if self.config.n_items is not None:
            self.items = self.items[: max(1, self.config.n_items)]
        if self.run_dir.is_dir():
            self._load_existing()
        else:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / "events.jsonl").touch()
        self._started = utc_now_iso()

    def _load_existing(self) -> None:
        state = _read_json(self.run_dir / "run.json")
        if state and self.config.resume:
            recorded = (state.get("paper") or {}).get("pack_hash")
            current = self.pack.content_hash()
            if recorded and recorded != current:
                raise ValueError(
                    "this run was produced by a different version of the pack (pack hash changed); "
                    "start a new run instead of resuming"
                )
        for row in _read_jsonl(self.run_dir / "outputs.jsonl"):
            self.outputs[(str(row.get("target_id")), str(row.get("item_id")))] = row
        for row in _read_jsonl(self.run_dir / "scores.jsonl"):
            key = (
                str(row.get("target_id")),
                str(row.get("item_id")),
                str(row.get("grader_id")),
                str(row.get("judge_id") or ""),
            )
            self.scores[key] = row
        self._seq = max((int(row.get("seq") or 0) for row in _read_jsonl(self.run_dir / "events.jsonl")), default=0)

    def plan(self) -> dict[str, Any]:
        """Pre-flight call/token/cost estimate (no model call)."""
        items = load_items(self.pack, stage=self.config.stage)
        if self.config.n_items is not None:
            items = items[: max(1, self.config.n_items)]
        target_calls = len(items) * len(self.config.targets)
        rubric_graders = [grader for grader in self.pack.graders if grader.type == "llm_rubric"]
        judge_calls = len(items) * len(rubric_graders) * len(self.config.judges)

        prompt_tokens = 0
        cache_hits = 0
        for item in items:
            user_prompt, system_prompt = self._target_prompt(item)
            prompt_tokens += estimate_tokens(user_prompt) + estimate_tokens(system_prompt)
        for target in self.config.targets:
            for item in items:
                user_prompt, system_prompt = self._target_prompt(item)
                key = cache_key(
                    target.model,
                    self._params(target, system_prompt),
                    user_prompt,
                    item.item_hash,
                )
                if self.cache.get(key) is not None:
                    cache_hits += 1
        average_prompt = max(1, prompt_tokens // max(1, len(items)))
        judge_prompt_tokens = average_prompt + 600
        input_upper = target_calls * average_prompt + judge_calls * (judge_prompt_tokens + self._max_target_output())
        output_upper = (
            target_calls * self._max_target_output()
            + judge_calls * 512
        )
        usd = 0.0
        priced = True
        for target in self.config.targets:
            amount = target.estimate_usd(
                target_calls // max(1, len(self.config.targets)) * average_prompt,
                target_calls // max(1, len(self.config.targets)) * target.max_tokens,
            )
            if amount is None:
                priced = False
            else:
                usd += amount
        for judge in self.config.judges:
            amount = judge.estimate_usd(judge_calls * judge_prompt_tokens // max(1, len(self.config.judges)),
                                        judge_calls * 512 // max(1, len(self.config.judges)))
            if amount is None:
                priced = False
            else:
                usd += amount
        return {
            "paper_id": self.pack.id,
            "paper_version": self.pack.version,
            "pack_hash": self.pack.content_hash(),
            "prompt_hash": self.pack.prompt_hash(),
            "n_items": len(items),
            "n_cached_target_outputs": cache_hits,
            "targets": [
                {"id": target.id, "label": target.label, "model": target.model,
                 "calls": len(items), "prices_verified": target.prices_verified}
                for target in self.config.targets
            ],
            "judges": [
                {"id": judge.id, "label": judge.label, "model": judge.model, "family": judge.family,
                 "calls": len(items) * len(rubric_graders), "prices_verified": judge.prices_verified}
                for judge in self.config.judges
            ],
            "rubric_graders": [grader.id for grader in rubric_graders],
            "target_calls": target_calls,
            "judge_calls": judge_calls,
            "total_calls": target_calls + judge_calls,
            "input_tokens_upper_bound": input_upper,
            "output_tokens_upper_bound": output_upper,
            "usd_upper_bound": round(usd, 6) if priced else None,
            "prices_verified": priced,
            "budget": {"max_calls": self.config.max_calls, "max_usd": self.config.max_usd},
            "pipeline_test_only": self._pipeline_test_only(),
            "cache_available": cache_hits,
            "estimator": "ceil(characters/4) per message, full max_tokens for every reply",
        }

    def _pipeline_test_only(self) -> bool:
        return bool(self.config.dry_run)

    def _params(self, binding: ModelBinding, system_prompt: str) -> dict[str, Any]:
        return {
            "temperature": binding.temperature,
            "max_tokens": binding.max_tokens,
            "seed": self.config.seed,
            "system": system_prompt,
            "api_style": binding.api_style,
        }

    def _max_target_output(self) -> int:
        return max((binding.max_tokens for binding in self.config.targets), default=1024)

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def execute(self) -> dict[str, Any]:
        if not self.items:
            self.prepare()
        self.status = "running"
        self._write_state(status="running", plan=self.plan())
        self._emit("run_started", {
            "paper_id": self.pack.id,
            "n_items": len(self.items),
            "targets": [binding.id for binding in self.config.targets],
            "judges": [binding.id for binding in self.config.judges],
            "dry_run": self.config.dry_run,
            "pipeline_test_only": self._pipeline_test_only(),
        })
        try:
            for target in self.config.targets:
                if self.stop_event.is_set():
                    break
                for item in self.items:
                    if self.stop_event.is_set():
                        break
                    output = self.outputs.get((target.id, item.item_id))
                    if output is None or output.get("error"):
                        output = self._call_target(target, item)
                        self.outputs[(target.id, item.item_id)] = output
                        self._append_jsonl("outputs.jsonl", output)
                    if output.get("error"):
                        continue
                    self._ensure_deterministic(target, item, output)
                    self._ensure_judges(target, item, output)
                    self._progress(item, target)
                    if self.config.pace_seconds:
                        time.sleep(max(0.0, self.config.pace_seconds))
                if self.stop_event.is_set():
                    break
        except BudgetExceeded as exc:
            self.status = "budget_stopped"
            self.reason = str(exc)
            self._emit("budget_stopped", {"reason": self.reason, **self.budget.summary()})
        except RunAborted as exc:
            self.status = "failed"
            self.reason = str(exc)
            self._emit("run_failed", {"reason": self.reason})
        except Exception as exc:
            self.status = "failed"
            self.reason = f"{type(exc).__name__}: {exc}"
            self._emit("run_failed", {"reason": self.reason})
            self._finalise()
            raise
        if self.status == "running":
            self.status = "stopped" if self.stop_event.is_set() else "completed"
            if self.status == "stopped":
                self.reason = "stopped by user; completed items are saved and the run can be resumed"
                self._emit("run_stopped", {"reason": self.reason})
        result = self._finalise()
        self._emit("run_completed" if self.status == "completed" else "run_finished", {"status": self.status})
        return result

    def _call_target(self, target: ModelBinding, item: PackItem) -> dict[str, Any]:
        user_prompt, system_prompt = self._target_prompt(item)
        params = self._params(target, system_prompt)
        key = cache_key(target.model, params, user_prompt, item.item_hash)
        cached = self.cache.get(key)
        if cached is not None:
            self.usage["cache_hits"] += 1
            self._emit("target_cached", {"target_id": target.id, "item_id": item.item_id, "cache_key": key})
            return {
                "target_id": target.id,
                "item_id": item.item_id,
                "item_hash": item.item_hash,
                "model": target.model,
                "text": cached.text,
                "latency_ms": cached.latency_ms,
                "input_tokens": cached.input_tokens,
                "output_tokens": cached.output_tokens,
                "reasoning_tokens": cached.reasoning_tokens,
                "cached": True,
                "error": None,
                "prompt_sha256": cached.prompt_sha256,
                "created_utc": utc_now_iso(),
            }

        started = time.perf_counter()
        if self.config.dry_run:
            completion = MockTarget(target.model, seed=self.config.seed).complete(item)
            self.usage["mock_calls"] += 1
        else:
            estimate = self.budget.check(target, estimate_tokens(user_prompt) + estimate_tokens(system_prompt), target.max_tokens)
            transport = self._transport(target)
            completion = transport.complete(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=target.temperature,
                seed=self.config.seed,
                max_tokens=target.max_tokens,
                timeout_s=120.0,
            )
            self.usage["target_calls"] += 1
            self.usage["calls_made"] += 1
            self.usage["input_tokens"] += completion.input_tokens
            self.usage["output_tokens"] += completion.output_tokens
            self.budget.record(target, completion.input_tokens, completion.output_tokens)
            if estimate is None:
                self.usage["unpriced_calls"] = self.usage.get("unpriced_calls", 0) + 1

        latency = completion.latency_ms or (time.perf_counter() - started) * 1000.0
        error = completion.error
        if error and completion.error_type == "auth":
            self._emit("auth_error", {"target_id": target.id, "error": error})
            raise RunAborted(f"authentication failed for {target.id}: {error}")
        text = completion.text or ""
        if not error:
            self.cache.put(_cache_entry(key, target.model, user_prompt, item.item_hash, completion, latency))
        record = {
            "target_id": target.id,
            "item_id": item.item_id,
            "item_hash": item.item_hash,
            "model": target.model,
            "text": text,
            "latency_ms": latency,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "reasoning_tokens": completion.reasoning_tokens,
            "cached": False,
            "error": error,
            "prompt_sha256": _sha256(user_prompt),
            "created_utc": utc_now_iso(),
        }
        self._emit(
            "target_output",
            {"target_id": target.id, "item_id": item.item_id, "cached": False,
             "error": error, "dry_run": self.config.dry_run},
        )
        if error:
            self.usage["errors"] = self.usage.get("errors", 0) + 1
        return record

    def _ensure_deterministic(self, target: ModelBinding, item: PackItem, output: Mapping[str, Any]) -> None:
        for grader in self.pack.graders:
            if grader.type != "deterministic":
                continue
            key = (target.id, item.item_id, grader.id, "")
            if key in self.scores:
                continue
            outcome = run_deterministic_grader(grader, item, str(output.get("text") or ""))
            self._record_score(target, item, grader, outcome, judge_id="")

    def _ensure_judges(self, target: ModelBinding, item: PackItem, output: Mapping[str, Any]) -> None:
        rubric_graders = [grader for grader in self.pack.graders if grader.type == "llm_rubric"]
        if not rubric_graders or not self.config.judges:
            return
        target_text = str(output.get("text") or "")
        for grader in rubric_graders:
            prompt = run_rubric_grader_prompt(grader, item, target_text)
            for judge in self.config.judges:
                key = (target.id, item.item_id, grader.id, judge.id)
                if key in self.scores:
                    continue
                score_key = cache_key(
                    judge.model,
                    self._params(judge, self.pack.prompt_template.system),
                    prompt,
                    item.item_hash + ":" + _sha256(target_text)[:12],
                )
                cached = self.cache.get(score_key)
                if cached is not None:
                    self.usage["cache_hits"] += 1
                    completion_text = cached.text
                    latency = cached.latency_ms
                    input_tokens, output_tokens = cached.input_tokens, cached.output_tokens
                    cached_hit = True
                elif self.config.dry_run:
                    completion = MockJudge(judge.model, family=judge.family, seed=self.config.seed).complete(
                        item, target_text, grader.scale_min, grader.scale_max
                    )
                    completion_text, latency = completion.text, completion.latency_ms
                    input_tokens, output_tokens = completion.input_tokens, completion.output_tokens
                    self.usage["mock_calls"] += 1
                    cached_hit = False
                else:
                    estimate = self.budget.check(judge, estimate_tokens(prompt), judge.max_tokens)
                    transport = self._transport(judge)
                    completion = transport.complete(
                        [{"role": "system", "content": self.pack.prompt_template.system},
                         {"role": "user", "content": prompt}],
                        temperature=judge.temperature,
                        seed=self.config.seed,
                        max_tokens=judge.max_tokens,
                        timeout_s=120.0,
                    )
                    completion_text, latency = completion.text, completion.latency_ms
                    input_tokens, output_tokens = completion.input_tokens, completion.output_tokens
                    self.usage["judge_calls"] += 1
                    self.usage["calls_made"] += 1
                    self.usage["input_tokens"] += input_tokens
                    self.usage["output_tokens"] += output_tokens
                    self.budget.record(judge, input_tokens, output_tokens)
                    if estimate is None:
                        self.usage["unpriced_calls"] = self.usage.get("unpriced_calls", 0) + 1
                    cached_hit = False
                    if completion.error and completion.error_type == "auth":
                        raise RunAborted(f"authentication failed for judge {judge.id}: {completion.error}")
                if not cached_hit:
                    completion = completion  # type: ignore[possibly-undefined]
                    if not getattr(completion, "error", None):
                        self.cache.put(_cache_entry(score_key, judge.model, prompt, item.item_hash,
                                                    completion, latency))
                parsed = parse_rubric_score(completion_text, scale_min=grader.scale_min, scale_max=grader.scale_max)
                outcome = GraderOutcome(
                    grader_id=grader.id,
                    metric="rubric_score",
                    value=parsed["score"] if parsed["ok"] else None,
                    passed=None,
                    parsed=parsed,
                    detail=str(parsed.get("rationale") or ""),
                    error=None if parsed["ok"] else str(parsed.get("error") or "parse_error"),
                )
                self._record_score(target, item, grader, outcome, judge_id=judge.id,
                                   latency_ms=latency, cached=cached_hit)
                self._emit("judge_score", {
                    "target_id": target.id, "item_id": item.item_id, "judge_id": judge.id,
                    "value": outcome.value, "error": outcome.error, "cached": cached_hit,
                })

    def _record_score(
        self,
        target: ModelBinding,
        item: PackItem,
        grader: GraderSpec,
        outcome: GraderOutcome,
        *,
        judge_id: str,
        latency_ms: float | None = None,
        cached: bool = False,
    ) -> None:
        record = {
            "target_id": target.id,
            "item_id": item.item_id,
            "grader_id": grader.id,
            "judge_id": judge_id or None,
            "metric": outcome.metric,
            "value": outcome.value,
            "passed": outcome.passed,
            "parsed": dict(outcome.parsed),
            "detail": outcome.detail,
            "error": outcome.error,
            "latency_ms": latency_ms,
            "cached": cached,
            "at": utc_now_iso(),
        }
        key = (target.id, item.item_id, grader.id, judge_id)
        self.scores[key] = record
        self._append_jsonl("scores.jsonl", record)
        self._emit("item_scored", {
            "target_id": target.id, "item_id": item.item_id, "grader_id": grader.id,
            "judge_id": judge_id or None, "metric": outcome.metric, "value": outcome.value,
        })

    def _transport(self, binding: ModelBinding) -> Any:
        if self.transport_factory is not None:
            return self.transport_factory(binding)
        if not binding.base_url:
            raise RunAborted(f"{binding.id} has no base_url; a live run needs an endpoint")
        return OpenAICompatibleTransport(
            base_url=binding.base_url,
            model=binding.model,
            api_key=binding.api_key,
            max_attempts=4,
            base_delay_s=0.5,
            max_delay_s=8.0,
            timeout_s=120.0,
        )

    def _target_prompt(self, item: PackItem) -> tuple[str, str]:
        variables = {**item.prompt_fields, "task_type": self.pack.task_type}
        system_prompt = render_template(
            self.pack.prompt_template.system, variables, where="prompt_template.system"
        )
        user_prompt = render_template(
            self.pack.prompt_template.user, variables, where="prompt_template.user"
        )
        return user_prompt, system_prompt

    # ------------------------------------------------------------------ #
    # State, events and exports
    # ------------------------------------------------------------------ #
    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self._seq += 1
        record = {"seq": self._seq, "at": utc_now_iso(), "type": event_type, "payload": dict(payload)}
        self._append_jsonl("events.jsonl", record)
        if self.progress:
            with contextlib.suppress(Exception):
                self.progress(record)

    def _append_jsonl(self, name: str, payload: Mapping[str, Any]) -> None:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _progress(self, item: PackItem, target: ModelBinding) -> None:
        if self.progress:
            with contextlib.suppress(Exception):
                self.progress({"type": "progress", "item_id": item.item_id, "target_id": target.id,
                               "done": len(self.outputs), "total": len(self.items) * len(self.config.targets)})

    def _write_state(self, *, status: str, plan: Mapping[str, Any] | None = None) -> None:
        state: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "kind": "paper_pack_matrix",
            "status": status,
            "pipeline_test_only": self._pipeline_test_only(),
            "paper": {
                "id": self.pack.id,
                "version": self.pack.version,
                "title": self.pack.title,
                "task_type": self.pack.task_type,
                "methodology_kind": self.pack.methodology.kind,
                "pack_hash": self.pack.content_hash(),
                "prompt_hash": self.pack.prompt_hash(),
            },
            "config": {
                **self.config.as_dict(),
                "n_items": len(self.items) or (plan or {}).get("n_items"),
                "bootstrap_resamples": self.pack.metrics.comparison.bootstrap_resamples,
                "bootstrap_seed": self.pack.metrics.comparison.bootstrap_seed,
                "primary_metric": self.pack.metrics.primary,
                "usage": self.usage,
            },
            "plan": dict(plan or {}),
            "usage": self.usage,
            "started_utc": self._started,
            "finished_utc": utc_now_iso() if status in TERMINAL_STATUSES else None,
            "blocked_reason": self.reason,
            "report_paths": {
                "report": str(self.run_dir / "report.md"),
                "latex": str(self.run_dir / "results_table.tex"),
                "bib": str(self.run_dir / "citations.bib"),
                "bundle": str(self.run_dir / "bundle.zip"),
                "metrics": str(self.run_dir / "metrics.json"),
            },
        }
        _atomic_write_json(self.run_dir / "run.json", state)

    def _finalise(self) -> dict[str, Any]:
        metrics = analyse_matrix(
            pack=self.pack,
            items=[item.as_dict() for item in self.items],
            outputs=list(self.outputs.values()),
            scores=list(self.scores.values()),
            targets=[binding.as_dict() for binding in self.config.targets],
            judges=[binding.as_dict() for binding in self.config.judges],
            config={**self.config.as_dict(), "usage": self.usage},
            pipeline_test_only=self._pipeline_test_only(),
        )
        self.usage["estimated_usd"] = metrics["overall"].get("total_estimated_usd")
        manifest = build_manifest(
            run_id=self.run_id,
            experiment=f"paper_pack:{self.pack.id}",
            started_utc=self._started,
            models={
                binding.id: binding.model for binding in (*self.config.targets, *self.config.judges)
            },
            seeds={"dataset": self.pack.dataset.seed, "bootstrap": self.pack.metrics.comparison.bootstrap_seed,
                   "run": self.config.seed},
            prompt_hashes={"target_template": self.pack.prompt_hash()},
            dataset={"path": self.pack.dataset.path, "format": self.pack.dataset.format,
                     "n_items": len(self.items), "pack_id": self.pack.id},
            configuration={**self.config.as_dict(), "usage": self.usage},
            extra={
                "pipeline_test_only": self._pipeline_test_only(),
                "pack_hash": self.pack.content_hash(),
                "prompt_hash": self.pack.prompt_hash(),
                "notice": MEDICAL_NOTICE,
            },
        )
        _atomic_write_json(self.run_dir / "manifest.json", manifest)
        terminal_status = self.status
        # Do not publish a terminal status until the report and bundle exist:
        # the UI polls run.json and would otherwise see "completed" with no
        # bundle for a few hundred milliseconds.
        self.status = "exporting"
        self._write_state(status=self.status)
        paths = write_exports(
            run_dir=self.run_dir,
            pack=self.pack,
            metrics=metrics,
            config={
                **self.config.as_dict(),
                "usage": self.usage,
                "bootstrap_resamples": self.pack.metrics.comparison.bootstrap_resamples,
                "bootstrap_seed": self.pack.metrics.comparison.bootstrap_seed,
                "primary_metric": self.pack.metrics.primary,
            },
            run_id=self.run_id,
            outputs=list(self.outputs.values()),
            scores=list(self.scores.values()),
            pipeline_test_only=self._pipeline_test_only(),
        )
        self.status = terminal_status
        self._write_state(status=self.status)
        refresh_bundle(self.run_dir)
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "status": self.status,
            "metrics": metrics,
            "usage": self.usage,
            "paths": paths,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------- #
# Helpers shared by live runs and regrades
# ---------------------------------------------------------------------- #
def _cache_entry(
    key: str,
    model: str,
    prompt: str,
    item_hash: str,
    completion: Any,
    latency_ms: float,
) -> Any:
    from .cache import CacheEntry

    return CacheEntry(
        key=key,
        model=model,
        prompt_sha256=_sha256(prompt),
        item_hash=item_hash,
        text=str(getattr(completion, "text", "") or ""),
        input_tokens=int(getattr(completion, "input_tokens", 0) or 0),
        output_tokens=int(getattr(completion, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(completion, "reasoning_tokens", 0) or 0),
        latency_ms=float(latency_ms),
        created_utc=utc_now_iso(),
        metadata={"pipeline_test_only": bool(getattr(completion, "pipeline_test_only", False))},
    )


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        rows.append(value)
    except OSError:
        return rows
    return rows


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(json_safe(payload), stream, ensure_ascii=False, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _replace_with_retry(source: str, destination: Path, *, attempts: int = 40, delay_s: float = 0.02) -> None:
    """``os.replace`` with a short retry window.

    On Windows a replace fails with ``PermissionError`` while another thread has
    the destination open for reading (the UI polls ``run.json`` every second).
    Retrying briefly turns that transient sharing violation into a successful
    atomic update instead of a failed run.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def load_matrix_run(run_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    base = (root or default_matrix_root()) / run_id
    state = _read_json(base / "run.json")
    if state is None:
        return None
    return {
        "run": state,
        "metrics": _read_json(base / "metrics.json"),
        "report": _read_text(base / "report.md"),
        "events": _read_jsonl(base / "events.jsonl"),
        "outputs": _read_jsonl(base / "outputs.jsonl"),
        "scores": _read_jsonl(base / "scores.jsonl"),
        "paths": {
            "run": str(base),
            "report": str(base / "report.md"),
            "latex": str(base / "results_table.tex"),
            "bib": str(base / "citations.bib"),
            "bundle": str(base / "bundle.zip"),
        },
    }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def regrade_run(
    run_id: str,
    pack: PaperPack,
    *,
    judges: Sequence[ModelBinding] = (),
    dry_run: bool = True,
    root: Path | None = None,
    cache_root: Path | None = None,
    run_root: Path | None = None,
) -> dict[str, Any]:
    """Re-score cached target outputs with a new pack/grader/judge set.

    Target models are **never** called: the outputs come from the original run's
    ``outputs.jsonl``.  Judge calls are cached like any other call, so repeating a
    regrade with the same rubric is free.
    """
    base = (root or default_matrix_root()) / run_id
    state = _read_json(base / "run.json")
    if state is None:
        raise FileNotFoundError(f"unknown matrix run {run_id!r}")
    outputs = _read_jsonl(base / "outputs.jsonl")
    if not outputs:
        raise ValueError(f"run {run_id!r} has no cached target outputs to regrade")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    regrade_id = f"{run_id}-regrade-{stamp}"
    destination = (run_root or base.parent) / regrade_id
    destination.mkdir(parents=True, exist_ok=False)
    targets = [
        ModelBinding.from_mapping(row, role="target") for row in (state.get("config") or {}).get("targets") or []
    ]
    if not judges:
        judges = tuple(
            ModelBinding.from_mapping(row, role="judge")
            for row in (state.get("config") or {}).get("judges") or []
        )
    config = MatrixConfig(
        targets=tuple(targets),
        judges=tuple(judges),
        dry_run=dry_run,
        stage=str((state.get("config") or {}).get("stage") or "full"),
        seed=int((state.get("config") or {}).get("seed") or 20260101),
        max_calls=max(50, len(outputs) * max(1, len(judges)) * 2 + 10),
        max_usd=float((state.get("config") or {}).get("max_usd") or 5.0),
        run_id=regrade_id,
        resume=False,
        regrade_of=run_id,
    )
    run = MatrixRun(
        pack,
        config,
        run_root=run_root or default_matrix_root(),
        cache_root=cache_root,
    )
    run.run_id = regrade_id
    run.run_dir = destination
    run.items = load_items(pack, stage=config.stage)
    run._started = utc_now_iso()
    # Seed outputs, never call a target.
    for row in outputs:
        target_id = str(row.get("target_id"))
        item_id = str(row.get("item_id"))
        if target_id and item_id:
            run.outputs[(target_id, item_id)] = dict(row)
            run._append_jsonl("outputs.jsonl", dict(row))
    run._write_state(status="running", plan={"regrade_of": run_id, "target_calls": 0})
    run._emit("regrade_started", {"of": run_id, "pack": pack.id, "judges": [judge.id for judge in judges]})
    for target in run.config.targets:
        for item in run.items:
            output = run.outputs.get((target.id, item.item_id))
            if output is None or output.get("error"):
                continue
            run._ensure_deterministic(target, item, output)
            run._ensure_judges(target, item, output)
    run.status = "completed"
    result = run._finalise()
    run._emit("regrade_completed", {"of": run_id, "regrade_id": regrade_id})
    result["regrade_of"] = run_id
    return result
