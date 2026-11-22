# Bring your own key: quick start

The short path from a fresh clone to a dry run and then a small live run with your
own provider key, in the local app (recommended) or headless in CI. medeval-lab is
a research tool, not a medical device: it has no clinical validation, no clinician
has reviewed any content, and small-sample results are exploratory.

There is no default provider, no default model and no shipped price. You bring a
provider endpoint, a model id and a key; caps decide when the run stops. Your key is
never written to the repository: the app keeps it in process memory only, and the
headless path reads it from an environment variable named in a git-ignored file.
Nothing here is telemetry — outbound requests go only to the provider base URLs you
configured, plus metadata lookups you initiate yourself (for example the Papers
page).

## 1. Install

Python 3.11+ is required (this repository is built and tested on CPython 3.14 on
Windows; CI targets 3.12 and 3.13).

```powershell
cd <repo>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` is the pinned, reproducible environment. Optionally install the
project itself for the `medeval` console script:

```powershell
python -m pip install -e .
```

The commands below use `.\.venv\Scripts\python.exe -m medeval ...`, which works
without the editable install. Check the environment first; it reads no key value:

```powershell
.\.venv\Scripts\python.exe -m medeval doctor
```

## 2. Start the local app

```powershell
.\.venv\Scripts\python.exe -m medeval ui
```

The app prints a loopback URL that contains a per-launch token, for example
`http://127.0.0.1:8765/#token=...`, and opens it in your browser. The fragment
token is required: API calls without it are refused.

The app is loopback-only. It refuses a non-loopback `--host` unless `--allow-remote`
is given, and that option prints a loud warning — the trust model is one user on
one machine, with no TLS and no accounts, so do not expose it. `.env` loading is off
unless you start the app with `--use-env`; the recommended path on this page never
uses `.env`.

## 3. Add a provider connection (Models page)

1. Open **Models** in the sidebar, then **Add a connection**.
2. Pick a **Provider preset**: DeepSeek, OpenRouter, OpenAI, Ollama (local, no key)
   or Custom OpenAI-compatible. The dropdown starts on an empty "Choose a provider"
   option and **Save is disabled** until you pick one, so no provider is preselected.
   A preset only fills a base URL and links; every field stays editable, so a moved
   or wrong URL is your edit, not a code change. It never selects a model or a price.
3. Set the **model id**. Type the id the endpoint serves. The **Test** action on a
   saved connection lists the endpoint's models without spending anything, so you can
   check the id there; clicking a listed id sets that connection's model. Endpoints
   that publish no model list offer an explicit one-token test call instead. The app
   never chooses a model id for you, and no preset pre-fills one.
4. Set a **family label** (for example `deepseek`, `llama`, `gpt`). Judges need it:
   cross-family comparisons depend on the label, and the app warns when two judges
   share one.
5. Paste the **API key**. It is held only in the app's process memory, is never
   written to disk, and the connection table afterwards shows only `key set
   ····last4`. Forgetting the key means re-entering it.
6. Set `max_tokens` and `temperature` (defaults 2048 and 0.3; reasoning models need
   room for hidden reasoning plus the JSON reply).
7. Set the per-connection caps: `per_connection_call_cap` (default 500 calls) and
   `per_connection_usd_cap` (default 5 USD).
8. Optionally enter prices in USD per 1M tokens. Empty is fine: no price is fetched
   or guessed, and the cost estimate then says "unavailable, call cap is binding".
   Copy a price from the provider's own pricing page if you need a dollar estimate.
9. **Save connection.**

## 4. Assign roles

On the same Models page, assign saved connections to `judge_1` and `judge_2` (use
**+ Add judge role** for more). `judge_2` cannot be assigned without `judge_1`, and
one connection cannot hold two judge roles.

Give two judges different model families. A same-family pair cannot separate
correlated error from shared family bias, and the app warns when two judges share a
family; no judge is substituted automatically. For Experiment 003, also assign a
separate **screening** connection: it must not be a judge (the app refuses that),
and its family label should differ from every judge's, because a screener from a
judge's family is not an independent instrument.

Save the roles.

## 5. Dry run first

In **Runs → New run**, keep **Dry run (mock judges, no network calls)** checked and
press **Start run**. The defaults are dataset `medqa_4options`, split `dev`, 60
items, 3 repeats, seed `20260101`, temperature 0.3 and a cap of 500 network calls.

