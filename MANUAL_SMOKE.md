# MANUAL_SMOKE.md — 15-minute click-through

Use this when a browser could not be driven automatically, or before showing the
app to anyone. It covers every flow the automated browser suite marks skipped.
No step spends money: live-provider steps are clearly marked and need your own
key and an explicit confirmation.

Start the app and keep the tab:

```powershell
cd <repo>
.\.venv\Scripts\python.exe -m medeval ui
```

The printed address carries a one-launch token in the `#` fragment. If a page says
the launch token is missing, you are on an old tab from a previous launch — open
the address the current terminal printed.

## Steps

1. **Start screen.** The heading reads "Describe an evaluation" with a Preview
   badge, a Workspace/Mode/Experiment chip row, a composer, and a "First run:
   four steps" card. PASS if all four are visible.
2. **First-run card.** Click *Hide this card*; reload; the card is gone. Go to
   Settings → *Show the first-run card again*; reload; it is back.
3. **Sidebar.** Workspace folders are listed, including **Experiment 002** with
   the old runs (`exp002-main`, …) as sessions, each with a relative time.
4. **New session by note.** Type "Hypothesis: hard items produce more disputes"
   and press Send. A session page opens with your note as the first card and no
   model call (Settings → prices unchanged; nothing spent).
5. **Slash commands.** Type `/` in the composer: a list shows `/run`, `/dryrun`,
   `/test`, `/papers`, `/report`. Type `/papers` + Send → the Papers page opens.
   Repeat for `/test` → Test center, `/report` → the newest run's results.
6. **Chords and palette.** On any page press `Ctrl+K`, type `papers`, Enter → the
   Papers page opens. Press `g` then `r` → Runs. Press `g` then `n` → New run.
   Press `g` then `s` → Settings. Then click into the composer and type `gr` —
   it must stay text (no navigation).
7. **Theme.** Click Light, then Dark, then Auto. Reload between each: the choice
   survives. Both themes should keep body text clearly readable.
8. **Models — add a connection.** Models → Add a connection: the preset dropdown
   starts on **Choose a provider** and *Save connection* is disabled until you pick
   one (v0.5.1). Pick the *Custom* preset: the base URL fills in and Save enables.
   Set a name, base URL (any OpenAI-compatible endpoint), model, family label, and
   paste a key. Save. The row shows `key set ····` and the last four characters
   only — never the whole key.
9. **Test connection.** Press *Test* on that row. With a reachable endpoint you
   should see "Verified, N models visible"; with an endpoint that publishes no
   model list you get a clear message and the option of a one-token test call
   behind an explicit confirm. Nothing is spent by listing models.
10. **Roles and warnings.** On Models, assign Judge 1 and Judge 2 to two
    connections **from different families**. Assign Screening to a third
    connection. Now set Screening to the same connection as Judge 1 → a red
    banner says the screening model must not also be a judge. Set Judge 2 to a
    connection with the same family label as Judge 1 → an amber banner says the
    cross-family correlation cannot be measured.
11. **Plan card.** Runs → *New run* (or `/dryrun` in the composer). Before
    starting, the plan shows the call count and the worst-case cost, or
    "cost unavailable, call cap is binding" when no prices are set.
12. **Dry run to completion.** Press *Dry run*. The run page shows progress, a
    live log, and a banner "Dry run: 0 network calls, US$0". When it completes,
    Results opens with metrics and a PIPELINE TEST ONLY label.
13. **Stages card (Experiment 003).** Start an Experiment 003 dry run from the
    start screen (Mode = Dry run). On the run page the **Stages** card lists
    Screening, Review hard pool, Pilot, Remaining with a worst-case call count
    each; Screening shows its stop reason (`target_met`, `exhausted`,
    `cap_reached` or `stopped_by_user`). Advance one stage at a time with
    *Confirm & run*; the Gate decision card appears after the pilot.
