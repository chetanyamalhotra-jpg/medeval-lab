# Reproduce Experiment 002

What can and cannot be reproduced from the archived Experiment 002 measurement, and
how to rebuild the analysis without spending anything.
medeval-lab is a research tool, not a medical device: there is no clinical
validation, no clinician has reviewed any content, and these small-sample results
are exploratory.

## What produced these numbers

The archived Experiment 002 record was produced under **one specific provider
configuration**, not a general or portable setup:

- a **remote judge from the DeepSeek family** — model `deepseek-flash`, backend
  `openai-compatible-api`, base URL `https://api.deepseek.com`;
- a **local CPU judge from the Llama family, served by Ollama** — model
  `llama3.2:3b`, base URL `http://127.0.0.1:11434`.

The judge ids, model ids, family labels, seeds, prompt hashes and the full per-run
configuration are recorded in the run directory:
[run.json](../results/study_runs/exp002-main/run.json) and
[manifest.json](../results/study_runs/exp002-main/manifest.json).

Two things are part of that record and must be read with it:

1. **Results depend on the model and on the provider's serving version.** Another
   provider, another model, or a later version of the same model name is a
   different measurement instrument. It will not reproduce these numbers exactly,
   and the difference is not necessarily an improvement or a regression.
2. **In the archived execution, only the DeepSeek judge produced verdicts.** The
   local Llama judge was recorded as **unavailable** because Ollama was not
   reachable (`URLError: <urlopen error timed out>`), so the 180 stored judgements
   are from the DeepSeek judge alone and the cross-family question (H1) is **not
   measured**. The report says this too; do not read it as a two-judge result.

The pre-registration and its deviation log are in
[PLAN.md](../experiments/002_judge_families/PLAN.md).

| Recorded item | Value |
| --- | --- |
| Run id | `exp002-main`, status `completed`, 180 judgements |
| Dataset | `medqa_4options`, split `dev`, 1,272 raw rows |
| Dataset source | `https://huggingface.co/datasets/GBaker/MedQA-USMLE-4-options-hf/resolve/main/dev.json`, `sha256=44212909568dcdaf` (truncated in the manifest) |
| Dataset licence | cc-by-sa-4.0, item text not redistributed |
| Sample | 60 items, seed `20260101`, 0 duplicate stems discarded |
| Candidates | 30 naming the key option, 30 naming a distractor, six shared templates `t1`–`t6` (recorded in the manifest) |
| Judges | `j1_deepseek` (`deepseek` family, `deepseek-flash`) and `j2_ollama` (`llama` family, `llama3.2:3b`) |
| Judge calls | 3 repeats per item per configured judge, temperature 0.3, `max_tokens` 2048 (the post-deviation value) |
| Primary repeat | 0 |
| Caps | 500 network calls, US$5 |
| Recorded usage | 187 calls, 67,727 input / 63,682 output / 60,940 reasoning tokens; estimated US$0.1699 at the recorded peak prices, not a provider invoice |
| Prompt version | `mcq_judge_v1`, with SHA-256 hashes for the system prompt, user template and prompt configuration |
| Environment | CPython 3.14.6 on Windows; package version 0.2.0; git commit `6a9b09b` with `dirty: true` |

The number most people look at is the DeepSeek judge's accuracy against the MedQA
key on the primary repeat: 98.3% (58 of 59 scorable items, 95% Wilson interval
91.0–99.7%). One item had no parseable verdict. Treat that as a description of one
model on one sample, not a target to hit.

## Re-analyse the archive without spending

`rebuild` recomputes metrics and rewrites the report from the stored verdicts. It
makes no model call and needs no key or provider. From `<repo>`:

```powershell
.\.venv\Scripts\python.exe -m medeval rebuild exp002-main
```

It re-reads `items.jsonl`, `candidates.jsonl` and `judgements.jsonl` in the run
directory, prints the number of judgements reanalysed and the accuracy per judge,
and rewrites `metrics.json` and `report.md` in place. The verdict files are not
changed. A second command runs the same reanalysis and prints the report path; it
writes the same two files:

```powershell
.\.venv\Scripts\python.exe -m medeval report exp002-main
```

Both commands need the run's local `items.jsonl` and `candidates.jsonl`. Those files
hold benchmark and candidate text and are **git-ignored**, so a fresh clone has only
the tracked files (`run.json`, `manifest.json`, `metrics.json`, `report.md`,
`items.csv`, `judgements.jsonl`) and `rebuild` stops with: "has no items/candidates
(they are git-ignored); rebuild needs the local run directory". On a clone, read the
committed
[report.md](../results/study_runs/exp002-main/report.md) and
[metrics.json](../results/study_runs/exp002-main/metrics.json) instead; there is no
supported way to reconstruct a past run's git-ignored item files without re-running.

If you want to compare your output with the committed archive rather than overwrite
it, copy the run directory first (for example into a scratch folder) and rebuild the
copy, or inspect the diff afterwards with `git diff -- results/study_runs/exp002-main`.

## Re-run it with your own key

What can be reproduced is the **design and analysis**, not the numbers. A re-run is a
new measurement. Match what the manifest records:

- dataset `medqa_4options`, split `dev`, 60 items, seed `20260101`, temperature 0.3;
- three repeats per item per judge, primary repeat 0;
- judge family labels `deepseek` and `llama` (the labels the archived run recorded);
- the same prompt version and prompt hashes, and the same six candidate-response
  template ids — do not edit the prompt or template files to make a re-run "look
  closer";
- the same sampling and judge-base seeds (`20260101`) and the same dataset snapshot.
  Item text is not redistributed, so a run needs a local copy.

The archived run used the prompt and template versions recorded in the manifest.
Experiment 002 does not score against the criteria catalogue, so no criteria version
is recorded for it; the catalogue, metric and rubric version constants in
`packages/medeval_core/version.py` are `v0.1` for this release and apply if you
extend the work to criteria-scored experiments.

### 1. Fetch the dataset and check the snapshot

```powershell
.\.venv\Scripts\python.exe -m medeval data fetch medqa_4options
```

This is a public download, needs no API key and costs nothing; it prints the licence
and evidence. The archived manifest recorded the source URL and the truncated hash
`sha256=44212909568dcdaf`. When the fetch uses the direct download strategy it
prints the source URL and a content hash; compare that prefix with the manifest. If
the fetch reports no hash (another loader strategy served it), compare the item
count and treat the snapshot as unverified. If the prefix does not match, the
upstream `main` branch has changed since the archive: you are looking at a different
snapshot, so the comparison is not like-for-like. Dataset drift is a known limitation
and the repository does not redistribute item text to pin it for you; see
[DATA_LICENSES.md](../DATA_LICENSES.md).

### 2. Run it

Either path works. Both keep the key out of the repository; see
[BYOK_QUICKSTART.md](BYOK_QUICKSTART.md).

**App path.** Start the app with `.\.venv\Scripts\python.exe -m medeval ui`, add two
connections on the **Models** page (one DeepSeek-family remote model, one
Llama-family model served locally by Ollama), set the family labels to `deepseek` and
`llama`, and assign them to `judge_1`/`judge_2`. Then run Experiment 002 from
**Runs → New run** with 60 items and seed `20260101`, dry run first if you want, then
a live run and confirm the real calls. The plan card shows the calls before anything
runs. The app currently asks each connection judge once per item, so the app path
does not reproduce the archived three repeats; use the file path below if repeats
matter to your comparison.

**Connections-file path.** Copy `connections.example.yaml` to `connections.yaml`
(git-ignored) and describe the same two instruments:

```yaml
connections:
  - name: "judge A"
    base_url: "https://api.deepseek.com"
    model: "deepseek-flash"
    key_env: "MEDEVAL_JUDGE_A_KEY"
    family_label: "deepseek"
    role: judge
    repeats: 3
    max_tokens: 2048
    temperature: 0.3
    call_cap: 600
    usd_cap: 5.0
  - name: "judge B"
    base_url: "http://127.0.0.1:11434/v1"
    model: "llama3.2:3b"
    family_label: "llama"
    role: judge
    repeats: 3
    max_tokens: 2048
    temperature: 0.3
    call_cap: 600
    usd_cap: 5.0
```

