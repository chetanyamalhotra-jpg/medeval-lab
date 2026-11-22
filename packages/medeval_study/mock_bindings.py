"""Neutral mock judge bindings for dry runs.

A dry run must work on a machine with no provider, no key and no dataset. It
uses two clearly labelled stand-in judges instead of a real one, and every run
that uses them is marked PIPELINE TEST ONLY in ``run.json``, ``metrics.json``
and ``report.md``.

The older Experiment 002 dry-run path mapped the archived ids ``j1_deepseek``
and ``j2_ollama`` to mocks. That names specific providers, so a new run no
longer does it: this module supplies provider-free bindings instead.
"""

from __future__ import annotations

from typing import Any

__all__ = ["dry_run_judge_bindings"]


def dry_run_judge_bindings(repeats: int = 1) -> tuple[dict[str, Any], ...]:
    """Two mock judges, as connection bindings, with no provider attached."""
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(("one", "two"), start=1):
        letter = "ab"[index - 1]
        rows.append(
            {
                "judge_id": f"judge_{name}",
                "connection_id": f"dry-{index}",
                "family_label": f"mock family {letter.upper()}",
                "backend": "mock",
                "base_url": "http://127.0.0.1:9",
                "model": f"mock-judge-{letter}",
                "temperature": 0.3,
                "repeats": max(1, int(repeats)),
                "max_tokens": 64,
                "call_cap": 500,
                "usd_cap": 5.0,
            }
        )
    return tuple(rows)
