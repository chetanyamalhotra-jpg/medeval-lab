"""Load and validate the versioned criterion catalogue.

The catalogue is data, not code: every construct, anchor and citation lives in
YAML so a reviewer can diff versions. Validation is strict because a malformed
criterion silently changes what a score means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from medeval_core.paths import criteria_dir, rubrics_dir

__all__ = [
    "Anchor",
    "Criterion",
    "CriterionCatalogue",
    "CriterionError",
    "Rubric",
    "catalogue_path",
    "get_criterion",
    "load_catalogue",
    "load_rubric",
]

VALID_REFERENCE_STANDARDS = {"reference-label", "judge-reliability"}
SAFETY_PHRASE = "literature-derived, not clinician-validated"


class CriterionError(ValueError):
    """Raised when the catalogue or rubric is malformed."""


@dataclass(frozen=True)
class Anchor:
    """One point on an anchored scoring scale."""

    score: int
    label: str
    description: str


@dataclass(frozen=True)
class Criterion:
    """A single evaluation construct."""

    id: str
    name: str
    version: str
    safety_related: bool
    definition: str
    anchors: tuple[Anchor, ...]
    failure_examples: tuple[str, ...]
    applicable_task_types: tuple[str, ...]
    literature_ids: tuple[str, ...]
    limitations: str
    reference_standard: str

    @property
    def max_score(self) -> int:
        return max(anchor.score for anchor in self.anchors)

    def anchor_map(self) -> dict[int, str]:
        return {anchor.score: anchor.label for anchor in self.anchors}

    def render_scale(self) -> str:
        """Markdown-ish rendering of the anchors, used in judge prompts."""
        return "\n".join(
            f"{anchor.score} = {anchor.label}: {anchor.description}" for anchor in self.anchors
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "safety_related": self.safety_related,
            "definition": self.definition,
            "anchors": [anchor.__dict__ for anchor in self.anchors],
            "failure_examples": list(self.failure_examples),
            "applicable_task_types": list(self.applicable_task_types),
            "literature_ids": list(self.literature_ids),
            "limitations": self.limitations,
            "reference_standard": self.reference_standard,
        }


@dataclass(frozen=True)
class CriterionCatalogue:
    """All criteria of one version."""

    version: str
    schema_version: str
    created: str
    review_status: str
    criteria: tuple[Criterion, ...]
    source_path: Path

    def by_id(self) -> dict[str, Criterion]:
        return {criterion.id: criterion for criterion in self.criteria}

    def get(self, criterion_id: str) -> Criterion:
        try:
            return self.by_id()[criterion_id]
        except KeyError as exc:
            raise CriterionError(
                f"unknown criterion {criterion_id!r}; known: {sorted(self.by_id())}"
            ) from exc

    def safety_criteria(self) -> list[Criterion]:
        return [criterion for criterion in self.criteria if criterion.safety_related]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema_version": self.schema_version,
            "created": self.created,
            "review_status": self.review_status,
            "source_path": str(self.source_path),
            "criteria": [criterion.as_dict() for criterion in self.criteria],
        }


@dataclass(frozen=True)
class Rubric:
    """Weights and judge instructions applied across criteria."""

    version: str
    criteria_version: str
    weights: dict[str, float]
    max_scale: float
    judge_instructions: str
    aggregation_note: str
    source_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "criteria_version": self.criteria_version,
            "weights": self.weights,
            "max_scale": self.max_scale,
            "judge_instructions": self.judge_instructions,
            "aggregation_note": self.aggregation_note,
            "source_path": str(self.source_path),
        }


def catalogue_path(version: str = "v0.1") -> Path:
    """Path of the YAML file for a catalogue version."""
    return criteria_dir() / version / "criteria.yaml"


def rubric_path(version: str = "v0.1") -> Path:
    return rubrics_dir() / f"rubric_{version}.yaml"


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, "", []):
        raise CriterionError(f"{where}: missing required field {key!r}")
    return data[key]


def _parse_anchor(raw: dict[str, Any], where: str) -> Anchor:
    if not isinstance(raw, dict):
        raise CriterionError(f"{where}: anchor must be a mapping")
    score = raw.get("score")
    if not isinstance(score, int):
        raise CriterionError(f"{where}: anchor score must be an integer")
    label = str(raw.get("label", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not label or not description:
        raise CriterionError(f"{where}: anchor {score} needs a label and a description")
    return Anchor(score=score, label=label, description=description)


def _parse_criteria(payload: dict[str, Any], path: Path) -> CriterionCatalogue:
    version = str(_require(payload, "criteria_version", str(path)))
    raw_criteria = _require(payload, "criteria", str(path))
    if not isinstance(raw_criteria, list):
        raise CriterionError(f"{path}: 'criteria' must be a list")
    criteria: list[Criterion] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_criteria):
        where = f"{path} criteria[{index}]"
        if not isinstance(raw, dict):
            raise CriterionError(f"{where}: must be a mapping")
        criterion_id = str(_require(raw, "id", where))
        if criterion_id in seen:
            raise CriterionError(f"{where}: duplicate criterion id {criterion_id!r}")
        seen.add(criterion_id)
        scale = _require(raw, "scoring_scale", where)
        anchors_raw = _require(scale, "anchors", where)
        anchors = tuple(_parse_anchor(anchor, where) for anchor in anchors_raw)
        scores = [anchor.score for anchor in anchors]
        if scores != list(range(len(scores))):
            raise CriterionError(
                f"{where}: anchors must be contiguous integers starting at 0, got {scores}"
            )
        literature_ids = tuple(str(x) for x in _require(raw, "literature_ids", where))
        if not literature_ids or any(not x.startswith("lit_") for x in literature_ids):
            raise CriterionError(f"{where}: literature_ids must be non-empty 'lit_*' identifiers")
        reference_standard = str(_require(raw, "reference_standard", where))
        if reference_standard not in VALID_REFERENCE_STANDARDS:
            raise CriterionError(
                f"{where}: reference_standard must be one of {sorted(VALID_REFERENCE_STANDARDS)}"
            )
        limitations = str(_require(raw, "limitations", where))
        safety_related = bool(raw.get("safety_related", False))
        if safety_related and SAFETY_PHRASE not in limitations.lower():
            raise CriterionError(
                f"{where}: safety-related criteria must state that they are "
                f"'{SAFETY_PHRASE}'"
            )
        criteria.append(
            Criterion(
                id=criterion_id,
                name=str(_require(raw, "name", where)),
                version=str(raw.get("version", version)),
                safety_related=safety_related,
                definition=" ".join(str(_require(raw, "definition", where)).split()),
                anchors=anchors,
                failure_examples=tuple(str(x) for x in (raw.get("failure_examples") or [])),
                applicable_task_types=tuple(
                    str(x) for x in _require(raw, "applicable_task_types", where)
                ),
                literature_ids=literature_ids,
                limitations=" ".join(limitations.split()),
                reference_standard=reference_standard,
            )
        )
    return CriterionCatalogue(
        version=version,
        schema_version=str(payload.get("schema_version", "0.1")),
        created=str(payload.get("created", "")),
        review_status=str(payload.get("review_status", "")),
        criteria=tuple(criteria),
        source_path=path,
    )


def load_catalogue(path: Path | None = None, version: str = "v0.1") -> CriterionCatalogue:
    """Load and validate a criterion catalogue."""
    source = Path(path) if path is not None else catalogue_path(version)
    if not source.is_file():
        raise FileNotFoundError(f"criterion catalogue not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise CriterionError(f"{source}: top level must be a mapping")
    return _parse_criteria(payload, source)


def get_criterion(criterion_id: str, *, version: str = "v0.1") -> Criterion:
    return load_catalogue(version=version).get(criterion_id)


def load_rubric(path: Path | None = None, version: str = "v0.1") -> Rubric:
    """Load the rubric weights and must agree with the catalogue it names."""
    source = Path(path) if path is not None else rubric_path(version)
    if not source.is_file():
        raise FileNotFoundError(f"rubric not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise CriterionError(f"{source}: top level must be a mapping")
    weights_raw = _require(payload, "weights", str(source))
    weights = {str(key): float(value) for key, value in weights_raw.items()}
    catalogue = load_catalogue(version=str(_require(payload, "criteria_version", str(source))))
    known = catalogue.by_id()
    unknown = sorted(set(weights) - set(known))
    if unknown:
        raise CriterionError(f"{source}: weights reference unknown criteria {unknown}")
    missing = sorted(set(known) - set(weights))
    if missing:
        raise CriterionError(f"{source}: no weight for criteria {missing}")
    if any(value <= 0 for value in weights.values()):
        raise CriterionError(f"{source}: weights must be positive")
    return Rubric(
        version=str(_require(payload, "rubric_version", str(source))),
        criteria_version=catalogue.version,
        weights=weights,
        max_scale=float(payload.get("max_scale", 2)),
        judge_instructions=" ".join(str(payload.get("judge_instructions", "")).split()),
        aggregation_note=" ".join(str(payload.get("aggregation_note", "")).split()),
        source_path=source,
    )
