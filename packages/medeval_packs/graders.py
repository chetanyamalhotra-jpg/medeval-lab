"""Composable graders for Paper Packs.

Two kinds:

* **deterministic** — pure functions over the target output and the item's gold
  fields: multiple-choice accuracy, label accuracy, abstention, format
  compliance, JSON-schema validity, field-level F1 / exact match, exact match and
  refusal detection.  These never call a model.
* **llm_rubric** — a prompt template rendered for every configured judge.  The
  judge's reply must be JSON with a numeric ``score`` (a bare number is also
  accepted); an unparseable reply is recorded as a parse failure, not as a zero.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .dataset import PackItem
from .render import PromptRenderError, render_template
from .schema import GraderSpec

__all__ = [
    "GraderOutcome",
    "detect_refusal",
    "parse_choice",
    "parse_json_object",
    "parse_label",
    "parse_numeric",
    "run_deterministic_grader",
    "run_rubric_grader_prompt",
]

_ABSTAIN_PATTERNS = (
    r"\bABSTAIN\b",
    r"\bUNKNOWN\b",
    r"\bNOT[\s-]?SURE\b",
    r"\bUNSURE\b",
    r"\bCAN(?:NOT|'T)\s+(?:ANSWER|DETERMINE|BE\s+DETERMINED)\b",
    r"\bINSUFFICIENT\s+(?:INFORMATION|EVIDENCE)\b",
    r"\bI\s+DON'?T\s+KNOW\b",
)
_REFUSAL_PATTERNS = (
    r"\bI\s+(?:CANNOT|CAN'T|AM\s+UNABLE\s+TO)\b",
    r"\bAS\s+AN\s+AI\b",
    r"\bNOT\s+(?:ABLE|ALLOWED)\s+TO\s+(?:PROVIDE|GIVE|ANSWER)\b",
)
_CHOICE_RE = re.compile(r"^\s*(?:ANSWER|OPTION|CHOICE)?\s*[:\-]?\s*\(?([A-E])\)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class GraderOutcome:
    """One grader's result for one item and one target output."""

    grader_id: str
    metric: str
    value: float | None
    passed: bool | None = None
    parsed: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "grader_id": self.grader_id,
            "metric": self.metric,
            "value": self.value,
            "passed": self.passed,
            "parsed": dict(self.parsed),
            "detail": self.detail,
            "error": self.error,
        }


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def detect_abstention(text: str) -> bool:
    upper = text.upper()
    return any(re.search(pattern, upper) for pattern in _ABSTAIN_PATTERNS)


def detect_refusal(text: str) -> bool:
    upper = text.upper()
    return any(re.search(pattern, upper) for pattern in _REFUSAL_PATTERNS)


