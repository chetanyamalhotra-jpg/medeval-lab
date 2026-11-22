"""Item loading, JSON Schema validation and semantic checks.

The JSON Schema (``schemas/item.schema.json``) enforces shape. This module adds
the checks a schema cannot express:

* ``gold_answer`` must be exactly one of ``options`` for multiple-choice items;
* synthetic / planted-error items must not claim a DOI, while paper and
  guideline items must supply one;
* identifiers must be unique, options must not carry ``A.`` style prefixes, and
  future-dated provenance is rejected;
* a human reference with fewer than two raters cannot support agreement and is
  flagged.

The validator never edits or drops an item: it returns issues. Callers decide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from .paths import schemas_dir

Severity = Literal["error", "warning"]

SCHEMA_PATH = schemas_dir() / "item.schema.json"

#: Licence strings that mean "no licence recorded".
_PLACEHOLDER_LICENCES = {"", "unknown", "tbd", "none", "n/a", "na", "null", "-"}

#: Licences considered open enough for redistribution of derived items.
OPEN_LICENCES = {
    "cc0-1.0",
    "cc-by-4.0",
    "cc-by-3.0",
    "cc-by-sa-4.0",
    "mit",
    "apache-2.0",
    "bsd-3-clause",
    "odc-by-1.0",
    "odbl-1.0",
    "synthetic-original",
    "public-domain",
}

_NO_DOI_SOURCES = {"synthetic", "planted_error"}
_REQUIRE_DOI_SOURCES = {"paper_finding", "guideline"}


@dataclass(frozen=True)
class Issue:
    """One validation finding. ``error`` blocks use; ``warning`` does not."""

    item_id: str
    code: str
    message: str
    severity: Severity = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class ValidationReport:
    """Aggregate validation outcome for one item set."""

    n_items: int = 0
    issues: list[Issue] = field(default_factory=list)
    source: str | None = None

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "n_items": self.n_items,
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "ok": self.ok,
            "counts_by_code": self.counts_by_code(),
            "issues": [issue.as_dict() for issue in self.issues],
        }

    def raise_for_errors(self) -> None:
        if self.errors:
            first = self.errors[0]
            raise ItemValidationError(
                f"{len(self.errors)} validation error(s); first: "
                f"[{first.item_id}] {first.code}: {first.message}"
            )


class ItemValidationError(ValueError):
    """Raised when an item set fails validation."""


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Load the shipped item JSON Schema."""
    schema_path = path or SCHEMA_PATH
    with schema_path.open("r", encoding="utf-8") as handle:
        schema: dict[str, Any] = json.load(handle)
    return schema


