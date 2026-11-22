# medeval-lab — troubleshooting

Each entry is symptom, cause, exact fix. Commands are PowerShell and assume you are in
the repository root; all Python commands use the repository virtual environment.

Run this first when something seems wrong:

```powershell
.\.venv\Scripts\python.exe -m medeval doctor
```

`medeval doctor` (v0.4) checks the Python version, dependencies, a free app port, the
launch-token flow, git status, Ollama reachability and runs a fast self-test, without
printing or storing a secret. If the doctor command does not exist, you are on an older
release; check the version shown in Settings (`0.4.0` for the version this guide covers).

Two facts that explain many failures:

- The app's API is loopback-only and every `/api/*` call needs the launch token from the
  current terminal. A URL without it, or one from a previous launch, returns 403.
- API keys live only in the app's process memory. Restarting the app forgets them, and
  the Models page only ever shows `key set ····last4`.

---

## 1. Page says "This server needs its launch token" (missing or invalid token)

**Symptom.** The app page shows "This server needs its launch token", or an API call
returns 403 with detail "missing or invalid launch token; open the app with medeval ui".
The Runs/Models pages are empty or error.

**Cause.** The tab has no valid token for the process that is answering: the URL was
opened without `#token=...`, or it was copied from an earlier launch. Tokens are
generated fresh on every launch and stored only in that tab's `sessionStorage`.

**Fix.** Reopen the link the running terminal printed. The terminal shows the URL and the
token:

```text
[ui] medeval-lab serving at http://127.0.0.1:8765/ (loopback only)
[ui] launch token: <token for this launch>
```

Open the URL with the fragment appended in that tab:

```powershell
Start-Process "http://127.0.0.1:8765/#token=<paste the printed token here>"
```

or simply use the browser tab the app opened. If you cannot find the terminal, restart
the app and use the new link (this also forgets any keys, so re-enter them):

```powershell
.\.venv\Scripts\python.exe -m medeval ui
```

If the app started on a different port than you expected, use the port in the printed
line, not the old bookmark. Do not paste the token into a file, note, chat or commit.

---

## 2. A stale medeval server is still on the port (old page, "token required")

**Symptom.** Opening `http://127.0.0.1:8765/` shows an old app shell or "token required",
but the terminal you just started says it is on 8766 (or another port). Two servers are
answering.

**Cause.** An earlier `medeval ui` process is still running and bound to 8765. Its token
differs from the new launch, so the old tab/post is rejected or shows stale UI.

**Fix.** Identify the process listening on the port, confirm it is the app, then stop it.
Verify the resolved absolute path before stopping anything.

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Use the `OwningProcess` id to see what it is:

```powershell
Get-Process -Id <pid> | Select-Object Id, ProcessName, Path, StartTime
```

Confirm it is this app before stopping it (a page check that does not reveal the token):

```powershell
(Invoke-WebRequest "http://127.0.0.1:8765/" -UseBasicParsing).StatusCode
```

A `200` serving the medeval-lab shell means it is a stale medeval server. Stop exactly
that process:

```powershell
Stop-Process -Id <pid> -Force
```

Then start one app and use the URL/token it prints:

```powershell
.\.venv\Scripts\python.exe -m medeval ui
```

If you no longer know which terminal owns it, list candidate Python processes and match
by start time and path before killing anything:

```powershell
Get-Process python* -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, Path
```

In v0.4, `medeval ui` itself says whether a busy port is a stale medeval server (with the
exact stop command) or another program, then picks the next free port and prints the new
URL. `medeval doctor` also checks that a port is free.

---

## 3. 403 "this server only accepts loopback Host headers" or "cross-site requests are not allowed"

**Symptom.** The browser or an API call returns 403 with one of those details.

**Cause.** The app is loopback-only by design:

- a non-loopback `Host` header (LAN IP, machine name, reverse proxy, port mismatch) is
  refused, which blocks DNS-rebinding;
- a cross-site `Origin` header is refused, so another site cannot drive the API.

**Fix.** Use exactly the loopback URL the app printed, including its port:
`http://127.0.0.1:<port>/` or `http://localhost:<port>/`. Do not put a proxy, tunnel, LAN
address or custom host name in front of it, and do not call the API from a page on
another origin. If you changed the port, update the URL.

Check the app's own answer with the current token (the token is not an API key and is
fine on your own command line):

