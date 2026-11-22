# healthbench-rubric

**Built-in preset** — HealthBench-inspired rubric panel. open-ended medical QA scored by a cross-family judge panel.

> Research benchmark only: not a medical device, no clinical validation, no clinician review.

## Offline smoke run (no network, no key)

```powershell
python -m medeval paper validate healthbench-rubric
python -m medeval matrix run --paper healthbench-rubric --dry-run --n-items 4
```

`smoke.jsonl` contains 4 strictly synthetic rows tagged `PIPELINE TEST / SYNTHETIC SMOKE DATA`.
No upstream dataset rows are tracked in this repository.

## Bring your own licensed data

The upstream source, Hugging Face reference and licence are declared in `paper.yaml`.
The optional `loader.py` verifies every row against the pack schema and writes only to the
git-ignored `.medeval_data/`:

```powershell
python papers/healthbench-rubric/loader.py --check
python papers/healthbench-rubric/loader.py --source path/to/licensed-export.jsonl --force
python papers/healthbench-rubric/loader.py --download --accept-license --limit 200
```

Do not commit a downloaded dataset. Check the upstream licence first; some benchmarks are
research-only or non-commercial.
