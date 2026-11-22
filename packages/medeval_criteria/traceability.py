"""Traceability checks: paper -> construct -> criterion -> metric -> judge.

The chain must be complete for every criterion and metric. Unverified rows are
allowed (the registry records ``verified=no``) but are reported, because
"unverified citation" and "no citation" are different states and only the second
is disqualifying.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from medeval_core.paths import literature_csv

from .loader import CriterionCatalogue

__all__ = [
    "LiteratureRow",
    "TraceabilityIssue",
    "check_traceability",
    "load_literature",
]

REQUIRED_COLUMNS = (
    "id",
    "doi",
    "title",
    "year",
    "task",
    "reference_standard",
    "metric_proposed",
    "how_we_operationalize",
    "limitations",
    "verified",
)


@dataclass(frozen=True)
class LiteratureRow:
    """One registry row."""

    id: str
    doi: str
    title: str
    year: str
    task: str
    reference_standard: str
    metric_proposed: str
    how_we_operationalize: str
    limitations: str
    verified: str

    @property
    def is_verified(self) -> bool:
        return self.verified.strip().lower() in {"yes", "true", "1"}

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "doi": self.doi,
            "title": self.title,
            "year": self.year,
            "task": self.task,
            "reference_standard": self.reference_standard,
            "metric_proposed": self.metric_proposed,
            "how_we_operationalize": self.how_we_operationalize,
            "limitations": self.limitations,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class TraceabilityIssue:
    """A broken or weak link in the chain."""

    severity: str  # "error" | "warning"
    subject: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "subject": self.subject, "message": self.message}


def load_literature(path: Path | None = None) -> dict[str, LiteratureRow]:
    """Read data/literature.csv into a dict keyed by literature id."""
    source = Path(path) if path is not None else literature_csv()
    if not source.is_file():
        raise FileNotFoundError(f"literature registry not found: {source}")
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            raise ValueError(f"{source}: missing columns {missing}")
        rows: dict[str, LiteratureRow] = {}
        for raw in reader:
            if not raw.get("id"):
                continue
            row = LiteratureRow(
                id=raw["id"].strip(),
                doi=(raw.get("doi") or "").strip(),
                title=(raw.get("title") or "").strip(),
                year=(raw.get("year") or "").strip(),
                task=(raw.get("task") or "").strip(),
                reference_standard=(raw.get("reference_standard") or "").strip(),
                metric_proposed=(raw.get("metric_proposed") or "").strip(),
                how_we_operationalize=(raw.get("how_we_operationalize") or "").strip(),
                limitations=(raw.get("limitations") or "").strip(),
                verified=(raw.get("verified") or "").strip(),
            )
            if row.id in rows:
                raise ValueError(f"{source}: duplicate literature id {row.id!r}")
            rows[row.id] = row
    return rows


def check_traceability(
    catalogue: CriterionCatalogue,
    metrics: Mapping[str, Any],
    literature: Mapping[str, LiteratureRow],
) -> list[TraceabilityIssue]:
    """Return every broken or weak link between criteria, metrics and papers."""
    issues: list[TraceabilityIssue] = []

    for row in literature.values():
        if not row.title:
            issues.append(TraceabilityIssue("error", row.id, "registry row has no title"))
        if not row.doi:
            issues.append(
                TraceabilityIssue("warning", row.id, "registry row has no DOI (marked unverified)")
            )
        if row.verified.lower() not in {"yes", "no"}:
            issues.append(
                TraceabilityIssue(
                    "error", row.id, f"verified column must be yes/no, found {row.verified!r}"
                )
            )
        if row.verified.lower() == "no":
            issues.append(
                TraceabilityIssue(
                    "warning",
                    row.id,
                    "citation not verified against a DOI resolver; see VERIFY_ME.md",
                )
            )

    for criterion in catalogue.criteria:
        if not criterion.literature_ids:
            issues.append(
                TraceabilityIssue("error", criterion.id, "criterion has no literature IDs")
            )
        for literature_id in criterion.literature_ids:
            if literature_id not in literature:
                issues.append(
                    TraceabilityIssue(
                        "error",
                        criterion.id,
                        f"cites {literature_id!r} which is absent from data/literature.csv",
                    )
                )

    for metric_id, definition in metrics.items():
        ids = getattr(definition, "literature_ids", ())
        if not ids:
            issues.append(TraceabilityIssue("error", metric_id, "metric has no literature IDs"))
        for literature_id in ids:
            if literature_id not in literature:
                issues.append(
                    TraceabilityIssue(
                        "error",
                        metric_id,
                        f"cites {literature_id!r} which is absent from data/literature.csv",
                    )
                )
    return issues


def format_issues(issues: Sequence[TraceabilityIssue]) -> str:
    if not issues:
        return "traceability: all criteria and metrics trace to registry rows"
    lines = []
    for issue in issues:
        lines.append(f"[{issue.severity}] {issue.subject}: {issue.message}")
    return "\n".join(lines)
