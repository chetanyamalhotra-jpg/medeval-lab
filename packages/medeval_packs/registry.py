"""Paper Pack discovery, validation and scaffolding.

The registry has two views:

* **runnable packs** under ``papers/<pack_id>/paper.yaml`` — validated, with a
  dataset count and a content hash;
* **archived studies** under ``results/study_runs/`` — Experiment 002/003 runs
  that predate Paper Packs.  They are exposed read-only so the Paper Registry can
  show them beside packs without pretending they are re-runnable.

Nothing here writes outside a caller-supplied destination during ``init``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medeval_core.paths import repo_root, results_dir

from .dataset import load_items
from .schema import (
    PackValidation,
    PackValidationError,
    PaperPack,
    load_pack,
)

__all__ = [
    "PackEntry",
    "archived_studies",
    "init_pack",
    "list_packs",
    "papers_dir",
    "resolve_pack",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def papers_dir(root: Path | None = None) -> Path:
    return root or (repo_root() / "papers")


@dataclass(frozen=True)
class PackEntry:
    """One row in the Paper Registry."""

    id: str
    kind: str  # "paper_pack" | "archived_study"
    title: str
    task_type: str
    version: str
    year: int | None
    venue: str
    doi_or_url: str
    license: str
    methodology_kind: str
    dataset_count: int | None
    validation: PackValidation
    pack_hash: str
    path: str
    runnable: bool
    graders: tuple[str, ...] = ()
    citation: dict[str, Any] = field(default_factory=dict)
    preset: bool = False
    source_dataset: dict[str, Any] = field(default_factory=dict)
    adaptation_status: str = ""
    loader: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "task_type": self.task_type,
            "version": self.version,
            "year": self.year,
            "venue": self.venue,
            "doi_or_url": self.doi_or_url,
            "license": self.license,
            "methodology_kind": self.methodology_kind,
            "dataset_count": self.dataset_count,
            "validation": self.validation.as_dict(),
            "pack_hash": self.pack_hash,
            "path": self.path,
            "runnable": self.runnable,
            "graders": list(self.graders),
            "citation": dict(self.citation),
            "preset": self.preset,
            "source_dataset": dict(self.source_dataset),
            "adaptation_status": self.adaptation_status,
            "loader": self.loader,
        }


def _entry_for_pack(pack: PaperPack) -> PackEntry:
    validation = pack.validate()
    count: int | None
    try:
        count = len(load_items(pack))
    except (PackValidationError, OSError, ValueError) as exc:
        count = None
        if validation.ok:
            validation = PackValidation(pack.id, (f"dataset could not be loaded: {exc}",))
    return PackEntry(
        id=pack.id,
        kind="paper_pack",
        title=pack.title,
        task_type=pack.task_type,
        version=pack.version,
        year=pack.year,
        venue=pack.venue,
        doi_or_url=pack.doi_or_url,
        license=pack.license,
        methodology_kind=pack.methodology.kind,
        dataset_count=count,
        validation=validation,
        pack_hash=pack.content_hash() if pack.path is not None else "",
        path=str(pack.path or ""),
        runnable=validation.ok,
        graders=tuple(grader.id for grader in pack.graders),
        citation=dict(pack.citation),
        preset=pack.preset,
        source_dataset=pack.source_dataset.as_dict(),
        adaptation_status=pack.adaptation_status,
        loader=pack.loader,
    )


def list_packs(root: Path | None = None) -> list[PackEntry]:
    """Every pack directory, including invalid ones (with their errors)."""
    base = papers_dir(root)
    entries: list[PackEntry] = []
    if not base.is_dir():
        return entries
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        yaml_path = directory / "paper.yaml"
        if not yaml_path.is_file():
            continue
        try:
            pack = load_pack(directory)
        except PackValidationError as exc:
            entries.append(
                PackEntry(
                    id=directory.name,
                    kind="paper_pack",
                    title=directory.name,
                    task_type="unknown",
                    version="",
                    year=None,
                    venue="",
                    doi_or_url="",
                    license="",
                    methodology_kind="",
                    dataset_count=None,
                    validation=PackValidation(directory.name, tuple(exc.errors) or (str(exc),)),
                    pack_hash="",
                    path=str(directory),
                    runnable=False,
                )
            )
            continue
        entries.append(_entry_for_pack(pack))
    return entries


def archived_studies(root: Path | None = None) -> list[PackEntry]:
    """Read-only metadata for pre-v0.6 study runs (Experiment 002/003)."""
    base = (root or results_dir()) / "study_runs"
    entries: list[PackEntry] = []
    if not base.is_dir():
        return entries
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        run_json = directory / "run.json"
        if not run_json.is_file():
            continue
        try:
            payload = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        experiment = str(payload.get("experiment") or "")
        if not (experiment.startswith("002") or experiment.startswith("003")):
            continue
        config = payload.get("config") or {}
        run_id = str(payload.get("run_id") or directory.name)
        entries.append(
            PackEntry(
                id=f"archive:{run_id}",
                kind="archived_study",
                title=f"{run_id} — {experiment}",
                task_type="mcq" if experiment.startswith("002") else "mcq_staged",
                version=str(payload.get("package_version") or "archived"),
                year=None,
                venue="Archived local study run (read-only)",
                doi_or_url="",
                license="see results/study_runs/<run>/manifest.json",
                methodology_kind="archived_study",
                dataset_count=config.get("n_items"),
                validation=PackValidation(f"archive:{run_id}", (), ("archived: read-only, not re-runnable as a pack",)),
                pack_hash="",
                path=str(directory),
                runnable=False,
                graders=(),
                citation={},
            )
        )
    return entries


def resolve_pack(identifier: str | Path, root: Path | None = None) -> PaperPack:
    """Resolve a pack id, directory or ``paper.yaml`` path."""
    candidate = Path(identifier)
    if candidate.exists():
        return load_pack(candidate)
    base = papers_dir(root) / str(identifier)
    if (base / "paper.yaml").is_file():
        return load_pack(base)
    available = [entry.id for entry in list_packs(root)]
    raise PackValidationError(
        f"unknown Paper Pack {identifier!r}; available: {', '.join(available) or '(none)'}"
    )


def init_pack(
    slug: str,
    task_type: str,
    destination: Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Create a documented starter pack. Returns its directory.

    The generated pack is a *template*: synthetic two-row data, a strict prompt,
    one deterministic grader (plus a rubric judge for rubric tasks) and a clear
    TODO list.  It is meant to be filled with a real protocol and dataset by the
    person who owns the paper, not run as evidence.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise PackValidationError("slug must be lowercase letters, digits and hyphens (2-64 characters)")
    if task_type not in {
        "mcq",
        "open_ended_rubric",
        "structured_extraction",
        "triage_classification",
        "multi_judge_comparison",
    }:
        raise PackValidationError(f"unknown task type {task_type!r}")
    base = destination or (papers_dir(root) / slug)
    if base.exists() and any(base.iterdir()):
        raise PackValidationError(f"destination already exists and is not empty: {base}")
    (base / "data").mkdir(parents=True, exist_ok=True)
    pack_dir = base
    yaml_text = _starter_yaml(slug, task_type)
    (pack_dir / "paper.yaml").write_text(yaml_text, encoding="utf-8")
    rows, columns = _starter_rows(task_type)
    dataset_path = pack_dir / "data" / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (pack_dir / "README.md").write_text(
        f"# {slug}\n\n"
        "PIPELINE TEST / TEMPLATE DATA. This pack was generated by `medeval paper init`.\n\n"
        "## Fill this in before you run it\n\n"
        "1. Replace the title, authors, year, venue, DOI/URL and licence with the paper's real metadata.\n"
        "2. Set `methodology_notes.kind` to `faithful_protocol_reproduction` or `inspired_adaptation`\n"
        "   and describe what you kept and what you changed.\n"
        "3. Replace `data/dataset.jsonl` with your own licensed dataset and keep `dataset.fields` accurate.\n"
        "   Do not commit text you do not have the right to redistribute.\n"
        "4. Review `prompt_template` so the target model never sees the answer key.\n"
        "5. Check the graders and the statistical protocol against the paper.\n\n"
        "Run `medeval paper validate "
        f"{slug}` then `medeval matrix run --paper {slug} --dry-run`.\n\n"
        "This template is not clinically validated; it is a research pipeline scaffold.\n"
        f"Dataset columns used: {', '.join(columns)}.\n",
        encoding="utf-8",
    )
    return pack_dir


def _starter_rows(task_type: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    if task_type == "mcq":
        rows = [
            {
                "id": "syn-001",
                "question": "Synthetic vignette: a person reports a symptom and a finding is described. "
                "Which option is the best next step? (TEMPLATE DATA)",
                "options": {"A": "Option A (synthetic)", "B": "Option B (synthetic)", "C": "Option C (synthetic)", "D": "Option D (synthetic)"},
                "answer": "A",
                "split": "template",
            },
            {
                "id": "syn-002",
                "question": "Synthetic vignette: a second scenario with the same structure. "
                "Which option is the best next step? (TEMPLATE DATA)",
                "options": {"A": "Option A (synthetic)", "B": "Option B (synthetic)", "C": "Option C (synthetic)", "D": "Option D (synthetic)"},
                "answer": "C",
                "split": "template",
            },
        ]
        return rows, ("id", "question", "options", "answer")
    if task_type in {"open_ended_rubric", "multi_judge_comparison"}:
        rows = [
            {
                "id": "syn-001",
                "question": "Synthetic open-ended question about a described finding. (TEMPLATE DATA)",
                "reference_answer": "A short reference answer written for the template.",
                "rubric": ["mentions the key finding", "gives a justified next step"],
                "split": "template",
            },
            {
                "id": "syn-002",
                "question": "Synthetic open-ended question about a second finding. (TEMPLATE DATA)",
                "reference_answer": "Another short reference answer for the template.",
                "rubric": ["mentions the key finding", "states a limitation"],
                "split": "template",
            },
        ]
        return rows, ("id", "question", "reference_answer", "rubric")
    if task_type == "structured_extraction":
        rows = [
            {
                "id": "syn-001",
                "abstract": "Synthetic abstract-style input. (TEMPLATE DATA)",
                "gold": {"finding": "synthetic finding", "population": "synthetic population"},
                "split": "template",
            },
            {
                "id": "syn-002",
                "abstract": "A second synthetic abstract-style input. (TEMPLATE DATA)",
                "gold": {"finding": "another finding", "population": "another population"},
                "split": "template",
            },
        ]
        return rows, ("id", "abstract", "gold")
    rows = [
        {"id": "syn-001", "input": "Synthetic triage input. (TEMPLATE DATA)", "label": "urgent", "split": "template"},
        {"id": "syn-002", "input": "A second synthetic triage input. (TEMPLATE DATA)", "label": "routine", "split": "template"},
    ]
    return rows, ("id", "input", "label")


def _starter_yaml(slug: str, task_type: str) -> str:
    head = f"""# Paper Pack scaffold generated by `medeval paper init`.
