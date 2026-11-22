"""Experiment 003: frontier judges on a difficulty-enriched MedQA sample.

The design is pre-registered in ``experiments/003_frontier_judges/PLAN.md``
(committed before this code existed). This module provides:

* the **screening step** — a screening-model connection answers N items
  closed-book; only ids and correct/incorrect flags are saved (committed);
* the **stratified pool builder** — 100 hard-pool items plus 40 random controls,
  new seed, Experiment 002's item ids excluded;
* the **pilot gate** — stop unless the first 40 hard items produce at least 5
  judge-versus-key disagreements across all judges;
* the **flag rule** — every verdict-returning judge (and at least two) disagrees
  with the key at confidence >= 90 on every repeat, written to
  ``review_queue.csv`` (item ids only);
* **stratified analysis** — every metric computed separately for hard and
  control strata.

The agent never makes live calls: screening and judging are executed by the
owner from the web app (keys in memory) or as a dry run with mock judges.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from medeval_core.calllog import utc_now_iso
from medeval_core.client import LLMClient, RawCompletion, parse_json_object
from medeval_core.paths import results_dir
from medeval_core.safety import ResponseCache
from medeval_study.judging import VERDICT_CORRECT, Verdict
from medeval_study.models import McqItem, normalise_stem
from medeval_study.prompts import SYSTEM_PROMPT  # noqa: F401 - re-exported for tests
from medeval_study.runner import StudyConfig, StudyRun
from medeval_study.sampling import SampledItem, SamplingPlan

__all__ = [
    "PILOT_MIN_DISAGREEMENTS",
    "PILOT_SIZE",
    "SCREENING_SEED",
    "SCREENING_SIZE",
    "build_screening_prompt",
    "build_stratified_pool",
    "exp003_review_queue",
    "load_excluded_item_ids",
    "run_screening",
    "write_review_queue",
]

#: Pre-registered constants (PLAN.md). Do not change them here without a
#: dated entry in the deviations log.
SCREENING_SIZE = 600
#: Amendment 1 (2026-11-19): screen in batches of 50, stop at 100 hard items,
#: at exhaustion, or at this cap (whichever binds first).
SCREENING_BATCH = 50
SCREENING_CAP = 1000
SCREENING_SEED = 20261118
N_HARD = 100
N_CONTROL = 40
PILOT_SIZE = 40
PILOT_MIN_DISAGREEMENTS = 5
FLAG_CONFIDENCE = 90


#: The committed Experiment 002 run records named by the pre-registration. The
#: exclusion list is read from these only: globbing ``exp002-*`` once scooped up
#: stray test runs whose items were the built-in synthetic sample, which emptied
#: the eligible pool and made a dry run crash.
CANONICAL_EXP002_RUNS: tuple[str, ...] = ("exp002-main", "exp002-full", "exp002-smoke")


def load_excluded_item_ids(run_root: Path | None = None) -> set[str]:
    """Item ids used by Experiment 002, from its committed run records."""
    base = run_root or (results_dir() / "study_runs")
    excluded: set[str] = set()
    for run_id in CANONICAL_EXP002_RUNS:
        run_dir = base / run_id
        if not run_dir.is_dir():
            continue
        for source in ("items.jsonl", "metrics.json"):
            path = run_dir / source
            if not path.is_file():
                continue
            try:
                if source == "items.jsonl":
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            excluded.add(json.loads(line)["item_id"])
                else:
                    metrics = json.loads(path.read_text(encoding="utf-8"))
                    for row in (metrics.get("possible_key_errors") or {}).get("possible_key_errors") or []:
                        excluded.add(row.get("item_id", ""))
                    for rows in (metrics.get("possible_key_errors") or {}).get("single_judge_flags") or []:
                        excluded.add(rows.get("item_id", ""))
            except (OSError, ValueError, KeyError):
                continue
    return {item for item in excluded if item}


def build_screening_prompt(item: McqItem) -> str:
    """Closed-book, letter-only screening prompt. No rationale, no candidate."""
    options = "\n".join(f"{letter}) {text}" for letter, text in item.options.items())
    return (
        "Answer the following single-best-answer medical question. "
        "Reply with ONLY the letter of the correct option.\n\n"
        f"Question:\n{item.question.strip()}\n\n"
        f"Options:\n{options}\n\n"
        "Answer letter:"
    )


def parse_screening_letter(text: str, letters: Sequence[str]) -> str | None:
    """Extract the chosen letter from a screening reply, or None."""
    payload = parse_json_object(text)
    if payload is not None:
        for key in ("answer", "letter", "choice"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().upper() in letters:
                return value.strip().upper()
    for letter in sorted(letters, key=len, reverse=True):
        if letter in text.upper():
            return letter
    return None


class MockLetterTransport:
    """Deterministic stand-in screening model. PIPELINE TEST ONLY.

    It needs the correct letter to imitate a screener of a given accuracy, and
    the study registers it in an oracle keyed by prompt digest — the same trick
    as the mock judges, so the key never becomes part of a prompt.
    """

    backend = "mock"
    base_url = None

    def __init__(
        self,
        *,
        model: str = "mock-screener",
        accuracy: float = 0.7,
        seed: int = SCREENING_SEED,
        oracle: Mapping[str, str] | None = None,
    ) -> None:
        import hashlib
        import random

        self.model = model
        self.accuracy = min(max(accuracy, 0.0), 1.0)
        self._seed = seed
        self._hashlib = hashlib
        self._random = random
        self.oracle: dict[str, str] = dict(oracle or {})
        self.pipeline_test_only = True

    def set_oracle(self, prompt: str, correct_letter: str) -> None:
        """Register the correct answer for one prompt.

        The digest is computed here, with the transport's own seed, so the
        caller cannot get the keying wrong.
        """
        digest = self._hashlib.sha256(f"{self._seed}:{prompt}".encode()).hexdigest()
        self.oracle[digest] = correct_letter

    def complete(
        self,
        messages: Any,
        *,
        temperature: float,
        seed: int,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        prompt = str(messages[-1]["content"]) if messages else ""
        digest = self._hashlib.sha256(f"{self._seed}:{prompt}".encode()).hexdigest()
        rng = self._random.Random(digest)
        correct_letter = self.oracle.get(digest)
        if correct_letter is None:
            return RawCompletion(text="", latency_ms=1.0, error="mock screener was asked an unregistered prompt")
        agrees = rng.random() < self.accuracy
        wrong_letters = [letter for letter in "ABCD" if letter != correct_letter] or ["A"]
        letter = correct_letter if agrees else wrong_letters[int(rng.random() * len(wrong_letters))]
        return RawCompletion(text=letter, latency_ms=1.0, input_tokens=max(1, len(prompt) // 4), output_tokens=1)


def run_screening(
    *,
    items: Sequence[McqItem],
    client: LLMClient,
    out_path: Path,
    progress: Callable[[dict[str, Any]], None] | None = None,
    stop_flag: Any | None = None,
    max_items: int = SCREENING_SIZE,
) -> dict[str, Any]:
    """Answer items closed-book and save ids + correct/incorrect flags only.

    The output file is committed by design: it contains no benchmark text, only
    item ids and a boolean. Resume: completed item ids are skipped; caching is
    the client's own (pass a ``ResponseCache`` when building it).
    """
    return run_screening_adaptive(
        items=items,
        client=client,
        out_path=out_path,
        progress=progress,
        stop_flag=stop_flag,
        cap=max_items,
        target_hard=None,
    )


def run_screening_adaptive(
    *,
    items: Sequence[McqItem],
    client: LLMClient,
    out_path: Path,
    seed: int = SCREENING_SEED,
    batch_size: int = SCREENING_BATCH,
    target_hard: int | None = N_HARD,
    cap: int = SCREENING_CAP,
    progress: Callable[[dict[str, Any]], None] | None = None,
    stop_flag: Any | None = None,
) -> dict[str, Any]:
    """Adaptive screening (pre-registration amendment 1, 2026-11-19).

    Items are shuffled by the pre-registered seed and screened in batches of 50.
    After each batch the hard count is compared with the target; screening stops
    with a recorded reason:

    * ``target_met`` — ``target_hard`` wrong answers found;
    * ``exhausted`` — every eligible item has been screened;
    * ``cap_reached`` — the screening cap was hit first;
    * ``stopped_by_user`` — the owner pressed stop between batches.

    Only item ids and correct/incorrect flags are written, so the committed file
    carries no benchmark text. Resume: already-screened ids are skipped, and the
    shuffled order is deterministic, so a resumed run continues where it left off.
    """
    import hashlib

    done: dict[str, bool] = {}
    if out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[str(row["item_id"])] = bool(row["correct"])

    def seeded_order(sequence: Sequence[McqItem]) -> list[McqItem]:
        return sorted(
            sequence,
            key=lambda row: hashlib.sha256(f"{seed}:{row.item_id}".encode()).hexdigest(),
        )

    ordered = seeded_order([item for item in items if item.item_id not in done])
    eligible = len(items)
    n_correct = sum(1 for value in done.values() if value)
    n_wrong = len(done) - n_correct
    batches = 0
    stop_reason = "cap_reached"
    # A file that already satisfies a stop condition needs no further batch.
    if target_hard is not None and n_wrong >= target_hard:
        stop_reason = "target_met"
    elif len(done) >= eligible:
        stop_reason = "exhausted"
    else:
        stop_reason = "running"

    oracle_register = getattr(client.transport, "set_oracle", None)
    index = 0
    while stop_reason == "running":
        batch = ordered[index:index + max(1, batch_size)]
        if not batch:
            stop_reason = "exhausted"
            break
        batches += 1
        batch_label = f"batch {batches} ({len(done) + 1}-{len(done) + len(batch)} of {eligible})"
        if progress:
            progress({
                "message": f"screening {batch_label}: {n_wrong} hard item(s) so far",
                "phase": "screening",
                "progress": len(done),
                "total": min(eligible, cap),
            })
        for item in batch:
            if len(done) >= cap:
                stop_reason = "cap_reached"
                break
            if stop_flag is not None and stop_flag.is_set():
                stop_reason = "stopped_by_user"
                break
            prompt = build_screening_prompt(item)
            if oracle_register is not None:
                oracle_register(prompt, item.key_letter)
            record = client.complete(
                prompt,
                system="You are an expert answering a medical multiple-choice question.",
                template_version="exp003_screening_v1",
                temperature=0.0,
                metadata={"item_id": item.item_id, "stage": "screening"},
            )
            letter = None if record.error else parse_screening_letter(record.output, item.letters)
            correct = letter == item.key_letter if letter is not None else False
            done[item.item_id] = correct
            if correct:
                n_correct += 1
            else:
                n_wrong += 1
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"item_id": item.item_id, "correct": correct, "model": record.model}) + "\n"
                )
        index += len(batch)
        if stop_reason != "running":
            break
        if target_hard is not None and n_wrong >= target_hard:
            stop_reason = "target_met"
        elif len(done) >= eligible:
            stop_reason = "exhausted"
        elif len(done) >= cap:
            stop_reason = "cap_reached"

    return {
        "n_screened": len(done),
        "n_correct": sum(1 for value in done.values() if value),
        "n_wrong": sum(1 for value in done.values() if not value),
        "n_eligible": eligible,
        "hard_target": target_hard,
        "cap": cap,
        "batch_size": batch_size,
        "batches": batches,
        "stop_reason": stop_reason,
        "complete": stop_reason in {"target_met", "exhausted", "cap_reached"},
        "model": client.transport.model,
        "screening_path": str(out_path),
    }


@dataclass(frozen=True)
class StratifiedPool:
    """The pre-registered stratified sample."""

    hard: tuple[McqItem, ...]
    control: tuple[McqItem, ...]
    seed: int
    n_hard_available: int
    note: str = ""

    @property
    def items(self) -> tuple[McqItem, ...]:
        """Hard pool first: the pilot gate judges the first 40 hard items."""
        return (*self.hard, *self.control)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_hard": len(self.hard),
            "n_control": len(self.control),
            "n_hard_available": self.n_hard_available,
            "seed": self.seed,
            "note": self.note,
            "hard_item_ids": [item.item_id for item in self.hard],
            "control_item_ids": [item.item_id for item in self.control],
        }


def build_stratified_pool(
    *,
    screening_rows: Sequence[Mapping[str, Any]],
    items: Sequence[McqItem],
    seed: int = SCREENING_SEED,
    n_hard: int = N_HARD,
    n_control: int = N_CONTROL,
    excluded_ids: set[str] | None = None,
) -> StratifiedPool:
    """Hard pool = screening misses; control = seeded draw from the rest.

    Every rule here was fixed in the pre-registration before any call.
    """
    import hashlib

    by_id = {item.item_id: item for item in items}
    excluded = excluded_ids or set()
    hard_ids: list[str] = []
    rest_ids: list[str] = []
    for row in screening_rows:
        item_id = str(row.get("item_id"))
        if item_id not in by_id or item_id in excluded:
            continue
        if bool(row.get("correct")):
            rest_ids.append(item_id)
        else:
            hard_ids.append(item_id)

    def seeded_order(ids: Sequence[str]) -> list[str]:
        return sorted(set(ids), key=lambda row: hashlib.sha256(f"{seed}:{row}".encode()).hexdigest())

    hard_available = len(hard_ids)
    chosen_hard = seeded_order(hard_ids)[:n_hard]
    note = ""
    if hard_available < n_hard:
        note = (
            f"the hard pool had only {hard_available} screening misses, below the pre-registered "
            f"{n_hard}; all of them are used and the shortfall is recorded"
        )
    chosen_control = seeded_order(rest_ids)[:n_control]
    return StratifiedPool(
        hard=tuple(by_id[item] for item in chosen_hard),
        control=tuple(by_id[item] for item in chosen_control),
        seed=seed,
        n_hard_available=hard_available,
        note=note,
    )


def exp003_review_queue(
    verdicts: Sequence[Verdict],
    *,
    candidate_letter_by_item: Mapping[str, str],
    key_letter_by_item: Mapping[str, str],
    confidence: int = FLAG_CONFIDENCE,
) -> dict[str, Any]:
    """Pre-registered flag rule.

    An item is flagged when **at least two judges, and all judges that returned
    a verdict for that item**, disagree with the key at confidence >= 90 on every
    repeat. The queue contains item ids only; it is a prompt for human review,
    never a finding that the key is wrong.
    """
    by_item: dict[str, list[Verdict]] = {}
    for verdict in verdicts:
        if verdict.item_id in key_letter_by_item:
            by_item.setdefault(verdict.item_id, []).append(verdict)
    flagged: list[dict[str, Any]] = []
    for item_id, rows in sorted(by_item.items()):
        by_judge: dict[str, list[Verdict]] = {}
        for row in rows:
            if row.parse_ok and row.verdict is not None and row.confidence is not None:
                by_judge.setdefault(row.judge_id, []).append(row)
        judges = sorted(by_judge)
        if len(judges) < 2:
            continue
        key_letter = key_letter_by_item[item_id]
        candidate_letter = candidate_letter_by_item.get(item_id)
        if candidate_letter is None:
            continue
        is_correct = candidate_letter == key_letter
        all_disagree = True
        for judge in judges:
            judge_rows = by_judge[judge]
            if not judge_rows:
                all_disagree = False
                break
            for row in judge_rows:
                said_correct = row.verdict == VERDICT_CORRECT
                if said_correct == is_correct or row.confidence < confidence:
                    all_disagree = False
                    break
            if not all_disagree:
                break
        if all_disagree:
            flagged.append({
                "item_id": item_id,
                "n_judges": len(judges),
                "judges": judges,
                "candidate_was_correct_per_key": is_correct,
            })
    return {
        "rule": (
            f"at least two judges, and all judges that returned a verdict, disagree with the key at "
            f"confidence >= {confidence} on every repeat"
        ),
        "n_flagged": len(flagged),
        "flagged": flagged,
        "flagged_item_ids": sorted(row["item_id"] for row in flagged),
        "caveat": (
            "a flag is a prompt for human review, never a finding that the key is wrong; the judges "
            "may be the ones who are wrong"
        ),
    }


def write_review_queue(path: Path, flagged_item_ids: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item_id"])
        for item_id in sorted(set(flagged_item_ids)):
            writer.writerow([item_id])
    return path


def _price_per_call(config: StudyConfig) -> float | None:
    """A rough per-call USD figure for the plan card, or None when unverified.

    Every judge connection may carry prices; when none does, the plan says the
    cost is unavailable and the call cap is binding, rather than inventing one.
    """
    prices = [
        (binding.get("price_in_per_mtok"), binding.get("price_out_per_mtok"))
        for binding in (config.connection_judges or ())
        if binding.get("price_in_per_mtok") is not None or binding.get("price_out_per_mtok") is not None
    ]
    if not prices:
        return None
    # One call reads roughly 700 prompt tokens and writes ~20; these are design
    # estimates for a worst case, not a measurement.
    per_call = []
    for price_in, price_out in prices:
        per_call.append(
            ((price_in or 0.0) * 700 / 1_000_000) + ((price_out or 0.0) * 20 / 1_000_000)
        )
    return sum(per_call) / len(per_call)


class Exp003Study(StudyRun):
    """Experiment 003 runner: stratified sampling on top of the study machinery.

    Caches the sampling and judge objects during ``execute`` so the
    post-processing step can compute strata and the review queue without
    re-deriving anything.
    """

    def __init__(self, config: StudyConfig, settings: Any, **kwargs: Any) -> None:
        super().__init__(config, settings, **kwargs)
        if config.experiment != "003_frontier_judges":
            raise ValueError("Exp003Study requires experiment='003_frontier_judges'")
        self._sampling_cache: list[Any] = []
        self._responses_cache: dict[str, Any] = {}
        self._judges_cache: list[Any] = []

    def load(self) -> tuple[Any, SamplingPlan, dict[str, Any]]:
        """Stratified sampling (hard pool first, pilot ordering) replaces the draw."""
        from medeval_study.responses import build_candidate_response

        bundle = self._load_bundle()
        excluded = load_excluded_item_ids()
        rows: list[dict[str, Any]] = []
        path = self.screening_path()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        if not rows:
            raise FileNotFoundError(
                "no screening results yet: run the screening stage first (the dry run builds a mock one)"
            )
        pool = build_stratified_pool(
            screening_rows=rows,
            items=bundle.items,
            seed=self.config.seed,
            excluded_ids=excluded,
        )
        items = pool.items
        n = len(items)
        n_correct = n // 2 + n % 2
        ordered = sorted(items, key=lambda row: row.item_id)
        correct_ids = {row.item_id for row in ordered[:n_correct]}
        sampled = tuple(
            SampledItem(
                item=item,
                condition="correct" if item.item_id in correct_ids else "incorrect",
            )
            for item in items
        )
        responses = {
            row.item.item_id: build_candidate_response(row.item, condition=row.condition, seed=self.config.seed)
            for row in sampled
        }
        counts = {
            "correct": sum(1 for row in sampled if row.condition == "correct"),
            "incorrect": sum(1 for row in sampled if row.condition == "incorrect"),
        }
        stems = {normalise_stem(row.item.question) for row in sampled}
        sampling = SamplingPlan(
            seed=self.config.seed,
            n_requested=n,
            items=sampled,
            pool_size=len(bundle.items),
            pool_unique_stems=len({normalise_stem(row.question) for row in bundle.items}),
            discarded_duplicate_stems=0,
            condition_counts=counts,
            note=(
                f"stratified: {len(pool.hard)} hard-pool items (pilot first) + {len(pool.control)} controls; "
                f"{len(stems)} distinct stems; exp002 item ids excluded"
            ),
        )
        self._sampling_cache = list(sampling.items)
        self._responses_cache = dict(responses)
        self.state["strata"] = pool.as_dict()
        self._emit(
            f"stratified pool: {len(pool.hard)} hard + {len(pool.control)} control "
            f"({pool.n_hard_available} hard available)",
            phase="sample",
        )
        return bundle, sampling, responses

    # ------------------------------------------------------------------ #
    # Stage 1: screening
    # ------------------------------------------------------------------ #
    def screening_path(self) -> Path:
        """Dry runs screen into the run directory; real screening is committed."""
        if self.config.dry_run:
            return self.run_dir / "screening.jsonl"
        return results_dir() / "experiment_003" / "screening.jsonl"

    def _build_clients(self, judges: Any, sampling: Any, responses: Any) -> Any:
        self._judges_cache = list(judges)
        return super()._build_clients(judges, sampling, responses)

    def _run_screening_stage(self) -> dict[str, Any]:
        """Screen adaptively (amendment 1) with the Screening-role connection.

        Screening stops when 100 hard items are found, when every eligible item
        has been screened, or when the cap is reached — the cap being the lesser
        of ``SCREENING_CAP`` and the screening connection's own call cap. The
        stop reason is recorded in the run state and the committed summary.
        """
        from medeval_core.client import LLMClient
        from medeval_study.synthetic import fetch_command

        bundle = self._load_bundle()
        excluded = load_excluded_item_ids()
        eligible = [item for item in bundle.items if item.item_id not in excluded]
        if not eligible:
            # Nothing to screen: say why and how to fix it instead of writing an
            # empty screening file that makes the next stage raise.
            reason = (
                f"screening found 0 eligible items in {bundle.dataset}/{bundle.split}: "
                f"{bundle.n} item(s) loaded and {len(excluded)} excluded by the Experiment 002 list. "
                f"Fix: run {fetch_command(self.config.dataset)} to fetch the real data, then start "
                "the run again."
            )
            summary = {
                "n_screened": 0,
                "n_correct": 0,
                "n_wrong": 0,
                "n_eligible": 0,
                "n_loaded": bundle.n,
                "n_excluded": len(excluded),
                "hard_target": N_HARD,
                "cap": SCREENING_CAP,
                "batch_size": SCREENING_BATCH,
                "batches": 0,
                "stop_reason": "no_eligible_items",
                "complete": False,
                "blocked_reason": reason,
                "model": bundle.dataset,
                "path": str(self.screening_path()),
                "mock": bool(self.config.dry_run),
                "note": "no screening calls were made",
            }
            self.state["screening"] = summary
            self.state.update({"status": "blocked", "blocked_reason": reason})
            self._write_state()
            self._emit(reason, phase="blocked")
            return summary
        binding = self.config.screening_binding or {}
        out_path = self.screening_path()
        if self.config.dry_run:
            transport = MockLetterTransport(
                model="mock-screener", accuracy=0.7, seed=self.config.seed
            )
            client = LLMClient(
                transport,
                replace(self.settings, temperature=0.0, max_tokens=16),
                sink=None,
                run_id=self.run_id,
                backend_label="mock:mock-screener",
                cache=ResponseCache(self.run_dir / "screening_cache.json"),
            )
            model_name = "mock-screener"
        elif binding:
            from dataclasses import replace as _replace

            from medeval_core.client import OpenAICompatibleTransport

            key = binding.get("api_key")
            transport_kwargs: dict[str, Any] = {
                "base_url": binding.get("base_url", ""),
                "model": binding.get("model", ""),
                "api_key": key,
                "backend": "api",
                "retries": self.settings.retries,
                "timeout_s": self.settings.timeout_s,
            }
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
            client = LLMClient(
                transport,
                _replace(self.settings, temperature=0.0, max_tokens=16, api_key=key),
                sink=None,
                run_id=self.run_id,
                backend_label=f"api:{binding.get('model', '')}",
                cache=ResponseCache(self.run_dir / "screening_cache.json"),
            )
            model_name = str(binding.get("model", ""))
        else:
            raise FileNotFoundError(
                "no screening results and no screening connection: assign a Screening model on the "
                "Models page (it must not also be a judge), then run the screening stage"
            )
        connection_cap = int(binding.get("call_cap") or SCREENING_CAP)
        cap = max(1, min(SCREENING_CAP, connection_cap))
        self._emit(
            f"screening adaptively: batches of {SCREENING_BATCH}, target {N_HARD} hard items, cap {cap} "
            f"({len(eligible)} eligible)",
            phase="screening",
        )
        summary = run_screening_adaptive(
            items=eligible,
            client=client,
            out_path=out_path,
            seed=self.config.seed,
            batch_size=SCREENING_BATCH,
            target_hard=N_HARD,
            cap=cap,
            progress=self.progress,
            stop_flag=self.stop_flag,
        )
        summary["path"] = str(out_path)
        summary["model"] = model_name
        summary["excluded_from_pool"] = len(excluded)
        summary["pool_eligible"] = len(eligible)
        summary["mock"] = bool(self.config.dry_run)
        summary["note"] = (
            "dry-run screening with a mock screener; this file stays in the run directory and is not "
            "the committed screening record"
            if self.config.dry_run
            else "real screening: item ids and correct/incorrect flags only; no benchmark text"
        )
        self.state["screening"] = summary
        self._emit(
            f"screening stopped: {summary['stop_reason']} after {summary['n_screened']} items "
            f"({summary['n_wrong']} hard)",
            phase="screening",
        )
        return summary

    # ------------------------------------------------------------------ #
    # Orchestration — staged (pre-registration amendment 1, staged controller)
    # ------------------------------------------------------------------ #
    #: The confirmable stages, in order. ``all`` runs every stage in one go.
    STAGES: tuple[str, ...] = ("screening", "pool", "pilot", "remaining")

    def next_stage(self) -> str | None:
        """The stage that still has work to do, or None when nothing can run."""
        state = self.state if isinstance(self.state, dict) else {}
        if state.get("status") == "blocked":
            return None
        completed = list(state.get("stages_completed") or [])
        gate = state.get("pilot_gate") or {}
        if "screening" not in completed or not self.screening_path().is_file():
            return "screening"
        if "pool" not in completed:
            return "pool"
        if "pilot" not in completed:
            return "pilot"
        if gate.get("passed") is not True:
            return None  # pilot stopped: nothing further may run
        if "remaining" not in completed:
            return "remaining"
        return None

    def stage_plan(self) -> list[dict[str, Any]]:
        """Every stage with its status and worst-case cost, for the UI plan card.

        Costs come from the pre-registered design: screening is capped by the
        lesser of 1 000 items and the screening connection's call cap; the pilot
        is the first 40 hard items through every judge; the remainder is the rest
        of the sample. Prices are only used when a connection supplies them.
        """
        config = self.config
        state = self.state if isinstance(self.state, dict) else {}
        completed = list(state.get("stages_completed") or [])
        screening = state.get("screening") or {}
        gate = state.get("pilot_gate") or {}
        n_judges = max(1, len(config.connection_judges or ()))
        repeats = max(1, config.repeats)
        subsample = max(0, config.consistency_subsample)
        target_calls = {
            "screening": int(
                screening.get("n_screened") if "screening" in completed and screening.get("n_screened")
                 else screening.get("cap") or min(SCREENING_CAP, int(
                     (config.screening_binding or {}).get("call_cap") or SCREENING_CAP))
            ),
            "pool": 0,
            "pilot": max(0, min(config.pilot_items, N_HARD)) * n_judges * repeats,
            "remaining": 0,
        }
        if "pilot" in completed:
            target_calls["pilot"] = int((state.get("pilot_gate") or {}).get("n_scored_pilot_verdicts") or target_calls["pilot"])
        # Remaining = the sample minus the pilot, plus the consistency repeats.
        try:
            pool = self._pool_counts()
        except Exception:  # a plan must not fail because sampling cannot load yet
            pool = None
        if pool:
            n_sample = pool["n_items"]
            remaining_items = max(0, n_sample - target_calls["pilot"] // max(1, n_judges * repeats))
            target_calls["remaining"] = remaining_items * n_judges * repeats + (
                min(subsample, n_sample) * n_judges * 2 if subsample else 0
            )
        usd_per_call = _price_per_call(config)
        stages: list[dict[str, Any]] = []
        labels = {
            "screening": "Screening (adaptive, batches of 50)",
            "pool": "Review hard pool",
            "pilot": f"Pilot ({config.pilot_items} hard items)",
            "remaining": "Remaining items",
        }
        blocked_run = state.get("status") == "blocked"
        for name in self.STAGES:
            done = name in completed
            calls = target_calls.get(name, 0)
            if name == "pool" and pool:
                detail = (
                    f"{pool['n_hard']} hard + {pool['n_control']} control items "
                    f"(hard pool available: {pool['n_hard_available']})"
                )
            elif name == "screening" and screening:
                detail = (
                    f"{screening.get('n_screened')} screened, {screening.get('n_wrong')} hard; "
                    f"stop reason: {screening.get('stop_reason')}"
                )
            elif name == "pilot" and gate:
                detail = gate.get("decision") or ""
            else:
                detail = ""
            blocked = (
                (name == "remaining" and gate and gate.get("passed") is not True)
                or (blocked_run and not done)
            )
            stages.append({
                "name": name,
                "label": labels[name],
                "status": "done" if done else ("blocked" if blocked else "pending"),
                "worst_case_calls": calls,
                "worst_case_usd": None if usd_per_call is None else round(calls * usd_per_call, 4),
                "detail": detail,
                "needs_confirmation": not done and not blocked,
            })
        return stages

    def _pool_counts(self) -> dict[str, int] | None:
        """Sample counts from the screening file, without loading the dataset twice."""
        strata = (self.state or {}).get("strata") or {}
        if strata.get("hard_item_ids") or strata.get("control_item_ids"):
            return {
                "n_hard": len(strata.get("hard_item_ids") or []),
                "n_control": len(strata.get("control_item_ids") or []),
                "n_hard_available": int(strata.get("n_hard_available") or 0),
                "n_items": len(strata.get("hard_item_ids") or []) + len(strata.get("control_item_ids") or []),
            }
        path = self.screening_path()
        if not path.is_file():
            return None
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        hard = sum(1 for row in rows if not row.get("correct"))
        return {
            "n_hard": min(hard, N_HARD),
            "n_control": min(len(rows) - hard, N_CONTROL),
            "n_hard_available": hard,
            "n_items": min(hard, N_HARD) + min(len(rows) - hard, N_CONTROL),
        }

    def _validate_bindings(self) -> None:
        """Pre-registered role rules: the screener is not a judge, and not a twin.

        Blocking (not warning) when the screening connection is also a judge or
        shares the same family label, because either would make the strata
        circular: the model that defines "hard" would also be an instrument
        being measured, and a same-family judge shares its blind spots.
        """
        screening = self.config.screening_binding or {}
        judges = list(self.config.connection_judges or ())
        if not judges:
            raise ValueError(
                "Experiment 003 needs judge connections: assign at least two connections to judge roles "
                "on the Models page"
            )
        if not screening:
            raise ValueError(
                "Experiment 003 needs a screening connection in the Screening role (it must not also be a "
                "judge): assign one on the Models page"
            )
        screening_id = screening.get("connection_id")
        if screening_id and any(binding.get("connection_id") == screening_id for binding in judges):
            raise ValueError(
                "the screening model is also a judge; it must be a different connection (the strata would "
                "be circular otherwise)"
            )
        screening_family = str(screening.get("family_label") or "").strip().lower()
        if screening_family:
            clashes = [
                str(binding.get("judge_id") or binding.get("connection_id"))
                for binding in judges
                if str(binding.get("family_label") or "").strip().lower() == screening_family
            ]
            if clashes:
                raise ValueError(
                    f"the screening model's family label {screening.get('family_label')!r} is the same as "
                    f"judge(s) {', '.join(clashes)}; the screening model must come from a different family"
                )

    def execute(self) -> dict[str, Any]:
        """Run the confirmed stage (``config.stage``) and stop there.

        ``all`` keeps the v0.3 behaviour (every stage in one process) for dry
        runs and tests; the app always advances one confirmed stage at a time,
        so the owner sees the pool size and cost before the pilot spends money.
        """
        stage = getattr(self.config, "stage", "all") or "all"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.config.resume:
            self._merge_existing_state()
        if not self.config.dry_run:
            try:
                self._validate_bindings()
            except ValueError as exc:
                return self._refuse(str(exc))
        if stage == "screening" and not self.screening_path().is_file():
            self.state.setdefault("status", "running")
            screening = self._run_screening_stage()
            if screening.get("stop_reason") == "no_eligible_items":
                return self._blocked(screening["blocked_reason"], screening=screening)
            self._mark_stage("screening")
            return self._await_confirmation("pool")
        if stage == "screening":
            self._mark_stage("screening")
            return self._await_confirmation("pool")
        if not self.screening_path().is_file():
            if stage in {"pool", "pilot", "remaining"}:
                return self._refuse(f"stage {stage!r} needs screening results first; run the screening stage")
            screening = self._run_screening_stage()
            if screening.get("stop_reason") == "no_eligible_items":
                return self._blocked(screening["blocked_reason"], screening=screening)
            self._mark_stage("screening")
        if stage == "pool":
            self._pool_stage()
            return self._await_confirmation("pilot")
        result = super().execute()
        if result.get("status") in {
            "completed", "stopped", "budget_stopped", "pilot_stopped", "awaiting_confirmation",
        }:
            self._post_process(result)
            completed_stage = getattr(self.config, "stage", "all")
            if completed_stage in {"pilot", "remaining"}:
                self._mark_stage(completed_stage)
                self.state["next_stage"] = self.next_stage()
                self._write_state()
                result["stage_plan"] = self.stage_plan()
        return result

    def _pool_stage(self) -> dict[str, Any]:
        """Load the dataset, build the stratified pool, record the plan (no calls)."""
        from medeval_core.manifest import write_manifest

        bundle, sampling, _responses = self.load()
        plan = {
            "n_items": len(sampling.items),
            "n_hard": len(self.state.get("strata", {}).get("hard_item_ids") or []),
            "n_control": len(self.state.get("strata", {}).get("control_item_ids") or []),
            "pool_eligible": len(bundle.items),
            "screening": self.state.get("screening") or {},
            "worst_case_calls": len(sampling.items) * max(1, len(self.config.connection_judges or ()))
            * max(1, self.config.repeats),
        }
        self.state.update(
            {
                "status": "awaiting_confirmation",
                "stage": "pool",
                "pool_plan": plan,
                "sampling": sampling.as_dict(),
                "dataset": bundle.as_dict(),
                "config": self.config.as_dict(),
            }
        )
        self._mark_stage("pool")
        self._write_state()
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("extra", {})["pool_plan"] = plan
            write_manifest(manifest_path, manifest)
        self._emit(
            f"hard pool ready: {plan['n_hard']} hard + {plan['n_control']} control items; "
            "confirm to run the pilot",
            phase="pool",
        )
        return plan

    def _mark_stage(self, name: str) -> None:
        completed = list(self.state.get("stages_completed") or [])
        if name not in completed:
            completed.append(name)
        self.state["stages_completed"] = completed
        self.state["stage"] = name

    def _await_confirmation(self, next_stage_name: str) -> dict[str, Any]:
        """Finish this stage cleanly and tell the app which stage comes next."""
        self.state.update(
            {
                "status": "awaiting_confirmation",
                "next_stage": next_stage_name,
                "finished_utc": utc_now_iso(),
            }
        )
        self._write_state()
        return {
            "status": "awaiting_confirmation",
            "next_stage": next_stage_name,
            "run_dir": str(self.run_dir),
            "run_id": self.run_id,
            "stage_plan": self.stage_plan(),
        }

    def _refuse(self, reason: str) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state.update({"status": "refused", "refused_reason": reason})
        self._write_state()
        return {"status": "refused", "refused_reason": reason, "run_dir": str(self.run_dir), "run_id": self.run_id}

    def _blocked(self, reason: str, *, screening: dict[str, Any] | None = None) -> dict[str, Any]:
        """Stop without a traceback: say what is missing and how to fix it.

        ``blocked`` (not ``failed``) is the honest status: the run did nothing
        wrong, it just has no data to work on. The app shows the same reason on
        the run page and on the Stages card.
        """
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state.update(
            {
                "status": "blocked",
                "blocked_reason": reason,
                "finished_utc": utc_now_iso(),
                "next_stage": None,
            }
        )
        if screening is not None:
            self.state["screening"] = screening
        self._write_state()
        return {
            "status": "blocked",
            "blocked_reason": reason,
            "run_dir": str(self.run_dir),
            "run_id": self.run_id,
            "stage_plan": self.stage_plan(),
        }

    def advance(self, stage: str | None = None) -> dict[str, Any]:
        """Run the next confirmable stage (or the named one)."""
        wanted = stage or self.next_stage()
        if wanted is None:
            return {"status": "finished", "run_dir": str(self.run_dir), "run_id": self.run_id}
        self.config.stage = wanted  # type: ignore[misc]
        return self.execute()

    def _post_process(self, result: dict[str, Any]) -> None:
        """Strata analysis, the review queue and the single-reviewer caveat."""
        from medeval_core.manifest import write_manifest
        from medeval_study.analysis import analyse_study

        metrics_path = self.run_dir / "metrics.json"
        if not metrics_path.is_file():
            return
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        verdicts = self._read_verdicts()
        strata = self.state.get("strata") or {}
        hard_ids = set(strata.get("hard_item_ids") or [])
        items = [row.item for row in self._sampling_cache or []]
        responses = self._responses_cache or {}
        if items and responses:
            strata_metrics: dict[str, Any] = {}
            for name, ids in (("hard", hard_ids), ("control", {row.item_id for row in items} - hard_ids)):
                stratum_items = [row for row in items if row.item_id in ids]
                if stratum_items:
                    judges_cache = self._judges_cache
                    strata_metrics[name] = analyse_study(
                        items=stratum_items,
                        responses=responses,
                        verdicts=verdicts,
                        judges=judges_cache,
                        primary_repeat=self.config.primary_repeat,
                        repeats=3 if self.config.consistency_subsample > 0 else max(1, self.config.repeats),
                        seed=self.config.seed,
                        pipeline_test_only=any(judge.pipeline_test_only for judge in judges_cache),
                    )
            metrics["strata"] = strata_metrics
            metrics["strata_note"] = (
                "hard-pool items were selected because a screening model got them wrong, so they are "
                "more likely to contain key errors; judge-versus-key disagreement there is not the same "
                "as judge error"
            )
            flags = exp003_review_queue(
                verdicts,
                candidate_letter_by_item={item_id: row.chosen_letter for item_id, row in responses.items()},
                key_letter_by_item={row.item_id: row.key_letter for row in items},
            )
            metrics["review_queue"] = flags
            metrics["single_reviewer_note"] = (
                "any review label recorded locally is one reviewer's opinion, not clinical validation "
                "and not a reference standard; no clinician is involved in this project"
            )
            queue_path = write_review_queue(self.run_dir / "review_queue.csv", flags["flagged_item_ids"])
            metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
            manifest_path = self.run_dir / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["extra"]["review_queue"] = {"n_flagged": flags["n_flagged"], "path": str(queue_path)}
                manifest["extra"]["strata"] = {"n_hard": len(hard_ids)}
                write_manifest(manifest_path, manifest)
