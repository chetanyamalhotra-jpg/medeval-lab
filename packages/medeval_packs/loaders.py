"""On-demand dataset loaders for built-in preset Paper Packs.

The repository never bundles an upstream medical dataset.  A preset ships only a
small synthetic ``smoke.jsonl``; this module is the explicit, opt-in path that a
user runs to materialise a licensed dataset into the git-ignored
``.medeval_data/<pack_id>/dataset.jsonl``.

Design rules:

* no network access unless ``--download`` is passed **and** the licence is
  acknowledged with ``--accept-license``;
* every row is verified against the pack's task schema before anything is
  written; a bad row aborts the whole load rather than producing a half dataset;
* the destination is always under ``.medeval_data/`` and never inside the pack;
* this module never reads or writes an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from medeval_core.paths import repo_root

from .dataset import normalise_item, read_rows
from .schema import PackValidationError, PaperPack, load_pack

__all__ = ["DATA_DIRNAME", "data_dir", "main", "verify_and_write"]

DATA_DIRNAME = ".medeval_data"


def data_dir(pack_id: str, *, root: Path | None = None) -> Path:
    """The git-ignored destination for one pack's on-demand dataset."""
    return (root or repo_root()) / DATA_DIRNAME / pack_id


def _detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson", ".json"}:
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    raise PackValidationError(f"cannot infer dataset format from {path.name!r}; use .jsonl or .csv")


def verify_and_write(
    pack: PaperPack,
    rows: list[dict[str, Any]],
    *,
    out_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Verify every row, then write a JSONL dataset under ``.medeval_data``."""
    if not rows:
        raise PackValidationError("the source contains no rows")
    items = []
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            items.append(
                normalise_item(
                    row,
                    task_type=pack.task_type,
                    fields=pack.dataset.fields,
                    row_number=index,
                    source="source dataset",
                )
            )
        except PackValidationError as exc:
            problems.append(str(exc))
    if problems:
        preview = "\n  ".join(problems[:10])
        suffix = f"\n  ... and {len(problems) - 10} more" if len(problems) > 10 else ""
        raise PackValidationError(
            f"{len(problems)} row(s) failed schema verification:\n  {preview}{suffix}"
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item.item_id in seen:
            duplicates.append(item.item_id)
        seen.add(item.item_id)
    if duplicates:
        raise PackValidationError(f"duplicate item id(s): {', '.join(sorted(set(duplicates))[:10])}")
    destination_dir = out_dir or data_dir(pack.id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / "dataset.jsonl"
    if destination.exists() and not force:
        raise PackValidationError(
            f"{destination} already exists; pass --force to replace it"
        )
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return destination


def _download(pack: PaperPack, *, split: str, config: str | None, limit: int | None) -> list[dict[str, Any]]:
    if not pack.source_dataset.huggingface:
        raise PackValidationError(
            "this pack has no source_dataset.huggingface reference; use --source with a local file"
        )
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise PackValidationError(
            "the optional 'datasets' package is not installed. Install it with "
            "'python -m pip install datasets', or use --source with a local file."
        ) from exc
    dataset = load_dataset(pack.source_dataset.huggingface, config, split=split)
    if limit is not None:
        dataset = dataset.select(range(min(limit, len(dataset))))
    rows = [dict(row) for row in dataset]
    return rows


def _check(pack: PaperPack) -> int:
    destination = data_dir(pack.id) / "dataset.jsonl"
    if not destination.is_file():
        print(f"[loader] no downloaded dataset at {destination}")
        print("[loader] run with --download (after accepting the licence) or --source <file>")
        return 1
    rows = read_rows(destination, "jsonl")
    for index, row in enumerate(rows, start=1):
        normalise_item(
            row,
            task_type=pack.task_type,
            fields=pack.dataset.fields,
            row_number=index,
            source=destination.name,
        )
    print(f"[loader] OK: {len(rows)} row(s) in {destination}")
    return 0


def main(pack_dir: Path, argv: list[str] | None = None) -> int:
    """CLI entry point shared by every preset's thin ``loader.py`` wrapper."""
    parser = argparse.ArgumentParser(
        description="On-demand loader for a medeval-lab preset dataset (offline unless --download)."
    )
    parser.add_argument("--source", type=Path, help="local JSONL/CSV file to verify and copy")
    parser.add_argument("--download", action="store_true", help="download the declared Hugging Face dataset")
    parser.add_argument("--split", default="test", help="Hugging Face split for --download (default: test)")
    parser.add_argument("--config", default=None, help="optional Hugging Face config name")
    parser.add_argument("--limit", type=int, default=None, help="maximum rows to load")
    parser.add_argument("--out", type=Path, default=None, help="destination directory (default: .medeval_data/<id>)")
    parser.add_argument("--force", action="store_true", help="replace an existing destination file")
    parser.add_argument("--check", action="store_true", help="verify the already-downloaded dataset")
    parser.add_argument(
        "--accept-license",
        action="store_true",
        help="acknowledge the upstream licence before downloading; required with --download",
    )
    args = parser.parse_args(argv)

    try:
        pack = load_pack(pack_dir)
    except PackValidationError as exc:
        print(f"[loader] pack invalid: {exc}", file=sys.stderr)
        return 2
    print(f"[loader] pack: {pack.id} ({pack.task_type})")
    print(f"[loader] upstream: {pack.source_dataset.name or 'not named'} | {pack.source_dataset.license or 'licence not declared'}")
    if pack.source_dataset.url:
        print(f"[loader] source URL: {pack.source_dataset.url}")
    if pack.source_dataset.huggingface:
        print(f"[loader] Hugging Face: {pack.source_dataset.huggingface}")
    print(f"[loader] adaptation_status: {pack.adaptation_status}")

    try:
        if args.check:
            return _check(pack)
        if args.download and args.source:
            print("[loader] choose one of --download or --source", file=sys.stderr)
            return 2
        if args.download:
            if not args.accept_license:
                print(
                    "[loader] refusing to download without --accept-license: read the upstream licence at "
                    f"{pack.source_dataset.url or pack.source_dataset.huggingface} first",
                    file=sys.stderr,
                )
                return 2
            rows = _download(pack, split=args.split, config=args.config, limit=args.limit)
            print(f"[loader] downloaded {len(rows)} row(s); verifying schema...")
        elif args.source:
            source = args.source
            if not source.is_file():
                print(f"[loader] source not found: {source}", file=sys.stderr)
                return 2
            rows = read_rows(source, _detect_format(source))
            if args.limit is not None:
                rows = rows[: args.limit]
            print(f"[loader] read {len(rows)} row(s) from {source}; verifying schema...")
        else:
            parser.print_help()
            print("\n[loader] nothing was downloaded and no file was written.", file=sys.stderr)
            return 0
        destination = verify_and_write(pack, rows, out_dir=args.out, force=args.force)
    except PackValidationError as exc:
        print(f"[loader] FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"[loader] wrote {destination}")
    print("[loader] this dataset is git-ignored under .medeval_data/; never commit it")
    return 0
