# lm-evaluation-harness integration

The harness is consumed as a **read-only** checkout and extended only through its
documented plugin system (`docs/plugins.md` upstream). No upstream file is edited.

## How the checkout is made importable

`run_all.ps1` first tries the canonical editable install:

```powershell
pip install -e <harness> --no-deps --no-build-isolation
```

That failed on this machine (setuptools editable-wheel/`bdist_wheel` incompatible
with the harness's packaging metadata), so the fallback writes a `.pth` file into
the venv's `site-packages` containing the checkout path. This is what an editable
install does; the difference is only in how the path reaches `sys.path`. See
`VERIFY_ME.md` A2.

## Metrics and aggregations

Registered through `pyproject.toml` entry points, so `lm-eval` discovers them with
no flags:

| Entry point group | Name | Purpose |
|---|---|---|
| `lm_eval.metrics` | `medeval_brier` | mean per-item Brier contribution |
| | `medeval_nll` | mean negative log-likelihood of the gold choice |
| | `medeval_mean_confidence` | mean probability assigned to the chosen answer |
| | `medeval_acc` | reference accuracy implementation (plugin-path test) |
| `lm_eval.aggregations` | `medeval_worst_decile` | 10th percentile — performance on the weakest tenth of items |
| | `medeval_trimmed_mean` | 10% trimmed mean |

Metrics must not pass `aggregation=` to `register_metric` at import time: the
built-in aggregation registry is populated lazily, and a metric that names `mean`
at import raises `KeyError: 'mean'` when the module is imported before the harness
has loaded its built-in metrics. The shipped task YAMLs specify the aggregation
explicitly instead.

`ece` is deliberately **not** a plugin: the harness averages per-document values,
and ECE is not decomposable that way.

## Tasks

`data/tasks/` holds two YAML task variants discovered with `--include_path`:

- `medmcqa_medeval.yaml`
- `medqa_4options_medeval.yaml`

They differ from upstream in two ways:

1. `dataset_path: json` with `dataset_kwargs.data_files` pointing at a pinned
   JSONL snapshot in `data/cache/` (written by the audit). Prompts, choices and the
   0-based target index are identical to upstream.
2. `process_results: !function lm_eval_utils.process_results_*`, which computes
   `acc`, `medeval_brier`, `medeval_nll` and `medeval_mean_confidence` per document
   from the choice log-probabilities that the harness otherwise discards.

The shim `data/tasks/lm_eval_utils.py` re-exports the implementations from
`medeval_metrics.lm_eval_tasks` so the YAML `!function` references resolve relative
to the include path.

## Running it

```powershell
python -m medeval harness --limit 20
```

That calls `lm_eval.simple_evaluate` with a `TaskManager(include_path="data/tasks")`
and saves `results/harness_runs/baseline_*.json` plus per-sample JSONL. The CLI
(`lm-eval run ...`) works too but crashed at the end of a successful run while
printing its Unicode table to a cp1252 console; the Python API avoids that.

## What the numbers mean

With `model="dummy"` the model emits deterministic pseudo-logits. The resulting
accuracy (~40% / 30% at limit 20) tests the wiring: task → per-item
log-probabilities → per-item metrics → aggregation. It is **not** a model
evaluation and must never be quoted as one.