```powershell
$headers = @{ "X-Medeval-Token" = "<token from the running terminal>" }
Invoke-RestMethod "http://127.0.0.1:8765/api/health" -Headers $headers
```

`{"ok":true,...}` means the host/token are correct.

---

## 4. "Key rejected by provider" (HTTP 401 or 403)

**Symptom.** Test connection shows a red banner starting `HTTP 401` or `HTTP 403`; live
judgements are recorded with `api_error: HTTP 401/403`; the report lists API errors and
the run's accuracy is scored only where a verdict came back.

**Cause.** The provider refused the credential: wrong or expired key, key for a different
provider, missing key, insufficient permissions/quota, or the wrong base URL/account.
Client errors other than 429 are not retried, so they are recorded straight away.

**Fix.** Re-enter the key on the Models page:

1. **Forget key** on the connection (removes it from memory only).
2. Paste the correct key into the API key field, Save, then **Test** again.
3. Confirm the key works at the provider (dashboard, model access), and that the model id
   and base URL belong to that provider.

In the terminal you can see whether the app holds a key for a connection (it prints only
a boolean/last-4, never the key):

```powershell
$headers = @{ "X-Medeval-Token" = "<token>" }
Invoke-RestMethod "http://127.0.0.1:8765/api/connections" -Headers $headers |
    Select-Object name, key_set, key_last4
```

Restarting the app forgets keys; that is expected. For a resumed staged run, re-enter
every key before confirming the next stage; the advance request refuses with a message
telling you to re-enter the key. For a CLI Experiment 002 run, set the `MEDEVAL_*`
credential variables in the environment or the git-ignored `.env`; the CLI reads those,
not the app's memory keys. Never put a key in a command you will save, and never commit
`.env`.

---

## 5. Rate limited (HTTP 429, Retry-After)

**Symptom.** Judgements recorded with `api_error: HTTP 429` (possibly
`Rate limit exceeded` or `Too Many Requests`); Test connection may show HTTP 429; some
items end up without a verdict.

**Cause.** The provider's requests/tokens-per-minute quota, or a per-key quota, was hit.
The transport retries 429 with a growing fixed backoff (2 s, then 4 s, capped at 8 s) up
to `MEDEVAL_RETRIES` retries (default 2). This build does **not** parse or honour the
provider's `Retry-After` header; if the provider asks for a longer wait than the backoff,
the call still fails.

**Fix.** Wait for the rate window to clear, then re-run/resume so completed calls are not
repeated (they are in `judgements.jsonl` and the cache):

```powershell
Start-Sleep -Seconds 60
.\.venv\Scripts\python.exe -m medeval run exp002 --resume <run_id>
```

For the app, restart with a higher retry count in the same terminal, then resume/re-run:

```powershell
$env:MEDEVAL_RETRIES = "4"
.\.venv\Scripts\python.exe -m medeval ui
```

Also reduce the load (fewer items, fewer judges, fewer repeats), check the provider's
usage dashboard and raise the account quota if this is a recurring cap. A run stopped by
a cap is recorded as `budget_stopped`, never hidden.

---

## 6. Empty replies from a reasoning model (max_tokens exhausted)

**Symptom.** Judgements fail with `parse_error: empty_output` / `empty_reply`, or the
report says items could not be scored. The CLI prints a warning that calls "used the
whole max_tokens budget (N) on hidden reasoning". The v0.2 `exp002-full` run was aborted
this way: thinking mode consumed the entire reply allowance and returned no visible JSON.

**Cause.** For reasoning/thinking models, hidden reasoning tokens count against
`max_tokens`. If the budget is too small, the visible answer is empty or unparseable. The
CLI settings default (`MEDEVAL_MAX_TOKENS`) is 512; a new app connection defaults to 2048.

**Fix for a CLI Experiment 002 run.** Raise the reply budget in the launching terminal,
then run or resume:

```powershell
$env:MEDEVAL_MAX_TOKENS = "2048"
.\.venv\Scripts\python.exe -m medeval run exp002 --resume <run_id>
```

**Fix for app connections.** New connections already use `max_tokens = 2048`, and the
Models page in this build has no max_tokens field. To raise it, stop the app, edit the
git-ignored local metadata (no key is in this file), and restart. Start from a backup copy:

