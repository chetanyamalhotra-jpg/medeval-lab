"""Prompt/template version identifiers.

Kept in a dependency-free module so deterministic judges can stamp their template
version without importing the prompt renderer (and therefore the criteria
package).
"""

from __future__ import annotations

PAIRWISE_TEMPLATE_VERSION = "pairwise-v0.1.0"
POINTWISE_TEMPLATE_VERSION = "pointwise-v0.1.0"
RULE_TEMPLATE_VERSION = "rules-v0.1.0"
EXACT_TEMPLATE_VERSION = "exact-match-v0.1.0"
JURY_TEMPLATE_VERSION = "jury-v0.1.0"
