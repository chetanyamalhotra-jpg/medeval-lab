"""The toolkit version must agree everywhere it is stated.

It lives in two places by necessity -- ``pyproject.toml`` for packaging and
``medeval_core.version`` for runtime reporting -- and they drifted once already
(v0.3.0 shipped with a runtime that still said 0.2.0). This test makes the drift
impossible to miss.
"""

from __future__ import annotations

import re

from medeval_core.version import __version__
from tests.support import ROOT


def test_pyproject_and_runtime_versions_agree() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version line"
    assert match.group(1) == __version__, (
        f"pyproject.toml says {match.group(1)!r} but medeval_core.version.__version__ says {__version__!r}"
    )


def test_version_is_a_three_part_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_changelog_has_a_section_for_the_running_version() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {__version__}" in text, f"CHANGELOG.md has no section for {__version__}"
