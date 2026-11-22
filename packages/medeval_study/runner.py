"""The study runner: sample, build candidates, ask judges, resume, analyse, write.

Resume works at two levels and both are needed:

* a **response cache** keyed by model + prompt digest + temperature + seed, so a
  re-run with identical settings costs nothing;
* an **append-only judgement log** written after every single judge call, so an
  interrupted run continues from the first missing ``(item, judge, repeat)``
  even if the cache was cleared.

Money is bounded before the first call and again on every call: the plan is
refused if its call count exceeds ``--max-api-calls``, and a process-local
:class:`~medeval_core.safety.SpendGuard` stops the run if retries would push past
the cap. A budget stop is recorded as such, never hidden.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from medeval_core.backends import BACKEND_API, BackendSelection
from medeval_core.calllog import JsonlCallSink, new_run_id, read_calls, utc_now_iso
from medeval_core.client import LLMClient, OpenAICompatibleTransport
from medeval_core.config import Settings
from medeval_core.cost import CallPlan, estimate_tokens, plan_summary
from medeval_core.manifest import build_manifest, sha256_text, write_manifest
from medeval_core.paths import results_dir
from medeval_core.safety import ChainedGuard, PriceCard, ResponseCache, SpendGuard
from medeval_study.analysis import analyse_study
from medeval_study.datasets import DatasetBundle, DatasetUnavailable, dataset_cache_status
from medeval_study.judging import (
    BACKEND_MOCK,
    FAILURE_REFUSED,
    JudgeRunner,
    JudgeSpec,
    MockJudgeTransport,
    Verdict,
    classify_failure,
)
from medeval_study.models import CandidateResponse, McqItem
from medeval_study.prompts import SYSTEM_PROMPT, prompt_fingerprint, render_prompt
from medeval_study.report import write_reports
from medeval_study.responses import build_candidate_response, template_inventory
from medeval_study.sampling import SamplingPlan, select_items

__all__ = [
    "ARCHIVED_EXP002_JUDGE_IDS",
    "JudgeBinding",
    "StudyConfig",
    "StudyRun",
    "build_judges",
    "default_run_root",
]

ProgressCallback = Callable[[dict[str, Any]], None]


def default_run_root() -> Path:
    return results_dir() / "study_runs"


def _optional_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pilot_gate_decision(
    verdicts: Sequence[Verdict],
    *,
    truth_by_item: Mapping[str, bool],
    pilot_items: Sequence[str],
    min_disagreements: int,
) -> dict[str, Any]:
    """The pre-registered Experiment 003 gate.

    Count judge-versus-key disagreements on the pilot items (primary repeat,
    parseable verdicts). Continue only if the floor is reached; otherwise stop,
    report the ceiling and make no correlation claim.
    """
    pilot_ids = set(pilot_items)
    disagreements = 0
    scored = 0
    for verdict in verdicts:
        if verdict.item_id not in pilot_ids or verdict.repeat != 0 or not verdict.parse_ok:
            continue
        was_correct = truth_by_item.get(verdict.item_id)
        if was_correct is None:
            continue
        scored += 1
        if (verdict.verdict == "CORRECT") != was_correct:
            disagreements += 1
    passed = disagreements >= min_disagreements
    return {
        "pilot_size": len(pilot_ids),
        "min_disagreements_required": min_disagreements,
        "n_scored_pilot_verdicts": scored,
        "n_disagreements": disagreements,
        "passed": passed,
        "decision": (
            "continue with the remaining items"
            if passed
            else (
                f"STOP: only {disagreements} disagreement(s) across all judges on the {len(pilot_ids)} pilot items "
                f"(needed {min_disagreements}). Report the ceiling; make no correlation claim."
            )
        ),
    }


@dataclass(frozen=True)
class JudgeBinding:
    """One judge bound to one connection.

    This is plain data so ``medeval_study`` never imports the web layer: the app
    builds bindings from its :class:`~medeval_web.connections.Connection` store
    and memory-only :class:`~medeval_web.connections.KeyStore`, and the CLI can
    build them from environment settings.

    ``api_key`` lives here **in memory only** — it is stripped from every
    serialized form and is redacted from logs and caches by the client.
    """

    judge_id: str
    connection_id: str
    family_label: str
    backend: str = "api"
    base_url: str = ""
    api_style: str = "chat"
    model: str = ""
    api_key: str | None = None
    temperature: float = 0.3
    repeats: int = 1
    max_tokens: int = 2048
    call_cap: int = 500
    usd_cap: float = 5.0
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None

    def as_dict(self, *, include_key: bool = False) -> dict[str, Any]:
        """Serializable form. The key is stripped unless explicitly requested."""
        payload = {
            "judge_id": self.judge_id,
            "connection_id": self.connection_id,
            "family_label": self.family_label,
            "backend": self.backend,
            "base_url": self.base_url,
            "api_style": self.api_style,
            "model": self.model,
            "temperature": self.temperature,
            "repeats": self.repeats,
            "max_tokens": self.max_tokens,
            "call_cap": self.call_cap,
            "usd_cap": self.usd_cap,
            "price_in_per_mtok": self.price_in_per_mtok,
            "price_out_per_mtok": self.price_out_per_mtok,
        }
        if include_key:
            payload["api_key"] = self.api_key
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JudgeBinding:
        known = {name for name in cls.__dataclass_fields__ if name != "api_key"}
        fields = {key: value for key, value in payload.items() if key in known}
        binding = cls(**fields)  # type: ignore[arg-type]
        if payload.get("api_key"):
            binding = replace(binding, api_key=str(payload["api_key"]))
        return binding


@dataclass(frozen=True)
class StudyConfig:
    """Everything a run needs to be reproducible.

    Judges come either from the legacy ``judges`` ids (Experiment 002's fixed
    pair) or from ``connection_judges`` (v0.3 bindings, one per judge), never a
    mixture. ``repeats`` is the default repeat count; ``consistency_subsample``
    limits the 3-repeat consistency pass to N items instead of all of them.
    """

    experiment: str = "002_judge_families"
    dataset: str = "medqa_4options"
    split: str = "dev"
    n_items: int = 60
    seed: int = 20260101
    repeats: int = 3
    temperature: float = 0.3
    primary_repeat: int = 0
    max_api_calls: int = 500
    allow_download: bool = True
    dry_run: bool = False
    judges: tuple[str, ...] = ()
    connection_judges: tuple[dict[str, Any], ...] = ()
    consistency_subsample: int = 0
    pilot_items: int = 0
    pilot_min_disagreements: int = 0
    screening_binding: dict[str, Any] | None = None
    screening_size: int = 600
    run_id: str | None = None
    resume: bool = True
    pace_seconds: float = 0.0
    #: Staged execution (Experiment 003): which confirmable stage to run.
    #: "all" runs every stage in one process (dry runs and tests). The app
    #: advances one confirmed stage at a time. Not part of the design, so it is
    #: deliberately excluded from the config lock.
    stage: str = "all"

    def as_dict(self) -> dict[str, Any]:
        """Serialized form. Judge bindings are stripped of their API keys: the
        key is a process-memory value and must never reach run.json, the manifest
        or any report."""
        return {
            "experiment": self.experiment,
            "dataset": self.dataset,
            "split": self.split,
            "n_items": self.n_items,
            "seed": self.seed,
            "repeats": self.repeats,
            "temperature": self.temperature,
            "primary_repeat": self.primary_repeat,
            "max_api_calls": self.max_api_calls,
            "allow_download": self.allow_download,
            "dry_run": self.dry_run,
            "judges": list(self.judges),
            "connection_judges": [
                {key: value for key, value in binding.items() if key != "api_key"}
                for binding in self.connection_judges
            ],
            "consistency_subsample": self.consistency_subsample,
            "pilot_items": self.pilot_items,
            "pilot_min_disagreements": self.pilot_min_disagreements,
            "screening_size": self.screening_size,
            "screening_binding": (
                {key: value for key, value in self.screening_binding.items() if key != "api_key"}
                if self.screening_binding
                else None
            ),
            "pace_seconds": self.pace_seconds,
            "stage": self.stage,
        }


#: The judge identifiers recorded in the archived Experiment 002 run. They name
#: one provider configuration (a remote DeepSeek-family API judge and a local
#: Llama-family judge via Ollama) and are kept **only** so archived runs can be
#: read and so their dry run can be reproduced with mock stand-ins. No new-run
#: code resolves them to a live provider, model or environment variable: a live
#: run takes its judges from saved connections or ``connections.yaml``.
ARCHIVED_EXP002_JUDGE_IDS: tuple[str, ...] = ("j1_deepseek", "j2_ollama")

#: Dry-run stand-ins for the archived pair, keyed by the original judge id:
#: (mock judge id, mock model, label).
_ARCHIVED_MOCK_JUDGES: dict[str, tuple[str, str, str]] = {
    "j1_deepseek": ("j1_mock", "mock-judge-a", "J1"),
    "j2_ollama": ("j2_mock", "mock-judge-b", "J2"),
}

#: The one message a live run gets when it names the archived configuration.
_ARCHIVED_LIVE_REASON = (
    "the archived Experiment 002 judge ids only identify the original run's configuration "
    "(one specific provider, model and family). A new run needs your own connections: add one "
    "on the Models page (medeval ui) or pass --connections connections.yaml. A dry run works "
    "without any connection."
)


def build_judges(
    config: StudyConfig,
    settings: Settings,
    *,
    selection: BackendSelection | None = None,
) -> tuple[list[JudgeSpec], dict[str, Any]]:
    """Resolve the requested judges, recording why any is unavailable.

    No substitution happens: a judge that cannot be built is returned as
    unavailable with a reason, and the study runs with the rest. That keeps the
    report honest -- one judge is reported as one judge, never as a jury.

    Two sources of judges, never mixed:

    * ``config.connection_judges`` — bring-your-own-key bindings, one
      :class:`JudgeBinding` per judge, built by the app's Models page or by a
      ``connections.yaml`` file (keys from environment variables only). This is
      the only path a new live run uses, for Experiment 002 and 003 alike;
    * ``config.judges`` — the archived Experiment 002 identifiers. They are kept
      to read that run and to let its dry run use mock stand-ins; a *live* run
      that names them is refused with the "add a connection" message rather than
      resolving a provider, model or environment variable.
    """
    specs: list[JudgeSpec] = []
    detail: dict[str, Any] = {"requested": list(config.judges), "resolved": {}}

    if config.connection_judges:
        bindings = [JudgeBinding.from_dict(row) for row in config.connection_judges]
        for index, binding in enumerate(bindings, start=1):
            if config.dry_run:
                specs.append(
                    JudgeSpec(
                        judge_id=binding.judge_id,
                        family="mock",
                        backend=BACKEND_MOCK,
                        model=f"mock-judge-{chr(96 + index)}" if index <= 26 else f"mock-judge-{index}",
                        temperature=binding.temperature,
                        repeats=binding.repeats,
                        notes="dry-run stand-in for a saved connection; PIPELINE TEST ONLY",
                    )
                )
                detail["resolved"][binding.judge_id] = "replaced by a mock judge (--dry-run)"
                continue
            specs.append(
                JudgeSpec(
                    judge_id=binding.judge_id,
                    family=binding.family_label or "unlabelled-family",
                    backend=BACKEND_API if binding.backend == "api" else binding.backend,
                    model=binding.model,
                    base_url=binding.base_url,
                    temperature=binding.temperature,
                    repeats=binding.repeats,
                    available=True,
                    notes=f"connection {binding.connection_id} ({binding.family_label or 'family not set'})",
                )
            )
            detail["resolved"][binding.judge_id] = f"connection {binding.connection_id}"
        detail["available"] = [spec.judge_id for spec in specs if spec.available]
        detail["unavailable"] = [spec.judge_id for spec in specs if not spec.available]
        return specs, detail

    for judge_id in config.judges:
        if judge_id not in ARCHIVED_EXP002_JUDGE_IDS:
            raise ValueError(f"unknown judge id {judge_id!r}")
        mock_id, mock_model, label = _ARCHIVED_MOCK_JUDGES[judge_id]
        if not config.dry_run:
            specs.append(
                JudgeSpec(
                    judge_id=judge_id,
                    family="archived-configuration",
                    backend=BACKEND_MOCK,
                    model="",
                    base_url=None,
                    temperature=config.temperature,
                    repeats=config.repeats,
                    available=False,
                    unavailable_reason=_ARCHIVED_LIVE_REASON,
                )
            )
            detail["resolved"][judge_id] = "unavailable: archived configuration (read-only)"
            continue
        specs.append(
            JudgeSpec(
                judge_id=mock_id,
                family="mock",
                backend=BACKEND_MOCK,
                model=mock_model,
                temperature=config.temperature,
                repeats=config.repeats,
                notes=(
                    f"dry-run stand-in for {label} in the original Experiment 002 "
                    "configuration; PIPELINE TEST ONLY"
                ),
            )
        )
        detail["resolved"][judge_id] = f"replaced by {mock_id} (--dry-run)"
    detail["available"] = [spec.judge_id for spec in specs if spec.available]
    detail["unavailable"] = [spec.judge_id for spec in specs if not spec.available]
    return specs, detail


class StudyRun:
    """One executed study, from sampling to report."""

    def __init__(
        self,
        config: StudyConfig,
        settings: Settings,
        *,
        run_root: Path | None = None,
        progress: ProgressCallback | None = None,
        selection: BackendSelection | None = None,
        stop_flag: Any | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.run_root = run_root or default_run_root()
        self.progress = progress
        self.selection = selection
        self.stop_flag = stop_flag
        self.run_id = config.run_id or new_run_id("exp002")
        self.run_dir = self.run_root / self.run_id
        self.state: dict[str, Any] = {
            "run_id": self.run_id,
            "experiment": config.experiment,
            "status": "planned",
            "started_utc": utc_now_iso(),
        }
        self.verdicts: list[Verdict] = []
        #: Set when a dry run fell back to the built-in synthetic sample.
        self.dataset_fallback: str | None = None

    # -- helpers ---------------------------------------------------------- #
    def _emit(self, message: str, **fields: Any) -> None:
        if self.progress is None:
            return
        payload = {"message": message, "timestamp_utc": utc_now_iso(), **fields}
        with contextlib.suppress(Exception):  # a broken UI callback must not kill a paid run
            self.progress(payload)

    def _write_state(self) -> None:
        # exist_ok=True can still raise FileExistsError on Windows when the run
        # thread and the caller create the directory at the same time.
        with contextlib.suppress(FileExistsError):
            self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run.json").write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _merge_existing_state(self) -> None:
        """Carry forward what a resumed (or staged) run already recorded.

        Verdicts live in ``judgements.jsonl`` and are re-read independently, but
        run-level facts — the pilot gate decision, strata, the screening summary,
        the stage history and the original start time — live in ``run.json``. A
        staged Experiment 003 run is several processes over the same directory,
        so without this every stage would forget that the previous one ran.
        """
        path = self.run_dir / "run.json"
        if not path.is_file():
            return
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(existing, dict):
            return
        for key, value in existing.items():
            if key in {"status", "finished_utc"}:
                continue  # the new stage decides these
            self.state.setdefault(key, value)
        self.state["started_utc"] = existing.get("started_utc") or self.state.get("started_utc")

    def _prices(self) -> PriceCard:
        return PriceCard(
            self.settings.input_usd_per_million,
            self.settings.output_usd_per_million,
            self.settings.reasoning_usd_per_million,
            self.settings.price_source,
        )

    # -- planning --------------------------------------------------------- #
    def load(self) -> tuple[DatasetBundle, SamplingPlan, dict[str, CandidateResponse]]:
        bundle = self._load_bundle()
        self._emit(
            f"loaded {bundle.n} usable items from {bundle.dataset}/{bundle.split} "
            f"({bundle.licence.licence}, via {bundle.strategy})",
            phase="load",
        )
        sampling = select_items(bundle.items, n=self.config.n_items, seed=self.config.seed)
        responses = {
            row.item.item_id: build_candidate_response(row.item, condition=row.condition, seed=self.config.seed)
            for row in sampling.items
        }
        self._emit(
            f"sampled {sampling.n} items ({sampling.discarded_duplicate_stems} duplicate stems discarded); "
            f"{sampling.condition_counts.get('correct', 0)} correct / {sampling.condition_counts.get('incorrect', 0)} incorrect candidates",
            phase="sample",
        )
        return bundle, sampling, responses

    def _load_bundle(self) -> DatasetBundle:
        """Read the dataset from the local cache only, or fall back for a dry run.

        A run never downloads: if the data is not cached, a dry run uses the
        labelled synthetic sample immediately (no network, no retries, no stall)
        and a real run raises with the fetch command. Downloading happens only
        when the owner runs ``medeval data fetch``.
        """
        cached = dataset_cache_status(self.config.dataset, self.config.split)
        if cached is not None:
            return cached

        not_cached = (
            f"{self.config.dataset}/{self.config.split} is not in the local cache "
            "(no download is attempted during a run)"
        )
        if not self.config.dry_run:
            raise DatasetUnavailable(not_cached)

        from medeval_study.synthetic import SYNTHETIC_SAMPLE_SIZE, builtin_synthetic_bundle

        sample_size = max(SYNTHETIC_SAMPLE_SIZE, self.config.n_items * 2)
        bundle = builtin_synthetic_bundle(n=sample_size, seed=self.config.seed)
        self.dataset_fallback = not_cached
        self.state["dataset_fallback"] = self.dataset_fallback
        self._emit(
            f"{not_cached}; using the BUILT-IN SYNTHETIC SAMPLE ({bundle.n} items) - "
            "PIPELINE TEST ONLY, the numbers describe no model",
            phase="load",
        )
        return bundle

    def plan(self, sampling: SamplingPlan, judges: Sequence[JudgeSpec]) -> dict[str, Any]:
        usable = [judge for judge in judges if judge.available]
        sample_prompt = render_prompt(sampling.items[0].item.judge_view(), "candidate answer placeholder")
        prompt_tokens = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(sample_prompt)
        subsample = max(0, min(self.config.consistency_subsample, sampling.n))
        plans = [
            CallPlan(
                calls=self._calls_for_judge(judge, sampling.n, subsample),
                prompt_tokens_each=prompt_tokens,
                max_output_tokens_each=(judge.backend != BACKEND_MOCK and self._max_tokens_for(judge)) or self.settings.max_tokens,
                label=judge.judge_id,
            )
            for judge in usable
        ]
        summary = plan_summary(plans, self._prices())
        summary.update(
            {
                "n_items": sampling.n,
                "repeats": self.config.repeats,
                "per_judge_repeats": {judge.judge_id: judge.repeats for judge in usable},
                "consistency_subsample": self.config.consistency_subsample,
                "judges": [judge.judge_id for judge in usable],
                "max_api_calls": self.config.max_api_calls,
                "worst_case_with_one_retry_each": summary["total_calls"] * 2,
                "within_call_cap": summary["total_calls"] <= self.config.max_api_calls,
                "max_usd_cap": self.settings.max_usd,
            }
        )
        return summary

    def _calls_for_judge(self, judge: JudgeSpec, n_items: int, subsample: int) -> int:
        """Calls for one judge: primary pass plus the consistency pass."""
        if subsample > 0:
            return n_items + 2 * subsample
        return n_items * max(1, judge.repeats)

    def _max_tokens_for(self, judge: JudgeSpec) -> int:
        binding = self._binding_for(judge.judge_id)
        return int(binding.get("max_tokens") or self.settings.max_tokens) if binding else self.settings.max_tokens

    def _binding_for(self, judge_id: str) -> dict[str, Any] | None:
        for row in self.config.connection_judges:
            if row.get("judge_id") == judge_id:
                return row
        return None

    # -- execution -------------------------------------------------------- #
    #: Fields that define a run's identity. A resume that changes any of them
    #: would mix judgements from two different designs, so it is refused before
    #: any call rather than silently merged.
    _LOCKED_FIELDS = (
        "dataset",
        "split",
        "n_items",
        "seed",
        "repeats",
        "temperature",
        "primary_repeat",
        "judges",
        "connection_judges",
        "consistency_subsample",
        "pilot_items",
        "pilot_min_disagreements",
        "dry_run",
    )

    def config_lock_diff(self) -> list[str]:
        """Return human-readable differences between the stored and requested config.

        Empty when there is nothing stored yet or everything matches. Lists and
        binding metadata are normalised so JSON round-trips compare equal; keys
        are never part of the comparison (they are not stored).
        """
        state_path = self.run_dir / "run.json"
        if not state_path.is_file():
            return []
        try:
            stored = json.loads(state_path.read_text(encoding="utf-8")).get("config") or {}
        except (OSError, ValueError):
            return ["stored run.json exists but is unreadable; refusing to merge unknown state"]
        requested = self.config.as_dict()
        diffs: list[str] = []
        for field in self._LOCKED_FIELDS:
            old, new = stored.get(field), requested.get(field)
            if isinstance(old, list):
                old = tuple(old)
            if isinstance(new, list):
                new = tuple(new)
            if field == "connection_judges":
                old = tuple(sorted(json.dumps(row, sort_keys=True) for row in (old or ())))
                new = tuple(sorted(json.dumps(row, sort_keys=True) for row in (new or ())))
            if old != new:
                diffs.append(f"{field}: stored {old!r} != requested {new!r}")
        return diffs

    def execute(self) -> dict[str, Any]:
        config = self.config
        if config.resume:
            self._merge_existing_state()
            diffs = self.config_lock_diff()
            if diffs:
                self.state.update(
                    {
                        "status": "refused",
                        "refused_reason": "config lock: resumed run changed " + "; ".join(diffs),
                    }
                )
                self.run_dir.mkdir(parents=True, exist_ok=True)
                self._write_state()
                for diff in diffs:
                    print(f"[config-lock] {diff}")
                return {
                    "status": "refused",
                    "run_dir": str(self.run_dir),
                    "run_id": self.run_id,
                    "config_lock_diffs": diffs,
                }
        judges, judge_detail = build_judges(config, self.settings, selection=self.selection)
        try:
            bundle, sampling, responses = self.load()
        except DatasetUnavailable as exc:
            from medeval_study.synthetic import fetch_command

            reason = (
                f"the dataset could not be read ({exc}). Fetch it once with: "
                f"{fetch_command(config.dataset)} - then start the run again. "
                "A dry run works without the data: it uses a labelled synthetic sample."
            )
            self.state.update({"status": "blocked", "blocked_reason": reason})
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._write_state()
            self._emit(reason, phase="blocked")
            return {
                "status": "blocked",
                "blocked_reason": reason,
                "run_dir": str(self.run_dir),
                "run_id": self.run_id,
            }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        plan = self.plan(sampling, judges)
        self.state.update(
            {
                "status": "running",
                "config": config.as_dict(),
                "judges": [judge.as_dict() for judge in judges],
                "judge_resolution": judge_detail,
                "dataset": bundle.as_dict(),
                "sampling": sampling.as_dict(),
                "plan": plan,
            }
        )
        self._write_state()
        usd_upper = plan.get("usd_upper_bound")
        self._emit(
            f"planned {plan['total_calls']} calls, worst-case "
            f"{'unknown (prices unverified)' if usd_upper is None else f'{usd_upper:.4f} USD'}",
            phase="plan",
            progress=0,
            total=plan["total_calls"],
        )

        usable = [judge for judge in judges if judge.available]
        if not usable:
            reasons = [f"{judge.judge_id}: {judge.unavailable_reason}" for judge in judges if judge.unavailable_reason]
            if not judges:
                # A fresh install has no provider and no key, so this is the first
                # thing a stranger can hit: say what to do, not just that it failed.
                reason = (
                    "no judge configured. A run gets its judges from provider connections you add yourself: "
                    "start the app (medeval ui) and add a connection on the Models page, or pass "
                    "--connections connections.yaml to the CLI. A dry run needs neither "
                    "(medeval run exp003 --dry-run)."
                )
            else:
                reason = "no judge could be constructed: " + "; ".join(reasons)
            self.state.update({"status": "blocked", "blocked_reason": reason})
            self._write_state()
            self._emit(reason, phase="blocked")
            return {"status": "blocked", "blocked_reason": reason, "run_dir": str(self.run_dir), "run_id": self.run_id}

        if plan["total_calls"] > config.max_api_calls:
            self.state.update(
                {
                    "status": "refused",
                    "refused_reason": (
                        f"plan needs {plan['total_calls']} calls, above --max-api-calls {config.max_api_calls}"
                    ),
                }
            )
            self._write_state()
            return {"status": "refused", "run_dir": str(self.run_dir), "run_id": self.run_id, "plan": plan}

        self._persist_items(bundle, sampling, responses)
        clients, judge_guards, _all_secrets = self._build_clients(usable, sampling, responses)
        guard = SpendGuard(config.max_api_calls, self.settings.max_usd, self._prices())
        for judge_id, client in clients.items():
            per_connection = judge_guards.get(judge_id)
            client.guard = (
                ChainedGuard(per_connection, guard, labels=(f"connection {judge_id}", "run total"))
                if per_connection
                else guard
            )

        completed = self._completed_keys()
        total = plan["total_calls"]
        done = len(completed)
        started = time.perf_counter()
        budget_stopped = False
        stopped_by_user = False
        subsample = max(0, min(config.consistency_subsample, sampling.n))
        subsample_items = list(sampling.items)[:subsample] if subsample else []

        def _run_pass(judge: JudgeSpec, runner: JudgeRunner, repeat: int, sampled_items: Sequence[Any]) -> None:
            """One (judge, repeat) pass over a list of sampled items."""
            nonlocal done, budget_stopped, stopped_by_user
            for sampled in sampled_items:
                if self.stop_flag is not None and self.stop_flag.is_set():
                    stopped_by_user = True
                    self._emit("stopped on request; writing reports from completed judgements", phase="stop")
                    return
                item = sampled.item
                key = (item.item_id, judge.judge_id, repeat)
                if key in completed:
                    continue
                if config.pace_seconds:
                    time.sleep(config.pace_seconds)
                try:
                    verdict = runner.judge(
                        item.judge_view(),
                        responses[item.item_id].text,
                        repeat=repeat,
                    )
                except RuntimeError as exc:
                    if "spend guard" in str(exc):
                        budget_stopped = True
                        refused = Verdict(
                            item_id=item.item_id,
                            judge_id=judge.judge_id,
                            repeat=repeat,
                            verdict=None,
                            confidence=None,
                            parse_ok=False,
                            attempts=0,
                            model=judge.model,
                            prompt_sha256="",
                            latency_ms=0.0,
                            error=f"refused: {exc}",
                            failure_type=FAILURE_REFUSED,
                        )
                        self._append_verdict(refused)
                        self.verdicts.append(refused)
                        self._emit(f"stopped by budget guard: {exc}", phase="budget_stop")
                        return
                    raise
                self._append_verdict(verdict)
                self.verdicts.append(verdict)
                completed.add(key)
                done += 1
                self._emit(
                    f"{judge.judge_id} repeat {repeat}: {item.item_id} -> "
                    f"{verdict.verdict or verdict.error}",
                    phase="judge",
                    progress=done,
                    total=total,
                    judge=judge.judge_id,
                    repeat=repeat,
                    item_id=item.item_id,
                )

        pilot_size = max(0, min(config.pilot_items, len(sampling.items)))
        pilot_stopped = False
        awaiting_confirmation = False
        remaining = list(sampling.items)
        if pilot_size > 0:
            # Pre-registered pilot gate (Experiment 003): all judges grade the
            # first pilot_size items (hard-pool ordered first); the run stops
            # unless the disagreement floor is reached. The partial analysis is
            # still written, so the ceiling is reported without a correlation
            # claim.
            pilot_items_list = list(sampling.items)[:pilot_size]
            stored_gate = self.state.get("pilot_gate")
            gate_already_passed = isinstance(stored_gate, dict) and stored_gate.get("passed") is True
            if not gate_already_passed:
                for judge in usable:
                    runner = JudgeRunner(judge, clients[judge.judge_id], base_seed=config.seed)
                    if budget_stopped or stopped_by_user:
                        break
                    _run_pass(judge, runner, 0, pilot_items_list)
                if not budget_stopped and not stopped_by_user:
                    truth = {
                        row.item.item_id: responses[row.item.item_id].chosen_letter == row.item.key_letter
                        for row in sampling.items
                    }
                    decision = _pilot_gate_decision(
                        self._read_verdicts(),
                        truth_by_item=truth,
                        pilot_items=[row.item.item_id for row in pilot_items_list],
                        min_disagreements=config.pilot_min_disagreements,
                    )
                    self.state["pilot_gate"] = decision
                    self._write_state()
                    self._emit(f"pilot gate: {decision['decision']}", phase="pilot_gate")
                    if not decision["passed"]:
                        pilot_stopped = True
                        remaining = []
            if pilot_stopped:
                remaining = []
            elif getattr(config, "stage", "all") == "pilot":
                # Staged controller: stop after the gate and let the owner review
                # the decision card before the remaining items spend money.
                awaiting_confirmation = True
                remaining = []

        if not pilot_stopped:
            for judge in usable:
                runner = JudgeRunner(judge, clients[judge.judge_id], base_seed=config.seed)
                if subsample > 0:
                    # Primary pass: one repeat over every item. Consistency pass:
                    # two more repeats over the pre-registered subsample only.
                    _run_pass(judge, runner, 0, remaining)
                    for repeat in (1, 2):
                        if budget_stopped or stopped_by_user:
                            break
                        _run_pass(judge, runner, repeat, subsample_items)
                else:
                    for repeat in range(max(1, judge.repeats)):
                        if budget_stopped or stopped_by_user:
                            break
                        _run_pass(judge, runner, repeat, remaining)
                if budget_stopped or stopped_by_user:
                    break

        elapsed = time.perf_counter() - started
        calls = read_calls(self.run_dir / "calls.jsonl") if (self.run_dir / "calls.jsonl").exists() else []
        cache_hits = sum(1 for row in calls if (row.get("metadata") or {}).get("cache_hit"))
        input_tokens = sum(int((row.get("metadata") or {}).get("input_tokens") or 0) for row in calls)
        output_tokens = sum(int((row.get("metadata") or {}).get("output_tokens") or 0) for row in calls)
        reasoning_tokens = sum(int((row.get("metadata") or {}).get("reasoning_tokens") or 0) for row in calls)
        prices = self._prices()

        all_verdicts = self._read_verdicts()
        metrics = analyse_study(
            items=[row.item for row in sampling.items],
            responses=responses,
            verdicts=all_verdicts,
            judges=judges,
            primary_repeat=config.primary_repeat,
            repeats=3 if subsample > 0 else max(1, config.repeats),
            seed=config.seed,
            pipeline_test_only=any(judge.pipeline_test_only for judge in usable),
        )
        auth_status = self._auth_probe(usable)
        metrics["auth"] = auth_status
        metrics["experiment"] = config.experiment
        if getattr(self, "dataset_fallback", None):
            metrics["dataset_fallback"] = self.dataset_fallback

        # Reasoning-model budget warning: the v0.2 abort came from hidden
        # reasoning consuming the whole max_tokens allowance, which shows up as
        # output_tokens == reasoning_tokens == max_tokens with no visible text.
        suspicious: list[dict[str, Any]] = []
        for judge in usable:
            judge_calls = [
                row for row in calls
                if (row.get("metadata") or {}).get("judge_id") == judge.judge_id
            ]
            budget = self._max_tokens_for(judge)
            truncated = sum(
                1 for row in judge_calls
                if not row.get("error")
                and int((row.get("metadata") or {}).get("output_tokens") or 0) >= budget
                and int((row.get("metadata") or {}).get("reasoning_tokens") or 0)
                >= int((row.get("metadata") or {}).get("output_tokens") or 0)
            )
            if truncated:
                suspicious.append({
                    "judge_id": judge.judge_id,
                    "n_truncated_calls": truncated,
                    "max_tokens": budget,
                    "advice": f"raise this connection's max_tokens above {budget} or disable thinking mode",
                })
        if suspicious:
            self._emit(
                "WARNING: " + "; ".join(
                    f"{row['judge_id']}: {row['n_truncated_calls']} call(s) used the whole max_tokens budget "
                    f"({row['max_tokens']}) on hidden reasoning — {row['advice']}"
                    for row in suspicious
                ),
                phase="reasoning_warning",
            )

        mock_only = all(judge.pipeline_test_only for judge in usable)
        usage = {
            "calls_made": guard.calls,
            "calls_logged": len(calls),
            "cache_hits": cache_hits,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "reasoning_budget_warnings": suspicious,
            "estimated_usd": None if mock_only else prices.estimate(input_tokens, output_tokens, reasoning_tokens),
            "price_source": prices.source,
            "prices_verified": prices.verified,
            "mock_run": mock_only,
            "spend_note": "mock run: no network calls and no spend" if mock_only else None,
            "max_api_calls": config.max_api_calls,
            "max_usd": self.settings.max_usd,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.state.update(
            {
                "status": (
                    "pilot_stopped" if pilot_stopped
                    else "awaiting_confirmation" if awaiting_confirmation
                    else "stopped" if stopped_by_user
                    else "budget_stopped" if budget_stopped
                    else "completed"
                ),
                "finished_utc": utc_now_iso(),
                "usage": usage,
                "n_judgements": len(all_verdicts),
            }
        )
        if awaiting_confirmation:
            self.state["next_stage"] = "remaining"
        self._write_state()

        manifest = build_manifest(
            run_id=self.run_id,
            experiment=config.experiment,
            started_utc=self.state["started_utc"],
            models={judge.judge_id: judge.model for judge in judges},
            seeds={"sampling": config.seed, "judge_base": config.seed, "primary_repeat": config.primary_repeat},
            prompt_hashes=prompt_fingerprint(),
            dataset=bundle.as_dict(),
            configuration={**config.as_dict(), "response_templates": template_inventory()},
            extra={
                "judges": [judge.as_dict() for judge in judges],
                "judge_resolution": judge_detail,
                "usage": usage,
                "sampling": sampling.as_dict(),
                "status": self.state["status"],
            },
        )
        manifest["status"] = self.state["status"]
        manifest["usage"] = usage
        write_manifest(self.run_dir / "manifest.json", manifest)
        write_reports(
            run_dir=self.run_dir,
            run_id=self.run_id,
            config=config,
            bundle=bundle,
            sampling=sampling,
            responses=responses,
            judges=judges,
            verdicts=all_verdicts,
            metrics=metrics,
            manifest=manifest,
            usage=usage,
            judge_detail=judge_detail,
        )
        return {
            "status": self.state["status"],
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "plan": plan,
            "usage": usage,
            "metrics": metrics,
        }

    # -- plumbing --------------------------------------------------------- #
    def _persist_items(
        self,
        bundle: DatasetBundle,
        sampling: SamplingPlan,
        responses: Mapping[str, CandidateResponse],
    ) -> None:
        """Write the local-only item and candidate files (git-ignored)."""
        with (self.run_dir / "items.jsonl").open("w", encoding="utf-8") as handle:
            for sampled in sampling.items:
                handle.write(json.dumps({**sampled.item.as_dict(), "condition": sampled.condition}, ensure_ascii=False) + "\n")
        with (self.run_dir / "candidates.jsonl").open("w", encoding="utf-8") as handle:
            for sampled in sampling.items:
                handle.write(json.dumps(responses[sampled.item.item_id].as_dict(), ensure_ascii=False) + "\n")

    def _build_clients(
        self,
        judges: Sequence[JudgeSpec],
        sampling: SamplingPlan,
        responses: Mapping[str, CandidateResponse],
    ) -> tuple[dict[str, LLMClient], dict[str, SpendGuard], tuple[str, ...]]:
        """Build one client per judge plus per-connection spend guards.

        Returns ``(clients, guards, all_secrets)``. Guards: a run-level guard on
        every client, and for connection-backed judges an additional guard with
        that connection's own call and USD caps. ``all_secrets`` is every key in
        play, for the redaction sinks — never log the value.
        """
        from medeval_study.models import JudgeView  # local import keeps module surface small

        oracle: dict[str, bool] = {}
        for sampled in sampling.items:
            view: JudgeView = sampled.item.judge_view()
            prompt = render_prompt(view, responses[sampled.item.item_id].text)
            oracle[sha256_text(prompt)] = responses[sampled.item.item_id].chosen_letter == sampled.item.key_letter

        cache = ResponseCache(self.run_dir / "cache.json")
        clients: dict[str, LLMClient] = {}
        guards: dict[str, SpendGuard] = {}
        all_secrets: list[str] = [self.settings.api_key] if self.settings.api_key else []
        for judge in judges:
            binding = self._binding_for(judge.judge_id)
            binding_key = (binding or {}).get("api_key")
            if binding_key:
                all_secrets.append(binding_key)
            judge_secrets = tuple(secret for secret in (self.settings.api_key, binding_key) if secret)
            sink = JsonlCallSink(self.run_dir / "calls.jsonl", secrets=judge_secrets)
            if judge.backend == BACKEND_MOCK:
                accuracy = 0.78 if judge.judge_id.endswith("mock") and "j1" in judge.judge_id else 0.68
                transport = MockJudgeTransport(
                    model=judge.model,
                    accuracy=accuracy,
                    seed=self.config.seed,
                    oracle=oracle,
                )
            else:
                transport_kwargs: dict[str, Any] = {
                    "base_url": judge.base_url or "",
                    "model": judge.model,
                    "api_key": binding_key if binding else (self.settings.api_key if judge.backend == BACKEND_API else None),
                    "backend": judge.backend,
                    "retries": self.settings.retries,
                    "timeout_s": self.settings.timeout_s,
                    "request_logprobs": bool(self.settings.request_logprobs),
                }
                # The v0.4 retry policy (attempt budget, backoff, Retry-After)
                # lives in the transport; pass it only when the transport
                # accepts it so older builds keep working.
                try:
                    transport = OpenAICompatibleTransport(
                        **transport_kwargs,
                        max_attempts=self.settings.retry_attempts,
                        base_delay_s=self.settings.retry_base_delay_s,
                        max_delay_s=self.settings.retry_max_delay_s,
                        max_concurrency=self.settings.max_concurrency,
                    )
                except TypeError:  # pragma: no cover - defensive for older transports
                    transport = OpenAICompatibleTransport(**transport_kwargs)
            client_settings = replace(
                self.settings,
                temperature=(binding or {}).get("temperature", self.config.temperature),
                max_tokens=int((binding or {}).get("max_tokens") or self.settings.max_tokens),
                api_key=binding_key or self.settings.api_key,
            )
            client = LLMClient(
                transport,
                client_settings,
                sink=sink,
                run_id=self.run_id,
                backend_label=f"{judge.backend}:{judge.model}",
                cache=cache,
            )
            clients[judge.judge_id] = client
            if binding:
                # Per-connection caps are enforced before every call, on top of
                # the run-level guard.
                prices = PriceCard(
                    _optional_price(binding.get("price_in_per_mtok")),
                    _optional_price(binding.get("price_out_per_mtok")),
                    None,
                    "connection-declared",
                )
                guards[judge.judge_id] = SpendGuard(
                    int(binding.get("call_cap") or 500),
                    float(binding.get("usd_cap") or 5.0),
                    prices,
                )
        # Every client also carries the other connections' keys so a leaked key
        # is redacted no matter which judge's client wrote the line.
        for judge_id, client in clients.items():
            client.guard = guards.get(judge_id, client.guard)
        return clients, guards, tuple(dict.fromkeys(all_secrets))

    def _verdict_path(self) -> Path:
        return self.run_dir / "judgements.jsonl"

    def _append_verdict(self, verdict: Verdict) -> None:
        with self._verdict_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(verdict.as_dict(), ensure_ascii=False) + "\n")
            handle.flush()

    def _read_verdicts(self) -> list[Verdict]:
        path = self._verdict_path()
        if not path.exists():
            return []
        rows: list[Verdict] = []
        seen: set[tuple[str, str, int]] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            key = (payload["item_id"], payload["judge_id"], int(payload["repeat"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                Verdict(
                    item_id=payload["item_id"],
                    judge_id=payload["judge_id"],
                    repeat=int(payload["repeat"]),
                    verdict=payload.get("verdict"),
                    confidence=payload.get("confidence"),
                    parse_ok=bool(payload.get("parse_ok")),
                    attempts=int(payload.get("attempts", 1)),
                    model=payload.get("model", ""),
                    prompt_sha256=payload.get("prompt_sha256", ""),
                    latency_ms=float(payload.get("latency_ms", 0.0)),
                    error=payload.get("error"),
                    raw_output_sha256=payload.get("raw_output_sha256"),
                    failure_type=payload.get("failure_type") or classify_failure(payload.get("error")),
                )
            )
        return sorted(rows, key=lambda row: (row.judge_id, row.repeat, row.item_id))

    def _completed_keys(self) -> set[tuple[str, str, int]]:
        if not self.config.resume:
            return set()
        return {(row.item_id, row.judge_id, row.repeat) for row in self._read_verdicts()}

    def _auth_probe(self, judges: Sequence[JudgeSpec]) -> dict[str, Any]:
        """Prove each judge actually answered, with the model id it reported."""
        returned: dict[str, Any] = {}
        if not self._verdict_path().exists():
            return {"probe": "no calls made", "returned_models": returned}
        for judge in judges:
            models = {
                row.model
                for row in self._read_verdicts()
                if row.judge_id == judge.judge_id and row.model
            }
            returned[judge.judge_id] = {
                "requested_model": judge.model,
                "returned_models": sorted(models),
                "n_calls": sum(1 for row in self._read_verdicts() if row.judge_id == judge.judge_id),
            }
        return {"probe": "model ids recorded from responses", "returned_models": returned}


# --------------------------------------------------------------------------- #
# Rebuilding a report from stored evidence, with no model calls
# --------------------------------------------------------------------------- #
def rebuild_run(run_id: str, *, run_root: Path | None = None) -> dict[str, Any]:
    """Recompute metrics and rewrite the report from a run directory.

    No judge is called and no cache entry is read: this only reanalyses
    ``items.jsonl``, ``candidates.jsonl`` and ``judgements.jsonl``. It exists so a
    reporting or analysis fix never costs money and never needs a re-run, and so a
    finished run can be re-scored against an updated metric without touching its
    evidence.
    """
    from medeval_study.datasets import DATASET_LICENCES, DatasetBundle, DatasetLicence
    from medeval_study.sampling import SampledItem

    base = (run_root or default_run_root()) / run_id
    state_path = base / "run.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"no run.json in {base}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    config_payload = state.get("config") or {}
    config = StudyConfig(
        experiment=config_payload.get("experiment", "002_judge_families"),
        dataset=config_payload.get("dataset", "medqa_4options"),
        split=config_payload.get("split", "dev"),
        n_items=int(config_payload.get("n_items", 60)),
        seed=int(config_payload.get("seed", 20260101)),
        repeats=int(config_payload.get("repeats", 3)),
        temperature=float(config_payload.get("temperature", 0.3)),
        primary_repeat=int(config_payload.get("primary_repeat", 0)),
        max_api_calls=int(config_payload.get("max_api_calls", 500)),
        dry_run=bool(config_payload.get("dry_run", False)),
        run_id=run_id,
    )

    items: list[McqItem] = []
    for row in _read_jsonl(base / "items.jsonl"):
        items.append(
            McqItem(
                item_id=row["item_id"],
                question=row["question"],
                options=dict(row["options"]),
                key_letter=row["key_letter"],
                key_text=row["key_text"],
                dataset=row.get("dataset", config.dataset),
                split=row.get("split", config.split),
                row_index=int(row.get("row_index", 0)),
                meta=dict(row.get("meta") or {}),
            )
        )
    responses: dict[str, CandidateResponse] = {}
    for row in _read_jsonl(base / "candidates.jsonl"):
        responses[row["item_id"]] = CandidateResponse(
            item_id=row["item_id"],
            chosen_letter=row["chosen_letter"],
            chosen_text=row["chosen_text"],
            text=row["text"],
            condition=row["condition"],
            template_id=row.get("template_id", ""),
            distractor_reason=row.get("distractor_reason", ""),
        )
    if not items or not responses:
        raise FileNotFoundError(
            f"{base} has no items/candidates (they are git-ignored); rebuild needs the local run directory"
        )

    spec_fields = {spec_field.name for spec_field in fields(JudgeSpec)}
    judges = [
        JudgeSpec(**{key: value for key, value in payload.items() if key in spec_fields})
        for payload in (state.get("judges") or [])
    ]
    verdict_path = base / "judgements.jsonl"
    verdicts: list[Verdict] = []
    seen: set[tuple[str, str, int]] = set()
    for payload in _read_jsonl(verdict_path):
        key = (payload["item_id"], payload["judge_id"], int(payload["repeat"]))
        if key in seen:
            continue
        seen.add(key)
        verdicts.append(
            Verdict(
                item_id=payload["item_id"],
                judge_id=payload["judge_id"],
                repeat=int(payload["repeat"]),
                verdict=payload.get("verdict"),
                confidence=payload.get("confidence"),
                parse_ok=bool(payload.get("parse_ok")),
                attempts=int(payload.get("attempts", 1)),
                model=payload.get("model", ""),
                prompt_sha256=payload.get("prompt_sha256", ""),
                latency_ms=float(payload.get("latency_ms", 0.0)),
                error=payload.get("error"),
                raw_output_sha256=payload.get("raw_output_sha256"),
                failure_type=payload.get("failure_type") or classify_failure(payload.get("error")),
            )
        )

    usable = [judge for judge in judges if judge.available]
    metrics = analyse_study(
        items=items,
        responses=responses,
        verdicts=verdicts,
        judges=judges,
        primary_repeat=config.primary_repeat,
        repeats=config.repeats,
        seed=config.seed,
        pipeline_test_only=any(judge.pipeline_test_only for judge in usable),
    )
    metrics["experiment"] = config.experiment
    metrics["rebuilt_utc"] = utc_now_iso()

    dataset_payload = state.get("dataset") or {}
    licence_payload = dataset_payload.get("licence") or {}
    licence = DATASET_LICENCES.get(config.dataset)
    if licence is None:
        licence = DatasetLicence(
            key=config.dataset,
            hf_repo=str(licence_payload.get("hf_repo", "")),
            licence=str(licence_payload.get("licence", "unknown")),
            licence_evidence=str(licence_payload.get("licence_evidence", "")),
            redistribute=bool(licence_payload.get("redistribute", False)),
            attribution=str(licence_payload.get("attribution", "")),
            homepage=str(licence_payload.get("homepage", "")),
        )
    bundle = DatasetBundle(
        items=tuple(items),
        licence=licence,
        dataset=config.dataset,
        split=config.split,
        strategy=dataset_payload.get("strategy", "rebuild"),
        source_detail=dataset_payload.get("source_detail", ""),
        n_raw_rows=int(dataset_payload.get("n_raw_rows", len(items))),
        n_dropped=int(dataset_payload.get("n_dropped", 0)),
        drop_reasons=dict(dataset_payload.get("drop_reasons") or {}),
    )
    sampling = SamplingPlan(
        seed=config.seed,
        n_requested=int((state.get("sampling") or {}).get("n_requested", len(items))),
        items=tuple(SampledItem(item=item, condition=responses[item.item_id].condition) for item in items),
        pool_size=int((state.get("sampling") or {}).get("pool_size", len(items))),
        pool_unique_stems=int((state.get("sampling") or {}).get("pool_unique_stems", len(items))),
        discarded_duplicate_stems=int((state.get("sampling") or {}).get("discarded_duplicate_stems", 0)),
        condition_counts=dict((state.get("sampling") or {}).get("condition_counts") or {}),
    )
    usage = dict(state.get("usage") or {})
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8")) if (base / "manifest.json").is_file() else {}
    write_reports(
        run_dir=base,
        run_id=run_id,
        config=config,
        bundle=bundle,
        sampling=sampling,
        responses=responses,
        judges=judges,
        verdicts=verdicts,
        metrics=metrics,
        manifest=manifest,
        usage=usage,
        judge_detail=state.get("judge_resolution") or {},
    )
    return {"run_id": run_id, "run_dir": str(base), "n_judgements": len(verdicts), "metrics": metrics}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
