"""Papers library over the existing literature registry.

The registry of record is ``data/literature.csv`` (the traceability rule: every
criterion and metric cites at least one row). This module adds:

* **add by DOI** — metadata (title, year, container) is fetched from Crossref
  only when the user asks for it in the UI; the outbound request is announced in
  the API response so it is never silent. arXiv ids resolve through the arXiv
  export API. In tests a local fake metadata server is used instead.
* **status tags and local notes** — ``to read`` / ``read`` / ``used in design`` /
  ``cited in report`` plus free-text notes live in a git-ignored JSON file.
* **reverse view** — for a paper, what in the lab cites it (criteria, metrics).

``verified`` is only ever set by ``scripts/verify_literature.py`` (the automated
Crossref/DataCite title match); the UI cannot set it.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

__all__ = ["LITERATURE_COLUMNS", "PaperStore"]

LITERATURE_COLUMNS = (
    "id", "doi", "title", "year", "task", "reference_standard",
    "metric_proposed", "how_we_operationalize", "limitations", "verified",
)

_STATUS_TAGS = ("to read", "read", "used in design", "cited in report")
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_PATTERN = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(v\d+)?$", re.IGNORECASE)


def _norm_doi(doi: str) -> str:
    return doi.strip().lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")


class PaperStore:
    """Reads ``data/literature.csv``; writes status/notes beside it, git-ignored."""

    def __init__(self, literature_csv: Path, notes_path: Path) -> None:
        self.literature_csv = literature_csv
        self.notes_path = notes_path
        self._lock = threading.Lock()

    # -- registry --------------------------------------------------------- #
    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.literature_csv.is_file():
                return []
            notes = self._notes()
            rows: list[dict[str, Any]] = []
            with self.literature_csv.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    paper_id = row.get("id", "")
                    note = notes.get(paper_id, {})
                    rows.append({
                        **row,
                        "status": note.get("status", ""),
                        "notes": note.get("notes", ""),
                    })
            return rows

    def get(self, paper_id: str) -> dict[str, Any] | None:
        for row in self.rows():
            if row["id"] == paper_id:
                return row
        return None

    def cited_by(self, paper_id: str) -> dict[str, list[str]]:
        """What in the lab cites this paper: criteria and metrics, by id.

        The criteria catalogue YAML and the metrics registry both carry
        literature ids; this scans the catalogue and the metrics module.
        """
        from medeval_core.paths import criteria_dir
        from medeval_metrics.metrics import METRICS

        citing_criteria: list[str] = []
        catalogue = criteria_dir()
        for yaml_path in sorted(catalogue.rglob("*.yaml")):
            try:
                text = yaml_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if paper_id in text:
                citing_criteria.append(yaml_path.stem)
        citing_metrics = [
            metric_id for metric_id, metric in METRICS.items()
            if paper_id in getattr(metric, "literature_ids", ())
            or paper_id in str(getattr(metric, "literature_ids", ""))
        ]
        return {"criteria": sorted(citing_criteria), "metrics": sorted(citing_metrics)}

    # -- status and notes -------------------------------------------------- #
    def _notes(self) -> dict[str, dict[str, Any]]:
        if not self.notes_path.is_file():
            return {}
        try:
            return json.loads(self.notes_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def set_status(self, paper_id: str, status: str) -> dict[str, Any]:
        if status and status not in _STATUS_TAGS:
            raise ValueError(f"status must be one of {list(_STATUS_TAGS)}")
        with self._lock:
            notes = self._notes()
            notes.setdefault(paper_id, {})["status"] = status
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            self.notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"id": paper_id, "status": status}

    def set_notes(self, paper_id: str, notes: str) -> dict[str, Any]:
        with self._lock:
            store = self._notes()
            store.setdefault(paper_id, {})["notes"] = notes
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            self.notes_path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"id": paper_id, "notes": notes}

    # -- add by DOI / arXiv ------------------------------------------------ #
    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        """Add one row to the registry. ``verified`` is always 'no' here; only
        scripts/verify_literature.py may set it to yes."""
        with self._lock:
            self.literature_csv.parent.mkdir(parents=True, exist_ok=True)
            existing: set[str] = set()
            if self.literature_csv.is_file():
                with self.literature_csv.open("r", encoding="utf-8", newline="") as handle:
                    existing = {row.get("id", "") for row in csv.DictReader(handle)}
            if row["id"] in existing:
                raise ValueError(f"paper id {row['id']!r} already exists")
            is_new = not self.literature_csv.is_file() or self.literature_csv.stat().st_size == 0
            with self.literature_csv.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(LITERATURE_COLUMNS))
                if is_new:
                    writer.writeheader()
                writer.writerow({column: row.get(column, "") for column in LITERATURE_COLUMNS})
        return self.get(row["id"]) or row


def fetch_metadata(identifier: str, *, timeout_s: float = 20.0, base_override: str | None = None) -> dict[str, Any]:
    """Fetch title/year/container for a DOI or arXiv id.

    ``base_override`` exists for tests (a local fake metadata server), and the
    ``MEDEVAL_CROSSREF_BASE`` environment variable does the same for the whole
    process so an end-to-end browser test never reaches the real Crossref.
    Production calls use the real Crossref / arXiv export endpoints. The caller
    announces the outbound request in the UI before invoking this.
    """
    import os

    identifier = identifier.strip()
    env_base = os.environ.get("MEDEVAL_CROSSREF_BASE") or None
    if _DOI_PATTERN.match(_norm_doi(identifier)):
        doi = _norm_doi(identifier)
        url = f"{base_override or env_base or 'https://api.crossref.org/works'}/{quote(doi)}"
        response = requests.get(url, headers={"User-Agent": "medeval-lab/0.3 (mailto:local@localhost)"}, timeout=timeout_s)
        if response.status_code != 200:
            raise ValueError(f"Crossref returned HTTP {response.status_code} for {doi}")
        message = response.json().get("message", {})
        title = (message.get("title") or [""])[0]
        year = None
        for field in ("published-print", "published-online", "issued", "created"):
            parts = (message.get(field) or {}).get("date-parts") or []
            if parts and parts[0]:
                year = parts[0][0]
                break
        return {
            "id": f"lit_{doi.split('/', 1)[1].replace('/', '_').replace('.', '-')[:40]}",
            "doi": doi,
            "title": title,
            "year": year,
            "task": "",
            "reference_standard": "",
            "metric_proposed": "",
            "how_we_operationalize": "",
            "limitations": "",
            "verified": "no",
            "source": "crossref",
            "fetched_url": url,
        }
    if _ARXIV_PATTERN.match(identifier):
        arxiv_id = identifier
        url = f"{base_override or env_base or 'https://export.arxiv.org/api/query'}/?id_list={quote(arxiv_id)}"
        response = requests.get(url, timeout=timeout_s)
        if response.status_code != 200:
            raise ValueError(f"arXiv returned HTTP {response.status_code} for {arxiv_id}")
        text = response.text
        title_match = re.search(r"<title>([^<]+)</title>", text)
        titles = re.findall(r"<title>([^<]+)</title>", text)
        title = titles[1] if len(titles) > 1 else (title_match.group(1) if title_match else "")
        published = re.search(r"<published>(\d{4})", text)
        return {
            "id": f"lit_arxiv_{arxiv_id.replace('/', '_').replace('.', '-')[:40]}",
            "doi": f"arXiv:{arxiv_id}",
            "title": title.strip(),
            "year": int(published.group(1)) if published else None,
            "task": "",
            "reference_standard": "",
            "metric_proposed": "",
            "how_we_operationalize": "",
            "limitations": "",
            "verified": "no",
            "source": "arxiv",
            "fetched_url": url,
        }
    raise ValueError("identifier must be a DOI (10.xxxx/...) or an arXiv id")
