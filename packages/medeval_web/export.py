"""Export a run as a shareable zip without item text, notes or secrets.

What goes in (per the specification): ``metrics.json``, ``items.csv`` (ids and
verdicts only), ``review_queue.csv`` (ids only), ``manifest.json``, ``report.md``.
Reviewer notes are excluded unless the owner explicitly asks for them, and the
bundle then carries a README warning that notes may quote item text.

Every file is scanned before it is added: a key-shaped string or a benchmark
question aborts the export rather than shipping it. The zip lands in a
git-ignored ``results/exports/`` directory.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from medeval_core.paths import results_dir
from medeval_study.registry import run_root

__all__ = ["EXPORT_FILES", "UnsafExport", "build_export_bundle"]

#: Files copied verbatim, in this order. Anything else is not exported.
EXPORT_FILES = ("metrics.json", "items.csv", "review_queue.csv", "manifest.json", "report.md")

_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")
#: A benchmark question or candidate answer is long prose in one of these fields.
#: Short metadata values (``"prompt_version": "v1"``) are fine; a long string is
#: treated as text that must not leave the run.
_TEXT_FIELD = re.compile(
    r'"(question|options|key_text|candidate_text|response_text|prompt)"\s*:\s*"([^"]{80,})"'
)


class UnsafExport(RuntimeError):
    """The bundle would have contained something that must not leave the run."""


def _sanitised_copy(name: str, text: str, *, allow_notes: bool) -> str:
    if _KEY_LIKE.search(text):
        raise UnsafExport(f"{name} contains a key-shaped string; refusing to export it")
    for match in _TEXT_FIELD.finditer(text):
        field = match.group(1).lower()
        raise UnsafExport(
            f"{name} appears to contain benchmark or prompt text (field {field!r}); refusing to export"
        )
    return text


def build_export_bundle(
    run_id: str,
    *,
    include_notes: bool = False,
    review_notes_path: Path | None = None,
    root: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Create the zip and return its path."""
    base = (root or run_root()) / run_id
    if not base.is_dir():
        raise FileNotFoundError(f"unknown run {run_id!r}")
    target_dir = out_dir or (results_dir() / "exports")
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"{run_id}-bundle.zip"

    entries: dict[str, str] = {}
    for name in EXPORT_FILES:
        path = base / name
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        entries[name] = _sanitised_copy(name, raw, allow_notes=include_notes)
    if not entries:
        raise FileNotFoundError(f"run {run_id!r} has no exportable files yet (no metrics/report)")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
        archive.writestr(
            "README.txt",
            (
                f"medeval-lab export for run {run_id}\n"
                "Contents: metrics.json, items.csv (item ids and verdicts only), review_queue.csv (item ids\n"
                "only), manifest.json, report.md.\n"
                "It contains no benchmark item text, no candidate text, no prompts and no API keys.\n"
                + (
                    "Reviewer notes are included because the owner asked for them: notes are one "
                    "reviewer's opinion and may quote item text.\n"
                    if include_notes
                    else "Reviewer notes are NOT included.\n"
                )
            ),
        )
        if include_notes:
            notes = _notes_text(run_id, review_notes_path)
            if notes:
                archive.writestr("review_notes.json", notes)
    return zip_path


def _notes_text(run_id: str, path: Path | None) -> str:
    from medeval_web.review import ReviewStore

    store = ReviewStore(path) if path is not None else ReviewStore()
    payload: dict[str, Any] = store.for_run(run_id)
    if not payload:
        return ""
    return store.as_json(payload)
