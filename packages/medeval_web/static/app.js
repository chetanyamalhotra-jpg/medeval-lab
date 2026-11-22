/* medeval-lab web app — vanilla JS, no build step, no external requests.
   Hash routing keeps it dependency-free and bookmarkable. */

(() => {
  "use strict";

  /* ------------------------------------------------------------ dom utils */
  const SVG_NS = "http://www.w3.org/2000/svg";
  const $ = (sel, root = document) => root.querySelector(sel);

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    applyAttrs(node, attrs);
    append(node, children);
    return node;
  }

  function svg(tag, attrs = {}, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    applyAttrs(node, attrs);
    append(node, children);
    return node;
  }

  function applyAttrs(node, attrs) {
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "class") {
        node.setAttribute("class", value);
      } else if (key === "dataset") {
        Object.assign(node.dataset, value);
      } else if (key === "style" && typeof value === "object") {
        Object.assign(node.style, value);
      } else if (key === "html") {
        node.innerHTML = value;
      } else if (value === true) {
        node.setAttribute(key, "");
      } else {
        node.setAttribute(key, String(value));
      }
    }
  }

  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }

  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };

  /* Every page renderer starts by clearing the view it was handed. Handlers such
     as "save connection" call a renderer again directly (not through the router),
     so without this the whole page stacked a second copy on every save. */
  function beginRender(view) {
    if (typeof eventSource !== "undefined" && eventSource) {
      eventSource.close();
      eventSource = null;
    }
    clear(view);
  }

  /* ------------------------------------------------------------ formatting */
  const pct = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(d)}%`);
  const num = (v, d = 3) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const ci = (interval) => (!interval ? "not computed" : `[${num(interval.low)}, ${num(interval.high)}]`);
  const wilson = (block) => (!block || !block.ci95_wilson ? "not computed" : `[${num(block.ci95_wilson[0])}, ${num(block.ci95_wilson[1])}]`);
  const when = (iso) => {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };
  const titleise = (text) => String(text || "").replace(/_/g, " ");

  const STATUS = {
    completed: ["ok", "completed"],
    running: ["warn", "running"],
    loading: ["info", "loading dataset…"],
    queued: ["info", "queued"],
    stopped: ["warn", "stopped"],
    budget_stopped: ["warn", "budget stopped"],
    failed: ["bad", "failed"],
    blocked: ["bad", "blocked"],
    refused: ["bad", "refused"],
    aborted: ["muted", "aborted"],
    unknown: ["muted", "unknown"],
  };

  function statusBadge(status, active) {
    const [tone, label] = STATUS[status] || STATUS.unknown;
    return el("span", { class: `badge ${tone}` },
      el("span", { class: `dot${status === "running" || active ? " pulse" : ""}` }), label);
  }

  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(node._timer);
    node._timer = setTimeout(() => { node.hidden = true; }, 6000);
  }

  /* ------------------------------------------------------------ api */
  let launchToken = sessionStorage.getItem("medeval.token") || "";
  let locked = false;

  function readLaunchToken() {
    // medeval ui opens the app at #token=...; the fragment never reaches the
    // server. Keep it in sessionStorage and clean it out of the address bar.
    // The token-bearing URL is the app entry point, so it lands on the start
    // screen; a token is not a route.
    const match = location.hash.match(/#token=([A-Za-z0-9_\-]+)/);
    if (match) {
      launchToken = match[1];
      sessionStorage.setItem("medeval.token", launchToken);
      history.replaceState(null, "", location.pathname + location.search + "#/");
    }
  }

  function authHeaders(extra = {}) {
    return launchToken ? { "X-Medeval-Token": launchToken, ...extra } : { ...extra };
  }

  function renderLockScreen(view) {
    clear(view);
    view.append(el("div", { class: "empty", style: { marginTop: "60px" } },
      el("h2", {}, "This server needs its launch token"),
      el("p", {}, "This app can hold API keys and spend money, so every API call must carry the ",
        "random token generated when the app was started."),
      el("p", {}, "Start it from PowerShell with ",
        el("code", {}, "python -m medeval ui"), " and open the address it prints — ",
        "the token travels in the URL fragment and stays in this tab only."),
      el("p", { class: "faint small" }, "Reload this page once the app is running with its token URL.")));
  }

  async function apiGet(path) {
    const response = await fetch(path, { headers: authHeaders({ "Accept": "application/json" }) });
    if (response.status === 403) { locked = !launchToken; throw new Error("missing or invalid launch token"); }
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); detail = body.detail || detail; } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    locked = false;
    return response.json();
  }

  async function apiPost(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body || {}),
    });
    if (response.status === 403) { locked = !launchToken; throw new Error("missing or invalid launch token"); }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
    return payload;
  }

  async function apiSend(method, path, body) {
    const response = await fetch(path, {
      method,
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 403) { locked = !launchToken; throw new Error("missing or invalid launch token"); }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
    return payload;
  }

  /* ------------------------------------------------------------ theme */
  const THEMES = ["auto", "light", "dark"];
  function currentTheme() { return localStorage.getItem("medeval.theme") || "auto"; }
  function applyTheme(theme) {
    if (!THEMES.includes(theme)) theme = "auto";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("medeval.theme", theme);
    document.querySelectorAll(".theme-option").forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.themeChoice === theme ? "true" : "false");
    });
  }
  function setTheme(theme) { applyTheme(theme); }
  function cycleTheme() {
    setTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]);
  }

  /* ------------------------------------------------------------ charts */
  function calibrationChart(bins) {
    const width = 460, height = 270;
    const m = { l: 46, r: 16, t: 16, b: 40 };
    const w = width - m.l - m.r, h = height - m.t - m.b;
    const x = (p) => m.l + p * w;
    const y = (a) => m.t + (1 - a) * h;
    const node = svg("svg", {
      viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img",
      "aria-label": "Reliability diagram: stated confidence against observed accuracy",
    });
    for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
      node.append(svg("line", { x1: x(tick), y1: y(0), x2: x(tick), y2: y(1), class: "grid-line" }));
      node.append(svg("line", { x1: x(0), y1: y(tick), x2: x(1), y2: y(tick), class: "grid-line" }));
      node.append(svg("text", { x: x(tick), y: height - m.b + 16, "text-anchor": "middle" }, `${Math.round(tick * 100)}%`));
      node.append(svg("text", { x: m.l - 8, y: y(tick) + 3, "text-anchor": "end" }, `${Math.round(tick * 100)}%`));
    }
    node.append(svg("line", { x1: x(0), y1: y(0), x2: x(1), y2: y(1), class: "ref" }));
    node.append(svg("line", { x1: m.l, y1: y(0), x2: x(1), y2: y(0), class: "axis" }));
    node.append(svg("line", { x1: m.l, y1: y(0), x2: m.l, y2: y(1), class: "axis" }));
    node.append(svg("text", { x: x(0.5), y: height - 6, "text-anchor": "middle" }, "stated confidence"));
    node.append(svg("text", {
      x: 12, y: y(0.5), "text-anchor": "middle",
      transform: `rotate(-90 12 ${y(0.5)})`,
    }, "observed accuracy"));
    for (const bin of bins || []) {
      if (!bin.n || bin.mean_confidence === null) continue;
      const radius = Math.min(9, 3 + Math.sqrt(bin.n));
      node.append(svg("circle", {
        cx: x(bin.mean_confidence), cy: y(bin.observed_accuracy), r: radius, class: "point",
        opacity: 0.85,
      }, svg("title", {}, `${bin.n} items · stated ${pct(bin.mean_confidence)} · observed ${pct(bin.observed_accuracy)}`)));
    }
    return node;
  }

  function accuracyChart(judges) {
    const entries = Object.entries(judges || {});
    if (!entries.length) return el("p", { class: "muted" }, "No judge data.");
    const width = 560, rowH = 46, height = entries.length * rowH + 34;
    const m = { l: 130, r: 24, t: 8, b: 26 };
    const w = width - m.l - m.r;
    const node = svg("svg", {
      viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img",
      "aria-label": "Accuracy against the key with 95% Wilson intervals",
    });
    for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
      node.append(svg("line", { x1: m.l + tick * w, y1: m.t, x2: m.l + tick * w, y2: height - m.b, class: "grid-line" }));
      node.append(svg("text", { x: m.l + tick * w, y: height - 10, "text-anchor": "middle" }, `${tick * 100}%`));
    }
    entries.forEach(([judgeId, payload], index) => {
      const accuracy = payload.primary_repeat.accuracy;
      const top = m.t + index * rowH + 12;
      const value = accuracy.estimate || 0;
      node.append(svg("text", { x: m.l - 10, y: top + 6, "text-anchor": "end" }, judgeId));
      node.append(svg("rect", {
        x: m.l, y: top - 5, width: Math.max(2, value * w), height: 12, rx: 3,
        class: index === 0 ? "bar" : "bar-alt",
      }));
      if (accuracy.ci95_wilson) {
        const [low, high] = accuracy.ci95_wilson;
        node.append(svg("line", {
          x1: m.l + low * w, y1: top + 1, x2: m.l + high * w, y2: top + 1,
          stroke: "currentColor", "stroke-width": 1, opacity: 0.55,
        }));
      }
      const label = pct(value);
      if (value > 0.82) {
        node.append(svg("text", {
          x: m.l + value * w - 6, y: top + 6, "text-anchor": "end", fill: "#fff8f4",
        }, label));
      } else {
        node.append(svg("text", { x: m.l + value * w + 6, y: top + 6 }, label));
      }
    });
    return node;
  }

  function confusionTable(judgeId, payload) {
    const primary = payload.primary_repeat;
    const fa = primary.false_accept_rate;
    const fr = primary.false_reject_rate;
    const nWrong = fa.denominator || 0;
    const nRight = fr.denominator || 0;
    const fp = fa.numerator || 0;
    const fn = fr.numerator || 0;
    const tp = nRight - fn;
    const tn = nWrong - fp;
    return el("div", {},
      el("h3", {}, judgeId),
      el("div", { class: "matrix-wrap" },
        el("table", { class: "matrix", "aria-label": `Confusion matrix for ${judgeId}` },
          el("thead", {}, el("tr", {},
            el("th", {}, ""), el("th", {}, "judge: CORRECT"), el("th", {}, "judge: INCORRECT"))),
          el("tbody", {},
            el("tr", {}, el("th", {}, "candidate correct"),
              el("td", { class: "cell good" }, String(tp)),
              el("td", { class: "cell bad" }, String(fn))),
            el("tr", {}, el("th", {}, "candidate wrong"),
              el("td", { class: "cell bad" }, String(fp)),
              el("td", { class: "cell good" }, String(tn)))))));
  }

  /* ------------------------------------------------------------ shared bits */
  function pageHead(title, sub, ...actions) {
    return el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, title), sub ? el("p", { class: "sub" }, sub) : null),
      el("div", { class: "spacer" }),
      ...actions);
  }

  function empty(title, text, ...rest) {
    return el("div", { class: "empty" }, el("h2", {}, title), el("p", {}, text), ...rest);
  }

  function limitationsBanner(metrics) {
    const items = [
      "Small sample: intervals are wide and small differences are not interpretable.",
      "The answer key may contain errors, missing information or ambiguity; no key audit was performed.",
      "No clinician reviewed any item, candidate answer, judged verdict or flagged case.",
      "Both judges are LLMs and the candidate answers come from a fixed template set, so agreement can reflect shared style bias.",
      "Exploratory: no confirmatory test, no multiple-comparison correction.",
      "Results do not generalise beyond this sample, prompt version and temperature. Nothing here is clinically validated.",
    ];
    return el("div", { class: "banner limits" },
      el("h3", {}, "Limitations"),
      el("ul", {}, items.map((text) => el("li", {}, text))),
      metrics && metrics.pipeline_test_only
        ? el("p", { class: "small" }, el("strong", {}, "PIPELINE TEST ONLY: "), "these numbers come from a mock, not a model.")
        : null);
  }

  /* ------------------------------------------------------------ page: runs */
  async function renderRuns(view) {
    beginRender(view);
    const runs = await apiGet("/api/runs");
    state.runs = runs;
    view.append(pageHead("Runs", "Every experiment run, newest first.", el("a", { class: "btn primary", href: "#/new" }, "New run")));

    if (!runs.length) {
      view.append(empty("No runs yet",
        "Start a dry run to watch the pipeline end to end without spending anything.",
        el("div", { class: "row", style: { justifyContent: "center", marginTop: "14px" } },
          el("a", { class: "btn primary", href: "#/new" }, "Configure a run"),
          el("code", { class: "small" }, "python -m medeval run exp002 --dry-run"))));
      return;
    }

    const rows = runs.map((run) => {
      const headline = run.judges.map((judge) => {
        const cell = (run.headline || {})[judge];
        if (!cell || cell.accuracy === null) return el("td", { class: "num" }, "—");
        return el("td", { class: "num" }, `${pct(cell.accuracy)} ${ci({ low: cell.ci95 && cell.ci95[0], high: cell.ci95 && cell.ci95[1] })}`.replace("not computed", ""));
      });
      return el("tr", {
        class: "clickable",
        onclick: () => { location.hash = `#/run/${run.run_id}`; },
        tabindex: "0",
        onkeydown: (event) => { if (event.key === "Enter") location.hash = `#/run/${run.run_id}`; },
      },
        el("td", {}, statusBadge(run.status, run.active)),
        el("td", {}, el("div", { class: "mono small" }, run.run_id),
          el("div", { class: "faint small" }, (run.headline || {}).dataset || "")),
        el("td", { class: "small muted nowrap" }, when(run.started_utc)),
        el("td", { class: "small" },
          run.judges.map((judge) => el("span", { class: "badge muted", style: { marginRight: "4px" } }, judge)),
          (run.unavailable_judges || []).map((judge) => el("span", {
            class: "badge bad", style: { marginRight: "4px" }, title: "requested but not available for this run",
          }, `${judge} unavailable`)),
          run.pipeline_test_only ? el("span", { class: "badge accent" }, "mock") : null),
        el("td", { class: "num" }, run.n_items ?? "—"),
        ...headline,
        el("td", { class: "num nowrap" },
          el("a", { href: `#/results/${run.run_id}`, onclick: (e) => e.stopPropagation() }, "Results"),
          " · ",
          el("a", { href: `#/items/${run.run_id}`, onclick: (e) => e.stopPropagation() }, "Items")));
    });

    const judgeIds = [...new Set(runs.flatMap((run) => run.judges))];
    view.append(el("div", { class: "card", style: { padding: "0", overflow: "hidden" } },
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "status"), el("th", {}, "run"), el("th", {}, "started"),
            el("th", {}, "judges"), el("th", { class: "num" }, "items"),
            ...judgeIds.map((judge) => el("th", { class: "num" }, `acc · ${judge}`)),
            el("th", {}, ""))),
          el("tbody", {}, rows)))));
    view.append(el("p", { class: "faint small", style: { marginTop: "10px" } },
      "Accuracy is against the official key, with a 95% Wilson interval. Mock runs validate the pipeline and say nothing about a model."));

    if (runs.some((run) => run.active)) {
      setTimeout(() => { if (location.hash.startsWith("#/runs")) route(); }, 4000);
    }
  }

  /* ------------------------------------------------------------ page: new */
  function numberField(id, label, value, hint, extra = {}) {
    return el("div", { class: "field" },
      el("label", { for: id }, label),
      el("input", { id, type: "number", value: String(value), ...extra }),
      hint ? el("span", { class: "hint" }, hint) : null);
  }

  async function renderNew(view) {
    beginRender(view);
    const [settings, connections, roles] = await Promise.all([
      state.settings ? Promise.resolve(state.settings) : apiGet("/api/settings"),
      apiGet("/api/connections"),
      apiGet("/api/roles"),
    ]);
    state.settings = settings;
    const form = {
      dataset: "medqa_4options",
      split: "dev",
      n_items: 60,
      repeats: 3,
      seed: 20260101,
      temperature: 0.3,
      max_api_calls: 500,
      dry_run: true,
    };
    const assignedJudges = (roles.judges || [])
      .map((row) => connections.find((connection) => connection.id === row.connection_id))
      .filter(Boolean);

    const estimateBox = el("div", { class: "card" }, el("p", { class: "muted" }, "Computing estimate…"));
    const drySwitch = el("input", { type: "checkbox", id: "dry_run", checked: true });
    const startButton = el("button", { class: "primary", type: "submit" }, "Start run");

    async function refreshEstimate() {
      try {
        const params = new URLSearchParams({
          n_items: form.n_items, repeats: form.repeats, seed: form.seed,
          temperature: form.temperature, dry_run: String(form.dry_run),
          max_api_calls: form.max_api_calls,
        });
        const data = await apiGet(`/api/estimate?${params}`);
        renderEstimate(estimateBox, data, form);
        startButton.disabled = !data.plan.total_calls;
      } catch (error) {
        clear(estimateBox);
        estimateBox.append(el("div", { class: "banner danger" }, `Estimate failed: ${error.message}`));
      }
    }

    const scheduleRefresh = debounce(refreshEstimate, 320);

    const formNode = el("form", {
      class: "card",
      onsubmit: async (event) => {
        event.preventDefault();
        startButton.disabled = true;
        if (!form.dry_run) {
          if (!assignedJudges.length) {
            toast("A live run needs judges: add a provider connection on the Models page and assign a judge role.");
            startButton.disabled = false;
            return;
          }
          const estimate = await apiGet(`/api/estimate?n_items=${form.n_items}&repeats=${form.repeats}&dry_run=false&max_api_calls=${form.max_api_calls}`);
          const worst = estimate.plan.usd_upper_bound ?? estimate.connection_plan?.worst_case_usd;
          const cost = worst == null
            ? "Worst-case cost: unavailable, the call cap is binding."
            : `Worst case US$${Number(worst).toFixed(4)} from the prices you declared.`;
          const ok = confirm(
            `This will make about ${estimate.plan.total_calls} real network calls and spend your own money ` +
            `(capped at ${form.max_api_calls} calls).\n\n${cost}\n\nContinue?`);
          if (!ok) { startButton.disabled = false; return; }
        }
        try {
          const result = await apiPost("/api/runs", { ...form, confirm_real_calls: !form.dry_run });
          location.hash = `#/run/${result.run_id}`;
        } catch (error) {
          toast(`Could not start: ${error.message}`);
          startButton.disabled = false;
        }
      },
    },
      el("div", { class: "form-row" },
        el("div", { class: "field" },
          el("label", { for: "dataset" }, "Dataset"),
          el("select", {
            id: "dataset",
            onchange: (event) => {
              form.dataset = event.target.value;
              scheduleRefresh();
            },
          }, (settings.datasets || []).map((dataset) =>
            el("option", { value: dataset.key, selected: dataset.key === form.dataset },
              `${dataset.key} — ${dataset.licence}`))),
          el("span", { class: "hint" }, "Item text is never committed; only ids, verdicts and hashes.")),
        el("div", { class: "field" },
          el("label", { for: "split" }, "Split"),
          el("select", { id: "split", onchange: (e) => { form.split = e.target.value; } },
            ["dev", "test", "train"].map((split) => el("option", { value: split, selected: split === "dev" }, split))))),

      el("div", { class: "form-row" },
        numberField("n_items", "Items", form.n_items, "Distinct stems only; default 60.", {
          min: 2, max: 400, oninput: (e) => { form.n_items = Number(e.target.value) || 0; scheduleRefresh(); },
        }),
        numberField("repeats", "Repeats per item", form.repeats, "Used to measure self-consistency.", {
          min: 1, max: 10, oninput: (e) => { form.repeats = Number(e.target.value) || 0; scheduleRefresh(); },
        })),

      el("div", { class: "form-row" },
        numberField("seed", "Seed", form.seed, "Sampling and judge seeds derive from it.", {
          oninput: (e) => { form.seed = Number(e.target.value) || 0; scheduleRefresh(); },
        }),
        numberField("temperature", "Temperature", form.temperature, "Judge sampling temperature.", {
          step: "0.1", min: 0, max: 2, oninput: (e) => { form.temperature = Number(e.target.value) || 0; scheduleRefresh(); },
        })),

      numberField("max_api_calls", "Cap on network calls", form.max_api_calls,
        "Hard stop. The run is refused if the plan exceeds it.", {
          min: 1, max: 20000, oninput: (e) => { form.max_api_calls = Number(e.target.value) || 0; scheduleRefresh(); },
        }),

      el("div", { class: "field" },
        el("label", {}, "Judges (from your connections)"),
        assignedJudges.length
          ? el("ul", { class: "small", "data-testid": "assigned-judges" }, assignedJudges.map((connection) =>
              el("li", {}, `${connection.name} — ${connection.model || "model not set"} (${connection.family_label || "family not set"})`,
                connection.key_set ? null : el("span", { class: "badge bad", style: { marginLeft: "6px" } }, "key not set"))))
          : el("p", { class: "small muted", "data-testid": "no-judges" },
              "No judge connections assigned yet. ",
              el("a", { href: "#/models" }, "Add a provider connection"),
              " (your key stays in this app's memory), then assign it to a judge role. A dry run works without one.")),

      el("div", { class: "field" },
        el("label", { class: "switch" }, drySwitch, el("span", {}, "Dry run (mock judges, no network calls)")),
        el("span", { class: "hint" }, "Unchecking this spends money. The app asks for confirmation first.")),

      el("div", { class: "row", style: { marginTop: "8px" } },
        startButton,
        el("span", { class: "faint small" }, "Runs execute on this machine and can be stopped at any time.")));

    drySwitch.addEventListener("change", () => {
      form.dry_run = drySwitch.checked;
      scheduleRefresh();
    });

    view.append(pageHead("New run", "Experiment 002 — judge error correlation and confidence (exploratory)."));
    view.append(el("div", { class: "grid cols-2" }, formNode, estimateBox));

    refreshEstimate();
  }

  function renderEstimate(box, data, form) {
    clear(box);
    const plan = data.plan || {};
    const judges = (data.judges || []).filter((judge) => judge.available);
    const unavailable = (data.judges || []).filter((judge) => !judge.available);
    const live = !data.dry_run;
    const connectionPlan = data.connection_plan || {};
    box.append(el("h2", {}, "Before you start"));

    if (!live) {
      box.append(el("div", { class: "metric", style: { marginBottom: "12px" } },
        el("span", { class: "label" }, "This run"),
        el("span", { class: "value" }, "Dry run: 0 network calls, US$0"),
        el("span", { class: "ci" }, "mock judges only; nothing below is spent")));
      box.append(el("h3", {}, "If run live"));
    }
    box.append(el("div", { class: "grid cols-2", style: { gap: "10px" } },
      el("div", { class: "metric" },
        el("span", { class: "label" }, live ? "Network calls" : "Network calls if live"),
        el("span", { class: "value" }, String(plan.total_calls ?? "—")),
        el("span", { class: "ci" }, `cap ${plan.max_api_calls ?? "—"}`)),
      el("div", { class: "metric" },
        el("span", { class: "label" }, live ? "Worst-case cost" : "Worst-case cost if live"),
        el("span", { class: "value" }, (plan.usd_upper_bound ?? connectionPlan.worst_case_usd) === null
          || (plan.usd_upper_bound ?? connectionPlan.worst_case_usd) === undefined
          ? "unavailable, call cap is binding" : `$${Number(plan.usd_upper_bound ?? connectionPlan.worst_case_usd).toFixed(4)}`),
        el("span", { class: "ci" }, connectionPlan.note || (plan.prices_verified ? `prices: ${plan.price_source}` : "prices unverified"))),
      el("div", { class: "metric" },
        el("span", { class: "label" }, "Input tokens (upper bound)"),
        el("span", { class: "value small" }, String(plan.input_tokens_upper_bound ?? "—"))),
      el("div", { class: "metric" },
        el("span", { class: "label" }, "Dataset"),
        el("span", { class: "value small" }, data.dataset?.dataset || "—"),
        el("span", { class: "ci" }, `${data.dataset?.licence?.licence || ""} · not redistributed`))));

    box.append(el("h3", {}, live ? "Judges in this run" : "Judges (dry run: mock stand-ins)"));
    if (!judges.length) box.append(el("p", { class: "badge bad" }, "no available judge"));
    for (const judge of judges) {
      box.append(el("p", { class: "small" },
        el("span", { class: judge.pipeline_test_only ? "badge accent" : "badge ok" }, judge.judge_id),
        ` ${judge.model} · temp ${judge.temperature} · ${judge.repeats} repeats`));
    }
    for (const judge of unavailable) {
      box.append(el("p", { class: "small" },
        el("span", { class: "badge bad" }, judge.judge_id),
        ` unavailable — ${judge.unavailable_reason || "no reason recorded"}`));
    }
    box.append(el("p", { class: "note" },
      `Estimate: ${plan.estimator || "characters/4 per message"}.`));
  }

  function debounce(fn, ms) {
    let timer = null;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  /* ------------------------------------------------------------ page: run */
  let eventSource = null;

  async function renderRunDetail(view, runId) {
    beginRender(view);
    if (eventSource) { eventSource.close(); eventSource = null; }
    const payload = await apiGet(`/api/runs/${runId}`);
    const run = payload.run || {};
    const config = run.config || {};
    const plan = run.plan || {};
    const usage = run.usage || {};

    const startedLabel = (run.status === "loading" || run.status === "queued")
      ? (run.status === "loading" ? "loading dataset…" : "queued")
      : run.started_utc
        ? `started ${when(run.started_utc)}`
        : "starting…";
    view.append(el("div", { class: "crumbs", "data-testid": "run-crumbs" },
      el("a", { href: "#/runs" }, "Runs"), " / ", runId));
    view.append(pageHead(runId,
      `${titleise(config.experiment || "experiment")} · ${startedLabel}`,
      statusBadge(run.status || "unknown", payload.active)));

    const NO_OUTPUT = new Set(["failed", "blocked", "refused", "aborted"]);
    const hasOutput = Boolean(payload.metrics) && !NO_OUTPUT.has(run.status || "");

    const REASONS = {
      aborted: run.aborted_reason,
      blocked: run.blocked_reason,
      refused: run.refused_reason,
      failed: run.error,
      stopped: run.stop_reason,
    };
    const reason = REASONS[run.status];
    if (reason) {
      view.append(el("div", { class: run.status === "failed" ? "banner danger" : "banner info", style: { marginBottom: "14px" } },
        el("h3", {}, `This run is ${run.status}`),
        el("p", {}, reason),
        run.status === "aborted" || run.status === "stopped"
          ? el("p", { class: "small" }, "No headline metric is shown for this run: its judgements cover only the items completed before it ended, which is not a representative sample.")
          : null));
    }

    const progressBar = el("div", { style: { width: "0%" } });
    const progressText = el("p", { class: "mono small", "data-testid": "run-progress-text" },
      "waiting for the first judgement…");
    const counters = el("div", { class: "row small muted", "data-testid": "run-counters" });
    const log = el("div", { class: "log", role: "log", "aria-live": "polite" });
    const stopButton = el("button", { class: "danger", type: "button", disabled: !payload.active,
      "data-testid": "stop-run", onclick: async () => {
        try {
          await apiPost(`/api/runs/${runId}/stop`, {});
          toast("Stop requested. The run finishes the current call, then writes its reports.");
          stopButton.disabled = true;
        } catch (error) {
          toast(error.message);
        }
      } }, "Stop run");

    const progressCard = el("div", { class: "card" },
      el("h2", {}, "Progress"),
      el("div", { class: "progress" }, progressBar),
      progressText, counters,
      el("div", { class: "row", style: { marginTop: "12px" } }, stopButton,
        el("span", { class: "faint small" }, "Reports are written from the judgements completed so far, even if stopped.")));

    const logCard = el("div", { class: "card" }, el("h2", {}, "Live log"), log);

    const links = el("div", { class: "card", "data-testid": "run-outputs" }, el("h2", {}, "Outputs"),
      hasOutput
        ? el("div", { class: "row" },
          el("a", { class: "btn", href: `#/results/${runId}`, "data-testid": "results-link" }, "Results"),
          el("a", { class: "btn", href: `#/items/${runId}`, "data-testid": "items-link" }, "Item explorer"),
          el("a", { class: "btn", href: `/api/runs/${runId}/report`, target: "_blank", rel: "noopener" }, "report.md"))
        : el("p", { class: "muted small", "data-testid": "no-outputs" },
          NO_OUTPUT.has(run.status || "")
            ? "This run has no results to show: it did not produce judgements."
            : "Results appear here when the run has written its analysis."));

    view.append(el("div", { class: "grid cols-2" }, progressCard, links));
    view.append(el("div", { class: "section" }, stagesCard(runId, payload)));
    view.append(el("div", { class: "section" }, logCard));

    function setProgress(snapshot) {
      const total = snapshot.total || plan.total_calls || 0;
      const done = snapshot.done || 0;
      progressBar.style.width = total ? `${Math.min(100, (done / total) * 100).toFixed(1)}%` : "0%";
      progressText.textContent = total
        ? `${done} of ${total} judge calls recorded`
        : `${done} judge calls recorded`;
      clear(counters);
      // Our append() helper skips null/undefined; Element.append() would render
      // them as the literal text "null" (the "nullnull…" status line bug).
      append(counters, [
        snapshot.status ? el("span", {}, `status: ${snapshot.status}`) : null,
        snapshot.calls_made !== null && snapshot.calls_made !== undefined ? el("span", {}, ` · network calls: ${snapshot.calls_made}`) : null,
        snapshot.cache_hits !== null && snapshot.cache_hits !== undefined ? el("span", {}, ` · cache hits: ${snapshot.cache_hits}`) : null,
        config.n_items ? el("span", {}, ` · items: ${config.n_items}`) : null,
        config.repeats ? el("span", {}, ` · repeats: ${config.repeats}`) : null,
      ]);
    }

    setProgress({ done: run.n_judgements || 0, total: plan.total_calls, status: run.status });

    eventSource = new EventSource(`/api/runs/${runId}/events`);
    eventSource.addEventListener("snapshot", (event) => {
      const snapshot = JSON.parse(event.data);
      setProgress(snapshot);
      stopButton.disabled = !snapshot.active;
      if (snapshot.status && run.status !== snapshot.status) {
        run.status = snapshot.status;
        if (["completed", "stopped", "failed", "budget_stopped", "refused", "blocked", "aborted",
             "pilot_stopped", "awaiting_confirmation"].includes(snapshot.status)) {
          stopButton.disabled = true;
          if (eventSource) {
            eventSource.close();
            eventSource = null;
          }
          setTimeout(() => route(), 600);
        }
      }
    });
    eventSource.addEventListener("judgement", (event) => {
      const row = JSON.parse(event.data);
      const cls = row.error ? "err" : row.verdict === "CORRECT" ? "ok" : "bad";
      const line = el("div", {},
        el("span", { class: "meta" }, `${row.judge_id} r${row.repeat} `),
        `${row.item_id} → `,
        el("span", { class: cls }, row.error ? row.error : `${row.verdict} (${row.confidence})`));
      log.append(line);
      log.scrollTop = log.scrollHeight;
    });
    eventSource.addEventListener("error", () => { /* EventSource retries by itself */ });
  }

  /* ------------------------------------------------------------ page: results */
  async function renderResults(view, runId) {
    beginRender(view);
    const payload = await apiGet(`/api/runs/${runId}`);
    const metrics = payload.metrics;
    view.append(el("div", { class: "crumbs" }, el("a", { href: "#/runs" }, "Runs"), " / ",
      el("a", { href: `#/run/${runId}` }, runId), " / results"));
    view.append(pageHead("Results", `${runId} · exploratory, not a clinical claim`,
      el("a", { class: "btn", href: `#/items/${runId}` }, "Item explorer"),
      el("button", {
        class: "", type: "button",
        title: "Zip with metrics.json, items.csv (ids only), review_queue.csv (ids only), manifest.json and report.md",
        onclick: async (event) => {
          const button = event.target;
          button.disabled = true;
          try {
            const withNotes = confirm(
              "Export bundle (no item text, no keys).\n\nOK = exclude your reviewer notes (recommended).\n" +
              "Cancel = include them — notes are one reviewer's opinion and may quote item text."
            );
            const response = await fetch(`/api/runs/${runId}/export?include_notes=${withNotes}`, {
              method: "POST", headers: authHeaders(),
            });
            if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = el("a", { href: url, download: `${runId}-bundle.zip` });
            document.body.append(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            toast(withNotes ? "Bundle downloaded (with your notes, local opinion)." : "Bundle downloaded: ids and aggregates only.");
          } catch (error) {
            toast(error.message);
          } finally {
            button.disabled = false;
          }
        },
      }, "Export bundle")));

    if (!metrics) {
      view.append(empty("No metrics yet", "This run has not finished writing its analysis."));
      return;
    }

    if (metrics.pipeline_test_only) {
      view.append(el("div", { class: "banner danger", style: { marginBottom: "14px" } },
        el("h3", {}, "Pipeline test only"),
        el("p", {}, "These judges were mocks. The numbers check that the pipeline runs; they describe no model.")));
    }

    const judges = Object.entries(metrics.judges || {});
    if (judges.length === 1) {
      view.append(el("div", { class: "banner info", style: { marginBottom: "14px" } },
        el("h3", {}, "One judge"),
        el("p", {}, "Only one judge was available, so cross-family error correlation could not be measured. ",
          "Single-judge metrics below are real measurements of that judge; they are not a jury result.")));
    }

    const cards = el("div", { class: judges.length === 1 ? "grid cols-2" : "grid cols-3" });
    for (const [judgeId, data] of judges) {
      const primary = data.primary_repeat;
      const calibration = primary.calibration || {};
      const consistency = data.across_repeats || {};
      cards.append(el("div", { class: "card" },
        el("h2", {}, `${judgeId} · ${data.spec.model}`),
        el("div", { class: "grid cols-2", style: { gap: "12px" } },
          metricBlock("Accuracy vs key", pct(primary.accuracy.estimate), `95% ${wilson(primary.accuracy)}`),
          metricBlock("Cohen's κ vs key", num(primary.cohen_kappa_vs_key), `95% ${ci(primary.cohen_kappa_vs_key_ci95)}`),
          metricBlock("False accept", pct(primary.false_accept_rate.estimate), `${primary.false_accept_rate.numerator}/${primary.false_accept_rate.denominator}`),
          metricBlock("False reject", pct(primary.false_reject_rate.estimate), `${primary.false_reject_rate.numerator}/${primary.false_reject_rate.denominator}`),
          metricBlock("ECE (5 bins)", num(calibration.ece_5_bins), `95% ${ci(calibration.ece_5_bins_ci95)}`),
          metricBlock("Brier", num(calibration.brier), `95% ${ci(calibration.brier_ci95)}`),
          metricBlock("AUROC (confidence)", num(calibration.auroc_confidence_predicts_own_correctness), `95% ${ci(calibration.auroc_confidence_predicts_own_correctness_ci95)}`),
          metricBlock("Same verdict, all repeats", pct(consistency.consistent_fraction), `${consistency.n_items_all_repeats ?? 0} items`)),
        el("p", { class: "note" },
          `Parse failures ${primary.n_parse_failures} · API errors ${primary.n_api_errors} · scored ${primary.n_scored}/${primary.n_items}`)));
    }

    view.append(el("div", { class: "section" }, el("h2", {}, "Headline metrics"), cards));

    const charts = el("div", { class: "grid cols-2" });
    charts.append(el("div", { class: "card" }, el("h2", {}, "Accuracy with intervals"), accuracyChart(metrics.judges)));
    for (const [judgeId, data] of judges) {
      const bins = (data.primary_repeat.calibration || {}).reliability_bins;
      charts.append(el("div", { class: "card" },
        el("h2", {}, `Calibration · ${judgeId}`),
        bins && bins.length ? calibrationChart(bins) : el("p", { class: "muted" }, "No reliability bins in this run (rebuild it)."),
        el("p", { class: "note" }, "Dashed line is perfect calibration. Each circle is a confidence bin, sized by item count.")));
    }
    view.append(el("div", { class: "section" }, el("h2", {}, "Calibration and discrimination"), charts));

    const matrices = el("div", { class: "grid cols-2" });
    for (const [judgeId, data] of judges) {
      matrices.append(el("div", { class: "card" }, el("h2", {}, "Confusion matrix"), confusionTable(judgeId, data)));
    }
    view.append(el("div", { class: "section" }, el("h2", {}, "Where the judge was right and wrong"), matrices));

    const agreementCard = el("div", { class: "card" }, el("h2", {}, "Judge agreement"));
    const pairs = Object.entries(metrics.cross_judge || {});
    if (!pairs.length) {
      agreementCard.append(el("p", { class: "muted" },
        "Not computed: this needs two judges. A single-judge run cannot estimate agreement or correlated error."));
    }
    for (const [pair, data] of pairs) {
      agreementCard.append(el("h3", {}, pair));
      agreementCard.append(el("p", {}, `Raw agreement ${pct(data.verdict_agreement.percent_agreement)}`,
        ` · Cohen's κ ${num(data.verdict_agreement.cohen_kappa)} (95% ${ci(data.verdict_agreement.cohen_kappa_ci95)})`,
        ` · Gwet AC1 ${num(data.verdict_agreement.gwet_ac1)}`));
      agreementCard.append(el("p", { class: "small muted" },
        `Both wrong ${data.error_correlation.both_wrong_count}/${data.n_common_items}`,
        ` · phi ${num(data.error_correlation.phi_coefficient)} (95% ${ci(data.error_correlation.phi_coefficient_ci95)})`,
        ` · expected if independent ${num((data.error_correlation.both_wrong_vs_independence_binomial || {}).expected)}`,
        ` · permutation p ${num((data.error_correlation.both_wrong_vs_independence_permutation || {}).p_value_two_sided)}`));
    }
    view.append(el("div", { class: "section" }, el("h2", {}, "Agreement and error correlation"), agreementCard));

    const flags = metrics.possible_key_errors || {};
    const rule = flags.rule || {};
    const ruleText = rule.applied === "two_judge" ? rule.two_judge : rule.single_judge_fallback;
    const reviewCard = el("div", { class: "card" },
      el("h2", {}, "Flagged for review"),
      el("p", { class: "small muted" }, "Rule applied: ", el("strong", {}, rule.applied || "—"), ` — ${ruleText || "not recorded"}`),
      el("p", { class: "small" },
        "A flag means confident judges disagreed with the key. It is a prompt for a human to look, ",
        el("strong", {}, "not a finding that the key is wrong"), ": the judges may be the ones who are wrong."));
    if (flags.n_flagged_possible_key_errors) {
      reviewCard.append(el("p", {}, `${flags.n_flagged_possible_key_errors} item(s) flagged. No key was changed.`));
      reviewCard.append(el("div", { class: "row" },
        el("a", { class: "btn", href: `#/items/${runId}?filter=key` }, "Review flagged items")));
    } else {
      reviewCard.append(el("p", { class: "muted" }, "No item met the pre-specified rule."));
    }
    if (flags.n_flagged_single_judge) {
      reviewCard.append(el("p", { class: "small" },
        `${flags.n_flagged_single_judge} item(s) flagged by the weaker single-judge rule (${rule.single_judge_fallback || "one judge, very confident"}); these cannot distinguish a wrong key from a wrong judge.`));
    }

    const health = metrics.benchmark_health || {};
    const healthCard = el("div", { class: "card" }, el("h2", {}, "Benchmark health"),
      el("p", {}, `Items ${health.n_items} · key letters ${JSON.stringify(health.key_letter_distribution || {})}`));
    for (const [judgeId, value] of Object.entries(health.ceiling_per_judge || {})) {
      healthCard.append(el("p", { class: "small" }, `${judgeId} ceiling rate ${pct(value.ceiling_rate)} (${value.n_all_repeats_correct}/${value.n_items_with_all_repeats} items right on every repeat)`));
    }
    view.append(el("div", { class: "section" }, el("h2", {}, "Review and health"),
      el("div", { class: "grid cols-2" }, reviewCard, healthCard)));

    view.append(el("div", { class: "section" }, limitationsBanner(metrics)));

    const cost = el("div", { class: "card" }, el("h2", {}, "Cost"),
      el("p", { class: "small" },
        `calls ${payload.run?.usage?.calls_made ?? "—"} · logged ${payload.run?.usage?.calls_logged ?? "—"} · cache hits ${payload.run?.usage?.cache_hits ?? "—"}`),
      el("p", { class: "small" }, payload.run?.usage?.estimated_usd === null || payload.run?.usage?.estimated_usd === undefined
        ? "estimated cost not computable"
        : `estimated US$${Number(payload.run.usage.estimated_usd).toFixed(4)} (${payload.run.usage.price_source})`));
    view.append(el("div", { class: "section" }, el("h2", {}, "Spend"), cost));
  }

  function metricBlock(label, value, sub) {
    return el("div", { class: "metric" },
      el("span", { class: "label" }, label),
      el("span", { class: "value small" }, value),
      el("span", { class: "ci" }, sub));
  }

  /* ------------------------------------------------------------ page: items */
  const FILTERS = {
    all: ["All items", () => true],
    disagree: ["Judges disagree", (row) => {
      const verdicts = Object.values(row.judges || {}).map((judge) => judge.verdict).filter(Boolean);
      return new Set(verdicts).size > 1;
    }],
    bothwrong: ["Everyone wrong", (row) => Object.values(row.judges || {}).length > 0 && Object.values(row.judges || {}).every((judge) => judge.verdict && judge.verdict !== (row.candidate_correct ? "CORRECT" : "INCORRECT"))],
    highconf: ["High-confidence wrong", (row) => Object.values(row.judges || {}).some((judge) =>
      judge.verdict && judge.verdict !== (row.candidate_correct ? "CORRECT" : "INCORRECT") && (judge.confidence || 0) >= 90)],
    parsefail: ["Parse failures", (row) => Object.values(row.judges || {}).some((judge) => judge.parse_ok === false)],
    key: ["Flagged for review", (row) => row.flagged === true],
  };

  async function renderItems(view, runId, query) {
    beginRender(view);
    const rows = await apiGet(`/api/runs/${runId}/items`);
    const params = new URLSearchParams(query || "");
    const active = FILTERS[params.get("filter")] ? params.get("filter") : "all";
    const judges = [...new Set(rows.flatMap((row) => Object.keys(row.judges || {})))];

    view.append(el("div", { class: "crumbs" }, el("a", { href: "#/runs" }, "Runs"), " / ",
      el("a", { href: `#/run/${runId}` }, runId), " / items"));
    view.append(pageHead("Item explorer", `${rows.length} items · question text stays local and is never committed`));

    const tableBody = el("tbody");
    const count = el("span", { class: "faint small" });
    const chips = el("div", { class: "row", style: { marginBottom: "14px" } });

    function renderRows() {
      const predicate = FILTERS[active][1];
      const visible = rows.filter(predicate);
      clear(tableBody);
      for (const row of visible) {
        tableBody.append(el("tr", { class: "clickable", onclick: () => openDrawer(row) },
          el("td", { class: "nowrap" }, el("span", { class: "mono small" }, row.item_id),
            row.flagged ? el("span", { class: "badge warn", style: { marginLeft: "6px" } }, "review") : null),
          el("td", { class: "small muted truncate" }, (row.question || "").slice(0, 160)),
          el("td", { class: "small nowrap" }, row.condition),
          el("td", { class: "mono nowrap" }, `${row.chosen_letter}${row.candidate_correct ? " ✓" : " ✗"}`),
          ...judges.map((judge) => {
            const cell = (row.judges || {})[judge] || {};
            const wrong = cell.verdict && cell.verdict !== (row.candidate_correct ? "CORRECT" : "INCORRECT");
            return el("td", { class: "num" },
              cell.verdict ? el("span", { class: wrong ? "badge bad" : "badge ok" }, `${cell.verdict === "CORRECT" ? "C" : "I"} ${cell.confidence}`)
                : el("span", { class: "badge muted" }, "—"));
          })));
      }
      count.textContent = `${visible.length} shown of ${rows.length}`;
    }

    for (const [key, [label]] of Object.entries(FILTERS)) {
      chips.append(el("button", {
        class: key === active ? "primary" : "",
        onclick: () => {
          location.hash = `#/items/${runId}?filter=${key}`;
        },
      }, label));
    }

    view.append(chips);
    view.append(el("div", { class: "card", style: { padding: "0", overflow: "hidden" } },
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "item"), el("th", {}, "question"), el("th", {}, "condition"), el("th", {}, "chosen"),
            ...judges.map((judge) => el("th", { class: "num" }, judge)))),
          tableBody))));
    view.append(el("p", { class: "faint small", style: { marginTop: "10px" } }, count));
    renderRows();
  }

  function openDrawer(row) {
    const backdrop = el("div", { class: "drawer-backdrop", onclick: (event) => { if (event.target === backdrop) close(); } });
    function close() { backdrop.remove(); document.removeEventListener("keydown", onKey); }
    function onKey(event) { if (event.key === "Escape") close(); }
    document.addEventListener("keydown", onKey);

    const optionRows = Object.entries(row.options || {}).map(([letter, text]) =>
      el("div", { class: `opt${letter === row.key_letter ? " key" : ""}${letter === row.chosen_letter ? " chosen" : ""}` },
        el("span", { class: "mono" }, letter),
        el("span", {}, text),
        el("span", { class: "faint small" },
          letter === row.key_letter && letter === row.chosen_letter ? "key · chosen"
            : letter === row.key_letter ? "key"
              : letter === row.chosen_letter ? "chosen" : "")));

    const judgeRows = Object.entries(row.judges || {}).map(([judge, data]) =>
      el("p", { class: "small" },
        el("span", { class: "badge muted" }, judge), " ",
        (data.repeats || []).map((verdict, index) =>
          el("span", { class: "mono", style: { marginRight: "6px" } },
            `${verdict ?? "fail"}${data.confidences && data.confidences[index] !== null ? ` ${data.confidences[index]}` : ""}`))));

    backdrop.append(el("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": `Item ${row.item_id}` },
      el("div", { class: "row" }, el("h2", { class: "mono" }, row.item_id), el("div", { class: "spacer" }),
        el("button", { class: "ghost", onclick: close, "aria-label": "Close" }, "Close")),
      el("p", { class: "small muted" }, `condition: ${row.condition} · candidate ${row.candidate_correct ? "correct" : "wrong"} per key`),
      el("p", { class: "q" }, row.question),
      el("h3", { class: "small muted" }, "Options"), ...optionRows,
      el("h3", { class: "small muted" }, "Candidate answer"),
      el("p", { class: "q mono small" }, row.candidate_text),
      el("h3", { class: "small muted" }, "Judge verdicts (all repeats)"), ...judgeRows,
      row.flagged ? el("div", { class: "banner limits" }, "Flagged for manual review: confident judges disagreed with the key. This does not prove the key is wrong.") : null));
    document.body.append(backdrop);
  }

  /* ------------------------------------------------------------ page: settings */
  async function renderSettings(view) {
    beginRender(view);
    const settings = await apiGet("/api/settings");
    state.settings = settings;
    view.append(pageHead("Settings", "Local status only. No key is ever displayed."));
    const connections = settings.connections || {};
    view.append(el("div", { class: "grid cols-2" },
      el("div", { class: "card", "data-testid": "settings-connections" },
        el("h2", {}, "Provider connections"),
        el("p", {}, `${connections.saved || 0} saved connection(s) on the Models page.`),
        el("p", { class: "small" }, connections.keys_in_process_memory_only
          ? "API keys live only in this app's process memory and are forgotten when it stops."
          : "Key handling: see SECURITY.md."),
        el("p", { class: "small" }, el("a", { href: "#/models" }, "Manage connections and roles"))),
      el("div", { class: "card", "data-testid": "settings-roles" },
        el("h2", {}, "Roles"),
        el("p", {}, `${connections.judges_assigned || 0} judge role(s) assigned; screening role ${connections.screening_assigned ? "assigned" : "not assigned"}.`),
        el("p", { class: "small" }, "New runs take their judges from these connections. Nothing is preconfigured and no model is chosen for you."))));

    view.append(el("div", { class: "section" }, el("h2", {}, "Paths and limits"),
      el("div", { class: "card" },
        el("p", { class: "small mono" }, `repo ${settings.repo_root}`),
        el("p", { class: "small mono" }, `data ${settings.data_dir}`),
        el("p", { class: "small mono" }, `results ${settings.results_dir}`),
        el("p", { class: "small mono" }, `runs ${settings.run_root}`),
        el("p", { class: "small" }, `caps: ${settings.caps?.max_api_calls} calls · US$${settings.caps?.max_usd}`),
        el("p", { class: "small" }, `prices: ${settings.prices?.verified ? `${settings.prices.input_per_million} in / ${settings.prices.output_per_million} out per 1M · ${settings.prices.source}` : "unverified"}`),
        settings.prices?.source_url ? el("p", { class: "small mono" }, settings.prices.source_url) : null)));

    view.append(el("div", { class: "section" }, el("h2", {}, "First-run card"),
      el("div", { class: "card" },
        el("p", { class: "small" }, "The four-step card is hidden on the start screen once you dismiss it. Bring it back here."),
        el("button", { class: "", type: "button", onclick: async () => {
          try {
            await apiSend("POST", "/api/preferences", { first_run_dismissed: false });
            toast("The first-run card is back on the start screen.");
          } catch (error) { toast(error.message); }
        } }, "Show the first-run card again"),
        el("p", { class: "note" }, "This preference is stored in a git-ignored local file."))));

    view.append(el("div", { class: "section" }, el("h2", {}, "Datasets"),
      el("div", { class: "card", style: { padding: "0", overflow: "hidden" } },
        el("div", { class: "table-wrap" },
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "key"), el("th", {}, "hub repo"), el("th", {}, "licence"), el("th", {}, "redistributed?"))),
            el("tbody", {}, (settings.datasets || []).map((dataset) =>
              el("tr", {},
                el("td", { class: "mono small" }, dataset.key),
                el("td", { class: "mono small" }, dataset.hf_repo),
                el("td", {}, el("span", { class: "badge info" }, dataset.licence)),
                el("td", {}, dataset.redistribute ? el("span", { class: "badge warn" }, "yes") : el("span", { class: "badge ok" }, "no"))))))))));

    view.append(el("div", { class: "section" }, el("h2", {}, "Backend resolution"),
      el("div", { class: "card" },
        el("pre", { class: "small", style: { whiteSpace: "pre-wrap" } }, JSON.stringify(settings.backend || {}, null, 2)))));
  }

  /* ------------------------------------------------------------ sidebar */
  async function renderSidebar(hash) {
    const list = $("#workspace-list");
    if (!list) return;
    try {
      state.workspaces = await apiGet("/api/workspaces");
    } catch (_) {
      return; // locked or offline; the lock screen handles it
    }
    clear(list);
    for (const workspace of state.workspaces) {
      const folder = el("div", { class: "workspace-folder" });
      folder.append(el("span", { class: "workspace-name" }, workspace.name));
      for (const session of (workspace.sessions || []).slice(0, 8)) {
        const active = hash === `#/session/${session.id}`;
        folder.append(el("a", {
          class: `session-link${active ? " primary" : ""}`,
          href: `#/session/${session.id}`,
          "aria-current": active ? "page" : null,
        },
          el("span", { class: "nowrap", style: { overflow: "hidden", textOverflow: "ellipsis" } }, session.title),
          el("span", { class: "rel" }, session.run_status === "running" ? "●" : (session.relative_time || ""))));
      }
      list.append(folder);
    }
  }

  /* ------------------------------------------------------------ start screen + composer */
  const SLASH_COMMANDS = [
    { command: "/run", description: "run the session's plan (live — asks before spending)" },
    { command: "/dryrun", description: "dry run of the session's plan (mock, no network)" },
    { command: "/test", description: "open the Test center" },
    { command: "/papers", description: "open the Papers library" },
    { command: "/report", description: "open the newest run's report" },
  ];

  async function renderStart(view) {
    beginRender(view);
    if (!state.workspaces.length) {
      try { state.workspaces = await apiGet("/api/workspaces"); } catch (_) { return; }
    }
    if (!state.roles) {
      try { state.roles = await apiGet("/api/roles"); } catch (_) { /* handled on submit */ }
    }
    const form = { workspace: state.workspaces[0]?.id || "ws-ungrouped", mode: "dry_run", experiment: "exp003" };

    const head = el("div", { class: "start-head" },
      el("h1", {}, "Describe an evaluation"),
      el("span", { class: "preview-badge" }, "Preview"));

    const workspaceSelect = el("select", {
      "aria-label": "Workspace",
      onchange: (e) => { form.workspace = e.target.value; },
    }, state.workspaces.map((workspace) => el("option", { value: workspace.id, selected: workspace.id === form.workspace }, workspace.name)));

    const modeSelect = el("select", {
      "aria-label": "Mode",
      onchange: (e) => { form.mode = e.target.value; updateSpendHint(); },
    },
      el("option", { value: "dry_run", selected: true }, "Dry run"),
      el("option", { value: "pilot" }, "Pilot"),
      el("option", { value: "full" }, "Full"));

    const experimentSelect = el("select", {
      "aria-label": "Experiment",
      onchange: (e) => { form.experiment = e.target.value; },
    },
      el("option", { value: "exp003", selected: true }, "Experiment 003"),
      el("option", { value: "exp002" }, "Experiment 002"));

    const spendSelect = el("select", {
      "aria-label": "Spend policy",
      onchange: () => updateSpendHint(),
    },
      el("option", { value: "dry", selected: true }, "Spend policy: Dry run only"),
      el("option", { value: "ask" }, "Ask before spending"),
      el("option", { value: "live" }, "Live up to caps"));

    const judgesSelect = el("select", {
      "aria-label": "Judges",
      onchange: () => updateSpendHint(),
    }, el("option", { value: "" }, "Judges: from roles on the Models page"));

    const textarea = el("textarea", {
      placeholder: "Describe what you want to evaluate",
      "aria-label": "Describe what you want to evaluate",
      rows: 4,
      oninput: () => updateSlash(),
    });

    const slashList = el("div", { class: "slash-list", hidden: true, role: "listbox" });

    const send = el("button", { class: "send primary", type: "submit", "aria-label": "Send" }, "➤");

    const spendHint = el("p", { class: "faint small", style: { margin: "6px 4px 0" } },
      "Dry run: 0 network calls, US$0.");

    function updateSpendHint() {
      clear(spendHint);
      if (form.mode === "dry_run" && spendSelect.value !== "live") {
        spendHint.append("Dry run: 0 network calls, US$0. Mock judges only; the plan below is what a live run would do.");
      } else {
        spendHint.append("Live mode spends money. The plan (judges, items, worst-case calls and cost) is shown before the run starts and requires explicit confirmation. Per-connection and total caps are enforced before every call.");
      }
    }

    function updateSlash() {
      const text = textarea.value;
      const slashMatch = text.match(/(^|\s)(\/[a-z]*)$/);
      if (!slashMatch) { slashList.hidden = true; return; }
      const prefix = slashMatch[2];
      clear(slashList);
      for (const entry of SLASH_COMMANDS.filter((row) => row.command.startsWith(prefix))) {
        slashList.append(el("div", {
          role: "option",
          onclick: () => {
            textarea.value = textarea.value.slice(0, textarea.value.length - prefix.length) + entry.command + " ";
            slashList.hidden = true;
            textarea.focus();
          },
        }, el("code", {}, entry.command), entry.description));
      }
      slashList.hidden = slashList.children.length === 0;
    }

    const composer = el("form", {
      class: "composer-card composer",
      onsubmit: async (event) => {
        event.preventDefault();
        send.disabled = true;
        try {
          await submitComposer(textarea.value, form, spendSelect.value);
          textarea.value = "";
        } catch (error) {
          toast(error.message);
        } finally {
          send.disabled = false;
        }
      },
    },
      el("div", { class: "composer-chips" },
        el("span", { class: "chip" }, el("label", {}, "Workspace"), workspaceSelect),
        el("span", { class: "chip" }, el("label", {}, "Mode"), modeSelect),
        el("span", { class: "chip" }, el("label", {}, "Experiment"), experimentSelect)),
      slashList,
      textarea,
      el("div", { class: "composer-actions" },
        el("button", {
          class: "ghost", type: "button", title: "Attach a config or paper (local files only)", "aria-label": "Attach",
          onclick: () => toast("Attach: pick a config JSON or a paper DOI on the Papers page; nothing is uploaded anywhere."),
        }, "+"),
        el("span", { class: "chip" }, spendSelect),
        el("span", { class: "chip" }, judgesSelect),
        el("div", { class: "spacer" }),
        send));

    view.append(head, medicalNotice(), composer, spendHint);
    const prefs = await apiGet("/api/preferences").catch(() => ({ first_run_dismissed: true }));
    if (!prefs.first_run_dismissed) {
      view.append(firstRunCard(() => route()));
    }
    const latest = (await apiGet("/api/runs"))[0];
    if (latest) {
      view.append(el("p", { class: "faint small", style: { textAlign: "center", marginTop: "18px" } },
        "Newest run: ",
        el("a", { href: `#/results/${latest.run_id}` }, `${latest.run_id} (${latest.status})`)));
    }
    updateSpendHint();

    async function submitComposer(text, formState, spendPolicy) {
      const trimmed = (text || "").trim();
      if (!trimmed) { toast("Describe what you want to evaluate, or type / for commands."); return; }
      if (trimmed.startsWith("/")) {
        const [command] = trimmed.split(/\s+/, 1);
        if (command === "/test") { location.hash = "#/tests"; return; }
        if (command === "/papers") { location.hash = "#/papers"; return; }
        if (command === "/report") {
          const runs = await apiGet("/api/runs");
          if (runs[0]) location.hash = `#/results/${runs[0].run_id}`;
          else toast("No runs yet.");
          return;
        }
        if (command === "/run" || command === "/dryrun") {
          const dry = command === "/dryrun";
          if (!dry && spendPolicy === "dry") {
            toast("The spend policy is 'Dry run only' — use /dryrun or change the policy.");
            return;
          }
          const session = await apiPost("/api/workspaces/" + formState.workspace + "/sessions", {
            title: trimmed.replace(command, "").trim() || "Run",
          });
          const result = await apiSend("POST", `/api/sessions/${session.id}/run`, { dry_run: dry, mode: formState.mode, experiment: formState.experiment });
          location.hash = `#/session/${session.id}`;
          return;
        }
        toast(`Unknown command ${command}. Try /run, /dryrun, /test, /papers or /report.`);
        return;
      }
      // Plain notes: saved as the session's hypothesis, with no model call.
      const session = await apiPost("/api/workspaces/" + formState.workspace + "/sessions", {
        title: trimmed.split("\n")[0].slice(0, 60),
        hypothesis: trimmed,
      });
      location.hash = `#/session/${session.id}`;
    }
  }

  /* ------------------------------------------------------------ session thread */
  async function renderSession(view, sessionId) {
    beginRender(view);
    const session = await apiGet(`/api/sessions/${sessionId}`);
    view.append(el("div", { class: "crumbs" }, el("a", { href: "#/" }, "Start"), " / ", session.title));
    view.append(pageHead(session.title, session.hypothesis ? session.hypothesis.split("\n")[0] : null));
    const thread = el("div", { class: "session-thread" });
    view.append(thread);
    for (const message of session.messages || []) {
      thread.append(el("div", { class: `card thread-card card-${message.kind}` },
        el("div", { class: "row small muted" }, el("strong", {}, message.role), el("span", {}, when(message.utc))),
        el("p", { class: "small", style: { whiteSpace: "pre-wrap" } }, message.payload?.text || JSON.stringify(message.payload || {}))));
    }
    if (session.run_id) {
      const run = await apiGet(`/api/runs/${session.run_id}`).catch(() => null);
      thread.append(el("div", { class: "card thread-card card-progress" },
        el("h2", {}, "Run"),
        el("p", { class: "small" },
          statusBadge(run?.run?.status || "unknown", run?.active),
          ` ${session.run_id}`),
        el("div", { class: "row" },
          el("a", { class: "btn", href: `#/run/${session.run_id}` }, "Progress"),
          el("a", { class: "btn", href: `#/results/${session.run_id}` }, "Results"),
          el("a", { class: "btn", href: `#/items/${session.run_id}` }, "Items"))));
    }
    // The composer for follow-ups in this session.
    const followUp = el("textarea", {
      placeholder: "Add a note or a / command",
      "aria-label": "Add a note or command", rows: 2,
    });
    thread.append(el("form", {
      class: "card composer",
      onsubmit: async (event) => {
        event.preventDefault();
        const text = followUp.value.trim();
        if (!text) return;
        await apiSend("POST", `/api/sessions/${sessionId}/messages`, { role: "user", kind: "note", payload: { text } });
        route();
      },
    }, followUp, el("div", { class: "row", style: { marginTop: "8px" } },
      el("button", { class: "primary", type: "submit" }, "Add to session"))));
  }

  /* ------------------------------------------------------------ models */
  async function renderModels(view) {
    beginRender(view);
    const [connections, roles, presets] = await Promise.all([
      apiGet("/api/connections"), apiGet("/api/roles"), apiGet("/api/presets"),
    ]);
    state.connections = connections;
    state.roles = roles;

    const modelsHead = pageHead("Models", "Bring your own provider and key. Keys live in this app's memory only and are forgotten when the app stops.");
    modelsHead.setAttribute("data-testid", "models-page");
    view.append(modelsHead);

    view.append(el("div", { class: "banner limits", style: { marginBottom: "14px" } },
      el("h3", {}, "How keys are handled"),
      el("ul", {},
        el("li", {}, "Keys are held only in this app's process memory — never written to disk, logs, reports or git."),
        el("li", {}, "'Forget key after this run' is on by default; the app forgets everything when it stops."),
        el("li", {}, "Preset base URLs were read from official documentation and stay editable."),
        el("li", {}, ".env loading is off unless the app was started with --use-env."))));

    // --- roles ------------------------------------------------------- //
    const rolesCard = el("div", { class: "card", style: { marginBottom: "14px" }, "data-testid": "roles-card" });
    const roleState = { judges: [...(roles.judges || [])], screening: roles.screening || "" };
    // Always show two judge roles, so the two-judge design is the default the
    // owner sees rather than something they have to discover a button for.
    while (roleState.judges.length < 2) {
      roleState.judges.push({ role: `judge_${roleState.judges.length + 1}`, connection_id: "" });
    }
    const roleWarnings = el("div", { "data-testid": "role-warnings" });
    const connectionsById = Object.fromEntries(connections.map((row) => [row.id, row]));

    // The warnings are computed from the current dropdown values on every change,
    // so the same-family warning and the screening-is-a-judge error appear before
    // Save. The server keeps its own checks on save.
    function refreshRoleWarnings() {
      clear(roleWarnings);
      const judgeIds = roleState.judges.map((row) => row.connection_id).filter(Boolean);
      const sameName = new Set(judgeIds).size !== judgeIds.length;
      const screeningConflict = Boolean(roleState.screening) && judgeIds.includes(roleState.screening);
      const families = roleState.judges
        .map((row) => (connectionsById[row.connection_id] || {}).family_label)
        .filter(Boolean);
      const familyConflict = new Set(families).size !== families.length;
      if (screeningConflict) {
        roleWarnings.append(el("div", { class: "banner danger", style: { marginTop: "10px" }, "data-testid": "screening-conflict" },
          "The screening model must not also be a judge."));
      }
      if (sameName) {
        roleWarnings.append(el("div", { class: "banner warn", style: { marginTop: "10px" }, "data-testid": "same-connection" },
          "The same connection is assigned to two judge roles; a judge cannot grade alongside itself."));
      }
      if (familyConflict) {
        roleWarnings.append(el("div", { class: "banner warn", style: { marginTop: "10px" }, "data-testid": "family-warning" },
          "Two judges share a provider or family label: cross-family error correlation cannot be measured between these judges. No judge is substituted automatically."));
      }
    }

    function renderRoles() {
      clear(rolesCard);
      rolesCard.append(el("h2", {}, "Roles"));
      rolesCard.append(el("p", { class: "small muted" },
        "Assign saved connections to Judge 1, 2, 3… and to the optional Screening model used by Experiment 003. ",
        "The screening model must not also be a judge."));
      const rows = el("div", { class: "stack" });
      for (const [index, judge] of roleState.judges.entries()) {
        const role = judge.role;
        rows.append(el("div", { class: "row" },
          el("span", { class: "mono small", style: { width: "80px" } }, role),
          el("select", {
            "aria-label": `Connection for ${role}`,
            "data-testid": `role-${role.replace(/_/g, "-")}`,
            onchange: (e) => {
              roleState.judges[index].connection_id = e.target.value;
              refreshRoleWarnings();
            },
          },
            el("option", { value: "" }, "— unassigned —"),
            connections.map((connection) => el("option", {
              value: connection.id, selected: connection.id === judge.connection_id,
            }, `${connection.name} (${connection.model || "no model"})`))),
          index >= 2 ? el("button", { class: "ghost", type: "button", onclick: () => { roleState.judges.splice(index, 1); renderRoles(); renderRoleWarnings(); } }, "Remove") : null));
      }
      rolesCard.append(rows);
      rolesCard.append(el("div", { class: "row", style: { marginTop: "10px" } },
        el("button", {
          class: "", type: "button", "data-testid": "add-judge-role",
          onclick: () => { roleState.judges.push({ role: `judge_${roleState.judges.length + 1}`, connection_id: "" }); renderRoles(); renderRoleWarnings(); },
        }, "+ Add judge role")));
      rolesCard.append(el("div", { class: "row", style: { marginTop: "12px" } },
        el("span", { class: "mono small", style: { width: "80px" } }, "screening"),
        el("select", {
          "aria-label": "Connection for the screening model",
          "data-testid": "role-screening",
          onchange: (e) => {
            roleState.screening = e.target.value;
            refreshRoleWarnings();
          },
        },
          el("option", { value: "" }, "— none —"),
          connections.map((connection) => el("option", {
            value: connection.id, selected: connection.id === roleState.screening,
          }, `${connection.name} (${connection.model || "no model"})`))),
        el("span", { class: "faint small" }, "Experiment 003 only; must not also be a judge")));
      rolesCard.append(roleWarnings);
      rolesCard.append(el("div", { class: "row", style: { marginTop: "12px" } },
        el("button", {
          class: "primary", type: "button", "data-testid": "save-roles",
          onclick: async () => {
            try {
              state.roles = await apiSend("POST", "/api/roles", {
                judges: roleState.judges.filter((row) => row.connection_id),
                screening: roleState.screening || null,
              });
              toast("Roles saved.");
              refreshRoleWarnings();
            } catch (error) { toast(error.message); }
          },
        }, "Save roles")));
      refreshRoleWarnings();
    }
    function renderRoleWarnings() { refreshRoleWarnings(); }
    renderRoles();
    view.append(rolesCard);

    // --- connections ------------------------------------------------- //
    const listCard = el("div", { class: "card" }, el("h2", {}, "Saved connections"));
    const connectionsTable = el("div", { class: "table-wrap" });
    listCard.append(connectionsTable);
    view.append(el("div", { class: "section" }, el("h2", {}, "Connections"), listCard));

    function renderConnections() {
      clear(connectionsTable);
      if (!connections.length) {
        connectionsTable.append(el("p", { class: "muted" }, "No connections yet — add one below."));
        return;
      }
      connectionsTable.append(el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "name"), el("th", {}, "provider"), el("th", {}, "base URL"), el("th", {}, "model"),
          el("th", {}, "family"), el("th", {}, "key"), el("th", {}, "caps"), el("th", {}, ""))),
        el("tbody", {}, connections.map((connection) => el("tr", {},
          el("td", { class: "small" }, connection.name),
          el("td", { class: "small" }, connection.provider_preset),
          el("td", { class: "mono small" }, connection.base_url),
          el("td", { class: "mono small" }, connection.model || "—"),
          el("td", { class: "small" }, connection.family_label || "—"),
          el("td", {}, connection.key_set
            ? el("span", { class: "badge ok", title: `key set, ending ${connection.key_last4}` }, `key set ····${connection.key_last4}`)
            : el("span", { class: "badge muted" }, "no key")),
          el("td", { class: "num small" }, `${connection.per_connection_call_cap} calls · $${connection.per_connection_usd_cap}`),
          el("td", { class: "nowrap" },
            el("button", { class: "ghost", type: "button", onclick: () => testConnection(connection), "data-testid": "connection-test" }, "Test"),
            " ",
            el("button", { class: "ghost", type: "button", onclick: async () => {
              if (!confirm(`Forget the key for ${connection.name}? It is removed from app memory.`)) return;
              await apiSend("DELETE", `/api/connections/${connection.id}/key`);
              renderModels(view);
            } }, "Forget key"),
            " ",
            el("button", { class: "ghost", type: "button", onclick: async () => {
              if (!confirm(`Delete connection ${connection.name}?`)) return;
              await apiSend("DELETE", `/api/connections/${connection.id}`);
              renderModels(view);
            } }, "Delete")))))));
    }
    renderConnections();

    const probeResult = el("div", { hidden: true });
    async function testConnection(connection) {
      clear(probeResult);
      probeResult.hidden = false;
      probeResult.append(el("p", { class: "small muted" }, `Testing ${connection.name}: listing models from ${connection.base_url} (no spend)…`));
      try {
        const result = await apiSend("POST", `/api/connections/${connection.id}/test`, {});
        clear(probeResult);
        if (result.verified) {
          probeResult.append(el("div", { class: "banner info" },
            el("strong", {}, `Verified, ${result.n_models} models visible.`),
            el("p", { class: "small mono" }, result.models.slice(0, 12).join(", ") + (result.n_models > 12 ? " …" : "")),
            el("p", { class: "small" }, "Choose a model for this connection. Nothing is selected automatically:"),
            el("div", { class: "row", style: { flexWrap: "wrap", gap: "6px" } },
              result.models.slice(0, 12).map((model) => el("button", {
                class: "ghost small", type: "button", "data-testid": "model-choice",
                onclick: async () => {
                  try {
                    await apiSend("PUT", `/api/connections/${connection.id}`, { model });
                    toast(`Model for ${connection.name} set to ${model}.`);
                    renderModels(view);
                  } catch (error) { toast(`Could not set the model: ${error.message}`); }
                },
              }, model)))));
        } else {
          probeResult.append(el("div", { class: "banner danger" },
            el("p", {}, result.error || "The endpoint answered but published no model list."),
            result.reachable && result.n_models === 0
              ? el("div", { class: "row", style: { marginTop: "8px" } },
                el("button", {
                  class: "", type: "button",
                  onclick: async () => {
                    try {
                      const retry = await apiSend("POST", `/api/connections/${connection.id}/test`, { confirm_one_token_call: true });
                      toast(retry.one_token_call?.ok ? `One-token call OK (model: ${retry.one_token_call.model_returned}).` : `One-token call failed: ${retry.one_token_call?.error}`);
                    } catch (error) { toast(error.message); }
                  },
                }, "Run one-token test call (explicit confirm)"))
              : null));
        }
      } catch (error) {
        clear(probeResult);
        probeResult.append(el("div", { class: "banner danger" }, error.message));
      }
    }
    view.append(probeResult);

    // --- add form ----------------------------------------------------- //
    const formCard = el("div", { class: "card", style: { marginTop: "14px" } }, el("h2", {}, "Add a connection"));
    const nameInput = el("input", { id: "conn-name", type: "text", placeholder: "e.g. judge A", "data-testid": "connection-name" });
    const baseUrlInput = el("input", { id: "conn-url", type: "text" });
    const styleSelect = el("select", { id: "conn-style" },
      el("option", { value: "chat" }, "chat completions"),
      el("option", { value: "responses" }, "responses API"));
    const modelInput = el("input", { id: "conn-model", type: "text", placeholder: "type a model id, or pick after Test" });
    const familyInput = el("input", { id: "conn-family", type: "text", placeholder: "e.g. family-a, family-b — used for the cross-family warning" });
    const keyInput = el("input", { id: "conn-key", class: "grow", type: "password", placeholder: "paste the key; it stays in app memory", "data-testid": "connection-key" });
    const keyLabel = el("label", { for: "conn-key" }, "API key");
    const keyToggle = el("button", { class: "ghost", type: "button", "aria-label": "Show or hide the key", onclick: () => {
      keyInput.type = keyInput.type === "password" ? "text" : "password";
    } }, "Show");
    const capCalls = el("input", { id: "conn-cap-calls", type: "number", value: "500", min: "1" });
    const capUsd = el("input", { id: "conn-cap-usd", type: "number", value: "5", min: "0", step: "0.5" });
    const priceIn = el("input", { id: "conn-price-in", type: "number", step: "0.01", min: "0", placeholder: "USD per 1M input tokens (optional)" });
    const priceOut = el("input", { id: "conn-price-out", type: "number", step: "0.01", min: "0", placeholder: "USD per 1M output tokens (optional)" });

    // No provider is selected on a fresh page: the first option is empty and
    // Save stays disabled until the owner chooses one. A preset only fills a
    // base URL; it never chooses a model, a family or a price for you.
    const presetHint = el("span", { class: "hint", "data-testid": "preset-hint" },
      "Choose a provider preset first. Nothing is preconfigured; Save stays disabled until you do.");
    const saveButton = el("button", {
      class: "primary", type: "button", "data-testid": "save-connection", disabled: true,
      onclick: async () => {
        if (!presetSelect.value) {
          toast("Choose a provider preset first; nothing is preconfigured.");
          return;
        }
        const payload = {
          name: nameInput.value, provider_preset: presetSelect.value, base_url: baseUrlInput.value,
          api_style: styleSelect.value, model: modelInput.value, family_label: familyInput.value,
          key: keyInput.value, per_connection_call_cap: Number(capCalls.value), per_connection_usd_cap: Number(capUsd.value),
          price_in_per_mtok: priceIn.value === "" ? null : Number(priceIn.value),
          price_out_per_mtok: priceOut.value === "" ? null : Number(priceOut.value),
        };
        try {
          await apiSend("POST", "/api/connections", payload);
          toast(`Connection ${payload.name} saved. The key is in app memory only.`);
          renderModels(view);
        } catch (error) { toast(error.message); }
      },
    }, "Save connection");

    function applyPreset() {
      const preset = presets.presets.find((row) => row.key === presetSelect.value);
      if (!preset) {
        baseUrlInput.value = "";
        saveButton.disabled = true;
        keyLabel.textContent = "API key";
        clear(presetHint);
        presetHint.append("Choose a provider preset first. Nothing is preconfigured; Save stays disabled until you do.");
        return;
      }
      // Base URL only: the model, family and prices are always the owner's choice.
      baseUrlInput.value = preset.base_url;
      saveButton.disabled = false;
      keyLabel.textContent = preset.requires_key ? "API key (required for remote providers)" : "API key (optional for local endpoints)";
      clear(presetHint);
      presetHint.append(`Base URL filled from the ${preset.label} preset (editable). ` +
        "Pick a model after Test; no model or price is selected for you.");
    }

    const presetSelect = el("select", {
      id: "conn-preset",
      "data-testid": "conn-preset",
      onchange: applyPreset,
    }, [
      el("option", { value: "", selected: true }, "Choose a provider"),
      ...presets.presets.map((preset) => el("option", { value: preset.key }, preset.label)),
    ]);

    formCard.append(
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "conn-preset" }, "Provider preset"), presetSelect, presetHint),
        el("div", { class: "field" }, el("label", { for: "conn-name" }, "Name"), nameInput)),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "conn-url" }, "Base URL (editable)"), baseUrlInput),
        el("div", { class: "field" }, el("label", { for: "conn-style" }, "API style"), styleSelect)),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "conn-model" }, "Model"), modelInput,
          el("span", { class: "hint" }, "Test the connection to pick from the endpoint's model list.")),
        el("div", { class: "field" }, el("label", { for: "conn-family" }, "Model family label (required for judges)"), familyInput)),
      el("div", { class: "field" }, keyLabel,
        el("div", { class: "row" }, keyInput, keyToggle),
        el("span", { class: "hint" }, "Sent from this page over loopback in a request body; stored only in app memory.")),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "conn-cap-calls" }, "Per-connection call cap"), capCalls),
        el("div", { class: "field" }, el("label", { for: "conn-cap-usd" }, "Per-connection USD cap"), capUsd)),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "conn-price-in" }, "Price in (optional)"), priceIn),
        el("div", { class: "field" }, el("label", { for: "conn-price-out" }, "Price out (optional)"), priceOut)),
      el("div", { class: "row" }, saveButton));
    view.append(formCard);
  }

  /* ------------------------------------------------------------ papers */
  async function renderPapers(view) {
    beginRender(view);
    const papers = await apiGet("/api/papers");
    const papersHead = pageHead("Papers", "The literature registry behind every criterion and metric. verified is set only by the automated title-match script, never by hand.");
    papersHead.setAttribute("data-testid", "papers-page");
    view.append(papersHead);

    const tableCard = el("div", { class: "card", style: { padding: "0", overflow: "hidden" } });
    const wrap = el("div", { class: "table-wrap" });
    tableCard.append(wrap);
    const detail = el("div", { class: "card", style: { marginTop: "14px" } });

    function renderTable() {
      clear(wrap);
      wrap.append(el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "id"), el("th", {}, "title"), el("th", {}, "year"), el("th", {}, "doi / arXiv"),
          el("th", {}, "verified"), el("th", {}, "status"))),
        el("tbody", {}, papers.map((row) => el("tr", { class: "clickable", onclick: () => showDetail(row.id) },
          el("td", { class: "mono small" }, row.id),
          el("td", { class: "small" }, row.title),
          el("td", { class: "num" }, row.year || "—"),
          el("td", { class: "mono small" }, row.doi || "—"),
          el("td", {}, row.verified === "yes" ? el("span", { class: "badge ok" }, "verified") : el("span", { class: "badge muted" }, "unverified")),
          el("td", { class: "small" }, row.status || "—"))))));
    }
    renderTable();
    view.append(tableCard, detail);

    async function showDetail(paperId) {
      const paper = await apiGet(`/api/papers/${paperId}`);
      clear(detail);
      detail.append(el("h2", {}, paper.id));
      detail.append(el("p", { class: "small" }, paper.title, paper.doi ? ` · ${paper.doi}` : ""));
      detail.append(el("h3", { class: "small muted" }, "What in the lab cites it"));
      const cited = paper.cited_by || {};
      detail.append(el("p", { class: "small" },
        `Criteria: ${(cited.criteria || []).join(", ") || "none recorded"} · Metrics: ${(cited.metrics || []).join(", ") || "none recorded"}`));
      detail.append(el("h3", { class: "small muted" }, "Status"));
      const statusSelect = el("select", { "aria-label": "Paper status" },
        ["", "to read", "read", "used in design", "cited in report"].map((status) =>
          el("option", { value: status, selected: status === (paper.status || "") }, status || "— unset —")));
      detail.append(el("div", { class: "row" }, statusSelect,
        el("button", { class: "", type: "button", onclick: async () => {
          await apiSend("POST", `/api/papers/${paperId}/status`, { status: statusSelect.value });
          toast("Status saved (local file, git-ignored).");
          renderPapers(view);
        } }, "Save status")));
      detail.append(el("h3", { class: "small muted" }, "Local notes (git-ignored)"));
      const notes = el("textarea", { rows: 3, "aria-label": "Local notes" });
      notes.value = paper.notes || "";
      detail.append(notes,
        el("div", { class: "row", style: { marginTop: "8px" } },
          el("button", { class: "", type: "button", onclick: async () => {
            await apiSend("POST", `/api/papers/${paperId}/notes`, { notes: notes.value });
            toast("Notes saved (local file, git-ignored).");
          } }, "Save notes")),
        el("p", { class: "note" }, "Notes are one reader's comments. They are not clinical validation or a reference standard."));
    }

    const addCard = el("div", { class: "card", style: { marginTop: "14px" } }, el("h2", {}, "Add a paper by DOI or arXiv id"));
    const identifier = el("input", { type: "text", placeholder: "10.xxxx/xxxxx or 2312.12345", "aria-label": "DOI or arXiv id", "data-testid": "paper-doi" });
    const fetchAnnounce = el("p", { class: "small muted" });
    const fetched = el("div");
    addCard.append(
      el("div", { class: "row" }, identifier,
        el("button", {
          class: "", type: "button", "data-testid": "paper-fetch",
          onclick: async () => {
            clear(fetched); clear(fetchAnnounce);
            fetchAnnounce.append(`Outbound request: fetching metadata for ${identifier.value} from Crossref or arXiv (user-initiated)…`);
            try {
              const result = await apiSend("POST", "/api/papers/fetch", { identifier: identifier.value });
              clear(fetchAnnounce);
              fetchAnnounce.append(`Fetched from ${result.fetched_from}. Nothing stored yet.`);
              clear(fetched);
              fetched.append(el("div", { class: "banner info" },
                el("p", { class: "small" }, el("strong", {}, result.metadata.title || "(no title)"), ` · ${result.metadata.year ?? ""}`),
                el("button", { class: "primary", type: "button", "data-testid": "paper-add", onclick: async () => {
                  try {
                    await apiSend("POST", "/api/papers", result.metadata);
                    toast("Paper added to the registry (verified=no until the script checks the DOI).");
                    renderPapers(view);
                  } catch (error) { toast(error.message); }
                } }, "Add to registry")));
            } catch (error) {
              clear(fetchAnnounce);
              fetchAnnounce.append(error.message);
            }
          },
        }, "Fetch metadata")),
      fetchAnnounce, fetched);
    view.append(el("div", { class: "section" }, el("h2", {}, "Add a paper"), addCard));
  }

  /* ------------------------------------------------------------ test center */
  async function renderTests(view) {
    beginRender(view);
    const [commands, results] = await Promise.all([apiGet("/api/tests/commands"), apiGet("/api/tests/results")]);
    view.append(pageHead("Tests", "Server-side, allow-listed commands only. The browser selects an item; it never sends a command string. Tests never make live paid calls."));

    view.append(el("div", { class: "card", style: { marginBottom: "14px" } },
      el("h2", {}, "Pre-flight checklist (shown on every run plan)"),
      el("ul", { class: "small" },
        el("li", {}, "key isolation test passed (run it below)"),
        el("li", {}, "config lock active (a resumed run cannot change its design)"),
        el("li", {}, "caps set (per connection and run total)"),
        el("li", {}, "prices set, or 'unavailable, call cap is binding'"),
        el("li", {}, "dry run completed for this config"))));

    const listCard = el("div", { class: "card" }, el("h2", {}, "Allow-listed commands"));
    const output = el("div", { class: "log", hidden: true });
    for (const command of commands) {
      listCard.append(el("div", { class: "row", style: { marginBottom: "6px" } },
        el("span", { class: "mono small", style: { flex: 1 } }, command.label),
        el("button", {
          class: "", type: "button",
          onclick: async (event) => {
            const button = event.target;
            button.disabled = true;
            output.hidden = false;
            clear(output);
            output.append(el("div", { class: "meta" }, `running ${command.label}…`));
            try {
              const result = await apiSend("POST", "/api/tests/run", { command: command.key });
              clear(output);
              output.append(el("div", { class: result.exit_code === 0 ? "ok" : "err" },
                `exit ${result.exit_code} in ${result.duration_s}s` +
                (result.n_passed !== null ? ` · ${result.n_passed} passed, ${result.n_failed ?? 0} failed, ${result.n_skipped ?? 0} skipped` : "")));
              output.append(el("div", { class: "meta" }, result.tail || "(no output)"));
            } catch (error) {
              clear(output);
              output.append(el("div", { class: "err" }, error.message));
            } finally {
              button.disabled = false;
            }
          },
        }, "Run")));
    }
    view.append(el("div", { class: "section" }, el("h2", {}, "Commands"), listCard, output));

    const historyCard = el("div", { class: "card", style: { marginTop: "14px" } }, el("h2", {}, "Last results (counts and ids only)"));
    if (!results.length) {
      historyCard.append(el("p", { class: "muted" }, "Nothing has run yet in this app session."));
    } else {
      historyCard.append(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "id"), el("th", {}, "command"), el("th", {}, "exit"), el("th", {}, "passed"), el("th", {}, "failed"), el("th", {}, "skipped"), el("th", {}, "duration"), el("th", {}, "when"))),
        el("tbody", {}, results.map((row) => el("tr", {},
          el("td", { class: "mono small" }, row.id),
          el("td", { class: "small" }, row.label),
          el("td", { class: "num" }, row.exit_code ?? (row.timed_out ? "timeout" : "running")),
          el("td", { class: "num" }, row.n_passed ?? "—"),
          el("td", { class: "num" }, row.n_failed ?? "—"),
          el("td", { class: "num" }, row.n_skipped ?? "—"),
          el("td", { class: "num" }, `${row.duration_s}s`),
          el("td", { class: "small muted nowrap" }, when(row.started_utc))))))));
    }
    view.append(historyCard);
  }

  /* ------------------------------------------------------------ first-run card */
  // The one notice the app must show wherever someone might mistake it for a
  // clinical tool: the start screen, the first-run card, and the shell footer.
  function medicalNotice() {
    return el("div", { class: "medical-notice", role: "note", "data-testid": "medical-device-notice" },
      el("strong", {}, "Research tool, not a medical device. "),
      "Not for patient care or clinical decisions. No clinical validation (no clinician reviewed ",
      "the content), and small samples are exploratory. Mock and dry runs say nothing about any model.");
  }

  function firstRunCard(onDone) {
    const card = el("div", { class: "card", style: { maxWidth: "680px", margin: "18px auto 0" } },
      el("h2", {}, "First run: four steps"),
      medicalNotice(),
      el("div", { class: "banner warn", "data-testid": "first-run-notice", style: { marginBottom: "12px" } },
        el("p", {}, el("strong", {}, "You pay your own provider. "),
          "This app has no default model, no default provider and no key of its own: add your own connection and key, "),
        el("p", {}, "a spend limit in your provider's dashboard (not only here), and read the plan card before any live run. "),
        el("p", {}, "The maintainers cannot see, stop or refund your usage.")),
      el("ol", { class: "small" },
        el("li", {}, el("a", { href: "#/models" }, "Add a connection"), " — pick a provider preset (or type a base URL), test it, choose one of the models it lists, paste a key (memory only), set its caps."),
        el("li", {}, "Assign it to a judge role (and, for Experiment 003, a screening role) on the Models page."),
        el("li", {}, "Run a dry run from the composer — mock judges, no network, no spend."),
        el("li", {}, "Read the plan card and the Stages card: worst-case calls and cost before anything real runs.")),
      el("div", { class: "row" },
        el("a", { class: "btn", href: "#/models" }, "Go to Models"),
        el("button", { class: "ghost", type: "button", onclick: async () => {
          try {
            await apiSend("POST", "/api/preferences", { first_run_dismissed: true });
            toast("Hidden. Reopen it any time from Settings.");
            card.remove();
            if (onDone) onDone();
          } catch (error) { toast(error.message); }
        } }, "Hide this card"),
        el("span", { class: "faint small" }, "Reopenable from Settings.")));
    return card;
  }

  /* ------------------------------------------------------------ stages card (Experiment 003) */
  function stagesCard(runId, payload) {
    const card = el("div", { class: "card" }, el("h2", {}, "Stages"));
    const body = el("div");
    card.append(body);
    const isExp003 = (payload?.run?.config?.experiment || "") === "003_frontier_judges";

    async function refresh() {
      clear(body);
      if (!isExp003) {
        body.append(el("p", { class: "muted small" },
          "This is an Experiment 002 run, which runs as a single pass. Experiment 003 runs as confirmed stages."));
        return;
      }
      let plan;
      try {
        plan = await apiGet(`/api/runs/${runId}/stages`);
      } catch (error) {
        body.append(el("p", { class: "muted small" }, error.message));
        return;
      }
      if (plan.blocked_reason) {
        body.append(el("div", { class: "banner danger", "data-testid": "stages-blocked", style: { marginBottom: "12px" } },
          el("strong", {}, "This run cannot continue"),
          el("p", { class: "small", style: { whiteSpace: "pre-wrap" } }, plan.blocked_reason)));
      }
      if (plan.screening) {
        const screening = plan.screening;
        body.append(el("p", { class: "small" },
          el("strong", {}, "Screening: "),
          `${screening.n_screened ?? "?"} items screened, ${screening.n_wrong ?? "?"} hard; `,
          "stop reason: ", el("code", {}, screening.stop_reason || "unknown"),
          screening.mock ? el("span", { class: "badge muted", style: { marginLeft: "6px" } }, "mock (dry run)") : null));
      }
      function confirmButton(stage) {
        if (!stage.needs_confirmation) return null;
        return el("button", {
          class: "primary", type: "button", disabled: plan.active,
          onclick: async (event) => {
            const button = event.target;
            button.disabled = true;
            button.textContent = "starting…";
            try {
              await apiSend("POST", `/api/runs/${runId}/advance`, { stage: stage.name, confirm: true });
              toast(`Stage '${stage.name}' started. Its worst-case cost is in the row above; Stop is on the Progress card.`);
              route();
            } catch (error) {
              toast(error.message);
              button.disabled = false;
              button.textContent = "Confirm & run";
            }
          },
        }, "Confirm & run");
      }
      const rows = plan.stages.map((stage) => el("tr", {},
        el("td", { class: "small" }, el("strong", {}, stage.label)),
        el("td", {}, stage.status === "done"
          ? el("span", { class: "badge ok" }, "done")
          : stage.status === "blocked"
            ? el("span", { class: "badge muted" }, "blocked")
            : el("span", { class: "badge info" }, "pending")),
        el("td", { class: "num" }, String(stage.worst_case_calls ?? 0)),
        el("td", { class: "num small" }, stage.worst_case_usd === null || stage.worst_case_usd === undefined
          ? "cost unavailable, call cap is binding"
          : `~US$${Number(stage.worst_case_usd).toFixed(4)}`),
        el("td", { class: "small muted" }, stage.detail || ""),
        el("td", {}, confirmButton(stage))));
      body.append(el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "stage"), el("th", {}, "status"), el("th", {}, "worst-case calls"),
            el("th", {}, "worst-case cost"), el("th", {}, "detail"), el("th", {}, ""))),
          el("tbody", {}, rows))));

      if (plan.gate) {
        const gate = plan.gate;
        body.append(el("div", {
          class: gate.passed ? "banner info" : "banner danger",
          style: { marginTop: "12px" },
        },
          el("strong", {}, gate.passed ? "Pilot gate: continue" : "Pilot gate: STOP"),
          el("p", { class: "small" }, gate.decision || ""),
          el("p", { class: "small muted" },
            `${gate.n_disagreements ?? "?"} disagreement(s) across ${gate.n_scored_pilot_verdicts ?? "?"} pilot verdict(s); `,
            `the pre-registered floor is ${gate.min_disagreements_required ?? 5} on ${gate.pilot_size ?? 40} items.`,
            gate.passed ? "" : " The run stopped here by design; the partial report is still written and no correlation claim is made.")));
      }

      body.append(el("div", { class: "row", style: { marginTop: "12px" } },
        el("a", { class: "btn", href: `#/review/${runId}` }, "Review flagged items"),
        el("a", { class: "btn", href: `#/results/${runId}` }, "Results"),
        plan.next_stage ? null : el("button", { class: "", type: "button", onclick: async () => {
          try {
            await apiSend("POST", `/api/runs/${runId}/resume`, {});
            toast("Run resumed; completed calls are never repeated.");
            route();
          } catch (error) { toast(error.message); }
        } }, "Resume run")));
      body.append(el("p", { class: "note" },
        "Each stage shows its worst-case cost before it runs and needs an explicit confirmation. ",
        "You can stop after any stage and resume later; if you restarted the app, re-enter the keys on the Models page first."));
    }
    refresh();
    return card;
  }

  /* ------------------------------------------------------------ page: review queue */
  async function renderReview(view, runId) {
    beginRender(view);
    const queue = await apiGet(`/api/runs/${runId}/review`);
    if (!queue.items.length) {
      view.append(pageHead("Review queue", "Nothing to review: this run has no judged items yet."));
      view.append(el("a", { class: "btn", href: `#/run/${runId}` }, "Back to the run"));
      return;
    }
    let index = 0;
    const labels = queue.labels;

    view.append(pageHead("Review queue",
      "One reviewer's labels and notes, keyboard-driven. Not a reference standard; never part of the headline metrics."));
    view.append(el("div", { class: "banner warn", style: { marginBottom: "14px" } },
      el("p", { class: "small" }, queue.caveat),
      el("p", { class: "small" },
        "Labels are saved in a git-ignored local file. The report's sensitivity table recomputes the numbers with ",
        el("strong", {}, `'${queue.sensitivity_label}'`), " items removed.")));

    const position = el("p", { class: "mono small" });
    const itemHeading = el("h2", {});
    const noteArea = el("textarea", { rows: 3, "aria-label": "Note for this item", placeholder: "Optional note (stays local; may quote item text)" });
    const labelRow = el("div", { class: "row" });
    let current = queue.items[0];

    function paint() {
      current = queue.items[index];
      itemHeading.textContent = current.item_id;
      noteArea.value = current.note || "";
      position.textContent = `${index + 1} of ${queue.items.length} · ${runId}`;
      clear(labelRow);
      for (const label of labels) {
        labelRow.append(el("button", {
          class: current.label === label ? "primary" : "", type: "button",
          "aria-pressed": current.label === label ? "true" : "false",
          onclick: () => save(label),
        }, label));
      }
      labelRow.append(el("button", { class: "ghost", type: "button", onclick: () => save("") }, "Clear label"));
    }

    async function save(label) {
      try {
        const saved = await apiSend("POST", `/api/runs/${runId}/review`, {
          item_id: current.item_id, label, note: noteArea.value,
        });
        current.label = saved.label || "";
        current.note = saved.note || "";
        toast(saved.label ? `Saved: ${saved.label}` : "Label cleared");
        paint();
      } catch (error) { toast(error.message); }
    }

    function step(delta) {
      index = Math.max(0, Math.min(queue.items.length - 1, index + delta));
      paint();
    }

    view.append(el("div", { class: "card" },
      position, itemHeading,
      el("p", { class: "small muted" },
        "Only the item id is shown here so the label can be recorded without copying benchmark text into this page."),
      labelRow, noteArea,
      el("div", { class: "row", style: { marginTop: "10px" } },
        el("button", { class: "", type: "button", onclick: () => step(-1) }, "← Previous"),
        el("button", { class: "", type: "button", onclick: () => step(1) }, "Next →"),
        el("button", { class: "", type: "button", onclick: () => save(current.label || "") }, "Save note"),
        el("div", { class: "spacer" }),
        el("a", { class: "btn", href: `#/run/${runId}` }, "Back to the run")),
      el("p", { class: "note" }, "Keyboard: n / p to move, 1-4 to label, click away to return.")));

    function onKey(event) {
      const tag = (event.target && event.target.tagName) || "";
      if (["TEXTAREA", "INPUT", "SELECT"].includes(tag)) return;
      if (event.key === "n") { step(1); event.preventDefault(); }
      else if (event.key === "p") { step(-1); event.preventDefault(); }
      else if (["1", "2", "3", "4"].includes(event.key)) { save(labels[Number(event.key) - 1]); event.preventDefault(); }
    }
    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", function once() {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("hashchange", once);
    });
    paint();
  }

  /* ------------------------------------------------------------ paper packs
     A Paper Pack is data (papers/<id>/paper.yaml). The builder keeps the
     BYOK rule: nothing is preselected. Targets and judges are checkbox lists of
     the owner's saved connections, and a dry run is the default. */

  function packBadge(pack) {
    if (pack.kind === "archived_study") return el("span", { class: "badge muted" }, "archived · read-only");
    if (pack.validation && pack.validation.ok) return el("span", { class: "badge ok" }, "valid");
    return el("span", { class: "badge bad" }, "invalid");
  }

  function packRow(pack) {
    const metrics = pack.validation || {};
    return el("tr", {},
      el("td", {}, el("a", { href: `#/packs/${encodeURIComponent(pack.id)}` }, pack.id),
        pack.preset ? el("span", { class: "badge accent", style: "margin-left:6px" }, "preset") : null,
        el("div", { class: "small muted" }, pack.title)),
      el("td", {}, el("span", { class: "badge info" }, pack.task_type)),
      el("td", {}, pack.version || "—"),
      el("td", {}, pack.dataset_count == null ? "—" : String(pack.dataset_count)),
      el("td", {}, pack.methodology_kind || "—"),
      el("td", {}, packBadge(pack)),
      el("td", {}, (metrics.warnings || []).length ? el("span", { class: "small muted" }, `${(metrics.warnings || []).length} warning(s)`) : "—"));
  }

  async function renderPacks(view) {
    beginRender(view);
    const payload = await apiGet("/api/packs?include_archived=true");
    const packs = payload.packs || [];
    const archived = payload.archived || [];
    view.append(pageHead("Paper Packs",
      "Declarative medical-AI evaluation protocols. A pack is data: task, dataset adapter, prompts, graders and a statistical protocol.",
      el("a", { class: "btn", href: "#/new" }, "Classic runs")));
    view.append(el("div", { class: "banner info" },
      el("h3", {}, "Research tool — not a medical device"),
      el("p", {}, "Paper Packs benchmark published methods; they are not clinically validated and no clinician reviewed them. Dry runs are PIPELINE TEST ONLY.")));
    const cards = el("div", { class: "grid cols-2" });
    cards.append(el("div", { class: "card" },
      el("h2", {}, "Registry"),
      el("p", { class: "muted" }, `${packs.length} runnable pack(s) under papers/; ${archived.length} archived study run(s).`),
      el("p", { class: "small muted" }, `Response cache: ${payload.cache ? payload.cache.entries : 0} entries, ${payload.cache ? payload.cache.bytes : 0} bytes.`),
      el("p", { class: "small" }, "Create a starter pack: ",
        el("code", {}, "python -m medeval paper init my-pack --task mcq"))));
    cards.append(el("div", { class: "card" },
      el("h2", {}, "How to read a pack"),
      el("ul", { class: "small" }, el("li", {}, "methodology is tagged faithful reproduction, inspired adaptation or synthetic template"),
        el("li", {}, "graders are deterministic checks and/or an LLM rubric panel"),
        el("li", {}, "comparison is McNemar (binary) or paired bootstrap (scores)"),
        el("li", {}, "the answer key is never rendered into a target prompt"))));
    view.append(cards);
    if (!packs.length) {
      view.append(empty("No Paper Packs yet", "Add one under papers/ or scaffold a starter pack.", el("code", {}, "python -m medeval paper init my-pack --task mcq")));
    } else {
      view.append(el("div", { class: "card" }, el("h2", {}, "Runnable packs"),
        el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, el("th", {}, "Pack"), el("th", {}, "Task"), el("th", {}, "Version"),
            el("th", {}, "Items"), el("th", {}, "Methodology"), el("th", {}, "Status"), el("th", {}, "Notes"))),
          el("tbody", {}, ...packs.map(packRow))))));
    }
    if (archived.length) {
      const archivedBody = el("tbody", {}, ...archived.map((row) => el("tr", {},
        el("td", {}, row.title), el("td", {}, row.task_type), el("td", {}, packBadge(row)))));
      const archivedTable = el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "Run"), el("th", {}, "Task"), el("th", {}, "Status"))),
        archivedBody);
      view.append(el("div", { class: "card" },
        el("h2", {}, "Archived studies (read-only)"),
        el("p", { class: "muted small" }, "Experiment 002/003 runs predate Paper Packs. They remain readable and are never re-run as packs."),
        el("div", { class: "table-wrap" }, archivedTable)));
    }
  }

  async function renderPackBuilder(view, packId) {
    beginRender(view);
    const [pack, connections, roles] = await Promise.all([
      apiGet(`/api/packs/${encodeURIComponent(packId)}`),
      apiGet("/api/connections"),
      apiGet("/api/roles"),
    ]);
    const usable = connections.filter((row) => row.model);
    view.append(pageHead(pack.title, `${pack.id} v${pack.version} · ${pack.task_type}`,
      el("a", { class: "btn", href: "#/packs" }, "Back to registry")));
    view.append(el("div", { class: "banner info" },
      el("h3", {}, "Methodology card"),
      el("p", {}, `${pack.methodology.kind} — ${pack.methodology.summary}`),
      el("p", { class: "small" }, `Pack hash ${String(pack.pack_hash).slice(0, 12)} · prompt hash ${String(pack.prompt_hash).slice(0, 12)} · pack licence ${pack.license}`),
      el("p", { class: "small muted" }, pack.methodology.limitations || "No pack-specific limitations declared.")));
    const source = pack.source_dataset || {};
    if (pack.preset || source.url || source.huggingface) {
      const sourceLink = source.url
        ? el("a", { href: source.url, target: "_blank", rel: "noopener noreferrer" }, source.name || "upstream dataset")
        : el("span", {}, source.huggingface || "upstream dataset");
      view.append(el("div", { class: "banner" },
        el("h3", {}, "Upstream data is not bundled"),
        el("p", {}, sourceLink, source.huggingface ? el("span", { class: "small muted" }, ` · Hugging Face: ${source.huggingface}`) : null),
        el("p", { class: "small" }, `Licence: ${source.license || "see upstream"}`),
        el("p", { class: "small muted" }, source.notes || "The repository ships synthetic smoke rows only."),
        el("p", { class: "small" }, `Adaptation: ${pack.adaptation_status || "not declared"}`),
        el("p", { class: "small" }, "Load a licensed dataset on demand: ",
          el("code", {}, `python papers/${pack.id}/loader.py --check`))));
    }

    const dry = el("input", { type: "checkbox", id: "matrix-dry", checked: true });
    const nItems = el("input", { id: "matrix-items", type: "number", min: "1", value: String(pack.dataset.count || 8) });
    const maxCalls = el("input", { id: "matrix-calls", type: "number", min: "1", value: "200" });
    const maxUsd = el("input", { id: "matrix-usd", type: "number", min: "0", step: "0.01", value: "1.00" });
    const targetBoxes = usable.map((row) => {
      const box = el("input", { type: "checkbox", value: row.id, id: `target-${row.id}` });
      return { row, box };
    });
    const judgeBoxes = usable.map((row) => {
      const box = el("input", { type: "checkbox", value: row.id, id: `judge-${row.id}` });
      return { row, box };
    });
    const assigned = new Set((roles.judges || []).map((row) => row.connection_id));
    judgeBoxes.forEach(({ row, box }) => { if (assigned.has(row.id)) box.checked = true; });
    const planBox = el("div", { class: "card" }, el("p", { class: "muted" }, "Computing pre-flight plan…"));
    const startButton = el("button", { class: "primary", type: "button", "data-testid": "matrix-start" }, "Start matrix run");

    function selected(boxes) { return boxes.filter((item) => item.box.checked).map((item) => item.row.id); }
    function familiesDiffer(boxes) {
      const families = boxes.filter((item) => item.box.checked).map((item) => (item.row.family_label || "").toLowerCase());
      return families.length >= 2 && new Set(families).size >= 2;
    }
    const familyNote = el("p", { class: "small muted" });
    function refreshFamilyNote() {
      const chosen = judgeBoxes.filter((item) => item.box.checked);
      if (!chosen.length) { familyNote.textContent = "No judge selected: deterministic graders only (or mock judges in a dry run)."; return; }
      familyNote.textContent = chosen.length >= 2 && familiesDiffer(judgeBoxes)
        ? "Cross-family panel: error correlation can be estimated."
        : "Same family (or family unset): cross-family error correlation cannot be measured.";
    }

    async function refreshPlan() {
      const body = {
        paper: pack.id,
        dry_run: dry.checked,
        targets: selected(targetBoxes),
        judges: selected(judgeBoxes),
        n_items: Number(nItems.value) || null,
        max_calls: Number(maxCalls.value) || 200,
        max_usd: Number(maxUsd.value) || 0,
      };
      try {
        const data = await apiPost("/api/matrix/plan", body);
        const plan = data.plan || {};
        clear(planBox);
        planBox.append(el("h2", {}, "Pre-flight plan card"));
        planBox.append(el("p", {}, dry.checked
          ? el("span", { class: "badge accent" }, "PIPELINE TEST ONLY")
          : el("span", { class: "badge warn" }, "LIVE — real calls")));
        planBox.append(el("table", {},
          el("tbody", {},
            el("tr", {}, el("th", {}, "Items"), el("td", {}, String(plan.n_items))),
            el("tr", {}, el("th", {}, "Planned calls"), el("td", {}, `${plan.total_calls} (${plan.target_calls} target, ${plan.judge_calls} judge)`)),
            el("tr", {}, el("th", {}, "Cached target outputs"), el("td", {}, String(plan.n_cached_target_outputs || 0))),
            el("tr", {}, el("th", {}, "Token upper bound"), el("td", {}, `${plan.input_tokens_upper_bound} in / ${plan.output_tokens_upper_bound} out`)),
            el("tr", {}, el("th", {}, "Worst-case cost"), el("td", {}, plan.usd_upper_bound == null ? "unavailable (a connection declares no prices; call cap is binding)" : `US$${Number(plan.usd_upper_bound).toFixed(4)}`)))));
        planBox.append(el("p", { class: "small muted" }, "This is a ceiling from your declared prices and max_tokens, not a bill. Caps are checked before every dispatched call."));
        startButton.disabled = false;
      } catch (error) {
        clear(planBox);
        planBox.append(el("div", { class: "banner danger" }, el("h3", {}, "Plan unavailable"), el("p", {}, error.message || String(error))));
        startButton.disabled = true;
      }
    }

    [...targetBoxes, ...judgeBoxes].forEach(({ box }) => box.addEventListener("change", () => { refreshFamilyNote(); refreshPlan(); }));
    [dry, nItems, maxCalls, maxUsd].forEach((node) => node.addEventListener("change", refreshPlan));
    refreshFamilyNote();

    const targetList = usable.length
      ? el("div", { class: "grid cols-2" }, ...targetBoxes.map(({ row, box }) => el("label", { class: "small", for: box.id },
        box, " ", el("strong", {}, row.name), " ", el("span", { class: "muted" }, `${row.model} · ${row.family_label || "family not set"}`))))
      : el("p", { class: "muted small" }, "No saved connection has a model id yet. A dry run still works with labelled mock targets; add connections on the Models page for a live run.");
    const judgeList = usable.length
      ? el("div", { class: "grid cols-2" }, ...judgeBoxes.map(({ row, box }) => el("label", { class: "small", for: box.id },
        box, " ", el("strong", {}, row.name), " ", el("span", { class: "muted" }, `${row.model} · ${row.family_label || "family not set"}`))))
      : el("p", { class: "muted small" }, "No saved connections. Dry runs use two cross-family mock judges when the pack has a rubric grader.");

    const form = el("div", { class: "card" },
      el("h2", {}, "Matrix builder"),
      el("p", { class: "small muted" }, "One Paper Pack × N target models × M graders/judges. Nothing is preselected and no provider is recommended."),
      el("h3", {}, "Target models (models under test)"), targetList,
      el("h3", {}, "Graders / judges"), judgeList, familyNote,
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "matrix-items" }, "Items"), nItems, el("span", { class: "hint" }, "Pack sampling and limit still apply.")),
        el("div", { class: "field" }, el("label", { for: "matrix-calls" }, "Run call cap"), maxCalls),
        el("div", { class: "field" }, el("label", { for: "matrix-usd" }, "Run USD cap"), maxUsd, el("span", { class: "hint" }, "Only binding when every called connection declares prices.")),
        el("div", { class: "field" }, el("label", { for: "matrix-dry" }, "Dry run (mock, no network)"), dry)),
      el("div", { class: "row" }, startButton));
    view.append(el("div", { class: "grid cols-2" }, form, planBox));
    await refreshPlan();

    startButton.addEventListener("click", async () => {
      startButton.disabled = true;
      try {
        const started = await apiPost("/api/matrix/runs", {
          paper: pack.id,
          dry_run: dry.checked,
          targets: selected(targetBoxes),
          judges: selected(judgeBoxes),
          n_items: Number(nItems.value) || null,
          max_calls: Number(maxCalls.value) || 200,
          max_usd: Number(maxUsd.value) || 0,
          confirm_real_calls: !dry.checked,
        });
        location.hash = `#/matrix/${started.run_id}`;
      } catch (error) {
        startButton.disabled = false;
        toast(error.message || String(error));
      }
    });
  }

  function metricTable(metrics) {
    const targets = metrics.targets || {};
    const rows = Object.entries(targets).map(([targetId, row]) => {
      const primary = row.primary || {};
      const interval = primary.ci95 || {};
      return el("tr", {},
        el("td", {}, el("strong", {}, row.label || targetId), el("div", { class: "small muted" }, row.model || "")),
        el("td", {}, num(primary.estimate)),
        el("td", {}, `[${num(interval.low)}, ${num(interval.high)}]`),
        el("td", {}, String(row.n_items)),
        el("td", {}, pct((row.abstention || {}).rate)),
        el("td", {}, pct((row.format_compliance || {}).rate)),
        el("td", {}, num((row.latency || {}).p50_ms, 1)),
        el("td", {}, num(row.estimated_usd, 4)));
    });
    return el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Target"), el("th", {}, metrics.primary_metric || "metric"),
        el("th", {}, "95% CI"), el("th", {}, "n"), el("th", {}, "Abstention"), el("th", {}, "Format"),
        el("th", {}, "p50 ms"), el("th", {}, "Est. USD"))),
      el("tbody", {}, ...rows)));
  }

  function pairwiseTable(metrics) {
    const rows = (metrics.pairwise || []).map((row) => el("tr", {},
      el("td", {}, row.left), el("td", {}, row.right), el("td", {}, row.method),
      el("td", {}, String(row.n)), el("td", {}, num(row.mean_difference)),
      el("td", {}, row.method === "mcnemar" ? `p = ${num(row.p_value, 4)}` : ci(row.ci95_difference)),
      el("td", {}, row.significant ? "yes" : "no")));
    return rows.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Left"), el("th", {}, "Right"), el("th", {}, "Method"),
        el("th", {}, "n"), el("th", {}, "Difference"), el("th", {}, "p / CI"), el("th", {}, "Significant"))),
      el("tbody", {}, ...rows))) : el("p", { class: "muted" }, "No pairwise comparison (one target or too few paired items).");
  }

  function reliabilityBlock(metrics) {
    const rel = metrics.judge_reliability || {};
    if (!rel.available) return el("p", { class: "muted" }, rel.note || "No LLM judge panel.");
    const bits = [el("p", {}, `Fleiss' κ ${num(rel.fleiss_kappa)} · Krippendorff's α ${num(rel.krippendorff_alpha)} · Cohen's κ ${num(rel.cohen_kappa)}`)];
    const pairs = (rel.disagreement || {}).pairs || [];
    if (pairs.length) {
      bits.push(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "Judge A"), el("th", {}, "Judge B"), el("th", {}, "n"), el("th", {}, "Disagreement"))),
        el("tbody", {}, ...pairs.map((pair) => el("tr", {}, el("td", {}, pair.left), el("td", {}, pair.right),
          el("td", {}, String(pair.n)), el("td", {}, pct(pair.disagreement_rate))))))));
    }
    const cross = rel.cross_family || [];
    if (cross.length) {
      bits.push(el("p", { class: "small muted" }, "Cross-family error correlation (positive φ = shared blind spots):"));
      bits.push(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "A"), el("th", {}, "B"), el("th", {}, "Families"), el("th", {}, "φ"), el("th", {}, "Both wrong"))),
        el("tbody", {}, ...cross.map((row) => el("tr", {}, el("td", {}, row.left), el("td", {}, row.right),
          el("td", {}, `${row.left_family} / ${row.right_family}`), el("td", {}, num(row.phi)), el("td", {}, pct(row.both_wrong_rate))))))));
    }
    return el("div", {}, ...bits);
  }

  async function renderMatrixItems(container, runId, filter) {
    clear(container);
    container.append(el("p", { class: "muted" }, "Loading items…"));
    const payload = await apiGet(`/api/matrix/runs/${runId}/items?filter=${encodeURIComponent(filter)}`);
    clear(container);
    container.append(el("p", { class: "small muted" }, `${payload.items.length} of ${payload.total_items} item(s) match “${filter.replace(/_/g, " ")}”.`));
    if (!payload.items.length) { container.append(el("p", { class: "muted" }, "No item matches this filter.")); return; }
    for (const item of payload.items) {
      const card = el("div", { class: "card" }, el("h3", {}, item.item_id));
      card.append(el("div", { class: "row small" },
        item.model_disagreement ? el("span", { class: "badge warn" }, "model disagreement") : null,
        item.judge_disagreement ? el("span", { class: "badge info" }, "judge disagreement") : null,
        item.format_failures ? el("span", { class: "badge bad" }, `${item.format_failures} format failure(s)`) : null,
        item.errors ? el("span", { class: "badge bad" }, `${item.errors} error(s)`) : null));
      for (const target of item.targets) {
        const details = el("details", { class: "small" },
          el("summary", {}, el("strong", {}, target.label || target.target_id), " — ",
            target.primary_value == null ? "no score" : num(target.primary_value),
            target.error ? el("span", { class: "badge bad" }, "error") : null));
        if (target.error) details.append(el("p", { class: "badge bad" }, target.error));
        details.append(el("pre", { class: "mono small" }, target.text || "(no output)"));
        if (target.checks.length) {
          details.append(el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "Check"), el("th", {}, "Metric"), el("th", {}, "Value"), el("th", {}, "Detail"))),
            el("tbody", {}, ...target.checks.map((check) => el("tr", {}, el("td", {}, check.grader_id),
              el("td", {}, check.metric), el("td", {}, num(check.value)), el("td", {}, check.detail || check.error || "—")))))));
        }
        if (target.judges.length) {
          details.append(el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "Judge"), el("th", {}, "Score"), el("th", {}, "Rationale"), el("th", {}, "Error"))),
            el("tbody", {}, ...target.judges.map((judge) => el("tr", {}, el("td", {}, judge.judge_id),
              el("td", {}, num(judge.value)), el("td", {}, judge.rationale || "—"), el("td", {}, judge.error || "—")))))));
        }
        card.append(details);
      }
      container.append(card);
    }
  }

  async function renderMatrixRun(view, runId) {
    beginRender(view);
    const payload = await apiGet(`/api/matrix/runs/${runId}`);
    const run = payload.run || {};
    const paper = run.paper || {};
    const metrics = payload.metrics || {};
    view.append(pageHead(paper.title || runId, `${paper.id || ""} · ${run.status}`,
      el("a", { class: "btn", href: "#/packs" }, "Paper Packs"),
      el("a", { class: "btn", href: `#/matrix/${runId}/report` }, "Report")));
    view.append(el("div", { class: "row" }, statusBadge(run.status, payload.active),
      run.pipeline_test_only ? el("span", { class: "badge accent" }, "PIPELINE TEST ONLY") : el("span", { class: "badge muted" }, "live"),
      el("span", { class: "small muted" }, `run ${runId}`)));
    view.append(el("div", { class: "banner info" },
      el("h3", {}, "Research tool — not a medical device"),
      el("p", {}, "No clinical validation; no clinician reviewed any output. Dry runs say nothing about any real model.")));
    if (payload.error) view.append(el("div", { class: "banner danger" }, el("h3", {}, "Run error"), el("p", {}, payload.error)));

    const actions = el("div", { class: "row" });
    if (payload.active) {
      actions.append(el("button", { class: "btn", type: "button", "data-testid": "matrix-stop" }, "Stop"),
        el("span", { class: "small muted" }, "Stop saves every completed item; Resume continues without repeating calls."));
      actions.firstChild.addEventListener("click", async () => {
        await apiPost(`/api/matrix/runs/${runId}/stop`, {});
        toast("Stopping after the current call; completed items are saved.");
      });
    } else if (["stopped", "budget_stopped", "failed"].includes(run.status)) {
      actions.append(el("button", { class: "btn", type: "button", "data-testid": "matrix-resume" }, "Resume"));
      actions.firstChild.addEventListener("click", async () => {
        try { await apiPost(`/api/matrix/runs/${runId}/resume`, {}); toast("Resume queued."); route(); }
        catch (error) { toast(error.message || String(error)); }
      });
    }
    actions.append(el("button", { class: "btn", type: "button", "data-testid": "matrix-export" }, "Export bundle"));
    actions.lastChild.addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/matrix/runs/${runId}/bundle`, { headers: authHeaders() });
        if (!response.ok) throw new Error(`bundle download failed (${response.status})`);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${runId}-bundle.zip`;
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      } catch (error) { toast(error.message || String(error)); }
    });
    const regradeButton = el("button", { class: "btn", type: "button" }, "Offline re-grade (deterministic)");
    regradeButton.addEventListener("click", async () => {
      regradeButton.disabled = true;
      try {
        const result = await apiPost(`/api/matrix/runs/${runId}/regrade`, { demo: true, dry_run: true });
        toast(`Re-graded as ${result.run_id} (0 target calls).`);
        location.hash = `#/matrix/${result.run_id}`;
      } catch (error) { regradeButton.disabled = false; toast(error.message || String(error)); }
    });
    actions.append(regradeButton);
    view.append(actions);

    if (payload.active || ["queued", "running"].includes(run.status)) {
      view.append(el("div", { class: "card" }, el("h2", {}, "Progress"),
        el("p", { class: "mono small" }, `${(payload.outputs || []).length} recorded target output(s) · ${(payload.scores || []).length} score(s)`),
        el("p", { class: "small muted" }, "This page refreshes automatically until the run reaches a terminal state.")));
      const timer = setTimeout(() => {
        if (location.hash === `#/matrix/${runId}`) route();
      }, 1500);
      window.addEventListener("hashchange", function once() { clearTimeout(timer); window.removeEventListener("hashchange", once); });
    }

    if (metrics.targets) {
      view.append(el("div", { class: "card" }, el("h2", {}, "Summary"),
        el("p", { class: "small muted" }, `Primary metric: ${metrics.primary_metric} · n=${metrics.n_items} · errors=${metrics.n_errors}`),
        metricTable(metrics)));
      view.append(el("div", { class: "grid cols-2" },
        el("div", { class: "card" }, el("h2", {}, "Pairwise comparisons"), pairwiseTable(metrics)),
        el("div", { class: "card" }, el("h2", {}, "Judge agreement and error correlation"), reliabilityBlock(metrics))));
      view.append(el("div", { class: "card" }, el("h2", {}, "Limitations"),
        el("ul", { class: "small" }, ...(metrics.limitations || []).map((text) => el("li", {}, text)))));
    }

    const explorer = el("div", { class: "card" }, el("h2", {}, "Head-to-head and disagreement explorer"));
    const filters = ["all", "model_disagreements", "judge_disagreements", "format_failures", "errors"];
    const filterRow = el("div", { class: "row" });
    const itemsBox = el("div", {});
    let activeFilter = "all";
    for (const filter of filters) {
      const button = el("button", { class: filter === activeFilter ? "btn primary" : "btn", type: "button" }, filter.replace(/_/g, " "));
      button.addEventListener("click", () => {
        activeFilter = filter;
        [...filterRow.children].forEach((node) => node.className = "btn");
        button.className = "btn primary";
        renderMatrixItems(itemsBox, runId, activeFilter);
      });
      filterRow.append(button);
    }
    explorer.append(filterRow, itemsBox);
    view.append(explorer);
    await renderMatrixItems(itemsBox, runId, activeFilter);
  }

  async function renderMatrixReport(view, runId) {
    beginRender(view);
    view.append(pageHead("Report", `run ${runId}`, el("a", { class: "btn", href: `#/matrix/${runId}` }, "Back to run")));
    let text = null;
    try {
      const response = await fetch(`/api/matrix/runs/${runId}/report`, { headers: authHeaders({ Accept: "text/plain" }) });
      if (response.ok) text = await response.text();
    } catch (_) { /* the empty state below explains it */ }
    view.append(text ? el("pre", { class: "mono small card" }, text) : el("p", { class: "muted" }, "No report yet."));
  }

  const routes = [
    [/^#\/$/, (view) => renderStart(view)],
    [/^#\/runs$/, (view) => renderRuns(view)],
    [/^#\/new$/, (view) => renderNew(view)],
    [/^#\/settings$/, (view) => renderSettings(view)],
    [/^#\/models$/, (view) => renderModels(view)],
    [/^#\/papers$/, (view) => renderPapers(view)],
    [/^#\/tests$/, (view) => renderTests(view)],
    [/^#\/packs$/, (view) => renderPacks(view)],
    [/^#\/packs\/([^/?]+)$/, (view, match) => renderPackBuilder(view, decodeURIComponent(match[1]))],
    [/^#\/matrix\/([^/?]+)\/report$/, (view, match) => renderMatrixReport(view, match[1])],
    [/^#\/matrix\/([^/?]+)$/, (view, match) => renderMatrixRun(view, match[1])],
    [/^#\/run\/([^/?]+)$/, (view, match) => renderRunDetail(view, match[1])],
    [/^#\/review\/([^/?]+)$/, (view, match) => renderReview(view, match[1])],
    [/^#\/results\/([^/?]+)$/, (view, match) => renderResults(view, match[1])],
    [/^#\/items\/([^/?]+)(?:\?(.*))?$/, (view, match) => renderItems(view, match[1], match[2])],
    [/^#\/session\/([^/?]+)$/, (view, match) => renderSession(view, match[1])],
  ];

  function navKeyFor(hash) {
    if (hash.startsWith("#/models")) return "models";
    if (hash.startsWith("#/packs") || hash.startsWith("#/matrix")) return "packs";
    if (hash.startsWith("#/papers")) return "papers";
    if (hash.startsWith("#/tests")) return "tests";
    if (hash.startsWith("#/settings")) return "settings";
    if (hash.startsWith("#/new")) return "new";
    return "runs";
  }

  async function route() {
    if (eventSource) { eventSource.close(); eventSource = null; }
    readLaunchToken();
    const hash = location.hash || "#/";
    const view = $("#view");
    clear(view);
    if (locked) {
      renderLockScreen(view);
      return;
    }
    renderSidebar(hash);
    const navKey = navKeyFor(hash);
    document.querySelectorAll("[data-nav]").forEach((link) => link.removeAttribute("aria-current"));
    const navLink = document.querySelector(`[data-nav="${navKey}"]`);
    if (navLink) navLink.setAttribute("aria-current", "page");

    for (const [pattern, handler] of routes) {
      const match = hash.match(pattern);
      if (match) {
        try {
          await handler(view, match);
        } catch (error) {
          clear(view);
          view.append(pageHead("Something went wrong", null));
          view.append(el("div", { class: "banner danger" }, el("h3", {}, "Error"), el("p", {}, error.message || String(error))));
        }
        window.scrollTo({ top: 0 });
        return;
      }
    }
    view.append(pageHead("Not found", null, el("a", { class: "btn", href: "#/" }, "Back to start")));
  }

  /* ------------------------------------------------------------ palette */
  const palette = $("#palette");
  const paletteInput = $("#palette-input");
  const paletteList = $("#palette-list");
  let paletteItems = [];
  let paletteIndex = 0;

  function commands() {
    const list = [
      { label: "Go to Runs", hint: "g r", run: () => { location.hash = "#/runs"; } },
      { label: "Start a new run", hint: "g n", run: () => { location.hash = "#/new"; } },
      { label: "Open Settings", hint: "g s", run: () => { location.hash = "#/settings"; } },
      { label: "Open Models (connections and keys)", hint: "", run: () => { location.hash = "#/models"; } },
      { label: "Open Papers library", hint: "", run: () => { location.hash = "#/papers"; } },
      { label: "Open Paper Packs", hint: "", run: () => { location.hash = "#/packs"; } },
      { label: "Open Test center", hint: "", run: () => { location.hash = "#/tests"; } },
      { label: "Toggle theme", hint: "t", run: () => { cycleTheme(); } },
    ];
    for (const run of state.runs.slice(0, 6)) {
      list.push({ label: `Results: ${run.run_id}`, hint: "results", run: () => { location.hash = `#/results/${run.run_id}`; } });
      list.push({ label: `Items: ${run.run_id}`, hint: "items", run: () => { location.hash = `#/items/${run.run_id}`; } });
    }
    return list;
  }

  function renderPalette(filter = "") {
    paletteItems = commands().filter((command) => command.label.toLowerCase().includes(filter.toLowerCase()));
    paletteIndex = 0;
    clear(paletteList);
    paletteItems.forEach((command, index) => {
      paletteList.append(el("li", {
        role: "option",
        "aria-selected": index === paletteIndex ? "true" : "false",
        onclick: () => { closePalette(); command.run(); },
      }, command.label, el("span", { class: "kbd" }, command.hint || "")));
    });
  }

  function openPalette() {
    palette.hidden = false;
    paletteInput.value = "";
    renderPalette("");
    paletteInput.focus();
  }

  function closePalette() {
    palette.hidden = true;
    $("#palette-open").focus();
  }

  paletteInput.addEventListener("input", () => renderPalette(paletteInput.value));
  paletteInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!paletteItems.length) return;
      paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : -1) + paletteItems.length) % paletteItems.length;
      [...paletteList.children].forEach((node, index) => node.setAttribute("aria-selected", index === paletteIndex ? "true" : "false"));
    } else if (event.key === "Enter") {
      const command = paletteItems[paletteIndex];
      if (command) { closePalette(); command.run(); }
    } else if (event.key === "Escape") {
      closePalette();
    }
  });
  palette.addEventListener("click", (event) => { if (event.target === palette) closePalette(); });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (palette.hidden) openPalette(); else closePalette();
      return;
    }
    if (event.key === "Escape" && !palette.hidden) {
      closePalette();
      return;
    }
    handleChord(event);
  });

  /* ------------------------------------------------------------ chords
     g r / g n / g s jump between pages; t cycles the theme. Chords are ignored
     while the user is typing in any input, textarea or select, with modifiers
     held, or while the palette is open (the palette has its own handling). */
  let pendingChordPrefix = null;

  function typingTarget(event) {
    const node = event.target;
    if (!node) return false;
    const tag = (node.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || node.isContentEditable;
  }

  const CHORDS = {
    r: () => { location.hash = "#/runs"; },
    n: () => { location.hash = "#/new"; },
    s: () => { location.hash = "#/settings"; },
  };

  function handleChord(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (!palette.hidden) return;
    if (typingTarget(event)) { pendingChordPrefix = null; return; }
    const key = event.key.toLowerCase();
    if (pendingChordPrefix === "g") {
      pendingChordPrefix = null;
      if (CHORDS[key]) {
        event.preventDefault();
        CHORDS[key]();
      }
      return;
    }
    if (key === "g") {
      pendingChordPrefix = "g";
      return;
    }
    if (key === "t") {
      event.preventDefault();
      cycleTheme();
    }
  }

  /* ------------------------------------------------------------ boot */
  const state = { runs: [], settings: null, workspaces: [], roles: null, connections: [] };

  $("#palette-open").addEventListener("click", openPalette);
  document.querySelectorAll(".theme-option").forEach((button) => {
    button.addEventListener("click", () => setTheme(button.dataset.themeChoice));
  });
  $("#sidebar-collapse").addEventListener("click", () => {
    $("#sidebar").classList.add("collapsed");
    $("#sidebar-open").hidden = false;
    localStorage.setItem("medeval.sidebar", "collapsed");
  });
  $("#sidebar-open").addEventListener("click", () => {
    $("#sidebar").classList.remove("collapsed");
    $("#sidebar-open").hidden = true;
    localStorage.setItem("medeval.sidebar", "open");
  });
  if (localStorage.getItem("medeval.sidebar") === "collapsed") {
    $("#sidebar").classList.add("collapsed");
    $("#sidebar-open").hidden = false;
  }
  $("#new-session").addEventListener("click", () => { location.hash = "#/"; });
  window.addEventListener("hashchange", route);
  window.addEventListener("hashchange", readLaunchToken);

  applyTheme(currentTheme());
  // Read the launch token BEFORE the first API call: otherwise the boot health
  // check 403s, sets the lock flag, and every later route shows the lock screen
  // even though the tab holds a valid token.
  readLaunchToken();
  apiGet("/api/health")
    .then((health) => { $("#version")?.replaceChildren(document.createTextNode(`v${health.version}`)); })
    .catch(() => { /* the app still works; the header just shows no version */ });

  route();
})();
