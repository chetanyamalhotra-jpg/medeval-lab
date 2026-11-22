from __future__ import annotations

from tests.support import ROOT


def test_experiment_002_does_not_open_item_split() -> None:
    source = (ROOT / "experiments" / "002a_pairwise_blocked" / "run.py").read_text(encoding="utf-8")
    assert 'data" / "items' not in source
    assert "seed_v0.1.json" not in source


def test_hidden_manifest_has_ten_hashes_only() -> None:
    lines = [line for line in (ROOT / "data" / "items" / "hidden_split_sha256.txt").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert len(lines) == 10
    assert all(len(line.split()[1]) == 64 for line in lines)


def test_public_item_file_contains_no_hidden_rows() -> None:
    import json
    rows = json.loads((ROOT / "data" / "items" / "seed_v0.1.json").read_text(encoding="utf-8"))
    assert len(rows) == 40
    assert all(row["split"] != "hidden" for row in rows)
