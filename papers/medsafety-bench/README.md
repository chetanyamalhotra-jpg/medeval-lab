# medsafety-bench

**Built-in preset** — MedSafetyBench-inspired safety evaluation. refusal rate plus dual-judge harmfulness scoring.

> Research benchmark only: not a medical device, no clinical validation, no clinician review.

## Offline smoke run (no network, no key)

```powershell
python -m medeval paper validate medsafety-bench
python -m medeval matrix run --paper medsafety-bench --dry-run --n-items 4
```

`smoke.jsonl` contains 4 strictly synthetic rows tagged `PIPELINE TEST / SYNTHETIC SMOKE DATA`.
No upstream dataset rows are tracked in this repository.

## Bring your own licensed data

The upstream source, Hugging Face reference and licence are declared in `paper.yaml`.
The optional `loader.py` verifies every row against the pack schema and writes only to the
git-ignored `.medeval_data/`:

```powershell
python papers/medsafety-bench/loader.py --check
python papers/medsafety-bench/loader.py --source path/to/licensed-export.jsonl --force
python papers/medsafety-bench/loader.py --download --accept-license --limit 200
```

Do not commit a downloaded dataset. Check the upstream licence first; some benchmarks are
research-only or non-commercial.