14. **Live run (costs money — optional).** With two judge connections assigned,
    set Mode to Pilot or Full, confirm the plan, and start. Watch the
    per-connection and total caps in Settings; press *Stop* mid-run to check that
    it finishes the call in flight and still writes a report.
15. **Resume.** After a Stop (or after closing the app), press *Resume run* on
    the run page. Completed calls are never repeated. If the app was restarted,
    re-enter the keys on Models first; the app refuses with a message naming the
    connection whose key it no longer holds.
16. **Reasoning-model warning.** If a judge's replies come back empty and its
    `max_tokens` is small, the run should warn that hidden reasoning consumed the
    budget and recommend raising `max_tokens`. (Trigger with a thinking model and
    a low max_tokens.)
17. **Stop and resume checklist.** Stopped run: status `stopped`, a reason
    banner, and **no headline metric**. Resumed run: continues from where it
    stopped, ending `completed`.
18. **Items and flags.** Item explorer → filter *Flagged for review*. Open one
    item: the drawer says the flag is a prompt to look, not a finding that the
    key is wrong.
19. **Review queue.** Run page → *Review flagged items*. Label one item
    `key looks wrong` with a note; move with Next/Previous and the `n`/`p` keys,
    label with `1`–`4`. Reload: the label persists. It is stored locally and is
    labelled as one reviewer's opinion.
20. **Papers by DOI.** Papers → paste a DOI (for example a known-good one) →
    *Fetch metadata*. The page announces the outbound request, then shows the
    title and year and an *Add to registry* button. Add it; the row appears with
    `verified=no` until the verification script runs.
21. **Export bundle.** Results → *Export bundle*. A zip downloads. Open it:
    `metrics.json`, `items.csv` (ids only), `review_queue.csv` (ids only),
    `manifest.json`, `report.md`, `README.txt`, and **no** `review_notes.json`
    unless you chose to include notes. Search the unzipped files for a question
    sentence: it must not be there.
22. **Test center.** Tests → run *secret scan* and *ruff check*. Results show
    exit code and counts, and the history keeps the last 20 runs. Try to make it
    run a raw command (you cannot: the browser sends a command key, and the API
    rejects anything else with HTTP 400).
23. **Token, Host and Origin.** Open `http://127.0.0.1:8765/api/health` in a new
    tab without the token → 403. Open the app's own tab in the same browser (with
    the token in sessionStorage) and run
    `fetch('/api/health').then(r => r.status)` in the console → 200.
24. **Stale server.** Start a second `medeval ui` while the first runs: the new
    process says the port is held by another medeval-lab server and prints the
    PowerShell command to stop it, then serves on the next free port with a fresh
    token. Open the new address; the old tab keeps 403ing.
25. **Accessibility spot-check.** Tab through the start screen: focus is always
    visible, every control announces a name, run progress is announced
    (`aria-live`), and with Windows' "Show animations" off the UI stops
    animating.

## Steps added in v0.5.0 (bring your own key)

These are the flows the v0.5 browser tests cannot reach without a real provider
(step 28 and 29 are the only ones that can spend money; both need your own key and
an explicit confirmation).

26. **First-run notice.** On a fresh profile (Settings → *Show the first-run card
    again*), the card says in plain words that you pay your own provider, that you
    should set a spend limit in the provider's dashboard too, and that the
    maintainers cannot see, stop or refund your usage. PASS if all three are
    stated. No "J1/J2" or provider names appear anywhere on the New run page.
27. **No judges, no live run.** With zero connections assigned, New run shows "No
    judge connections assigned yet" plus a link to Models. Uncheck *Dry run* and
    press Start: the app refuses with a message naming the Models page and spends
    nothing. A dry run still starts and runs to completion labelled
    `PIPELINE TEST ONLY`.
28. **Provider-listed models.** Add a connection, press *Test connection* (spends
    nothing — it lists models). PASS if the returned model ids appear as buttons
    and clicking one fills the Model field **only after you click**; nothing is
    selected automatically and the field starts empty even after a successful
    test.