# PIPELINE TEST / TEMPLATE DATA: replace every TODO before treating a run as evidence.
schema_version: 1
id: {slug}
version: 0.1.0
title: "TODO: the paper's title (TEMPLATE)"
authors: ["TODO: author or maintainer"]
year: {2026}
venue: "TODO: venue or 'unpublished'"
doi_or_url: "TODO: DOI or URL"
license: "TODO: dataset/code licence (for example CC-BY-4.0)"
methodology_notes:
  kind: inspired_adaptation   # faithful_protocol_reproduction | inspired_adaptation | synthetic_pipeline_template
  summary: "TODO: what this pack reproduces and what it changes. This is a research pipeline, not a medical device."
  limitations: "TODO: sample size, dataset licensing, construct validity, absence of clinical validation."
task_type: {task_type}
dataset:
  path: data/dataset.jsonl
  format: jsonl
  seed: 20260101
  limit: 2
  screening_size: 0
  fields: {{}}
prompt_template:
  system: |
    You are answering a research evaluation prompt. Do not give patient-specific advice.
  user: |
"""
    if task_type == "mcq":
        body = """    Question: $question

    Options:
    $options_block

    Answer with the single best option letter and one sentence of reasoning.
"""
        graders = """graders:
  - id: accuracy
    type: deterministic
    metric: mcq_accuracy
  - id: abstention
    type: deterministic
    metric: abstention
  - id: format
    type: deterministic
    metric: format_compliance
