"""Verify every DOI in data/literature.csv and update the `verified` column.

For each row the script asks Crossref (and DataCite for arXiv/DataCite DOIs) for
the registered title, compares it with the CSV title by token overlap, and marks
``verified=yes`` only when the DOI resolves to a work whose title agrees.

Writes ``data/literature_verification.json`` with the evidence for every row and
rewrites ``data/literature.csv`` in place (same columns, re-quoted).

Usage:
    python scripts/verify_literature.py [--offline]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "literature.csv"
EVIDENCE_PATH = ROOT / "data" / "literature_verification.json"
COLUMNS = [
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

_STOP = {"a", "an", "the", "of", "for", "and", "on", "in", "to", "with", "using", "large", "language", "models"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Verification:
    literature_id: str
    doi: str
    status: str  # verified | unverified | no_doi
    registry: str
    registered_title: str
    title_similarity: float
    http_status: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.literature_id,
            "doi": self.doi,
            "status": self.status,
            "registry": self.registry,
            "registered_title": self.registered_title,
            "title_similarity": round(self.title_similarity, 3),
            "http_status": self.http_status,
            "detail": self.detail,
        }


def _get_json(url: str, timeout: float = 30.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "medeval-lab/0.1 (doi verification)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return 0, {"error": str(exc)}


def lookup(doi: str) -> tuple[str, str, int]:
    """Return (registry, registered_title, http_status) for a DOI."""
    quoted = urllib.parse.quote(doi, safe="")
    status, payload = _get_json(f"https://api.crossref.org/works/{quoted}")
    if status == 200 and isinstance(payload, dict):
        message = payload.get("message", {})
        titles = message.get("title") or []
        return "crossref", str(titles[0]) if titles else "", status
    status_dc, payload_dc = _get_json(f"https://api.datacite.org/dois/{quoted}")
    if status_dc == 200 and isinstance(payload_dc, dict):
        attributes = payload_dc.get("data", {}).get("attributes", {})
        titles = attributes.get("titles") or []
        title = str(titles[0].get("title", "")) if titles else ""
        return "datacite", title, status_dc
    return "none", "", status or status_dc


def verify_row(row: dict[str, str], offline: bool = False) -> Verification:
    literature_id = row["id"]
    doi = (row.get("doi") or "").strip()
    title = row.get("title", "")
    if not doi:
        return Verification(literature_id, doi, "no_doi", "none", "", 0.0, 0, "no DOI in registry")
    if offline:
        return Verification(literature_id, doi, "unverified", "none", "", 0.0, 0, "offline run")
    registry, registered_title, status = lookup(doi)
    similarity = _similarity(title, registered_title) if registered_title else 0.0
    if registry != "none" and similarity >= 0.6:
        return Verification(
            literature_id,
            doi,
            "verified",
            registry,
            registered_title,
            similarity,
            status,
            f"title overlap {similarity:.2f} against {registry} record",
        )
    reason = (
        f"registry={registry}, status={status}, title overlap {similarity:.2f}"
        if registry != "none"
        else f"DOI did not resolve (status={status})"
    )
    return Verification(literature_id, doi, "unverified", registry, registered_title, similarity, status, reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="mark every DOI unverified without network")
    args = parser.parse_args(argv)

    with CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            print(f"literature.csv is missing columns: {missing}", file=sys.stderr)
            return 2

    verifications = [verify_row(row, offline=args.offline) for row in rows]
    for row, result in zip(rows, verifications, strict=True):
        row["verified"] = "yes" if result.status == "verified" else "no"

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "n_rows": len(rows),
        "n_verified": sum(1 for v in verifications if v.status == "verified"),
        "n_unverified": sum(1 for v in verifications if v.status != "verified"),
        "rows": [v.as_dict() for v in verifications],
    }
    EVIDENCE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"verified {payload['n_verified']}/{payload['n_rows']} DOIs; evidence -> {EVIDENCE_PATH.name}")
    for v in verifications:
        if v.status != "verified":
            print(f"  UNVERIFIED {v.literature_id}: {v.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
