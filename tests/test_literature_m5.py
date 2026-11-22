"""M5: Papers library content (``data/literature.csv`` + ``VERIFY_ME.md``).

All checks are offline: they parse the registry, enforce the same traceability
rule as ``python -m medeval traceability``, and pin the honesty rule that every
row still marked ``verified=no`` is named in the M5 verification section of
``VERIFY_ME.md``. The suite-wide conftest refuses network access, so a passing
run also proves these tests do not look anything up.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from medeval_core.paths import literature_csv
from medeval_criteria import check_traceability, load_catalogue, load_literature
from medeval_metrics import METRICS

ROOT = Path(__file__).resolve().parents[1]
VERIFY_ME = ROOT / "VERIFY_ME.md"
M5_HEADING = "## M5 papers — verification status"

EXPECTED_COLUMNS = [
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
]

# The 21 rows that existed before M5. They must not be rewritten or dropped.
ORIGINAL_IDS = {
    "lit_medqa",
    "lit_medmcqa",
    "lit_pubmedqa",
    "lit_healthbench",
    "lit_medhelm",
    "lit_medhalt",
    "lit_medhallu",
    "lit_llm_judge_bias",
    "lit_rubric_grading",
    "lit_benchmark_quality",
    "lit_contamination",
    "lit_error_bars",
    "lit_self_consistency",
    "lit_cohen_kappa",
    "lit_fleiss_kappa",
    "lit_krippendorff",
    "lit_icc",
    "lit_landis_koch",
    "lit_brier",
    "lit_ece",
    "lit_mcnemar",
}

# The 19 candidate rows added by M5 (three supplied candidates - MedHELM,
# HealthBench and Adding Error Bars - already had registry rows and were not
# duplicated).
M5_IDS = {
    "lit_benchmark_plateau",
    "lit_judge_correlation",
    "lit_jev_judge",
    "lit_skillsbench",
    "lit_agent_judge_survey",
    "lit_claw_eval",
    "lit_cua_verifiers",
    "lit_terminal_bench",
    "lit_posttrainbench",
    "lit_lifeval",
    "lit_same_verdict",
    "lit_missing_info_judges",
    "lit_rater_validity",
    "lit_jury_vs_clinician",
    "lit_gemini_medicine",
    "lit_medmarks",
    "lit_evalstats",
    "lit_clt_interval",
    "lit_jmir_medqa_integrity",
}


def _raw_rows() -> list[dict[str, str]]:
    with literature_csv().open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_csv_parses_with_expected_columns() -> None:
    with literature_csv().open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    assert fieldnames == EXPECTED_COLUMNS
    assert rows, "literature registry is empty"
    for row in rows:
        assert row["id"].strip(), row
        assert row["title"].strip(), row["id"]


def test_ids_are_unique_and_non_empty() -> None:
    rows: list[dict[str, Any]] = _raw_rows()
    ids = [row["id"].strip() for row in rows]
    assert all(ids), "every row must have a non-empty id"
    assert len(ids) == len(set(ids)), f"duplicate literature ids: {sorted({i for i in ids if ids.count(i) > 1})}"


def test_existing_registry_rows_are_intact() -> None:
    registry = load_literature()
    missing = ORIGINAL_IDS - set(registry)
    assert not missing, f"pre-M5 registry rows were removed: {sorted(missing)}"
    for row_id in ORIGINAL_IDS:
        assert registry[row_id].title.strip(), row_id


def test_all_m5_candidate_rows_were_added() -> None:
    registry = load_literature()
    missing = M5_IDS - set(registry)
    assert not missing, f"M5 rows missing from data/literature.csv: {sorted(missing)}"
    for row_id in M5_IDS:
        row = registry[row_id]
        assert row.how_we_operationalize.strip(), row_id
        assert row.limitations.strip(), row_id


def test_every_criterion_and_metric_literature_id_is_in_the_registry() -> None:
    registry = load_literature()
    catalogue = load_catalogue()
    for criterion in catalogue.criteria:
        for literature_id in criterion.literature_ids:
            assert literature_id in registry, f"{criterion.id} cites missing row {literature_id!r}"
    for metric_id, definition in METRICS.items():
        for literature_id in definition.literature_ids:
            assert literature_id in registry, f"{metric_id} cites missing row {literature_id!r}"


def test_traceability_has_no_errors() -> None:
    issues = check_traceability(load_catalogue(), METRICS, load_literature())
    errors = [issue.as_dict() for issue in issues if issue.severity == "error"]
    assert not errors, errors


def test_verified_column_is_yes_or_no() -> None:
    for row_id, row in load_literature().items():
        assert row.verified.lower() in {"yes", "no"}, (row_id, row.verified)


def test_every_unverified_row_is_documented_in_verify_me() -> None:
    text = VERIFY_ME.read_text(encoding="utf-8")
    assert M5_HEADING in text, f"VERIFY_ME.md is missing the section {M5_HEADING!r}"
    section = text.split(M5_HEADING, 1)[1]
    unverified = [
        row_id for row_id, row in load_literature().items() if row.verified.lower() == "no"
    ]
    for row_id in unverified:
        assert row_id in section, (
            f"{row_id} is verified=no but is not named in the {M5_HEADING!r} section of VERIFY_ME.md"
        )


@pytest.mark.parametrize("row_id", sorted(M5_IDS))
def test_m5_row_is_nonempty_metadata(row_id: str) -> None:
    row = load_literature()[row_id]
    assert row.title.strip()
    assert row.year.strip()
    assert row.task.strip()
    assert row.reference_standard.strip()
    assert row.metric_proposed.strip()