A dry run makes no network calls and spends nothing. Every artefact is labelled
**PIPELINE TEST ONLY**, because mock judgements say nothing about any model. A dry
run also does not need the benchmark data: if the dataset is not cached it uses a
labelled synthetic sample.

The equivalent command line dry run, which needs no key and no connections file:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --dry-run --n-items 8
```

## 6. Fetch the benchmark once

A real run reads the dataset from the local cache and never downloads during the
run. Fetch it once; this is a public download, needs no API key and costs nothing:

```powershell
.\.venv\Scripts\python.exe -m medeval data fetch medqa_4options
```

The command prints the licence and the licence evidence. `medeval data status` lists
which datasets are cached. MedQA-USMLE is CC-BY-SA-4.0 and item text is not
redistributed; see [DATA_LICENSES.md](../DATA_LICENSES.md).

## 7. Small live run with caps

Go back to the run form, uncheck the dry-run switch, and lower the run to something
small first (for example 20 items, 1 repeat, a run cap of about 60 calls). The plan
card estimates planned calls and the worst-case cost from the prices you declared
before anything runs; if no prices are declared it says the cost is unavailable and
the call cap is binding.

Press **Start run**. A confirmation dialog names the approximate number of real
network calls and asks you to confirm that they spend your own money; only then does
the run start. Per-connection call and USD caps and the run-wide call cap are
enforced before every call. A run can be stopped, and a stopped or cap-stopped run
says so and reports only the results it actually produced.

## 8. Headless path (CI, servers, no browser)

The command-line equivalent uses a git-ignored `connections.yaml`. Start from the
example:

```powershell
Copy-Item connections.example.yaml connections.yaml
```

Each entry names the environment variable that holds its key. Replace the
placeholder base URL and model id with your own; keep the key out of the file:

```yaml
connections:
  - name: "judge A"
    base_url: "https://api.example.com/v1"
    model: "<provider-model-a-id>"
    key_env: "MEDEVAL_JUDGE_A_KEY"
    family_label: "family-a"
    role: judge
    repeats: 1
    max_tokens: 2048
    temperature: 0.3
    call_cap: 200
    usd_cap: 2.0
    # price_in_per_mtok: 0.50
    # price_out_per_mtok: 1.50
```

Allowed fields are `name`, `base_url`, `model`, `key_env`, `family_label`, `role`
(`judge` or `screening`), `repeats`, `max_tokens`, `temperature`, `call_cap`,
`usd_cap`, `price_in_per_mtok`, `price_out_per_mtok` and `api_style` (`chat` or
`responses`). Unknown fields are refused rather than ignored. A `key`/`api_key`
field, a base URL with embedded credentials (`user:pass@host`) and a non-http(s)
scheme are refused before anything is called. A non-loopback host requires
`key_env`; a loopback model server does not.

Set the keys in your shell. Never put them in the file, a commit, an issue or a
chat, and remember that a literal command sits in your shell history:

```powershell
$env:MEDEVAL_JUDGE_A_KEY = "<your key>"
```

There is deliberately no `--api-key` argument, because it would leak into shell
history and process lists.

Print the plan first; it lists every connection (host, model, family, no key), the
call count, the per-connection cap check, the worst-case cost (or "unavailable, call
cap is binding") and the run caps, then exits with status 2 saying nothing was
called. It checks that the named environment variables exist even though it makes
no call:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml
```

Then make the calls, small and capped:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --connections connections.yaml --confirm-live --n-items 20 --repeats 1 --max-api-calls 60 --max-usd 2
```

Two judges at 20 items and one repeat is 40 judge calls, inside the 60-call run cap
and the 200-call per-connection cap. `--max-usd` defaults to `MEDEVAL_MAX_USD` or 5.
Experiment 003 additionally needs a `role: screening` connection; the app runs it in
confirmable stages, and `medeval run exp003 --dry-run` runs every stage with mock
models for free.

## 8A. Paper Packs with your own key

Paper Packs use the same connections and the same memory-only key store. The
shortest path is a dry run, which needs no key:

```powershell
.\.venv\Scripts\python.exe -m medeval paper list
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-clinical-mcq --dry-run --n-items 8
```

For a live matrix run, add `role: target` entries (the models under test) as well
as `role: judge` entries to `connections.yaml`. Each entry names the environment
variable that holds its key — `key_env` or the v0.6 alias `api_key_env`:

```yaml
connections:
  - name: "target model"
    base_url: "https://api.example.com/v1"
    model: "provider-model-to-test"
    role: target
    api_key_env: "MY_TARGET_KEY"
    family_label: "family-a"
    call_cap: 100
    usd_cap: 1.0
  - name: "judge model"
    base_url: "https://other.example.com/v1"
    model: "judge-model"
    role: judge
    key_env: "MY_JUDGE_KEY"
    family_label: "family-b"
    call_cap: 100
    usd_cap: 1.0
