from __future__ import annotations

import importlib.util
import sys

from tests.support import ROOT


def _module():
    path = ROOT / "experiments" / "002a_pairwise_blocked" / "run.py"
    spec = importlib.util.spec_from_file_location("exp002_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_case_count_and_heldout_provenance() -> None:
    module = _module()
    cases = module.load_cases()
    assert len(cases) == 70
    assert sum(c.case_id.startswith("heldout-") for c in cases) == 20
    assert all(c.metadata["reference_standard"] == "planted_error" for c in cases)


def test_dry_run_has_no_api_calls(capsys) -> None:
    module = _module()
    assert module.run(dry_run=True) == 0
    assert '"total_paid_max"' in capsys.readouterr().out


def test_plan_is_preregistration_ready() -> None:
    text = (ROOT / "experiments" / "002a_pairwise_blocked" / "PLAN.md").read_text(encoding="utf-8")
    for heading in ("Question and hypotheses", "Stopping rules", "What the results can and cannot support"):
        assert heading in text


def test_frozen_rule_only_writes_heldout_result(tmp_path, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "OUT", tmp_path)
    assert module.run_frozen_rule_only() == 0
    payload = (tmp_path / "rule_heldout_summary.json").read_text(encoding="utf-8")
    assert '"n": 20' in payload
