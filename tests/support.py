"""Shared test helpers as a real importable module.

Tests must not import `conftest` by name: a second conftest in a subdirectory
(tests/browser) would shadow it and break unrelated modules. Both directories are
packages, so pytest imports `tests.conftest` and `tests.browser.conftest`
under unique names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def make_item(**overrides: Any) -> dict[str, Any]:
    """A minimal valid synthetic item; overrides are applied on top."""
    item: dict[str, Any] = {
        "id": "test-0001",
        "text": "A 54-year-old man presents with crushing central chest pain radiating to the left arm.",
        "options": [
            "Acute myocardial infarction",
            "Pulmonary embolism",
            "Aortic dissection",
            "Pericarditis",
        ],
        "gold_answer": "Acute myocardial infarction",
        "label_source": "synthetic",
        "provenance": {
            "doi": None,
            "section": None,
            "licence": "synthetic-original",
            "extraction_date": "2026-01-15",
            "source_title": None,
        },
        "publication_date": "2026-01-15",
        "split": "dev",
        "version": "v0.1",
        "review_status": "single-rater, author-reviewed",
        "human_reference": None,
        "notes": "Synthetic vignette written for the v0.1 seed set.",
    }
    item.update(overrides)
    return item