def load_items(path: Path | str) -> list[dict[str, Any]]:
    """Load items from a ``.json`` array file or a ``.jsonl`` file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"item file not found: {p}")
    if p.suffix.lower() == ".jsonl":
        items: list[dict[str, Any]] = []
        with p.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ItemValidationError(f"{p}:{line_no}: invalid JSON: {exc}") from exc
        return items
    with p.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return list(data["items"])
    raise ItemValidationError(f"{p}: expected a JSON array or an object with an 'items' array")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def validate_item(
    item: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[Issue]:
    """Validate one item and return its issues."""
    today = today or date.today()
    schema = schema if schema is not None else load_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    item_id = str(item.get("id", "<missing-id>"))
    issues: list[Issue] = []

    for err in sorted(validator.iter_errors(item), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(part) for part in err.absolute_path) or "<root>"
        issues.append(
            Issue(
                item_id=item_id,
                code="schema",
                message=f"{location}: {err.message}",
                severity="error",
            )
        )

    # If the shape is broken, semantic checks may crash; guard them.
    options = item.get("options")
    gold = item.get("gold_answer")
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    label_source = item.get("label_source")

    if isinstance(options, list) and isinstance(gold, str):
        if options:
            if gold not in options:
                issues.append(
                    Issue(
                        item_id=item_id,
                        code="gold_not_in_options",
                        message="gold_answer is not one of the options",
                    )
                )
            lowered = [_norm(str(opt)) for opt in options]
            if len(set(lowered)) != len(lowered):
                issues.append(
                    Issue(
                        item_id=item_id,
                        code="duplicate_option_text",
                        message="two options are identical after normalisation",
                    )
                )
            for opt in options:
                if isinstance(opt, str) and len(opt) > 2 and opt[0].isalpha() and opt[1] in ").:":
                    issues.append(
                        Issue(
                            item_id=item_id,
                            code="option_has_letter_prefix",
                            message=f"option {opt[:12]!r} carries a letter prefix; the harness adds its own",
                            severity="warning",
                        )
                    )
                    break
        else:
            issues.append(
                Issue(
                    item_id=item_id,
                    code="open_ended_gold",
                    message="open-ended item: gold_answer is free text and cannot be exact-matched",
                    severity="warning",
                )
            )

    doi = provenance.get("doi")
    if label_source in _NO_DOI_SOURCES and doi:
        issues.append(
            Issue(
                item_id=item_id,
                code="synthetic_claims_doi",
                message=f"label_source={label_source} must not carry a DOI (found {doi})",
            )
        )
    if label_source in _REQUIRE_DOI_SOURCES and not doi:
        issues.append(
            Issue(
                item_id=item_id,
                code="missing_doi",
                message=f"label_source={label_source} requires a DOI",
            )
        )

    licence = provenance.get("licence")
    if isinstance(licence, str):
        if licence.strip().lower() in _PLACEHOLDER_LICENCES:
            issues.append(
                Issue(item_id=item_id, code="missing_licence", message="licence is a placeholder")
            )
        elif licence.strip().lower() not in OPEN_LICENCES:
            issues.append(
                Issue(
                    item_id=item_id,
                    code="non_open_licence",
                    message=f"licence {licence!r} is not in the known open set; verify redistribution rights",
                    severity="warning",
                )
            )
    elif licence is None:
        issues.append(
            Issue(item_id=item_id, code="missing_licence", message="provenance.licence is missing")
        )

    for field_name in ("publication_date",):
        raw = item.get(field_name)
        if isinstance(raw, str):
            try:
                parsed = date.fromisoformat(raw)
            except ValueError:
                continue  # schema already reported it
            if parsed > today:
                issues.append(
                    Issue(
                        item_id=item_id,
                        code="future_date",
                        message=f"{field_name} is in the future ({raw})",
                    )
                )
    extraction = provenance.get("extraction_date")
    if isinstance(extraction, str):
        try:
            if date.fromisoformat(extraction) > today:
                issues.append(
                    Issue(
                        item_id=item_id,
                        code="future_date",
                        message=f"provenance.extraction_date is in the future ({extraction})",
                    )
                )
        except ValueError:
            pass

    if item.get("review_status") == "unreviewed-draft":
        issues.append(
            Issue(
                item_id=item_id,
                code="unreviewed_draft",
                message="item has not been author-reviewed",
                severity="warning",
            )
        )

    if label_source == "planted_error" and not (item.get("notes") or "").strip():
        issues.append(
            Issue(
                item_id=item_id,
                code="planted_error_without_notes",
                message="planted_error items should describe the planted error in notes",
                severity="warning",
            )
        )

    human = item.get("human_reference")
    if isinstance(human, dict):
        raters = human.get("rater_ids")
        if not isinstance(raters, list) or len(raters) < 2:
            issues.append(
                Issue(
                    item_id=item_id,
                    code="single_rater_reference",
                    message="human_reference has fewer than two raters; agreement cannot be computed",
                    severity="warning",
                )
            )

    return issues


def validate_items(
    items: list[dict[str, Any]],
    *,
    source: str | None = None,
    schema: dict[str, Any] | None = None,
    today: date | None = None,
) -> ValidationReport:
    """Validate a list of items, including cross-item duplicate detection."""
    schema = schema if schema is not None else load_schema()
    report = ValidationReport(n_items=len(items), source=source)
    seen_ids: dict[str, int] = {}
    seen_text: dict[str, str] = {}

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            report.issues.append(
                Issue(item_id=f"<index {index}>", code="not_an_object", message="item is not an object")
            )
            continue
        report.issues.extend(validate_item(item, schema=schema, today=today))

        item_id = str(item.get("id", ""))
        if item_id:
            if item_id in seen_ids:
                report.issues.append(
                    Issue(
                        item_id=item_id,
                        code="duplicate_id",
                        message=f"duplicate id also used at index {seen_ids[item_id]}",
                    )
                )
            else:
                seen_ids[item_id] = index

        text = item.get("text")
        if isinstance(text, str):
            key = _norm(text)
            if key in seen_text:
                report.issues.append(
                    Issue(
                        item_id=item_id or f"<index {index}>",
                        code="duplicate_text",
                        message=f"same wording as {seen_text[key]}",
                        severity="warning",
                    )
                )
            else:
                seen_text[key] = item_id or f"<index {index}>"
    return report


def validate_file(path: Path | str) -> ValidationReport:
    """Load and validate an item file."""
    p = Path(path)
    items = load_items(p)
    return validate_items(items, source=str(p))