```

```powershell
$env:MY_TARGET_KEY = "..."   # your shell only; never a file
$env:MY_JUDGE_KEY  = "..."
.\.venv\Scripts\python.exe -m medeval matrix run --paper template-rubric-panel `
  --connections connections.yaml --target "target model" --judge "judge model" `
  --n-items 8 --max-calls 40 --max-usd 1.00 --confirm-live
```

A plaintext `key:`/`api_key:` field is refused with a message that never echoes
it. `connections.yaml` and `connections.*.yaml` are git-ignored. The matrix runner
also stores every successful completion in `.medeval_cache/` keyed by model,
parameters, rendered prompt and item hash, so stopping and resuming (or repeating
the same experiment) never pays twice for an identical request. Offline re-grade:

```powershell
.\.venv\Scripts\python.exe -m medeval regrade <run_id> --paper template-rubric-panel
```

Re-grading calls no target models (`target calls: 0`); it only re-applies graders
and judges to cached outputs. See `docs/PAPER_PACK_SPEC.md` for the full format.

The five presets (`healthbench-rubric`, `medcalc-bench`, `medhallu-detect`,
`medxpertqa-text`, `medsafety-bench`) run on synthetic smoke rows by default and
declare their upstream `source_dataset` in `paper.yaml`. To use real data, run
`python papers/<preset>/loader.py --source path/to/licensed-export.jsonl --force`
(or `--download --accept-license` after checking the licence). The loader writes
only to the git-ignored `.medeval_data/`; never commit it.

## 9. What leaves your machine

Outbound requests go only to the provider base URLs you configured, plus metadata
lookups you initiate yourself (for example the Papers page). There is no telemetry,
no CDN and no hosted service. The key travels to your provider, and in the app the
browser sends it to the local server over loopback. Run directories record model
ids, base URL, family labels, caps, hashes, verdicts and aggregates — never the key;
tests enforce that redaction.

Never commit `connections.yaml` (it is git-ignored), a `.env` file, or a key.
Report a leak privately as described in [SECURITY.md](../SECURITY.md), and revoke
the key at the provider.

## 10. Safety reminders

- Caps are process-local: they stop this run, not your provider account. Set a
  spend limit in the provider's dashboard as well.
- A cost figure is an upper-bound estimate from token counts and the prices *you*
  declared; it is not a provider invoice, and prices change.
- The maintainers cannot see, stop or refund your provider usage. You pay your own
  provider.
- Dry, mock and synthetic runs are pipeline tests. They say nothing about any model
  and must never be read as a result.
- Retired or deprecated model versions cannot be re-run, and a model served by a
  different provider is a different measurement instrument. See
  [REPRODUCE_EXPERIMENT_002.md](REPRODUCE_EXPERIMENT_002.md).

## Read next

- [USER_GUIDE.md](../USER_GUIDE.md) — every app flow in detail, including the
  Experiment 003 stages.
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) — token problems, 401/403/429 errors,
  empty reasoning-model replies, timeouts, Ollama.
- [SECURITY.md](../SECURITY.md) — key handling, threat model and private reporting.
- [VERIFY_ME.md](../VERIFY_ME.md) — what is verified, unverified, blocked or
  exploratory in this repository.
- [REPRODUCE_EXPERIMENT_002.md](REPRODUCE_EXPERIMENT_002.md) — re-run or re-analyse
  the archived Experiment 002.
- [ADD_A_PROVIDER.md](ADD_A_PROVIDER.md) — add a provider preset to the app.
- [ADD_A_BENCHMARK.md](ADD_A_BENCHMARK.md) — add a public or synthetic benchmark.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development setup and the checks to run.