`key_env` is required for the remote host and not needed for the loopback Ollama
endpoint. Set the remote key in the shell (never in the file):

```powershell
$env:MEDEVAL_JUDGE_A_KEY = "<your key>"
```

Print the plan first (no calls, no spend):

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml --n-items 60 --seed 20260101 --repeats 3 --temperature 0.3
```

Then confirm the live run with caps:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml --confirm-live --n-items 60 --seed 20260101 --repeats 3 --temperature 0.3 --max-api-calls 500 --max-usd 5
```

The CLI's per-connection cap check is deliberately conservative and multiplies the
run's repeats by the connection's `repeats`; with both set to 3 that is an upper
bound of 540 calls per connection, which is why the example sets `call_cap` to 600.
The connection actually makes about 180 calls, and the run-wide cap of 500 still
applies. If prices are not declared, the worst-case cost reads "unavailable, call cap
is binding"; declare prices only if you copied them from the provider's current
pricing page.

If Ollama is not running, the local judge's calls fail: the archived legacy path
recorded that judge as unavailable before any call, while the connections path keeps
the judge in the plan and records the failures as API errors per item. Either way
you do not get a two-judge cross-family result, and nothing is substituted.

### 3. Compare honestly

The new run gets its own run id and its own manifest. Compare the re-run with the
archive only after checking that the resolved model ids, provider hosts, prompt
hashes, seeds, dataset fingerprint and number of repeats match. If any of them
differs, say so before quoting a difference in accuracy or reliability; otherwise
you are comparing two instruments and calling it reproduction. Note that a connection
name becomes the judge id in the report, so a re-run through `connections.yaml` uses
ids like `judge-a` rather than the archived `j1_deepseek`/`j2_ollama`; compare by
family label and model id.

## What cannot be reproduced

- **The exact numbers.** Provider serving versions, tokenizer and prompt handling,
  sampling at temperature 0.3, quantization and hardware can all move outputs. A
  different model version or a different hosting provider is a different instrument.
- **The original key.** It is not in the repository and is not available. Never ask
  for it, never reuse it, and never write a key into a file or a result.
- **Retired or deprecated model versions.** If `deepseek-flash` or `llama3.2:3b` is
  no longer served by your account or provider, you cannot re-run the archived
  instrument; you can only run a different one.
- **The exact dataset snapshot.** If upstream `main` has moved, a fresh fetch is a
  different item set.
- **A cross-family result.** The archive measured one judge. H1 was not measured and
  remains unmeasured unless you supply two available, differently-labelled judges.
- **A different software version as if it were identical.** The manifest records
  commit `6a9b09b`, a dirty working tree and package version 0.2.0. The stored
  verdicts are the evidence; a re-run from this release is a re-run under a later
  analysis version too.

No one else has reproduced these numbers. This page describes what the archive
allows you to re-analyse and what a personal re-run would mean; it is not a
replication claim. The honest summary of the original result — one judge, one
sample, near-ceiling accuracy and confidence that never expressed doubt — is in
[REPORT_v0.2.md](../REPORT_v0.2.md), and the open limitations are listed in
[VERIFY_ME.md](../VERIFY_ME.md).

## Related documentation

- [BYOK_QUICKSTART.md](BYOK_QUICKSTART.md) — the app and headless key paths.
- [USER_GUIDE.md](../USER_GUIDE.md) — Experiment 002 flows, caps and resuming.
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) — Ollama, timeouts, empty replies.
- [DATA_LICENSES.md](../DATA_LICENSES.md) — why item text is not redistributed.
- [VERIFY_ME.md](../VERIFY_ME.md) — verified, unverified and blocked claims.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development setup and checks.
- [SECURITY.md](../SECURITY.md) — key handling and private reporting.
