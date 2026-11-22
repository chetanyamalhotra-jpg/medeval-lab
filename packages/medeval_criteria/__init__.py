"""Literature-traced evaluation criteria.

The catalogue is versioned YAML; :func:`load_catalogue` validates it, and
:func:`load_rubric` checks that weights cover exactly the criteria of the version
they cite. :mod:`medeval_criteria.traceability` enforces that every criterion and
metric cites a row in ``data/literature.csv``.
"""

from __future__ import annotations

from .loader import (
    Anchor,
    Criterion,
    CriterionCatalogue,
    CriterionError,
    Rubric,
    catalogue_path,
    get_criterion,
    load_catalogue,
    load_rubric,
    rubric_path,
)
from .traceability import (
    LiteratureRow,
    TraceabilityIssue,
    check_traceability,
    format_issues,
    load_literature,
)

__all__ = [
    "Anchor",
    "Criterion",
    "CriterionCatalogue",
    "CriterionError",
    "LiteratureRow",
    "Rubric",
    "TraceabilityIssue",
    "catalogue_path",
    "check_traceability",
    "format_issues",
    "get_criterion",
    "load_catalogue",
    "load_literature",
    "load_rubric",
    "rubric_path",
]
