"""Move v0.1 hidden rows to a gitignored private file and verify their hashes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "items" / "seed_v0.1.json"
PRIVATE = ROOT / ".private" / "hidden_items_v0.1.json"
MANIFEST = ROOT / "data" / "items" / "hidden_split_sha256.txt"


def canonical(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    items = json.loads(SOURCE.read_text(encoding="utf-8"))
    hidden = [row for row in items if row.get("split") == "hidden"]
    public = [row for row in items if row.get("split") != "hidden"]
    if len(hidden) != 10:
        raise RuntimeError(f"expected 10 hidden rows before sealing, found {len(hidden)}")
    expected = {line.split()[0]: line.split()[1] for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
    actual = {str(row["id"]): hashlib.sha256(canonical(row).encode("utf-8")).hexdigest() for row in hidden}
    if actual != expected:
        raise RuntimeError("hidden split hashes do not match the committed manifest; refusing to move rows")
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE.write_text(json.dumps(hidden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SOURCE.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sealed {len(hidden)} rows to {PRIVATE}; {len(public)} public rows remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