def parse_choice(text: str, options: Mapping[str, str]) -> dict[str, Any]:
    """Parse a multiple-choice answer into ``{"letter", "abstained", "ok"}``.

    A reply that names exactly one option's text is mapped to that option even
    when it never writes a letter.  Two different letters means the reply is
    ambiguous and is a parse failure rather than a guess.
    """
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {"letter": None, "abstained": False, "ok": False, "reason": "empty output"}
    if detect_abstention(cleaned):
        return {"letter": None, "abstained": True, "ok": True, "reason": "abstention phrase"}
    match = _CHOICE_RE.search(cleaned)
    if match:
        letter = match.group(1).upper()
        if letter in options:
            return {"letter": letter, "abstained": False, "ok": True, "reason": "leading letter"}
    letters = sorted({found.upper() for found in re.findall(r"\b([A-E])\b", cleaned) if found.upper() in options})
    if len(letters) == 1:
        return {"letter": letters[0], "abstained": False, "ok": True, "reason": "single letter in reply"}
    named = [
        letter
        for letter, option in options.items()
        if option.strip() and option.strip().casefold() in cleaned.casefold()
    ]
    if len(named) == 1:
        return {"letter": named[0], "abstained": False, "ok": True, "reason": "option text named"}
    return {"letter": None, "abstained": False, "ok": False, "reason": "no unambiguous option in reply"}


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Extract the first JSON object from a reply, tolerating a code fence."""
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return None, "empty output"
    try:
        value = json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None, "no JSON object in reply"
        try:
            value = json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            return None, f"invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "JSON value is not an object"
    return value, None


def _normalise(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def parse_label(text: str, allowed: Sequence[str]) -> dict[str, Any]:
    """Parse a three-class (or general) label from a reply.

    Accepts JSON (``{"label": "..."}``), a bare first line, or a reply that names
    exactly one allowed label.  Two different allowed labels in one reply is an
    ambiguous parse failure, not a guess.
    """
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {"label": None, "abstained": False, "ok": False, "reason": "empty output"}
    wanted = {_normalise(label).replace("_", "-"): label for label in allowed}
    parsed, _error = parse_json_object(cleaned)
    candidate = ""
    if parsed is not None:
        candidate = str(parsed.get("label") or parsed.get("verdict") or parsed.get("class") or "")
    if not candidate:
        candidate = cleaned.splitlines()[0]
    key = _normalise(candidate).replace("_", "-").strip(".,;:")
    if key in wanted:
        label = wanted[key]
        return {
            "label": label,
            "abstained": key in {"not-sure", "abstain", "unknown"},
            "ok": True,
            "reason": "label field" if parsed is not None and parsed.get("label") else "first line",
        }
    mentioned = sorted(
        {
            original
            for key, original in wanted.items()
            if re.search(rf"(?<![a-z-]){re.escape(key)}(?![a-z-])", _normalise(cleaned).replace("_", "-"))
        }
    )
    if len(mentioned) == 1:
        label = mentioned[0]
        return {
            "label": label,
            "abstained": _normalise(label).replace("_", "-") in {"not-sure", "abstain", "unknown"},
            "ok": True,
            "reason": "single allowed label mentioned",
        }
    return {"label": None, "abstained": False, "ok": False, "reason": "no unambiguous allowed label in reply"}


def parse_numeric(text: str, field: str = "result") -> dict[str, Any]:
    """Extract one numeric value from JSON (preferred) or from plain text."""
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {"value": None, "ok": False, "reason": "empty output"}
    parsed, error = parse_json_object(cleaned)
    raw: Any = None
    if parsed is not None:
        if isinstance(parsed.get("fields"), Mapping):
            raw = parsed["fields"].get(field)
        if raw is None:
            raw = parsed.get(field)
        if raw is None and field != "result":
            raw = parsed.get("result")
        if raw is None and field != "answer":
            raw = parsed.get("answer")
    if raw is None:
        match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", cleaned)
        raw = match.group(0) if match else None
        error = None if raw is not None else (error or "no numeric value in reply")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"value": None, "ok": False, "reason": error or "not numeric"}
    if not math.isfinite(value):
        return {"value": None, "ok": False, "reason": "non-finite number"}
    return {"value": value, "ok": True, "reason": None}


def _field_map(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        inner = value.get("fields")
        if isinstance(inner, Mapping):
            return {str(key): item for key, item in inner.items()}
        return {str(key): item for key, item in value.items()}
    return {}


def _field_f1(predicted: Mapping[str, Any], gold: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    per_field: dict[str, Any] = {}
    true_positive = 0
    for name in fields:
        predicted_value = _normalise(predicted.get(name))
        gold_value = _normalise(gold.get(name))
        if not gold_value and not predicted_value:
            continue
        if predicted_value == gold_value and gold_value:
            true_positive += 1
            per_field[name] = {"match": True, "predicted": predicted.get(name), "gold": gold.get(name)}
        else:
            per_field[name] = {"match": False, "predicted": predicted.get(name), "gold": gold.get(name)}
    predicted_present = sum(1 for name in fields if _normalise(predicted.get(name)))
    gold_present = sum(1 for name in fields if _normalise(gold.get(name)))
    precision = true_positive / predicted_present if predicted_present else 0.0
    recall = true_positive / gold_present if gold_present else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    exact = true_positive == gold_present == predicted_present and gold_present > 0
    return {
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "exact_match": exact,
        "true_positive": true_positive,
        "gold_fields": gold_present,
        "predicted_fields": predicted_present,
        "per_field": per_field,
    }


def _format_ok(item: PackItem, text: str) -> tuple[bool, dict[str, Any], str]:
    if not text.strip():
        return False, {}, "empty output"
    if item.task_type == "mcq":
        parsed = parse_choice(text, item.gold.get("options") or item.prompt_fields.get("options") or {})
        return bool(parsed["ok"]), parsed, "" if parsed["ok"] else str(parsed.get("reason") or "unparseable")
    if item.task_type == "structured_extraction":
        parsed, error = parse_json_object(text)
        return parsed is not None, parsed or {}, error or ""
    return True, {"text": text}, ""


def run_deterministic_grader(
    grader: GraderSpec,
    item: PackItem,
    output_text: str,
    *,
    latency_ms: float | None = None,
) -> GraderOutcome:
    """Run one deterministic grader.  Never raises on malformed model output."""
    metric = grader.metric
    options = item.gold.get("options") or item.prompt_fields.get("options") or {}

    if metric == "mcq_accuracy":
        parsed = parse_choice(output_text, options)
        if not parsed["ok"]:
            # A malformed reply is an incorrect answer for accuracy, not a
            # missing observation: dropping it would inflate the denominator.
            return GraderOutcome(grader.id, metric, 0.0, False, parsed,
                                 "unparseable multiple-choice reply (counted incorrect)", "parse_error")
        if parsed["abstained"]:
            return GraderOutcome(grader.id, metric, 0.0, False, parsed, "abstained")
        correct = parsed["letter"] == item.gold.get("letter")
        return GraderOutcome(grader.id, metric, 1.0 if correct else 0.0, correct, parsed,
                             "correct" if correct else "incorrect")

    if metric == "label_accuracy":
        predicted = _normalise(output_text.splitlines()[0] if output_text.strip() else "")
        gold = _normalise(item.gold.get("label"))
        if not gold:
            return GraderOutcome(grader.id, metric, None, None, {}, "item has no label", "missing_gold")
        if not predicted:
            return GraderOutcome(grader.id, metric, None, None, {}, "empty output", "parse_error")
        correct = gold in predicted or predicted in gold
        return GraderOutcome(grader.id, metric, 1.0 if correct else 0.0, correct,
                             {"predicted": predicted, "gold": gold}, "compared" )

    if metric in {"abstention", "refusal"}:
        if metric == "refusal":
            detected = detect_refusal(output_text)
            detail = "refusal phrase detected" if detected else "no refusal detected"
        else:
            detected = detect_abstention(output_text)
            detail = "abstention phrase detected" if detected else "no abstention detected"
        return GraderOutcome(grader.id, metric, 1.0 if detected else 0.0, detected, {"detected": detected}, detail)

    if metric in {"macro_f1", "multiclass_f1", "macro_precision"}:
        allowed = grader.options.get("labels")
        labels = [str(label) for label in allowed] if isinstance(allowed, Sequence) and not isinstance(allowed, str) else [
            "hallucinated",
            "not-hallucinated",
            "not-sure",
        ]
        parsed = parse_label(output_text, labels)
        gold = _normalise(item.gold.get("label"))
        if not parsed["ok"]:
            return GraderOutcome(
                grader.id,
                metric,
                0.0,
                False,
                {**parsed, "predicted": "__parse_error__", "gold": item.gold.get("label"), "labels": labels,
                 "correct": False},
                "unparseable or ambiguous label reply (counted incorrect)",
                "parse_error",
            )
        if not gold:
            return GraderOutcome(grader.id, metric, None, None, parsed, "item has no gold label", "missing_gold")
        correct = _normalise(parsed["label"]) == gold
        return GraderOutcome(
            grader.id,
            metric,
            1.0 if correct else 0.0,
            correct,
            {**parsed, "predicted": parsed["label"], "gold": item.gold.get("label"), "labels": labels,
             "correct": correct},
            "correct" if correct else "incorrect",
        )

    if metric == "coverage":
        parsed = parse_label(
            output_text,
            [
                str(label)
                for label in (grader.options.get("labels") or ["hallucinated", "not-hallucinated", "not-sure"])
            ],
        )
        covered = bool(parsed["ok"]) and not parsed["abstained"]
        return GraderOutcome(
            grader.id,
            metric,
            1.0 if covered else 0.0,
            covered,
            parsed,
            "made a decision" if covered else "abstained or unparseable",
        )

    if metric in {"numeric_match_exact", "numeric_match_tolerance"}:
        field = str(grader.options.get("field") or "result")
        tolerance_pct = float(
            grader.options.get(
                "tolerance_pct",
                0.0 if metric == "numeric_match_exact" else 5.0,
            )
        )
        parsed = parse_numeric(output_text, field)
        gold_fields = item.gold.get("fields") if isinstance(item.gold.get("fields"), Mapping) else item.gold
        gold_raw = gold_fields.get(field)
        try:
            gold_value = float(gold_raw)
        except (TypeError, ValueError):
            return GraderOutcome(grader.id, metric, None, None, parsed, f"gold has no numeric {field!r}", "missing_gold")
        if not parsed["ok"]:
            return GraderOutcome(grader.id, metric, 0.0, False, parsed,
                                 "no parseable numeric answer (counted incorrect)", "parse_error")
        predicted = float(parsed["value"])
        absolute_error = abs(predicted - gold_value)
        if gold_value == 0:
            within = absolute_error <= 1e-9
            relative_error = 0.0 if within else float("inf")
        else:
            relative_error = absolute_error / abs(gold_value) * 100.0
            within = relative_error <= tolerance_pct + 1e-12
        detail = {
            "predicted": predicted,
            "gold": gold_value,
            "absolute_error": absolute_error,
            "relative_error_pct": relative_error,
            "tolerance_pct": tolerance_pct,
            "within_band": within,
        }
        label = "exact" if tolerance_pct == 0 else f"±{tolerance_pct:g}% band"
        return GraderOutcome(
            grader.id,
            metric,
            1.0 if within else 0.0,
            within,
            detail,
            f"{label}: {'match' if within else 'outside band'}",
        )

    if metric == "format_compliance":
        ok, parsed, error = _format_ok(item, output_text)
        return GraderOutcome(grader.id, metric, 1.0 if ok else 0.0, ok, parsed,
                             "format ok" if ok else f"format failure: {error}", None if ok else "format_error")

    if metric == "json_schema":
        parsed, error = parse_json_object(output_text)
        if parsed is None:
            return GraderOutcome(grader.id, metric, 0.0, False, {}, f"format failure: {error}", "parse_error")
        schema = grader.options.get("schema") or item.gold.get("schema")
        if not isinstance(schema, Mapping):
            return GraderOutcome(grader.id, metric, None, None, {"parsed": parsed},
                                 "no JSON schema declared in grader options or item", "missing_schema")
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - jsonschema is a dependency
            return GraderOutcome(grader.id, metric, None, None, {"parsed": parsed},
                                 "jsonschema is not installed", "missing_dependency")
        try:
            jsonschema.validate(instance=parsed, schema=dict(schema))
        except jsonschema.ValidationError as exc:
            return GraderOutcome(grader.id, metric, 0.0, False, {"parsed": parsed, "error": exc.message},
                                 f"schema violation: {exc.message}", None)
        return GraderOutcome(grader.id, metric, 1.0, True, {"parsed": parsed}, "valid against schema")

    if metric in {"field_f1", "field_exact_match"}:
        parsed, error = parse_json_object(output_text)
        if parsed is None:
            return GraderOutcome(grader.id, metric, 0.0, False, {}, f"format failure: {error}", "parse_error")
        predicted = _field_map(parsed)
        gold = _field_map(item.gold.get("fields"))
        declared = grader.options.get("fields")
        fields = [str(name) for name in declared] if isinstance(declared, Sequence) and not isinstance(declared, str) else sorted(
            set(predicted) | set(gold)
        )
        stats = _field_f1(predicted, gold, fields)
        value = 1.0 if stats["exact_match"] else 0.0 if metric == "field_exact_match" else stats["f1"]
        return GraderOutcome(
            grader.id,
            metric,
            float(value),
            bool(stats["exact_match"]) if metric == "field_exact_match" else None,
            stats,
            f"field F1 {stats['f1']:.3f} ({stats['true_positive']}/{stats['gold_fields']} fields)",
        )

    if metric == "exact_match":
        predicted = _normalise(output_text)
        gold = _normalise(item.gold.get("reference_answer") or item.gold.get("text") or item.gold.get("label"))
        if not gold:
            return GraderOutcome(grader.id, metric, None, None, {}, "item has no reference answer", "missing_gold")
        correct = predicted == gold
        return GraderOutcome(grader.id, metric, 1.0 if correct else 0.0, correct, {"predicted": predicted}, "compared")

    return GraderOutcome(grader.id, metric, None, None, {}, f"unsupported deterministic metric {metric!r}",
                         "unsupported_metric")


def run_rubric_grader_prompt(grader: GraderSpec, item: PackItem, target_text: str) -> str:
    """Render the judge prompt for one rubric grader.

    The judge sees the question, the candidate answer, the reference answer and
    the rubric.  It does not see multiple-choice keys: an ``mcq`` item's gold
    letter is not part of any rubric prompt.
    """
    variables = {
        "item_id": item.item_id,
        "question": item.prompt_fields.get("question") or item.prompt_fields.get("input") or "",
        "target_answer": target_text,
        "reference_answer": item.gold.get("reference_answer") or "",
        "rubric": item.gold.get("rubric") or item.prompt_fields.get("rubric") or "",
        "scale_min": grader.scale_min,
        "scale_max": grader.scale_max,
    }
    try:
        return render_template(grader.prompt, variables, where=f"grader {grader.id!r} prompt")
    except PromptRenderError:
        raise


def parse_rubric_score(text: str, *, scale_min: float, scale_max: float) -> dict[str, Any]:
    """Parse ``{"score": n, "rationale": "..."}`` (or a bare number)."""
    parsed, error = parse_json_object(text)
    if parsed is not None:
        raw = parsed.get("score", parsed.get("rating", parsed.get("value")))
        rationale = str(parsed.get("rationale") or parsed.get("reason") or "")
    else:
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", _strip_code_fence(text))
        raw = match.group(1) if match else None
        rationale = _strip_code_fence(text)[:500]
        error = None if raw is not None else error
    try:
        score = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"score": None, "rationale": rationale, "ok": False, "error": error or "no numeric score"}
    if score < scale_min or score > scale_max:
        return {
            "score": score,
            "rationale": rationale,
            "ok": False,
            "error": f"score {score:g} outside declared scale [{scale_min:g}, {scale_max:g}]",
        }
    return {"score": score, "rationale": rationale, "ok": True, "error": None}
