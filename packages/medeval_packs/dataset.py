"""Dataset adapters for Paper Packs.

A pack points at a local JSONL or CSV file (never a URL: a pack must run offline)
and declares how its columns map onto the canonical fields for its task type.
This module loads, validates, normalises and deterministically samples those
rows.

Key rule: the **answer key never enters a target-model prompt**.  :class:`PackItem`
keeps ``gold`` separate from ``prompt_fields`` and the runner renders target
prompts only from ``prompt_fields``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import PackValidationError, PaperPack

__all__ = ["PackItem", "load_items", "normalise_item", "read_rows"]

#: Canonical field names a pack may map, per task type.  Anything else is a
#: pack error, not a silently ignored column.
_REQUIRED = {
    "mcq": ("id", "question", "options", "answer"),
    "open_ended_rubric": ("id", "question"),
    "multi_judge_comparison": ("id", "question"),
    "structured_extraction": ("id", "input", "gold"),
    "triage_classification": ("id", "input", "label"),
}
_OPTIONAL = {
    "mcq": ("category", "split", "stratum", "abstain_expected", "meta"),
    "open_ended_rubric": ("reference_answer", "rubric", "category", "split", "stratum", "meta"),
    "multi_judge_comparison": ("reference_answer", "rubric", "candidate_a", "candidate_b", "split", "stratum", "meta"),
    "structured_extraction": ("schema", "split", "stratum", "meta"),
    "triage_classification": ("category", "split", "stratum", "meta"),
}


@dataclass(frozen=True)
class PackItem:
    """One normalised dataset row.

    ``prompt_fields`` is the only mapping the target prompt may use.  ``gold`` is
    used by deterministic graders and by the report; it is never rendered into a
    target-model prompt.
    """

    item_id: str
    task_type: str
    prompt_fields: Mapping[str, Any]
    gold: Mapping[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)
    stratum: str = ""
    item_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "task_type": self.task_type,
            "prompt_fields": dict(self.prompt_fields),
            "gold": dict(self.gold),
            "meta": dict(self.meta),
            "stratum": self.stratum,
            "item_hash": self.item_hash,
        }


def read_rows(path: Path, data_format: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise PackValidationError(f"could not read dataset {path.name}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    if data_format == "jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise PackValidationError(f"{path.name}:{line_number} is not valid JSON: {exc}") from exc
            if not isinstance(value, Mapping):
                raise PackValidationError(f"{path.name}:{line_number} is not a JSON object")
            rows.append(dict(value))
    elif data_format == "csv":
        reader = csv.DictReader(text.splitlines())
        for row in reader:
            rows.append({key: value for key, value in row.items() if key is not None})
    else:  # pragma: no cover - schema validation prevents this
        raise PackValidationError(f"unsupported dataset format {data_format!r}")
    if not rows:
        raise PackValidationError(f"dataset {path.name} has no rows")
    return rows


def _column(row: Mapping[str, Any], fields: Mapping[str, str], canonical: str) -> Any:
    column = fields.get(canonical, canonical)
    return row.get(column)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _normalise_options(value: Any) -> dict[str, str]:
    parsed = _parse_jsonish(value)
    if isinstance(parsed, Mapping):
        return {str(key).strip().upper(): str(text) for key, text in parsed.items()}
    if isinstance(parsed, str):
        parts = [part.strip() for part in parsed.split("|") if part.strip()]
        return {chr(65 + index): text for index, text in enumerate(parts)}
    if isinstance(parsed, Sequence):
        return {chr(65 + index): str(text) for index, text in enumerate(parsed)}
    raise PackValidationError(f"options must be a mapping, list or pipe-delimited string, got {type(parsed).__name__}")


def _normalise_answer(value: Any, options: Mapping[str, str]) -> dict[str, Any]:
    text = str(value).strip()
    upper = text.upper()
    if upper in options:
        return {"letter": upper, "text": options[upper]}
    for letter, option_text in options.items():
        if option_text.strip().casefold() == text.casefold():
            return {"letter": letter, "text": option_text}
    # A bare letter not in options is still recorded; validation happens later.
    return {"letter": upper, "text": options.get(upper, "")}


def _item_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]


def normalise_item(
    row: Mapping[str, Any],
    *,
    task_type: str,
    fields: Mapping[str, str],
    row_number: int,
    source: str,
) -> PackItem:
    """Map one raw row onto :class:`PackItem`, with task-specific validation."""
    if task_type not in _REQUIRED:
        raise PackValidationError(f"unsupported task_type {task_type!r}")
    missing = [
        canonical
        for canonical in _REQUIRED[task_type]
        if _column(row, fields, canonical) in (None, "")
    ]
    if missing:
        raise PackValidationError(
            f"{source} row {row_number} is missing required field(s) {missing}; check dataset.fields"
        )
    item_id = str(_column(row, fields, "id")).strip()
    if not item_id:
        raise PackValidationError(f"{source} row {row_number} has an empty id")

    allowed = set(_REQUIRED[task_type]) | set(_OPTIONAL[task_type])
    unknown = sorted(
        canonical for canonical in fields if canonical not in allowed
    )
    if unknown:
        raise PackValidationError(
            f"dataset.fields names {unknown}, which are not canonical fields for {task_type}"
        )

    prompt_fields: dict[str, Any] = {}
    gold: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    stratum = str(_column(row, fields, "stratum") or "")

    if task_type == "mcq":
        options = _normalise_options(_column(row, fields, "options"))
        if len(options) < 2:
            raise PackValidationError(f"{source} row {row_number} has fewer than two options")
        answer = _normalise_answer(_column(row, fields, "answer"), options)
        if answer["letter"] not in options:
            raise PackValidationError(
                f"{source} row {row_number}: answer {answer['letter']!r} is not one of {sorted(options)}"
            )
        prompt_fields.update({
            "item_id": item_id,
            "question": str(_column(row, fields, "question")).strip(),
            "options": options,
            "options_block": "\n".join(f"{letter}. {text}" for letter, text in sorted(options.items())),
        })
        gold.update({"letter": answer["letter"], "text": answer["text"], "options": options})
        abstain_expected = _column(row, fields, "abstain_expected")
        if abstain_expected not in (None, ""):
            gold["abstain_expected"] = str(abstain_expected).strip().lower() in {"1", "true", "yes"}
    elif task_type in {"open_ended_rubric", "multi_judge_comparison"}:
        question = str(_column(row, fields, "question")).strip()
        reference = _column(row, fields, "reference_answer")
        rubric = _parse_jsonish(_column(row, fields, "rubric"))
        prompt_fields.update({"item_id": item_id, "question": question})
        if rubric not in (None, ""):
            prompt_fields["rubric"] = rubric
        if reference not in (None, ""):
            prompt_fields["reference_answer"] = str(reference)
        gold.update({
            "reference_answer": "" if reference is None else str(reference),
            "rubric": rubric if rubric is not None else [],
        })
        if task_type == "multi_judge_comparison":
            prompt_fields["candidate_a"] = str(_column(row, fields, "candidate_a") or "")
            prompt_fields["candidate_b"] = str(_column(row, fields, "candidate_b") or "")
    elif task_type == "structured_extraction":
        raw_input = _column(row, fields, "input")
        gold_value = _parse_jsonish(_column(row, fields, "gold"))
        if not isinstance(gold_value, Mapping):
            raise PackValidationError(
                f"{source} row {row_number}: gold must be a JSON object (column {fields.get('gold', 'gold')!r})"
            )
        prompt_fields.update({"item_id": item_id, "input": str(raw_input), "abstract": str(raw_input)})
        gold.update({"fields": dict(gold_value)})
        schema_value = _parse_jsonish(_column(row, fields, "schema"))
        if isinstance(schema_value, Mapping):
            gold["schema"] = dict(schema_value)
    elif task_type == "triage_classification":
        raw_input = _column(row, fields, "input")
        label = str(_column(row, fields, "label")).strip()
        prompt_fields.update({"item_id": item_id, "input": str(raw_input), "text": str(raw_input)})
        gold.update({"label": label})

    for canonical in ("category", "split"):
        value = _column(row, fields, canonical)
        if value not in (None, ""):
            meta[canonical] = str(value)
    raw_meta = _column(row, fields, "meta")
    if raw_meta not in (None, ""):
        parsed_meta = _parse_jsonish(raw_meta)
        if isinstance(parsed_meta, Mapping):
            meta.update({str(key): value for key, value in parsed_meta.items()})

    return PackItem(
        item_id=item_id,
        task_type=task_type,
        prompt_fields=prompt_fields,
        gold=gold,
        meta=meta,
        stratum=stratum or str(meta.get("split") or meta.get("category") or ""),
        item_hash=_item_hash({"item_id": item_id, "prompt": prompt_fields, "gold": gold}),
    )


def load_items(pack: PaperPack, *, stage: str = "full") -> list[PackItem]:
    """Load, normalise and deterministically sample a pack's dataset.

    Sampling is content-addressed: rows are ordered by
    ``sha256("<seed>:<item_id>")`` and the first ``limit`` are kept.  That order is
    stable across Python versions (unlike ``random.sample``) and independent of
    file order, so two people with the same pack get the same items.

    ``stage`` is ``"screening"`` for the optional first pass (the first
    ``screening_size`` sampled items) or ``"full"`` for every sampled item.
    """
    path = pack.dataset_file()
    rows = read_rows(path, pack.dataset.format)
    items: list[PackItem] = []
    for row_number, row in enumerate(rows, start=1):
        items.append(
            normalise_item(
                row,
                task_type=pack.task_type,
                fields=pack.dataset.fields,
                row_number=row_number,
                source=path.name,
            )
        )
    seen: dict[str, int] = {}
    for item in items:
        seen[item.item_id] = seen.get(item.item_id, 0) + 1
    duplicates = sorted(item_id for item_id, count in seen.items() if count > 1)
    if duplicates:
        raise PackValidationError(f"dataset has duplicate item ids: {', '.join(duplicates[:5])}")

    items.sort(key=lambda item: hashlib.sha256(f"{pack.dataset.seed}:{item.item_id}".encode()).hexdigest())
    if pack.dataset.limit is not None:
        items = items[: pack.dataset.limit]
    if stage == "screening" and pack.dataset.screening_size:
        items = items[: pack.dataset.screening_size]
    elif stage not in {"screening", "full"}:
        raise PackValidationError(f"unknown stage {stage!r}; use 'screening' or 'full'")
    if not items:
        raise PackValidationError(f"pack {pack.id!r} selected zero items")
    return items
