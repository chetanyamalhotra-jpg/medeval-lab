"""Version constants for artefacts and the toolkit itself.

Bumping a version here must be accompanied by a CHANGELOG.md entry. Dataset
versions are the versions recorded by the audit run (see results/audit/*.json);
the values below are the defaults the code assumes when nothing has been audited
yet.
"""

from __future__ import annotations

__version__ = "0.6.0"

#: Version of the criterion catalogue in packages/medeval_criteria/catalogue/.
CRITERIA_VERSION = "v0.1"

#: Version of the metric definitions and harness plugins.
METRICS_VERSION = "v0.1"

#: Version of the scoring rubric (data/rubrics/).
RUBRIC_VERSION = "v0.1"

#: Version of the seed item set (data/items/).
ITEMS_VERSION = "v0.1"

#: Upstream harness version this toolkit was developed against.
HARNESS_VERSION = "0.4.14.dev0"

#: Default dataset versions. The audit records the actual dataset fingerprint
#: (row count + content hash) because public datasets change without version
#: bumps; treat these strings as labels, not guarantees.
DATASET_VERSIONS: dict[str, str] = {
    "medmcqa": "openlifescienceai/medmcqa@main",
    "medqa_4options": "GBaker/MedQA-USMLE-4-options-hf@main",
    "pubmedqa": "bigbio/pubmed_qa@main",
}
