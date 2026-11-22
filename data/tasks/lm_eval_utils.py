"""Task-local helpers referenced from the YAML task files in this directory.

``lm-eval --include_path data/tasks`` imports ``!function`` references relative
to the YAML file, so this shim keeps the actual implementation in the installed
package.
"""

from __future__ import annotations

from medeval_metrics.lm_eval_tasks import (
    doc_to_text_medmcqa,
    doc_to_text_medqa,
    process_results_medmcqa,
    process_results_medqa,
)

__all__ = [
    "doc_to_text_medmcqa",
    "doc_to_text_medqa",
    "process_results_medmcqa",
    "process_results_medqa",
]
