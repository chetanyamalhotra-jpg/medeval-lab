# medeval-lab — user guide

This is the practical guide to running medeval-lab yourself: starting the local app,
adding your own model connections, running Experiment 002 and Experiment 003, keeping
spend under control, reading flags, and exporting results.

Read this first, plainly:

- **This is a measurement toolkit, not clinical validation.** There is no clinician
  in the project, no human reference standard, and the benchmark answer key is treated
  as an imperfect reference. Nothing here says a model is clinically safe or accurate.
- **You run the live experiments.** The documentation author (an AI agent) made no
  live, paid API call while writing this guide and holds no real key. Every live run
  described here is run by you, from this app, with your own credentials.
- **Keys live only in the app's process memory.** They are shown as
  "key set ····last4", never written to disk, logs, reports or git, and forgotten when
  the app stops. `.env` loading is off unless you start the app with `--use-env`.
- **Version.** This guide describes the v0.4 release behaviour. Re-check a section if
  the app version shown in Settings is not 0.4.0; features older than v0.4 (staged
  Experiment 003, `medeval doctor`, `medeval report`, `medeval export`, the first-run
  card) are not in v0.3.0.

Everything below assumes PowerShell and the repository virtual environment. Use
`.\.venv\Scripts\python.exe` (never a bare `python`), and run from the repository root.
If a command looks out of date, check `.\.venv\Scripts\python.exe -m medeval --help`.

Contents