```powershell
Copy-Item .\data\connections.json .\data\connections.json.bak
$path = (Resolve-Path .\data\connections.json).Path
$data = Get-Content $path -Raw | ConvertFrom-Json
foreach ($connection in $data.connections) { $connection.max_tokens = 4096 }
[System.IO.File]::WriteAllText($path, ($data | ConvertTo-Json -Depth 10))
```

Restart the app and re-enter keys (memory only). Alternatively choose a model without a
hidden reasoning budget. The runner emits a `reasoning_warning` in the run's usage and the
report when it detects truncated replies; check `run.json` under `usage`.

---

## 7. Timeouts and connection resets

**Symptom.** Judgements recorded with `api_error: ...ReadTimeout`, `ConnectTimeout`,
`ConnectionError`, `ConnectionResetError` or similar; a call hangs for the timeout period
and the run continues after retries.

**Cause.** A slow endpoint (long reasoning replies), a flaky network/VPN/proxy, a provider
outage, or a timeout that is too low. The client timeout is `MEDEVAL_TIMEOUT_S` (default
60 s); transient errors are retried with backoff up to `MEDEVAL_RETRIES` retries.

**Fix.** Raise the timeout in the terminal that launches the app or the CLI, then
re-run/resume:

```powershell
$env:MEDEVAL_TIMEOUT_S = "180"
.\.venv\Scripts\python.exe -m medeval ui
```

For a CLI run, set the same variable before `medeval run exp002 --resume <run_id>`. Check
the network path without exposing a key by using the app's **Test** button (it lists models
over `/models`, no spend), or test the root host:

```powershell
Test-NetConnection api.deepseek.com -Port 443
```

If a proxy is in the way, configure it for the whole session (`$env:HTTPS_PROXY = ...`)
before starting the app, then retry. Completed calls are never repeated after a resume.

The app's Test connection uses a fixed 20 s probe timeout; a slow endpoint can pass a
run's larger timeout and still fail the probe. Retry Test, or rely on the one-token call
when the endpoint publishes no model list.

---

## 8. Test connection says no models visible

**Symptom.** Test connection shows "The endpoint answered but published no model list"
or a non-JSON/404 error, so you cannot pick a model.

**Cause.** Not every OpenAI-compatible endpoint implements `GET /models`. Or the base URL
is wrong: the probe calls `{base}/models`, inserting `/v1` unless the base URL already
ends in `/v1`.

**Fix.**

1. Type the exact model id into the connection's **Model** field and Save.
2. In the no-model-list card, click **"Run one-token test call (explicit confirm)"**. It
   makes a single real call with `max_tokens=1` and reports the model id the endpoint
   returned. That is a tiny spend, but it is a real call — only run it knowingly.
3. If the error is 404/HTML, fix the base URL: provider root or the `/v1` root, for
   example `https://openrouter.ai/api/v1` (do not add a second `/v1`). Then Test again.

---

## 9. A local model server is not reachable (Ollama and friends)

**Symptom.** "Test connection" for an Ollama (or LM Studio/vLLM) connection fails; a run
records that judge as unavailable; the report says the cross-family question was not
measured. In v0.5 there is no default local judge: a run uses only the connections you
assigned, and none is substituted automatically.

**Cause.** The server is not installed, not running, not listening on the expected port,
or the configured model is not pulled.

**Fix.** Install and start it, pull a small model, and check the endpoint:

```powershell
winget install --id Ollama.Ollama
ollama serve
ollama pull llama3.2:3b
Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing
```

In the app, add a connection with the Ollama preset (`http://localhost:11434/v1`), press
Test and pick a model it lists, then assign it a judge role. For `connections.yaml`:

```yaml
connections:
  - name: "local judge"
    base_url: "http://127.0.0.1:11434/v1"
    model: "llama3.2:3b"
    family_label: "llama"
    role: judge
    # no key_env needed: a loopback endpoint needs no key
```

The pull is about 2 GB and CPU inference is fine. If the model name differs, use one from
the `/api/tags` output. A run without a local judge still runs with the judges you did
assign: it is reported as one judge, never as a jury.

---

## 10. A run looks stuck

**Symptom.** Progress stops moving, the status stays `running`, or a staged Experiment 003
run does nothing after a stage. It may simply be waiting for you.

**Cause.** A long call plus retries, rate-limit backoff, a slow local model, a stage
waiting at `awaiting_confirmation`, or a missing key that an advance call refuses.

