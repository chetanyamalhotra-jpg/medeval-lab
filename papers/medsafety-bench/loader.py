"""On-demand loader for the medsafety-bench preset.

Run from the repository root:

    python papers/medsafety-bench/loader.py --check
    python papers/medsafety-bench/loader.py --source path/to/licensed-export.jsonl --force
    python papers/medsafety-bench/loader.py --download --accept-license --limit 200

The upstream dataset is never bundled. Downloads land in git-ignored
`.medeval_data/medsafety-bench/dataset.jsonl` after schema verification.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACK_DIR.parents[1]
PACKAGES = REPO_ROOT / "packages"
if str(PACKAGES) not in sys.path:
    sys.path.insert(0, str(PACKAGES))

from medeval_packs.loaders import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(PACK_DIR))
