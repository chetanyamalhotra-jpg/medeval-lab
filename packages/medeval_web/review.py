"""Single-reviewer item labels and notes (M4).

One reviewer (the owner) can label flagged items while reading the review queue.
The four labels are fixed; a free-text note is optional. Everything lives in one
git-ignored local file (``data/review_notes.json``) because it is one person's
opinion, not a reference standard, and it must never be committed or written into
a run record.

Labels are **excluded from headline metrics** by construction: nothing here is
read by the analysis layer. The only place they surface is the clearly labelled
sensitivity table, which recomputes the numbers with ``key looks wrong`` items
removed.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from medeval_core.calllog import utc_now_iso
from medeval_core.paths import data_dir

__all__ = ["LABELS", "SENSITIVITY_LABEL", "ReviewStore"]

#: The four labels, verbatim (they appear in the UI and the docs).
LABELS = ("key looks correct", "key looks wrong", "ambiguous", "unsure")

#: Items with this label are removed in the sensitivity table.
SENSITIVITY_LABEL = "key looks wrong"

_CAVEAT = (
    "These labels are one reviewer's opinion, recorded locally. They are not a "
    "reference standard, not clinical validation, and they do not enter the "
    "headline metrics."
)


class ReviewStore:
    """Labels and notes for one reviewer, in a git-ignored JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "review_notes.json")
        self._lock = threading.Lock()

    # -- reading --------------------------------------------------------- #
    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def for_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._load().get(run_id) or {})

    def as_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def labelled_item_ids(self, run_id: str, label: str | None = None) -> set[str]:
        rows = self.for_run(run_id)
        return {
            item_id for item_id, row in rows.items()
            if label is None or (row or {}).get("label") == label
        }

    # -- writing --------------------------------------------------------- #
    def set_label(self, run_id: str, item_id: str, label: str, note: str = "") -> dict[str, Any]:
        label = (label or "").strip()
        if label and label not in LABELS:
            raise ValueError(f"label must be one of {list(LABELS)}")
        with self._lock:
            payload = self._load()
            run_rows = payload.setdefault(run_id, {})
            if not label and not note:
                run_rows.pop(item_id, None)
            else:
                run_rows[item_id] = {
                    "label": label,
                    "note": note.strip(),
                    "updated_utc": utc_now_iso(),
                }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            row = run_rows.get(item_id) or {}
            return {
                "run_id": run_id,
                "item_id": item_id,
                "label": row.get("label", ""),
                "note": row.get("note", ""),
                "updated_utc": row.get("updated_utc"),
                "caveat": _CAVEAT,
            }

    def queue(self, run_id: str, item_ids: list[str]) -> dict[str, Any]:
        """The review queue with any labels already recorded, in a stable order."""
        rows = self.for_run(run_id)
        return {
            "run_id": run_id,
            "labels": list(LABELS),
            "sensitivity_label": SENSITIVITY_LABEL,
            "caveat": _CAVEAT,
            "items": [
                {
                    "item_id": item_id,
                    "label": (rows.get(item_id) or {}).get("label", ""),
                    "note": (rows.get(item_id) or {}).get("note", ""),
                }
                for item_id in item_ids
            ],
        }