**Fix.** Check the actual progress on disk instead of guessing. The run detail page tails
`judgements.jsonl` once a second; the same facts are readable in PowerShell (replace
`<run_id>`):

```powershell
Get-Content .\results\study_runs\<run_id>\run.json -Raw
(Get-Content .\results\study_runs\<run_id>\judgements.jsonl | Measure-Object -Line).Lines
Get-Content .\results\study_runs\<run_id>\calls.jsonl -Tail 5
```

- Status `awaiting_confirmation`: not stuck. Open the run, re-enter keys if the app was
  restarted, and press the next stage's **Confirm & run**.
- Status `running` with no new judgements for a long time: check `calls.jsonl` tail and
  the timeout item above; the call may be retrying.
- Stop it from the run detail page (**Stop run**, or **Stop** while a stage runs). For a
  CLI run, press Ctrl+C. Reports are then written from completed judgements; resume with
  `--resume <run_id>`.
- For a terminal-started run, the app's Stop is rejected with 409 because the app does not
  own the worker; stop the terminal process instead.

---

## 11. Windows long paths

**Symptom.** `WinError 206` ("filename or extension is too long"), `WinError 3`
(path not found) or `WinError 5` (access denied) during install, dataset download or test
runs, especially under a deep OneDrive path.

**Cause.** Windows' 260-character path limit (unless long paths are enabled), long
Hugging Face cache file names derived from deep paths, and `git` refusing long paths.

**Fix.** Enable long paths machine-wide (administrator terminal), enable git long paths,
then reopen the terminal:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
    -Name LongPathsEnabled -Value 1 -PropertyType DWord -Force
git config --global core.longpaths true
```

`run_all.ps1` already avoids the worst case by setting a relative dataset cache
(`HF_DATASETS_CACHE=hf`) and an in-repo Matplotlib cache, so run pipeline commands from
the repository root. If failures persist, move the checkout shallower, for example
`C:\medeval-lab` (see the OneDrive item below).

---

## 12. `pip install` fails under a OneDrive path

**Symptom.** The editable install or dependency install fails while the repository lives
under `OneDrive\...`: `WinError 206`/`WinError 5`, permission errors, files locked or
"being used by another process", or installs that are very slow.

**Cause.** OneDrive sync holds files open, the path is long, and restricted Windows
tokens/ACLs can block temp or cache files. The project notes this in `VERIFY_ME.md`.

**Fix.**

1. Pause OneDrive syncing for the folder (OneDrive tray icon: Pause syncing) or move the
   repository outside OneDrive, for example `C:\medeval-lab`.
2. Re-run the installs the repository supports, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-input -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e ".[dev]"
```

`run_all.ps1` does the same requirements + editable install (with no build isolation) and
falls back to a `.pth` link for the separate `lm-evaluation-harness` checkout. The
harness is not a PyPI dependency; point `MEDEVAL_HARNESS_PATH` at your checkout, or let
the run skip harness-dependent work.

---

## 13. `pytest` fails on a fresh clone

**Symptom.** `pytest` is not found, or tests fail to import (`ModuleNotFoundError:
httpx`, FastAPI `TestClient` errors), or the harness tests error. Dataset-dependent
stages (audit, study runs) fail separately with download/cache errors.

**Cause.** The runtime install (`pip install -e .`) does not include the test tooling;
the `dev` extra adds `pytest`, `pytest-cov`, `httpx` and `ruff`. The harness tests need
the separate `lm_eval` checkout and are skipped when it is missing. The unit suite is
offline by design (network is refused except for marked loopback fake-server tests), but
the pipeline commands do need the dataset cache and network on first use.

**Fix.** Install the dev extra and run the suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

or use the one-command reproduction, which creates the venv, installs, lints and tests:

```powershell
.\run_all.cmd
```

If only dataset stages fail, run from the repository root so the relative cache is used,
allow the first download, and keep the cache inside the repo:

```powershell
$env:HF_DATASETS_CACHE = "hf"
.\.venv\Scripts\python.exe -m medeval audit
```

On restricted machines, also set the caches that `run_all.ps1` sets:

```powershell
$env:TEMP = ".\.tmp"; $env:TMP = ".\.tmp"
$env:MPLCONFIGDIR = ".\.tmp\mpl"
```

Tests use a repository-local `tmp_path` fixture and disable the pytest cache because
owner-only temp directories can be unreadable under restricted Windows tokens; that is
expected and not a failure.
