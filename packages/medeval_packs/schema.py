"""Declarative Paper Pack schema (``papers/<pack_id>/paper.yaml``).

A Paper Pack turns a published evaluation method into data: a task type, a local
dataset adapter, prompt templates, composable graders, a statistical protocol and
citation metadata.  Nothing in this module calls a model and nothing hard-codes a
provider, model, family or price.

The schema is strict on purpose.  Unknown keys are errors rather than silently
ignored fields, because a typo in ``prompt_template`` or ``graders`` would
otherwise change a run without changing the plan card.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medeval_core.hashing import sha256_text, stable_json_dumps

__all__ = [
    "METHODOLOGY_KINDS",
    "TASK_TYPES",
    "PackValidation",
    "PaperPack",
    "SourceDataset",
    "load_pack",
    "pack_from_mapping",
]

SCHEMA_VERSION = 1
TASK_TYPES = (
    "mcq",
    "open_ended_rubric",
    "structured_extraction",
    "triage_classification",
    "multi_judge_comparison",
)
METHODOLOGY_KINDS = (
    "faithful_protocol_reproduction",
    "inspired_adaptation",
    "synthetic_pipeline_template",
)
DATASET_FORMATS = ("jsonl", "csv")
GRADER_TYPES = ("deterministic", "llm_rubric")
DETERMINISTIC_METRICS = (
    "mcq_accuracy",
    "label_accuracy",
    "multiclass_f1",
    "macro_f1",
    "macro_precision",
    "abstention",
    "coverage",
    "format_compliance",
    "json_schema",
    "field_f1",
    "field_exact_match",
    "exact_match",
    "refusal",
    "numeric_match_exact",
    "numeric_match_tolerance",
)
COMPARISON_METHODS = ("mcnemar", "paired_bootstrap", "none")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
#: Claims that this project must never make about a pack.  The report adds its own
#: "not a medical device / no clinical validation" notice regardless, but a pack
#: must not contradict it in its own metadata either.
_FORBIDDEN_CLAIMS = (
    "clinically validated",
    "clinician reviewed",
    "clinician-reviewed",
    "clinically proven",
    "medical device certified",
)


class PackValidationError(ValueError):
    """A pack cannot be used. ``errors`` holds every problem found."""

    def __init__(self, message: str, errors: Sequence[str] = ()) -> None:
        self.errors = list(errors)
        detail = message if not self.errors else f"{message}: " + "; ".join(self.errors)
        super().__init__(detail)


@dataclass(frozen=True)
class PackValidation:
    """Result of validating a pack: errors block a run, warnings do not."""

    pack_id: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        return "valid" if self.ok else "invalid"

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "status": self.status,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SourceDataset:
    """Where the *real* dataset lives, without bundling it in git.

    A built-in preset must say exactly where a user can obtain the upstream data,
    what its licence is, and that the repository ships only synthetic smoke rows.
    """

    name: str = ""
    url: str = ""
    huggingface: str = ""
    license: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "huggingface": self.huggingface,
            "license": self.license,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class DatasetSpec:
    path: str
    format: str = "jsonl"
    fields: Mapping[str, str] = field(default_factory=dict)
    seed: int = 20260101
    limit: int | None = None
    screening_size: int = 0
    split_field: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "fields": dict(self.fields),
            "seed": self.seed,
            "limit": self.limit,
            "screening_size": self.screening_size,
            "split_field": self.split_field,
        }


@dataclass(frozen=True)
class PromptTemplate:
    system: str
    user: str
    variables: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"system": self.system, "user": self.user, "variables": list(self.variables)}


@dataclass(frozen=True)
class GraderSpec:
    id: str
    type: str
    metric: str = ""
    prompt: str = ""
    scale_min: float = 1.0
    scale_max: float = 5.0
    options: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.type == "deterministic":
            payload["metric"] = self.metric
            payload["options"] = dict(self.options)
        else:
            payload.update({
                "prompt": self.prompt,
                "scale_min": self.scale_min,
                "scale_max": self.scale_max,
            })
        return payload


@dataclass(frozen=True)
class ComparisonSpec:
    method: str = "none"
    alpha: float = 0.05
    bootstrap_resamples: int = 2000
    bootstrap_seed: int = 20260101

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "alpha": self.alpha,
            "bootstrap_resamples": self.bootstrap_resamples,
            "bootstrap_seed": self.bootstrap_seed,
        }


@dataclass(frozen=True)
class MetricsSpec:
    primary: str
    secondary: tuple[str, ...] = ()
    comparison: ComparisonSpec = field(default_factory=ComparisonSpec)

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "secondary": list(self.secondary),
            "comparison": self.comparison.as_dict(),
        }


@dataclass(frozen=True)
class MethodologyNotes:
    kind: str
    summary: str
    limitations: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary, "limitations": self.limitations}


@dataclass(frozen=True)
class PaperPack:
    """One validated Paper Pack, including its on-disk location."""

    schema_version: int
    id: str
    version: str
    title: str
    authors: tuple[str, ...]
    year: int
    venue: str
    doi_or_url: str
    license: str
    methodology: MethodologyNotes
    task_type: str
    dataset: DatasetSpec
    prompt_template: PromptTemplate
    graders: tuple[GraderSpec, ...]
    metrics: MetricsSpec
    citation: Mapping[str, Any]
    source_dataset: SourceDataset = field(default_factory=SourceDataset)
    adaptation_status: str = ""
    loader: str = ""
    preset: bool = False
    path: Path | None = None
    #: Raw mapping, so the manifest can record exactly what was declared.
    raw: Mapping[str, Any] = field(default_factory=dict)

    # -- provenance ------------------------------------------------------ #
    def content_hash(self, *, pack_dir: Path | None = None) -> str:
        """Stable hash of the declaration plus the dataset file's bytes.

        Two runs with the same hash used the same protocol text, prompt
        templates and dataset content.  The pack directory is not part of the
        hash, so a pack keeps its identity when the repository moves.
        """
        base = pack_dir or self.path
        payload = dict(self.raw)
        payload.pop("_path", None)
        digest = stable_json_dumps(payload)
        dataset_digest = ""
        if base is not None:
            dataset_path = (base / self.dataset.path).resolve()
            if dataset_path.is_file():
                dataset_digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        return sha256_text(digest + "|" + dataset_digest)

    def prompt_hash(self) -> str:
        return sha256_text(stable_json_dumps(self.prompt_template.as_dict()))

    def dataset_file(self) -> Path:
        if self.path is None:
            raise PackValidationError(f"pack {self.id!r} has no on-disk path")
        candidate = (self.path / self.dataset.path).resolve()
        try:
            candidate.relative_to(self.path.resolve())
        except ValueError as exc:
            raise PackValidationError(
                f"dataset path {self.dataset.path!r} escapes the pack directory; packs must be self-contained"
            ) from exc
        return candidate

    def summary(self, *, dataset_count: int | None = None) -> dict[str, Any]:
        """Small JSON-safe view for the registry and the UI."""
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "venue": self.venue,
            "doi_or_url": self.doi_or_url,
            "license": self.license,
            "methodology": self.methodology.as_dict(),
            "task_type": self.task_type,
            "dataset": {
                **self.dataset.as_dict(),
                "count": dataset_count,
            },
            "graders": [grader.as_dict() for grader in self.graders],
            "metrics": self.metrics.as_dict(),
            "citation": dict(self.citation),
            "source_dataset": self.source_dataset.as_dict(),
            "adaptation_status": self.adaptation_status,
            "loader": self.loader,
            "preset": self.preset,
            "pack_hash": self.content_hash(),
            "prompt_hash": self.prompt_hash(),
            "path": str(self.path) if self.path is not None else None,
        }

    def validate(self) -> PackValidation:
        errors: list[str] = list(self.raw.get("_schema_errors") or ())
        warnings: list[str] = list(self.raw.get("_schema_warnings") or ())
        _validate_common(self, errors, warnings)
        # Keep first-seen order while removing duplicates: a schema error that
        # also surfaces in the assembled-object check is one problem, not two.
        return PackValidation(
            self.id,
            tuple(dict.fromkeys(errors)),
            tuple(dict.fromkeys(warnings)),
        )


def _require(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    errors: list[str],
    *,
    default: Any = None,
) -> Any:
    value = mapping.get(key, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{where}.{key} is required")
        return default
    return value


def _check_keys(mapping: Mapping[str, Any], allowed: Sequence[str], where: str, errors: list[str]) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        errors.append(f"{where} has unknown key(s): {', '.join(unknown)}")


def _as_mapping(value: Any, where: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{where} must be a mapping")
        return {}
    return value


def _as_list(value: Any, where: str, errors: list[str]) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{where} must be a list")
        return []
    return value


def pack_from_mapping(raw: Mapping[str, Any], *, path: Path | None = None) -> PaperPack:
    """Build a :class:`PaperPack` from parsed YAML, collecting schema errors.

    The returned object is always usable enough to inspect (``validate()`` reports
    what is wrong); callers that intend to run the pack must check
    ``pack.validate().ok`` first.
    """
    errors: list[str] = []
    warnings: list[str] = []
    _check_keys(
        raw,
        (
            "schema_version",
            "id",
            "version",
            "title",
            "authors",
            "year",
            "venue",
            "doi_or_url",
            "license",
            "methodology_notes",
            "task_type",
            "dataset",
            "prompt_template",
            "graders",
            "metrics",
            "citation",
            "source_dataset",
            "adaptation_status",
            "loader",
            "preset",
        ),
        "paper.yaml",
        errors,
    )

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}, got {schema_version!r}")

    pack_id = str(_require(raw, "id", "paper.yaml", errors, default=""))
    if pack_id and not _ID_RE.fullmatch(pack_id):
        errors.append("id must be lowercase letters, digits and hyphens (2-64 characters)")

    version = str(_require(raw, "version", "paper.yaml", errors, default=""))
    if version and not _VERSION_RE.fullmatch(version):
        errors.append("version must be a three-part semantic version such as 0.1.0")

    title = str(_require(raw, "title", "paper.yaml", errors, default=""))
    authors_raw = _as_list(raw.get("authors"), "authors", errors)
    authors = tuple(str(author).strip() for author in authors_raw if str(author).strip())
    if not authors:
        errors.append("authors must list at least one author or maintainer")

    year_raw = raw.get("year")
    try:
        year = int(year_raw)
    except (TypeError, ValueError):
        errors.append("year must be an integer")
        year = 0
    if year and not 1900 <= year <= 2100:
        errors.append("year must be between 1900 and 2100")

    venue = str(_require(raw, "venue", "paper.yaml", errors, default=""))
    doi_or_url = str(_require(raw, "doi_or_url", "paper.yaml", errors, default=""))
    license_name = str(_require(raw, "license", "paper.yaml", errors, default=""))

    methodology_raw = _as_mapping(raw.get("methodology_notes"), "methodology_notes", errors)
    _check_keys(methodology_raw, ("kind", "summary", "limitations"), "methodology_notes", errors)
    kind = str(methodology_raw.get("kind") or "").strip()
    if kind not in METHODOLOGY_KINDS:
        errors.append(f"methodology_notes.kind must be one of {list(METHODOLOGY_KINDS)}")
    summary = str(methodology_raw.get("summary") or "").strip()
    if not summary:
        errors.append("methodology_notes.summary is required")
    limitations = str(methodology_raw.get("limitations") or "").strip()
    combined = " ".join((title, summary, limitations)).casefold()
    for phrase in _FORBIDDEN_CLAIMS:
        for match in re.finditer(re.escape(phrase), combined):
            prefix = combined[max(0, match.start() - 16) : match.start()]
            if re.search(r"(?:not|no|never|without|n't)\s*$", prefix):
                continue
            errors.append(
                f"metadata claims {phrase!r}; this project may not claim clinical validation or clinician review"
            )
            break
    if kind == "faithful_protocol_reproduction" and "reproduction" not in summary.casefold():
        warnings.append(
            "methodology_notes.kind is faithful_protocol_reproduction; say explicitly what was reproduced "
            "and what was adapted"
        )

    task_type = str(_require(raw, "task_type", "paper.yaml", errors, default=""))
    if task_type and task_type not in TASK_TYPES:
        errors.append(f"task_type must be one of {list(TASK_TYPES)}")

    dataset_raw = _as_mapping(raw.get("dataset"), "dataset", errors)
    _check_keys(
        dataset_raw,
        ("path", "format", "fields", "seed", "limit", "screening_size", "split_field"),
        "dataset",
        errors,
    )
    dataset_path = str(_require(dataset_raw, "path", "dataset", errors, default=""))
    data_format = str(dataset_raw.get("format") or "jsonl").strip().lower()
    if data_format not in DATASET_FORMATS:
        errors.append(f"dataset.format must be one of {list(DATASET_FORMATS)}")
    fields_raw = dataset_raw.get("fields") or {}
    if not isinstance(fields_raw, Mapping):
        errors.append("dataset.fields must be a mapping of canonical field -> source column")
        fields_raw = {}
    seed_raw = dataset_raw.get("seed", 20260101)
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError):
        errors.append("dataset.seed must be an integer")
        seed = 20260101
    limit_raw = dataset_raw.get("limit")
    limit: int | None
    if limit_raw is None or limit_raw == "":
        limit = None
    else:
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            errors.append("dataset.limit must be an integer or omitted")
            limit = None
        if limit is not None and limit < 1:
            errors.append("dataset.limit must be at least 1")
    screening_raw = dataset_raw.get("screening_size", 0)
    try:
        screening_size = int(screening_raw)
    except (TypeError, ValueError):
        errors.append("dataset.screening_size must be an integer")
        screening_size = 0
    if screening_size < 0:
        errors.append("dataset.screening_size must not be negative")

    prompt_raw = _as_mapping(raw.get("prompt_template"), "prompt_template", errors)
    _check_keys(prompt_raw, ("system", "user", "variables"), "prompt_template", errors)
    system_prompt = str(_require(prompt_raw, "system", "prompt_template", errors, default=""))
    user_prompt = str(_require(prompt_raw, "user", "prompt_template", errors, default=""))
    variables = tuple(str(value) for value in _as_list(prompt_raw.get("variables"), "prompt_template.variables", errors))

    graders_raw = _as_list(raw.get("graders"), "graders", errors)
    graders: list[GraderSpec] = []
    if not graders_raw:
        errors.append("graders must contain at least one grader")
    seen_ids: set[str] = set()
    for index, item in enumerate(graders_raw, start=1):
        where = f"graders[{index}]"
        grader_raw = _as_mapping(item, where, errors)
        _check_keys(
            grader_raw,
            ("id", "type", "metric", "prompt", "scale_min", "scale_max", "options"),
            where,
            errors,
        )
        grader_id = str(grader_raw.get("id") or "").strip()
        if not grader_id:
            errors.append(f"{where}.id is required")
        elif grader_id in seen_ids:
            errors.append(f"grader id {grader_id!r} is duplicated")
        seen_ids.add(grader_id)
        grader_type = str(grader_raw.get("type") or "").strip()
        if grader_type not in GRADER_TYPES:
            errors.append(f"{where}.type must be one of {list(GRADER_TYPES)}")
        metric = str(grader_raw.get("metric") or "").strip()
        prompt = str(grader_raw.get("prompt") or "").strip()
        options_raw = grader_raw.get("options") or {}
        if not isinstance(options_raw, Mapping):
            errors.append(f"{where}.options must be a mapping")
            options_raw = {}
        if grader_type == "deterministic":
            if metric not in DETERMINISTIC_METRICS:
                errors.append(f"{where}.metric must be one of {list(DETERMINISTIC_METRICS)}")
            if prompt:
                errors.append(f"{where}.prompt is only valid for an llm_rubric grader")
        elif grader_type == "llm_rubric":
            if not prompt:
                errors.append(f"{where}.prompt is required for an llm_rubric grader")
            if metric:
                errors.append(f"{where}.metric is only valid for a deterministic grader")
            if task_type in {"open_ended_rubric", "multi_judge_comparison"} and len(graders_raw) == 1:
                warnings.append(
                    "a rubric-only pack has no deterministic format check; add a format_compliance grader "
                    "so malformed outputs are separable from low-quality ones"
                )
        scale_min_raw = grader_raw.get("scale_min", 1.0)
        scale_max_raw = grader_raw.get("scale_max", 5.0)
        try:
            scale_min = float(scale_min_raw)
            scale_max = float(scale_max_raw)
        except (TypeError, ValueError):
            errors.append(f"{where}.scale_min/scale_max must be numbers")
            scale_min, scale_max = 1.0, 5.0
        if scale_min >= scale_max:
            errors.append(f"{where}.scale_min must be below scale_max")
        graders.append(
            GraderSpec(
                id=grader_id,
                type=grader_type,
                metric=metric,
                prompt=prompt,
                scale_min=scale_min,
                scale_max=scale_max,
                options=dict(options_raw),
            )
        )

    metrics_raw = _as_mapping(raw.get("metrics"), "metrics", errors)
    _check_keys(metrics_raw, ("primary", "secondary", "comparison"), "metrics", errors)
    primary = str(_require(metrics_raw, "primary", "metrics", errors, default=""))
    secondary = tuple(str(value) for value in _as_list(metrics_raw.get("secondary"), "metrics.secondary", errors))
    comparison_raw = _as_mapping(metrics_raw.get("comparison") or {}, "metrics.comparison", errors)
    _check_keys(
        comparison_raw,
        ("method", "alpha", "bootstrap_resamples", "bootstrap_seed"),
        "metrics.comparison",
        errors,
    )
    comparison_method = str(comparison_raw.get("method") or "none").strip()
    if comparison_method not in COMPARISON_METHODS:
        errors.append(f"metrics.comparison.method must be one of {list(COMPARISON_METHODS)}")
    try:
        alpha = float(comparison_raw.get("alpha", 0.05))
    except (TypeError, ValueError):
        errors.append("metrics.comparison.alpha must be a number")
        alpha = 0.05
    if not 0 < alpha < 1:
        errors.append("metrics.comparison.alpha must be between 0 and 1")
    try:
        resamples = int(comparison_raw.get("bootstrap_resamples", 2000))
    except (TypeError, ValueError):
        errors.append("metrics.comparison.bootstrap_resamples must be an integer")
        resamples = 2000
    if resamples < 1:
        errors.append("metrics.comparison.bootstrap_resamples must be at least 1")
    try:
        bootstrap_seed = int(comparison_raw.get("bootstrap_seed", 20260101))
    except (TypeError, ValueError):
        errors.append("metrics.comparison.bootstrap_seed must be an integer")
        bootstrap_seed = 20260101

    citation_raw = raw.get("citation") or {}
    if not isinstance(citation_raw, Mapping):
        errors.append("citation must be a mapping (for example with a bibtex key)")
        citation_raw = {}
    _check_keys(citation_raw, ("bibtex", "key"), "citation", errors)
    bibtex = str(citation_raw.get("bibtex") or "").strip()
    if not bibtex:
        warnings.append("citation.bibtex is empty; the export will only carry the medeval-lab citation")
    elif not re.search(r"@\w+\s*\{", bibtex):
        errors.append("citation.bibtex must contain a BibTeX entry such as @misc{key, ...}")

    preset = bool(raw.get("preset", False))
    adaptation_status = str(raw.get("adaptation_status") or "").strip()
    loader = str(raw.get("loader") or "").strip()
    source_raw = raw.get("source_dataset") or {}
    if not isinstance(source_raw, Mapping):
        errors.append("source_dataset must be a mapping")
        source_raw = {}
    _check_keys(source_raw, ("name", "url", "huggingface", "license", "notes"), "source_dataset", errors)
    source_url = str(source_raw.get("url") or "").strip()
    source_hf = str(source_raw.get("huggingface") or "").strip()
    if source_url and not source_url.startswith(("http://", "https://")):
        errors.append("source_dataset.url must start with http:// or https://")
    if preset:
        if not source_url and not source_hf:
            errors.append("a preset must declare source_dataset.url or source_dataset.huggingface")
        if not adaptation_status:
            errors.append("a preset must declare adaptation_status (for example 'synthetic_smoke_only')")
        if not str(source_raw.get("license") or "").strip():
            errors.append("a preset must declare source_dataset.license")
        if not loader:
            warnings.append("this preset has no loader.py; users can only run its synthetic smoke dataset")
    source_dataset = SourceDataset(
        name=str(source_raw.get("name") or "").strip(),
        url=source_url,
        huggingface=source_hf,
        license=str(source_raw.get("license") or "").strip(),
        notes=str(source_raw.get("notes") or "").strip(),
    )

    pack = PaperPack(
        schema_version=SCHEMA_VERSION,
        id=pack_id,
        version=version,
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi_or_url=doi_or_url,
        license=license_name,
        methodology=MethodologyNotes(kind=kind, summary=summary, limitations=limitations),
        task_type=task_type,
        dataset=DatasetSpec(
            path=dataset_path,
            format=data_format,
            fields={str(key): str(value) for key, value in fields_raw.items()},
            seed=seed,
            limit=limit,
            screening_size=screening_size,
            split_field=str(dataset_raw.get("split_field") or ""),
        ),
        prompt_template=PromptTemplate(system=system_prompt, user=user_prompt, variables=variables),
        graders=tuple(graders),
        metrics=MetricsSpec(
            primary=primary,
            secondary=secondary,
            comparison=ComparisonSpec(
                method=comparison_method,
                alpha=alpha,
                bootstrap_resamples=resamples,
                bootstrap_seed=bootstrap_seed,
            ),
        ),
        citation=dict(citation_raw),
        source_dataset=source_dataset,
        adaptation_status=adaptation_status,
        loader=loader,
        preset=preset,
        path=path,
        raw=dict(raw),
    )
    # Extra checks that need the assembled object.
    _validate_common(pack, errors, warnings)
    if errors:
        # Keep the object usable for inspection; attach the schema errors so
        # load_pack can raise with every one of them.
        pack = PaperPack(**{**pack.__dict__, "raw": {**pack.raw, "_schema_errors": list(errors),
                                                     "_schema_warnings": list(warnings)}})
    return pack


def _validate_common(pack: PaperPack, errors: list[str], warnings: list[str]) -> None:
    """Checks that apply to a parsed pack and to the public ``validate()``."""
    if pack.path is not None:
        dataset_path = (pack.path / pack.dataset.path).resolve()
        try:
            dataset_path.relative_to(pack.path.resolve())
        except ValueError:
            errors.append("dataset.path must stay inside the pack directory")
        if not dataset_path.is_file():
            errors.append(f"dataset file not found: {pack.dataset.path}")
        elif dataset_path.stat().st_size == 0:
            errors.append(f"dataset file is empty: {pack.dataset.path}")
        if pack.loader:
            loader_path = (pack.path / pack.loader).resolve()
            try:
                loader_path.relative_to(pack.path.resolve())
            except ValueError:
                errors.append("loader must stay inside the pack directory")
            else:
                if not loader_path.is_file():
                    errors.append(f"declared loader file not found: {pack.loader}")
                elif loader_path.suffix != ".py":
                    errors.append("loader must be a .py file")

    if pack.metrics.primary not in DETERMINISTIC_METRICS and pack.metrics.primary not in {
        "accuracy",
        "rubric_score",
        "mean_rubric_score",
        "judge_score",
    }:
        warnings.append(
            f"metrics.primary {pack.metrics.primary!r} is not a built-in metric; the report will use the "
            "closest deterministic metric it can find"
        )

    if pack.dataset.screening_size and pack.dataset.limit and pack.dataset.screening_size > pack.dataset.limit:
        warnings.append("dataset.screening_size is larger than dataset.limit; the screening stage will use every item")

    if pack.task_type == "structured_extraction" and not any(
        grader.metric in {"json_schema", "field_f1", "field_exact_match"} for grader in pack.graders
    ):
        warnings.append("a structured_extraction pack should declare a json_schema or field_f1 grader")

    if pack.task_type == "mcq" and not any(grader.metric == "mcq_accuracy" for grader in pack.graders):
        warnings.append("an mcq pack without an mcq_accuracy grader has no accuracy metric")


def load_pack(path: Path | str) -> PaperPack:
    """Load and validate one pack directory or ``paper.yaml`` file.

    Raises :class:`PackValidationError` with every schema problem, so a user can
    fix the file in one pass instead of one error at a time.
    """
    target = Path(path)
    pack_dir = target if target.is_dir() else target.parent
    yaml_path = pack_dir / "paper.yaml" if target.is_dir() else target
    if not yaml_path.is_file():
        raise PackValidationError(f"no paper.yaml at {yaml_path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a dependency
        raise PackValidationError("PyYAML is required to read a Paper Pack") from exc
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackValidationError(f"could not read {yaml_path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise PackValidationError(f"{yaml_path} must contain a mapping")
    pack = pack_from_mapping(raw, path=pack_dir)
    schema_errors = list(pack.raw.get("_schema_errors") or ())
    validation = pack.validate()
    all_errors = list(dict.fromkeys([*schema_errors, *validation.errors]))
    if all_errors:
        raise PackValidationError(f"pack {pack.id or yaml_path.name} is invalid", all_errors)
    return pack


def pack_json(pack: PaperPack) -> str:
    """Canonical JSON for hashing or the UI (never contains a key)."""
    return json.dumps(pack.summary(), sort_keys=True, ensure_ascii=False)