1. [Start the app](#1-start-the-app)
2. [Connections: providers, keys, roles](#2-connections-providers-keys-roles)
3. [Experiment 002: dry run, then live](#3-experiment-002-dry-run-then-live)
4. [Experiment 003 in stages](#4-experiment-003-in-stages)
5. [Caps and spend safety](#5-caps-and-spend-safety)
6. [Review queue and flags](#6-review-queue-and-flags)
7. [Report and export](#7-report-and-export)
8. [Test center, first-run card, doctor, Settings](#8-test-center-first-run-card-doctor-settings)

Troubleshooting lives in `TROUBLESHOOTING.md`. Known limitations live in
`VERIFY_ME.md`; read that before citing a result.

---

## 1. Start the app

### 1.1 The command

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m medeval ui
```

The app binds `127.0.0.1` only. It prints something like:

```text
[ui] medeval-lab serving at http://127.0.0.1:8765/ (loopback only)
[ui] launch token: <random token for this launch>
[ui] the browser opens with the token in the address bar; keep the tab open
[ui] press Ctrl+C to stop; any stored keys are forgotten when the app stops
```

Useful options (all verified in the CLI):

```powershell
.\.venv\Scripts\python.exe -m medeval ui --port 9000
.\.venv\Scripts\python.exe -m medeval ui --no-browser
.\.venv\Scripts\python.exe -m medeval ui --use-env
```

- `--port` sets the preferred port; the default is 8765.
- `--no-browser` prints the URL without opening a browser.
- `--use-env` is the only way the app reads the git-ignored `.env`. It is off by
  default because v0.3+ keys come from the Models page. The CLI (`medeval run ...`)
  does load `.env` by default; the app does not unless you pass `--use-env`.

Keep the app terminal open. The process is the app: closing the terminal (or pressing
Ctrl+C) stops the server and **forgets every key it was holding**.

### 1.2 What the token link means

The browser is opened at a URL ending in `#token=<random token>`. The token is:

- generated fresh on every launch, so a link or tab from a previous launch is dead;
- carried in the URL *fragment*, which the browser never sends to the server, then
  stored in that tab's `sessionStorage`;
- required on every `/api/*` request (`X-Medeval-Token` header). Without a valid one,
  API calls return 403 and the page shows "This server needs its launch token".

So: reopen the link from the terminal that is actually running the app. Do not
bookmark the URL, and do not paste the token into a file, chat or note. The token is
not an API key, but it is the key to this loopback process for as long as it runs.
The Troubleshooting guide covers a stale or missing token.

### 1.3 Ports on Windows

The app never tries to serve on a busy port silently.

- If the preferred port is free, it uses it.
- If not, it prints `[ui] port <preferred> was busy; using <next free>` and prints the
  full URL (with the new port and token) for you to open.
- In v0.4 it also says whether the busy port appears to be a stale medeval server
  (printing the exact PowerShell command to stop it) or another program, then picks
  the next free port, prints it, and opens the browser at that URL.

A stale app on port 8765 answering "token required" is a different process with a
different token. Find and stop it with the commands in `TROUBLESHOOTING.md` (section 2),
or let v0.4 pick the next port and open the newly printed link.

### 1.4 Pages and keyboard

Pages (hash routes, safe to refresh):

| Route | Page | What it is for |
|---|---|---|
| `#/` | Start screen | Describe an evaluation; `/run`, `/dryrun`, `/test`, `/papers`, `/report` |
| `#/runs` | Runs | Every study run, newest first |
| `#/new` | New run | Experiment 002 configuration and plan card |
| `#/run/<id>` | Run detail | Progress, live log, Outputs, Stages (Experiment 003), Stop |
| `#/results/<id>` | Results | Metrics, calibration, confusion matrices, flags, cost |
| `#/items/<id>` | Item explorer | Per-item verdicts; question text stays local, never committed |
| `#/models` | Models | Connections, keys, roles, Test connection |
| `#/papers` | Papers | Literature registry, local notes |
| `#/tests` | Tests | Allow-listed server-side commands |
| `#/settings` | Settings | Paths, caps, prices, status, datasets, backend |

The left sidebar lists workspaces (folders of sessions) and holds the Settings links.
Sessions are the start screen's saved notes and plans; each session can link to a run.

Keyboard:

- `Ctrl+K` (or `Cmd+K`) opens the command palette. Arrow keys move, Enter runs, Esc closes.
- `g` then `r` goes to Runs, `g` then `n` to New run, `g` then `s` to Settings.
- `t` cycles the theme (auto / light / dark).
- Chords are ignored while you are typing in an input, textarea or select.

---

## 2. Connections: providers, keys, roles

Connections are how you bring your own provider, model and key. Open **Models** in the
sidebar. Connection *metadata* (name, base URL, model, family, caps, optional prices) is
saved to the git-ignored `data/connections.json`; the key is never in that file.

### 2.1 Add a connection

1. Pick a **Provider preset**. The dropdown starts on an empty **Choose a
   provider** option and **Save connection is disabled** until you pick one, so
   nothing is preselected. The presets are DeepSeek, OpenRouter, OpenAI, Ollama
   (local, no key) and Custom OpenAI-compatible.
2. The **Base URL** is filled from the preset and stays editable (a moved or wrong
   URL is your edit, not a code change). A preset fills only the base URL: the
   model, family label and prices are always your choice. Presets as shipped:
   DeepSeek `https://api.deepseek.com`, OpenRouter `https://openrouter.ai/api/v1`,
   OpenAI `https://api.openai.com/v1`, Ollama `http://localhost:11434/v1`.
3. **API style**: `chat completions` (default) or `responses API`.
4. **Model**: type the model id, or run Test connection and pick from the endpoint's
   list. The model id must be the one the endpoint serves.
5. **Model family label**: for example `DeepSeek`, `Llama`, `GPT`. It is required for
   judges because cross-family analysis depends on it; the app warns when two judges
   share a family.
6. **API key**: paste it into the password field. It is sent to the local app over
   loopback in a request body and held only in process memory. The Show/Hide button
   only reveals what you just typed in that box.
7. **Per-connection call cap** and **per-connection USD cap** (defaults 500 calls and
   $5).
8. **Price in / price out** per 1M tokens (optional). If you leave both empty, cost is
   reported as unavailable and the call cap is the only binding limit.
9. **Save connection.**

After saving, the table shows the key as `key set ····last4` or `no key`; the full key
is not retrievable, by design.

### 2.2 Test connection (no spend) and the explicit one-token call

Press **Test** on a saved connection. It lists the endpoint's models with
`GET {base}/models` (it inserts `/v1` unless the base URL already ends with `/v1`).
This costs nothing.

- Success: a green card says how many models are visible and lists some of them.
- Failure: a red card shows a redacted HTTP or network error (provider error bodies are
  truncated and passed through key redaction).
- If the endpoint is reachable but publishes no model list, the card offers
  **"Run one-token test call (explicit confirm)"**. That is a real, tiny paid call
  (`max_tokens=1`) and only runs after you click it. Use it for gateways without a
  model list; then type the returned model id into the connection's Model field and save.

### 2.3 Forget a key

**Forget key** removes the key from app memory for that connection; the metadata stays.
The app also forgets every key when it stops. "Forget key after this run" is the
behaviour on every stop; there is no remember-on-disk option.

If you restart the app, saved connections and roles are still there, but every key is
gone. Re-enter keys on the Models page before a live run. (This matters when resuming a
staged Experiment 003 run: see section 4.9.)

### 2.4 Roles: Judge 1..N and the Screening model

Below the connections table is the **Roles** card.

- Assign saved connections to `judge_1`, `judge_2`, `judge_3` ... Use **+ Add judge role**
  for more. `judge_2` cannot be set without `judge_1`, and one connection cannot hold
  two judge roles.
- Assign an optional **screening** connection. It is used by Experiment 003 only.
- Press **Save roles**.

Rules the app enforces or warns about:

- The **screening model must not also be a judge**. The app shows a red banner and the
  server rejects the role set.
- In v0.4 the **screening connection's family label must differ from every judge's
  family label**; the app warns when identical and blocks that role set. The screening
  model has to be an independent instrument, not a rerun of a judge.
- A judge without a family label is treated as an "unlabelled-family" judge; assign
  labels before a real run.
- Experiment 003 also needs at least one judge role assigned; roles are required, not
  optional.

---

## 3. Experiment 002: dry run, then live

Experiment 002 is the judge-grading study: sample medicines questions, build a candidate
answer, ask each judge `CORRECT`/`INCORRECT` with a confidence, and report accuracy,
error rates, kappa, calibration, ceiling and cost. **You choose the judges from your own
connections** — there is no fixed judge pair and no default provider in v0.5. Assign one
or more connections to judge roles on the Models page (two judges from different families
are what make the error-correlation question answerable at all), or use a
`connections.yaml` file for the headless CLI path.

The archived Experiment 002 run used one remote judge from the DeepSeek family
(`j1_deepseek`) and one local Llama-family judge via Ollama (`j2_ollama`). Those ids
still work if you name them explicitly (`--judges j1_deepseek j2_ollama` with
`MEDEVAL_*` environment variables), but they are the original run's configuration, not a
default: a new run uses connections.

### 3.1 Dry run in the app (always do this first)

1. Sidebar: **Runs**, then **New run**.
2. Keep **Dry run (mock judges, no network calls)** checked. The form defaults are
   dataset `medqa_4options`, split `dev`, 60 items, 3 repeats, seed 20260101,
   temperature 0.3, and a cap of 500 network calls.
3. Adjust items/repeats/seed/temperature if you want. The plan card recomputes as you
   type.
4. Press **Start run**. A dry run makes no network calls and spends nothing; every
   result is labelled **PIPELINE TEST ONLY** and says nothing about a model.

### 3.2 What the plan card shows

The **"Before you start"** card on the New run page is the spend plan. It shows:

- **This run**: "Dry run: 0 network calls, US$0" when the dry-run switch is on.
- **Network calls** (and, live, "Network calls") with the cap it is checked against.
- **Worst-case cost**: a US dollar figure when prices are configured; otherwise
  **"unavailable, call cap is binding"** (see section 5).
- **Input tokens (upper bound)**: a conservative estimate (characters/4, full
  `max_tokens` for every reply), not a measurement.
- **Dataset**: the dataset key, its licence, and "not redistributed".
- **Judges in this run**: each available judge with its model, temperature and repeat
  count; unavailable judges are listed with the reason and are never substituted.
- The estimator note.

If Ollama is not reachable, a banner explains that a live run will use J1 only and the
cross-family question stays unmeasured.

### 3.3 Live run in the app

1. On the New run page, uncheck **Dry run**.
2. Press **Start run**. The app first re-estimates and shows a confirmation:
   "This will make about N real network calls (worst case US$X). Continue?"
3. Only if you accept does the API receive `confirm_real_calls=true` and the run start.
4. The browser moves to the run detail page: a progress bar of judge calls, counters
   (status, network calls, cache hits, items, repeats), a live log streamed from the
   run directory, and a **Stop run** button. Outputs link to Results, Item explorer and
   `report.md`.

The run detail page tails files on disk, so a page reload or a second tab shows the same
progress; nothing is lost when the browser closes. A terminal-started run also appears
in Runs, but its Stop button is rejected with a 409 ("not active in the app") because
there is no in-app worker to signal; stop a terminal run with Ctrl+C.

### 3.4 Experiment 002 from the command line

Dry run first (mock judges, no network, no spend):

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --dry-run --n-items 8
```

A live CLI run prints the plan and worst-case cost before anything is spent:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --n-items 60 --repeats 3 --max-api-calls 500
```

Useful verified flags: `--dry-run`, `--n-items`, `--seed`, `--repeats`,
`--temperature`, `--max-api-calls`, `--dataset`, `--split`, `--no-download`,
`--judges`, `--consistency-subsample`, `--resume <run_id>`, `--run-id <id>`,
`--resume-latest`, `--json`. The CLI exits non-zero when the plan is refused or blocked.

A CLI live run needs credentials in the environment or the repo `.env` (`MEDEVAL_*`
variables; see the README's "Model backends" section). It talks to the provider
directly; the app's memory-only keys are not involved.

### 3.5 Interrupting and resuming

Every judgement is appended to the run directory immediately, and completed calls are
cached, so a stopped, crashed or refused run can be resumed:

```powershell
.\.venv\Scripts\python.exe -m medeval run exp002 --resume exp002-main
```

Resume skips `(item, judge, repeat)` keys already in `judgements.jsonl` and refuses to
merge judgements from a changed design (config lock). To re-score an existing run
without any model call:

```powershell
.\.venv\Scripts\python.exe -m medeval rebuild exp002-main
```

After a run, the Runs list shows status, judges, item count and accuracy with a 95%
Wilson interval; Results shows accuracy, kappa, false-accept/false-reject, ECE, Brier,
AUROC, repeat consistency, calibration charts, confusion matrices, agreement, flagged
items, benchmark health, limitations and cost. The Item explorer filters by all items,
judges disagree, everyone wrong, high-confidence wrong, parse failures, and flagged for
review; click a row to open the item drawer (question, options, candidate answer, all
judge verdicts and confidences).

---

## 4. Experiment 003 in stages

Verified against the v0.4 release; re-check if the app version shown in Settings is not
0.4.0. In v0.3.0, Experiment 003 is a single run with no stage UI.

Experiment 003 enriches for difficulty: a separate **screening model** answers many
items closed-book, the items it got wrong become the **hard pool**, and judges grade a
stratified sample of 100 hard items plus 40 controls. A pilot gate stops the run before
the expensive remainder if the judges do not disagree with the key often enough.

### 4.1 Before you start

1. Start the app (section 1).
2. On Models, add and test your judge connections (three or more families is the
   intent), assign them to Judge 1..N, add a separate Screening connection with a
   different family label, and save roles.
3. Enter every key now. Keys are forgotten on app stop; a staged run that is resumed
   after a restart needs the keys again.
4. Run a dry run first (section 4.10). It is free and mock-only.

### 4.2 The stages, in order

| Stage | Name | What it does | Worst-case calls shown |
|---|---|---|---|
| 1 | `screening` | Screening model answers closed-book in batches of 50; stops at 100 hard items, exhaustion, or the screening cap | the screening cap (lesser of 1000 items and the screening connection's call cap) |
| 2 | `pool` ("Review hard pool") | Loads the dataset, builds the stratified sample, records hard and control counts; no model calls | 0 |
| 3 | `pilot` | First 40 hard items through every judge | pilot items x judges x repeats |
| 4 | gate decision | Automatic after the pilot: count judge-versus-key disagreements and decide continue / stop | n/a |
| 5 | `remaining` | The rest of the sample through every judge | remaining items x judges x repeats |

Each stage card shows its label, worst-case call count, worst-case USD (only when the
judge connections carry prices; otherwise **"cost unavailable, call cap is binding"**),
status (`done` / `pending` / `blocked`), a detail line (stop reason, pool counts or the
gate decision), and a **Confirm & run** button. A stage runs only after you confirm it.
While a stage runs, a **Stop** button is available.

### 4.3 Start the run

1. Go to the start screen (`#/`).
2. Choose a workspace, Experiment **Experiment 003**, a mode and a spend policy. `Mode`
   `Dry run` means mock judges and a mock screener; the other modes are live and spend.
   The spend policy `Dry run only` blocks a live `/run`.
3. Type `/dryrun` (free) or `/run` (live) in the composer and send it.

Starting the run from the composer puts Experiment 003 at its **screening** stage. If the
run shows `awaiting_confirmation`, press the screening stage's **Confirm & run**; every
later stage always waits in the run's **Stages** card for its own confirmation. The
composer's `/run` and `/dryrun` always start Experiment 003 at the screening stage. The
session page links to its run's Stages card.

The app allows **one active run at a time**; an advance call while another run is active
returns 409 ("a run is already active; stop it first").

### 4.4 Screening stage

- **Dry run**: a deterministic mock screener marks items; the file is
  `results/study_runs/<run_id>/screening.jsonl` and everything is labelled mock. No
  network, no spend.
- **Live**: the Screening-role connection answers each item closed-book with a
  letter-only prompt at temperature 0. Screening is adaptive: batches of 50, stopping on
  `target_met` (100 hard items), `exhausted` (every eligible item screened), or
  `cap_reached` (the lesser of 1000 items and the screening connection's call cap), or
  `stopped_by_user` if you press Stop between batches.
- **Real screening writes only item ids and correct/incorrect flags**, to
  `results/experiment_003/screening.jsonl`. No benchmark text, no model output, no key.
  Dry-run screening stays inside the run directory and is labelled as a mock.
- The stage detail shows how many items were screened, how many were hard, and the stop
  reason. Experiments 002 item ids are excluded before screening.

If screening results already exist at `results/experiment_003/screening.jsonl`, a new
live run skips straight past screening to the pool stage.

### 4.5 Review hard pool stage

The pool stage loads the dataset and builds the pre-registered sample from the screening
file: up to 100 hard items (screening misses) plus 40 controls (screened-correct items),
with Experiment 002 item ids excluded. It makes no model calls. Its card shows the hard
and control counts and the hard-pool size available; if fewer than 100 hard items were
found, all of them are used and the shortfall is stated in the detail and the report.

Confirm this stage only after checking the counts: the next stage spends the pilot
budget.

### 4.6 Pilot stage and gate decision

The pilot runs the first 40 hard items through every judge, at one repeat per judge in
the pre-registered design. Then the gate decision is recorded automatically:

- **Continue** if the pilot produced at least 5 judge-versus-key disagreements across
  all judges on those items.
- **Stop** (`pilot_stopped`) if fewer. The run ends, a partial report is written, and no
  correlation claim of any kind may be made from it.

The **Gate decision** card shows the disagreement count, the minimum required and the
decision text. A failed gate marks the remaining stage `blocked` (it cannot run).

### 4.7 Remaining stage and report

Confirming the remaining stage grades the rest of the sample through every judge. When
it finishes, the run is `completed`: the report, `metrics.json`, `items.csv`,
`manifest.json` and the Experiment 003 `review_queue.csv` are written, and the strata
analysis (hard vs control) is recorded in `metrics.json`.

### 4.8 Confirmation, stop reasons and status meanings

Run statuses you will see in the Runs list and run detail:

| Status | Meaning |
|---|---|
| `awaiting_confirmation` | A stage finished; the next stage exists and needs your Confirm |
| `completed` | All confirmed stages finished; report written |
| `pilot_stopped` | Gate failed: run ended, partial report written, no correlation claims |
| `stopped` | You pressed Stop; reports written from the judgements completed so far |
| `budget_stopped` | A call or USD cap bound; partial evidence is kept and reported |
| `blocked` | No usable judge could be constructed |
| `refused` | The plan exceeded the run call cap, or a resume changed the locked config |
| `failed` | An unexpected error in the app's run worker; the error is recorded in `run.json` |

Screening's own stop reason is recorded in `run.json` and the report: `target_met`,
`exhausted`, `cap_reached` or `stopped_by_user`.

A stopped, aborted or pilot-stopped run's judgements cover only the part that ran before
it ended; never read a partial run as a complete result. Resume it (section 4.9) or start
a fresh run if you need the full sample.

### 4.9 Resuming a staged run

Stage state is on disk (`run.json` plus the append-only `judgements.jsonl`), so you can
close the app and continue later:

1. Start the app again and reopen the token link. Saved connections and roles persist.
2. **Re-enter keys** on the Models page for every judge and for the screening
   connection. Keys only ever lived in the old process's memory. If a key is missing,
   the advance call refuses and tells you to re-enter it on the Models page.
3. Open the run (Runs, then the run id) and press the next stage's **Confirm & run**.
   Completed judgements are never repeated.

A resume with a changed design (items, seed, repeats, judges, dataset, dry/live mode) is
refused by the config lock before any call, with the differing fields named. Start a new
run id instead.

### 4.10 Dry run vs live, in one paragraph

Run `.\.venv\Scripts\python.exe -m medeval run exp003 --dry-run` (optional verified flags:
`--n-items`, `--screening-size`) for a free end-to-end pipeline test: mock screener, mock
judges, every stage in one process, no network. A live CLI Experiment 003 run is
deliberately refused — keys live in the app's memory, so live screening and judging are
run from the app. Dry-run data stays under `results/study_runs/<run_id>/`; the real
screening record is `results/experiment_003/screening.jsonl` (item ids and flags only).

---

## 4A. Paper Packs and matrix runs (v0.6)

The **Paper Packs** page in the sidebar is a second runner, separate from
Experiment 002/003. A Paper Pack is a declarative protocol under `papers/<id>/`
(a `paper.yaml`, a local dataset, prompts, graders, metrics and citation). It is
research scaffolding, not a clinical protocol.

### 4A.1 Registry

The registry lists every pack with its task type, version, item count, methodology
tag (faithful reproduction / inspired adaptation / synthetic template) and
validation status. It contains three neutral templates and **five built-in
presets**:

| Preset | Primitive |
|---|---|
| `healthbench-rubric` | rubric-scored open-ended QA; cross-family judge panel |
| `medcalc-bench` | parameter extraction; exact and ±5% numeric grades |
| `medhallu-detect` | 3-class hallucination labels; macro F1/precision, coverage |
| `medxpertqa-text` | hard MCQ; paired McNemar |
| `medsafety-bench` | refusal rate + dual-judge harmfulness |

Presets ship only synthetic `smoke.jsonl` rows tagged
`PIPELINE TEST / SYNTHETIC SMOKE DATA`. Their upstream datasets are **not**
bundled: `paper.yaml` declares the source URL/HF reference and licence, and an
optional `loader.py` can materialise a licensed export into the git-ignored
`.medeval_data/` after schema verification:

```powershell
python papers/medcalc-bench/loader.py --source path\to\licensed-export.jsonl --force
python papers/medcalc-bench/loader.py --check
```

Open a preset in the builder to see its "Upstream data is not bundled" card.
The archived Experiment 002/003 runs appear below the packs as **read-only**
entries; they are never re-run as packs.

### 4A.2 Matrix builder

Open a pack to reach the builder:

1. Tick one or more **target models** (models under test). They are your saved
   connections; nothing is preselected.
2. Tick **graders/judges**. A rubric pack needs at least one judge. A note tells
   you whether the selected judges span different families (only then can
   cross-family error correlation be estimated).
3. Set the item count and the run-wide call/USD caps. **Dry run** is on by
   default and badged `PIPELINE TEST ONLY`.
4. Read the **pre-flight plan card**: planned calls (target + judge), cached
   target outputs, worst-case tokens and cost. Cost is unavailable when a
   connection declares no prices; the call cap is then the binding limit.
5. Start. A dry run uses labelled mock targets/judges and makes no network call.

### 4A.3 Run page

- **Progress** refreshes while the run is active.
- **Stop** saves every completed item. **Resume** continues the same run id and
  never repeats a completed `(target, item)` or grader.
- The **head-to-head and disagreement explorer** filters items by *All*, *Model
  disagreements*, *Judge disagreements*, *Format failures* and *Errors*, and shows
  each target's output, deterministic checks and judge rationales side by side.
- **Export bundle** downloads the deterministic zip (`report.md`, LaTeX, BibTeX,
  metrics, per-item CSV, manifest, run manifest).
- **Offline re-grade** re-scores the cached target outputs with the current pack
  and judges; it reports `target calls: 0` and never re-queries a target model.

### 4A.4 Command line

```powershell
python -m medeval paper list
python -m medeval paper validate template-clinical-mcq
python -m medeval matrix run --paper template-clinical-mcq --dry-run --n-items 12
python -m medeval matrix show <run_id>
python -m medeval regrade <run_id> --paper template-clinical-mcq
```

For a live matrix run, pass `--connections connections.yaml` (entries with
`role: target` and `role: judge`; each names `key_env` or `api_key_env`) plus
`--confirm-live`. The plan is printed first and every call is cap-checked.

### 4A.5 What the exports contain

`report.md` starts with the `MEDICAL_NOTICE`, then the methodology card, summary
table with 95% CIs, pairwise comparisons (McNemar or paired bootstrap), judge
agreement and cross-family error correlation, cost/latency and explicit
limitations. `results_table.tex` starts with `% MEDICAL_NOTICE`.
`citations.bib` combines the medeval-lab citation with the pack's own BibTeX.
`bundle.zip` is byte-reproducible and contains no API key, no prompt and no raw
dataset text.

## 5. Caps and spend safety

### 5.1 Caps that exist

- **Per-connection call cap and USD cap** (Models page; defaults 500 calls / $5): checked
  on every call of that judge connection, on top of the run total. A connection-backed
  judge must satisfy both.
- **Run call cap**: `--max-api-calls` on the CLI and the "Cap on network calls" field on
  the New run page (default 500). The plan is refused before the first call if it needs
  more calls than the cap.
- **Run USD cap**: from `MEDEVAL_MAX_USD` (default 5). It is enforced by the process
  spend guard, which estimates the next call before making it and stops the run
  (`budget_stopped`) rather than overshooting.
- **Screening cap**: Experiment 003 screening stops at the lesser of 1000 items and the
  screening connection's call cap (section 4.4).
- **Cache hits are free**: a repeated identical prompt reads the response cache and is
  not billed or counted as a network call.

Caps are process-local: they hold for the run process, not as a provider-side budget.

### 5.2 "Cost unavailable, call cap is binding"

Prices are never fetched or invented. A USD figure appears only when prices are
configured (connection price fields for Experiment 003 stages; price environment
variables for the Experiment 002 plan). When they are not, the plan/stage card says the
cost is unavailable and the call cap is the only enforced ceiling. Read that as: "I can
stop this at N calls, but I cannot tell you the dollar amount in advance." Fill in the
optional per-connection prices if you need a USD estimate.

Worst-case figures are deliberately conservative: characters/4 for prompt tokens and
the full `max_tokens` allowance for every reply, not the expected reply length.

### 5.3 Stop a run

- **App run**: run detail page, **Stop run** (or the Stages card's **Stop** while a
  stage runs). The run finishes the call in flight, writes reports from the judgements
  completed so far, and records `stopped`.
- **CLI run**: Ctrl+C in the terminal. Resume with `--resume <run_id>`; completed calls
  are not repeated.
- **Terminal-started run in the app**: the Stop button is rejected with 409; stop the
  terminal process (Ctrl+C) instead.
- A cap stop is not hidden: the status is `budget_stopped`, and the report says so.

### 5.4 Checks before you spend

1. Start with a dry run of the same configuration.
2. Check the plan card or stages card: calls, cap, worst-case cost (or its absence),
   judges and repeats.
3. Confirm keys are set (`key set ····last4`) and Test each connection.
4. Read the live confirmation dialog on the New run page; for Experiment 003, confirm
   each stage deliberately rather than clicking through.
5. Watch progress and use Stop; an interrupted run can be resumed.

Never put a key in a shell history line, note, issue, commit or this repository's docs.
The docs never contain one.

---

## 6. Review queue and flags

### 6.1 Experiment 002 flag rule

With two judges, an item is flagged **possible key error** when both judges disagree with
the key at confidence >= 80 on the primary repeat. With one judge that rule cannot be
applied, so a weaker fallback flags a single judge's confident (>= 90) disagreement for
manual review only; no item is promoted to "possible key error". The Results page's
"Flagged for review" card states which rule was applied, the Item explorer has a
"Flagged for review" filter, and `items.csv` has a `flagged_for_manual_review` column.
No key is changed by any of this.

### 6.2 Experiment 003 review queue rule

An Experiment 003 item enters the pre-registered review queue when **at least two judges,
and all judges that returned a verdict for that item**, disagree with the key at
confidence >= 90 on **every** repeat. The queue is written to
`results/study_runs/<run_id>/review_queue.csv` and contains **item ids only**; the same
rule and the flagged rows are recorded under `review_queue` in `metrics.json`. The
report's shared "Flagged for review" section uses the Experiment 002 rule; for
Experiment 003 the pre-registered queue is the `review_queue.csv`/`metrics.json` one.

### 6.3 A flag is not a key-error finding

A flag means confident judges disagreed with the key. It is a prompt for a human to
look, **not** a finding that the key is wrong: the judges may be the ones who are wrong.
Reasons to keep that distinction:

- The official key is an imperfect reference; no key medication audit was performed.
- No clinician reviewed any item, candidate answer or verdict.
- Hard-pool items were selected precisely because a screening model got them wrong, so
  key errors are *more likely* there — still not proven.
- Judges are LLMs; shared training data can produce shared errors.

Experiment 003 reports also carry the single-reviewer caveat: any review label recorded
locally is one reviewer's opinion, not clinical validation and not a reference standard.

### 6.4 Review queue, labels and notes

Verified against the v0.4 release; re-check if the app version shown in Settings is not
0.4.0.

The **Review queue** is where a human reads the flagged items. Open it from the run's
Stage plan or the Results page (it is also reachable from the item drawer) and work
through the queue by keyboard: previous/next item, set a label, add a free-text note.

The labels are exactly: `key looks correct`, `key looks wrong`, `ambiguous`, `unsure`.

- Labels and notes go to **one git-ignored local file**, `data/review_notes.json`.
  Nothing about them is committed, uploaded, or written into run records, and they are
  **excluded from headline metrics**.
- A clearly labelled **sensitivity table** re-computes the headline numbers with the
  items you labelled `key looks wrong` removed. It is a sensitivity analysis, never the
  headline result.
- Every surface shows the same caveat: these are **one reviewer's opinion**, not a
  reference standard and not clinical validation. There is no clinician and no second
  rater in this project.
- The local API is `GET/POST /api/runs/{run_id}/review` (item ids, label, note); its
  response never includes benchmark text.

If the Review queue or the label controls are missing from your build, it predates v0.4:
use `review_queue.csv` as a worklist and keep notes outside the repository (see
`VERIFY_ME.md`).

The separate local note store that exists in every v0.3+ build is the Papers library's
`data/paper_notes.json` (git-ignored): one reader's comments on papers, not clinical
validation and not a reference standard either.

### 6.5 What to do with the queue

Open the Review queue (or `review_queue.csv` and the Item explorer if you are on an older
build) and inspect each item's question, options, candidate answer and every judge's
verdict/confidence. Record your labels and notes in the queue; they are kept locally and
stay out of the committed results. A reviewer may conclude either side is wrong; nothing
in this repository changes the key. Do not label the queue as confirmed key errors
anywhere (see `VERIFY_ME.md`).

---

## 7. Report and export

Verified against the v0.4.1 release; re-check if the app version shown in Settings is not
0.4.1. Both commands exist in this build:

`powershell
.\.venv\Scripts\python.exe -m medeval report exp003      # regenerate report.md, no model call
.\.venv\Scripts\python.exe -m medeval export <run_id>    # zip of ids and aggregates only
`

### 7.1 Files a run writes

Every run directory is `results/study_runs/<run_id>/` and contains:

| File | Contents | Committed? |
|---|---|---|
| `report.md` | Auto-generated report: design, per-judge metrics with intervals, agreement, flags, benchmark health, cost, limitations, stop reasons | yes (ids and aggregates only) |
| `metrics.json` | All metrics, intervals, flags, statuses; Experiment 003 adds `strata` and `review_queue` | yes |
| `items.csv` | Per-item ids, condition, chosen letter, verdicts and confidences, flag column — no question text | yes |
| `manifest.json` | Git commit, versions, models, seeds, prompt hashes, usage | yes |
| `review_queue.csv` | Experiment 003 flagged item ids only | yes |
| `run.json` | Status, config, plan, stage state, stop reasons | yes |
| `judgements.jsonl` | Append-only verdict log (resume source) | yes |
| `items.jsonl`, `candidates.jsonl`, `calls.jsonl`, `cache.json`, `screening_cache.json` | Local-only item text, candidates, prompts and cache | no (git-ignored) |

### 7.2 Regenerate a report with `medeval report`

```powershell
.\.venv\Scripts\python.exe -m medeval report exp002-main
.\.venv\Scripts\python.exe -m medeval report exp003
```

`medeval report <run_id>` re-derives every number, interval, limitation and stop reason
from the stored verdicts and rewrites the run's report. `medeval report exp003` targets
the newest Experiment 003 run when you do not want to name a run id. It never makes a
model call and never needs a key.

The older `rebuild` command also re-scores metrics and rewrites the report without a
model call:

```powershell
.\.venv\Scripts\python.exe -m medeval rebuild exp002-main
```

### 7.3 Export a bundle

```powershell
.\.venv\Scripts\python.exe -m medeval export exp002-main --out .\exp002-main.zip
```

The Results page also has an **Export** button. The zip contains:

- `metrics.json`
- `items.csv` (ids, verdicts and aggregates only)
- `review_queue.csv` (Experiment 003 item ids only)
- `manifest.json`
- `report.md`
- `README.txt` describing exactly what is and is not in the bundle

The app writes the zip to the git-ignored `results/exports/<run_id>-bundle.zip`; the
`--out` option lets the CLI write it wherever you ask.

Reviewer notes are **excluded by default**. A checkbox can include them, with a warning
that notes may quote item text; leave it unticked if the bundle will leave your machine.
**No benchmark item text and no API key is ever in an export.**

### 7.4 Where to read results outside the app

`results/study_runs/<run_id>/report.md` is the full written report; `metrics.json` is the
machine-readable version. The top-level `REPORT.md`, `REPORT_v0.2.md`,
`results/audit/` and `results/experiment_001/` are project-level reports, not a run's
report. A top-level results report for a release is published only after a real live run
has happened; do not expect one before that.

---

## 8. Test center, first-run card, doctor, Settings

Verified against the v0.4 release; re-check if the app version shown in Settings is not
0.4.0 (the first-run card and `medeval doctor` are not in v0.3.0).

### 8.1 Test center

Sidebar: **Tests**. The browser can only choose from a fixed, server-side allow-list of
commands with fixed arguments, a fixed working directory, no shell and a 1200 s timeout.
It never sends a command string. The list (exact labels):

- `pytest` — pytest (full suite)
- `ruff` — ruff check
- `secret-scan` — secret scan (tracked files)
- `traceability` — literature traceability check
- `key-isolation` — key isolation tests
- `dry-run-exp002` — Experiment 002 dry run (mock, no network)
- `dry-run-exp003` — Experiment 003 dry run (mock, no network)

Each run reports exit code, duration and (for pytest) passed/failed/skipped counts, with
the last ~40 lines of output. The history keeps counts, ids and duration only, not output
text. **Tests never make live paid calls.** The page also shows the pre-flight checklist:
key-isolation test passed, config lock active, caps set, prices set or "unavailable, call
cap is binding", and a dry run done for this configuration.

### 8.2 First-run card

On the start screen, a dismissible first-run card walks through the first session:

1. add a connection on the Models page;
2. test it (no spend) and, if needed, the explicit one-token call;
3. run a dry run;
4. open the plan card and read the calls/cost before going live.

If you dismiss it, it can be reopened from Settings.

### 8.3 `medeval doctor`

```powershell
.\.venv\Scripts\python.exe -m medeval doctor
```

The doctor checks the Python version, installed dependencies, a free port for the app,
the launch-token flow, git status, Ollama reachability and runs a fast self-test. It never
prints or stores a secret. Run it first whenever something seems wrong, then see
`TROUBLESHOOTING.md`.

### 8.4 Settings

Sidebar: **Settings**. It is local status only; no key is ever displayed.

- **J1 / J2 status**: whether credentials are present (never the key), model and base URL
  for the CLI judge pair; Ollama reachability and model list, or install instructions.
- **Paths and limits**: repo root, data, results and run roots; call and USD caps; price
  status and source URL.
- **Datasets**: key, Hugging Face repo, licence and whether the text is redistributed
  (always "no").
- **Backend resolution**: which backend the CLI will resolve and why.
- Environment files loaded (only populated when the app was started with `--use-env`).

The Settings page is also where the first-run card can be reopened and where you check
the app version against the one this guide documents. If your build shows no version,
read it without starting a run:

```powershell
.\.venv\Scripts\python.exe -c "from medeval_core.version import __version__; print(__version__)"
```

## Datasets: fetch once, or let a dry run use the synthetic sample

A **real** run needs the public dataset downloaded once:

```powershell
cd <repo>
.\.venv\Scripts\python.exe -m medeval data status        # what is cached, with licences
.\.venv\Scripts\python.exe -m medeval data fetch medqa   # download + cache, prints the licence
```

`data fetch` prints the licence (name, redistribute flag, evidence URL) and the
source hash, and keeps item text in the git-ignored dataset cache. This is a public
data download, not a model call; it spends nothing.

A **dry run** does not need it. If the dataset cannot be read (no `datasets`
package, no network, no cache) a dry run falls back to a small built-in synthetic
sample and labels everything **PIPELINE TEST ONLY**: the items are fabricated, so
the numbers prove the pipeline runs and describe no model. The fallback is recorded
in `run.json`, `metrics.json` and `report.md`.

A real run never falls back. It stops with status `blocked` and a reason naming the
exact command to run, which the run page shows as a card.

## The review queue and its sensitivity table

Flagged items appear in the run's Review queue. The four labels are `key looks
correct`, `key looks wrong`, `ambiguous` and `unsure`; `n`/`p` move, `1`-`4`
label, and notes are free text. Labels and notes live only in the git-ignored
`data/review_notes.json`: they are one reviewer's opinion, they never enter the
headline metrics, and `medeval report` adds a clearly labelled sensitivity table
with `key looks wrong` items removed.

## Export a bundle

Results page -> **Export bundle** (or `medeval export <run_id>`). The zip holds
`metrics.json`, `items.csv` (ids only), `review_queue.csv` (ids only),
`manifest.json`, `report.md` and a README. Reviewer notes are excluded unless you
explicitly ask, because they may quote item text. No item text and no keys ever
appear in a bundle.