29. **Live confirmation wording.** With a key set and a price left empty, uncheck
    *Dry run*: the plan card's worst-case cost reads "unavailable, call cap is
    binding", and the confirmation dialog says the calls are real and charged to
    you. Cancel at the dialog: nothing is called, no spend.
30. **Headless plan (no key needed for this step).** In the terminal:
    `python -m medeval run exp002 --connections connections.example.yaml` → it
    lists both example connections with host/model/family and **no key**, prints
    the call count, the cap check and "nothing was called", then exits 2.
31. **Key safety.** Type a base URL containing `user:pass@` into a connection and
    save: the app refuses it with a message about credentials. Run
    `python -m medeval doctor`: it reports no local `.env` files (or names them
    without reading them), a loopback bind, and `--use-env` off.

If any step fails, note the page, the printed banner text, and whether the
browser console showed an error, then file it — the automated suite covers the
API side of most of these, so a UI failure is usually a rendering or wording bug.

## Steps added in v0.6.0 (Paper Packs and matrix runs)

32. **Registry.** Sidebar → *Paper Packs*. Three runnable templates appear with
    task type, version, item count, methodology tag and validation status. The
    archived Experiment 002/003 runs appear **below** them marked read-only. The
    banner says research tool, not a medical device.
33. **Pre-flight plan card.** Open `template-clinical-mcq`. Nothing is selected
    except *Dry run*. The plan card shows item count, planned target/judge calls,
    token upper bound and worst-case cost (or "unavailable, call cap is
    binding"). Select a saved connection as a target: the call count and cost
    update without starting anything.
34. **Cross-family indicator.** Open `template-rubric-panel`. Select two judge
    connections with different `family_label`s: the note says cross-family error
    correlation can be estimated. Select two with the same family: the note says
    it cannot. No judge is selected automatically.
35. **Dry run to completion.** Start the MCQ dry run. The run page shows
    `PIPELINE TEST ONLY`, progress, a summary table with 95% CIs, pairwise
    McNemar rows, and the disagreement explorer. Filter by *Model disagreements*,
    *Judge disagreements*, *Format failures* and *Errors*; each shows the target
    outputs side by side.
36. **Export.** Press *Export bundle*. The downloaded zip contains `report.md`
    (starts with the notice), `results_table.tex` (`% MEDICAL_NOTICE`),
    `citations.bib`, `metrics.json`, `items.csv`, `run.json`, `manifest.json` and
    `README.txt`. No key, prompt or raw dataset text is inside.
37. **Stop and resume without duplicates.** Use a live target (costs money) or
    the browser suite's fake-provider path. Stop mid-run: status `stopped`,
    completed items saved, report written. Resume: status `completed`; compare
    `outputs.jsonl` and `scores.jsonl` — every `(target, item, grader, judge)` key
    appears once.
38. **Offline re-grade.** On a completed MCQ run, press *Offline re-grade*. A new
    `…-regrade-…` run appears with `target calls: 0`; its report carries the same
    notice and metrics. Then try the CLI: `python -m medeval regrade <run_id>
    --paper template-clinical-mcq`. It must not contact any provider.
39. **Invalid pack.** Run `python -m medeval paper validate` after adding an
    unknown key to a copy of a `paper.yaml`. The validator names the unknown key
    and exits non-zero; the registry marks that pack invalid and the builder
    refuses to plan it.
40. **Preset registry and source card.** Paper Packs shows the five presets with
    a `preset` badge. Open `medcalc-bench`: the page says "Upstream data is not
    bundled", names `nsk7153/MedCalc-Bench`, the licence, the adaptation status
    and the loader command. No upstream rows are in the repository.
41. **Preset loader.** In the terminal:
    `python papers/medcalc-bench/loader.py --source papers/medcalc-bench/smoke.jsonl --out .tmp/loader-check`
    writes a verified copy; a deliberately malformed row aborts without writing.
    `--download` without `--accept-license` exits 2 and makes no network call.
    `.medeval_data/` is git-ignored.