metrics:
  primary: accuracy
  secondary: [abstention, format_compliance]
  comparison:
    method: mcnemar
    alpha: 0.05
"""
    elif task_type == "structured_extraction":
        body = """    Input:
    $input

    Return one JSON object matching the requested fields. Do not add prose outside the JSON.
"""
        graders = """graders:
  - id: schema
    type: deterministic
    metric: json_schema
    options:
      schema:
        type: object
        properties:
          fields:
            type: object
        required: [fields]
  - id: fields
    type: deterministic
    metric: field_f1
metrics:
  primary: field_f1
  secondary: [json_schema, format_compliance]
  comparison:
    method: paired_bootstrap
    alpha: 0.05
"""
    elif task_type == "triage_classification":
        body = """    Input:
    $input

    Choose exactly one category from the study's label set and give one sentence of reasoning.
"""
        graders = """graders:
  - id: accuracy
    type: deterministic
    metric: label_accuracy
  - id: abstention
    type: deterministic
    metric: abstention
metrics:
  primary: accuracy
  secondary: [abstention]
  comparison:
    method: mcnemar
    alpha: 0.05
"""
    else:
        body = """    Question:
    $question

    Reference answer (for the judge only; answer independently):
    $reference_answer

    Give a concise answer, then address each rubric point.
"""
        graders = """graders:
  - id: format
    type: deterministic
    metric: format_compliance
  - id: panel
    type: llm_rubric
    prompt: |
      You are one judge on a cross-family panel evaluating a research answer.
      Question: $question
      Reference answer: $reference_answer
      Candidate answer: $target_answer
      Rubric: $rubric
      Return JSON only: {"score": <number between $scale_min and $scale_max>, "rationale": "<one sentence>"}
    scale_min: 1
    scale_max: 5
metrics:
  primary: mean_rubric_score
  secondary: [format_compliance]
  comparison:
    method: paired_bootstrap
    alpha: 0.05
"""
    tail = """citation:
  bibtex: |
    @misc{__KEY__,
      title = {TODO: title},
      author = {TODO: authors},
      year = {2026},
      note = {Paper Pack scaffold; replace with the paper's real citation}
    }
"""
    return head + body + graders + tail.replace("__KEY__", slug.replace("-", "_"))
