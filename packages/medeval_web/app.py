"""Local-only web app for configuring runs, watching progress and reading results.

Design constraints that shaped this module:

* **Loopback only, launch-token gated.** :func:`medeval_web.server.serve` binds
  ``127.0.0.1`` and generates a random token per launch; API calls must carry it
  (``X-Medeval-Token``). This is local-site protection, not remote auth: since
  v0.3 the app accepts API keys and can spend money, so other browser tabs,
  other local users, and cross-site pages must not be able to drive it.
* **No build step, no CDN.** The frontend is hand-written HTML, CSS and JavaScript
  served from ``static/``; nothing is fetched from the internet at runtime.
* **Progress comes from disk, not from memory.** The event stream tails the run
  directory, so a page reload, a second tab, or a run started from the terminal
  all show the same progress. Nothing is lost when the browser closes.
* **Live runs are opt-in.** Starting a run that can spend money requires
  ``confirm_real_calls``; the default form state is a dry run.
* **Keys live in memory only.** See :mod:`medeval_web.connections`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from medeval_core.backends import select_backend
from medeval_core.calllog import utc_now_iso as _utc_now
from medeval_core.config import Settings, load_settings
from medeval_core.paths import data_dir, literature_csv, repo_root, results_dir
from medeval_core.version import __version__
from medeval_study.datasets import DATASET_LICENCES
from medeval_study.mock_bindings import dry_run_judge_bindings
from medeval_study.registry import item_table, list_runs, load_json, load_report, load_run, run_root
from medeval_study.runner import StudyConfig, StudyRun, build_judges
from medeval_web.connections import PRESETS, ConnectionStore, KeyStore
from medeval_web.papers import PaperStore, fetch_metadata
from medeval_web.review import ReviewStore
from medeval_web.testcenter import TestCenter
from medeval_web.workspaces import WorkspaceStore

__all__ = ["TERMINAL_STATUSES", "create_app", "generate_launch_token"]

STATIC_DIR = Path(__file__).parent / "static"
TERMINAL_STATUSES = {
    "completed", "stopped", "budget_stopped", "failed", "blocked", "refused", "aborted",
    "pilot_stopped", "awaiting_confirmation", "finished",
}
#: Experiment 003 advances one confirmed stage at a time; these are its stages.
EXP003_STAGES = ("screening", "pool", "pilot", "remaining")


def generate_launch_token() -> str:
    """A fresh random token for one app launch."""
    return secrets.token_urlsafe(24)


class RunManager:
    """Starts and stops the in-app runs. One at a time, by design."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}

    def is_active(self, run_id: str) -> bool:
        thread = self._threads.get(run_id)
        return bool(thread and thread.is_alive())

    def active_run(self) -> str | None:
        for run_id, thread in self._threads.items():
            if thread.is_alive():
                return run_id
        return None

    def start(self, config: StudyConfig, settings: Settings, *, run_factory: Any = None) -> str:
        with self._lock:
            running = self.active_run()
            if running:
                raise RuntimeError(f"run {running} is already active; stop it first")
            stop = threading.Event()
            factory = run_factory or (lambda cfg, st, flag: StudyRun(cfg, st, stop_flag=flag))
            run = factory(config, settings, stop)
            with contextlib.suppress(FileExistsError, OSError):
                run.run_dir.mkdir(parents=True, exist_ok=True)
            existing = (
                json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
                if (run.run_dir / "run.json").is_file()
                else {}
            )
            state = {
                **existing,
                "run_id": run.run_id,
                "experiment": config.experiment,
                "status": "loading",
                # A queued run must carry its start time: the run page shows it
                # and "started —" was a bug, not a missing value.
                "started_utc": existing.get("started_utc") or _utc_now(),
                "config": config.as_dict(),
                "stage": getattr(config, "stage", "all"),
            }
            (run.run_dir / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            thread = threading.Thread(target=self._worker, args=(run,), name=f"study-{run.run_id}", daemon=True)
            self._threads[run.run_id] = thread
            self._stops[run.run_id] = stop
            thread.start()
            return run.run_id

    def _worker(self, run: StudyRun) -> None:
        try:
            run.execute()
        except Exception as exc:
            state = {
                "run_id": run.run_id,
                "experiment": run.config.experiment,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            with contextlib.suppress(OSError):
                (run.run_dir / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def stop(self, run_id: str) -> bool:
        stop = self._stops.get(run_id)
        if stop is None or not self.is_active(run_id):
            return False
        stop.set()
        return True


def _settings() -> Settings:
    return load_settings()


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _count_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except OSError:
        return rows
    return rows


def create_app(
    manager: RunManager | None = None,
    *,
    token: str | None = None,
    allowed_hosts: set[str] | None = None,
    connections_path: Path | None = None,
    settings: Settings | None = None,
    local_data_dir: Path | None = None,
    literature_path: Path | None = None,
    metadata_base: str | None = None,
    matrix_root: Path | None = None,
    cache_root: Path | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    ``token`` enables launch-token gating: every ``/api/*`` request must carry it
    in the ``X-Medeval-Token`` header. ``allowed_hosts`` is the set of acceptable
    ``Host`` headers (``serve`` passes the loopback forms of the bound port);
    ``None`` skips the check, which only tests do. ``settings`` pins the Settings
    used for runs so ``--use-env`` is an explicit choice rather than an accident.
    ``local_data_dir`` redirects the writable stores (connections, paper notes,
    workspaces) and ``literature_path`` the paper registry — tests use both to
    stay out of the real data directory.
    """
    app = FastAPI(title="medeval-lab", version=__version__, docs_url=None, redoc_url=None)
    runs = manager or RunManager()
    app.state.run_manager = runs
    local_data = local_data_dir
    keys = KeyStore()
    store = ConnectionStore(connections_path or ((local_data or data_dir()) / "connections.json"))
    papers = PaperStore(literature_path or literature_csv(), (local_data or data_dir()) / "paper_notes.json")
    tests = TestCenter(repo_root())
    spaces = WorkspaceStore((local_data or data_dir()) / "workspaces.json", runs_root=run_root())
    review = ReviewStore((local_data or data_dir()) / "review_notes.json")
    spaces.import_all_runs()
    app.state.key_store = keys
    app.state.connection_store = store
    app.state.launch_token = token
    app.state.settings = settings
    app.state.papers = papers
    app.state.tests = tests
    #: Metadata endpoint for the Papers page. Tests point this at a fake Crossref
    #: server; production leaves it None so the real endpoints are used.
    app.state.metadata_base = metadata_base
    app.state.workspaces = spaces

    def _server_settings() -> Settings:
        return settings or _settings()

    # v0.6 Paper Packs + matrix runs. The router owns its own run root and
    # content cache so tests can keep them inside a temporary directory.
    from medeval_web.packs_api import attach_pack_routes

    attach_pack_routes(
        app,
        store=store,
        keys=keys,
        settings=_server_settings(),
        cache_root=cache_root,
        matrix_root=matrix_root,
    )

    def _redaction_secrets() -> tuple[str, ...]:
        """Every secret this process could leak, for redaction sinks."""
        return (*keys.all_keys(), _server_settings().api_key)

    @app.middleware("http")
    async def _local_protection(request: Request, call_next: Any) -> Any:
        """Loopback hardening: Host check, Origin check, token gate, headers.

        * ``Host`` must be one of the loopback forms of the bound port, so a
          request addressed to some other host name (DNS rebinding) is refused.
        * A cross-site ``Origin`` is refused, so a page on another origin cannot
          drive the API even from the owner's browser.
        * ``/api/*`` requires the launch token; the token never travels in a URL
          (the browser keeps it in ``sessionStorage`` and sends the header).
        * Every response gets ``no-store`` and a conservative security-header
          set.  The UI is same-origin static HTML/CSS/JS, so the CSP can be
          strict: no third-party script, connect or frame origins.
        """
        path = request.url.path
        host = request.headers.get("host", "")

        def _secure(response: Any) -> Any:
            """Apply the same headers to refusals as to normal responses."""
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            # Same-origin only. ``unsafe-inline`` is limited to styles because
            # the lightweight UI sets a few style attributes on generated nodes.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            )
            return response

        if allowed_hosts is not None and host not in allowed_hosts:
            return _secure(JSONResponse({"detail": "this server only accepts loopback Host headers"}, status_code=403))
        origin = request.headers.get("origin")
        if origin:
            parts = urlsplit(origin)
            origin_host = parts.netloc.lower()
            if allowed_hosts is not None and origin_host not in {host.lower() for host in allowed_hosts}:
                return _secure(JSONResponse({"detail": "cross-site requests are not allowed"}, status_code=403))
        if token is not None and path.startswith("/api/"):
            supplied = request.headers.get("x-medeval-token", "")
            if not secrets.compare_digest(supplied, token):
                return _secure(JSONResponse(
                    {"detail": "missing or invalid launch token; open the app with medeval ui"},
                    status_code=403,
                ))
        response = await call_next(request)
        return _secure(response)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "active_run": runs.active_run()}

    @app.get("/api/presets")
    def presets() -> dict[str, Any]:
        return {
            "presets": [
                {"key": key, "label": row["label"], "base_url": row["base_url"],
                 "api_style": row["api_style"], "requires_key": row["requires_key"],
                 "docs_url": row["docs_url"], "pricing_url": row.get("pricing_url", ""),
                 "last_verified": row.get("last_verified", ""),
                 # Presets never carry a price: the fields start empty and the
                 # owner copies the current price from pricing_url.
                 "prices_default": None}
                for key, row in PRESETS.items()
            ],
            "note": "Presets only fill a base URL and links. No price is shipped: set one from the provider's pricing page or leave it empty and the call cap is binding.",
        }

    # ---------------------------------------------------------------- #
    # Connections: bring your own provider, model and key (memory only)
    # ---------------------------------------------------------------- #
    def _connection_view(connection_id: str) -> dict[str, Any]:
        connection = store.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
        view = connection.as_dict(key_tail=keys.tail(connection_id))
        view["warnings"] = connection.warnings()
        return view

    @app.get("/api/connections")
    def list_connections() -> list[dict[str, Any]]:
        rows = []
        for connection in store.list():
            view = connection.as_dict(key_tail=keys.tail(connection.id))
            view["warnings"] = connection.warnings()
            rows.append(view)
        return rows

    @app.post("/api/connections")
    async def create_connection(request: Request) -> JSONResponse:
        body = await request.json()
        # A key may arrive here; it goes straight to memory and is never stored.
        key = str(body.pop("key", "") or "").strip()
        connection, problems = store.create(body)
        if problems:
            duplicate = any("already exists" in problem for problem in problems)
            raise HTTPException(
                status_code=409 if duplicate else 400,
                detail="; ".join(problems),
            )
        if key:
            keys.set(connection.id, key)
        return JSONResponse(_connection_view(connection.id), status_code=201)

    @app.put("/api/connections/{connection_id}")
    async def update_connection(connection_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        key = str(body.pop("key", "") or "").strip()
        connection, problems = store.update(connection_id, body)
        if problems:
            raise HTTPException(status_code=400, detail="; ".join(problems))
        if connection is None:
            raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
        if key:
            keys.set(connection_id, key)
        return _connection_view(connection_id)

    @app.delete("/api/connections/{connection_id}")
    def delete_connection(connection_id: str) -> dict[str, Any]:
        if not store.delete(connection_id):
            raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
        keys.forget(connection_id)
        return {"deleted": connection_id}

    @app.post("/api/connections/{connection_id}/key")
    async def set_key(connection_id: str, request: Request) -> dict[str, Any]:
        if store.get(connection_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
        body = await request.json()
        key = str(body.get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="key must not be empty")
        keys.set(connection_id, key)
        return {"connection_id": connection_id, "key_set": True, "key_last4": keys.tail(connection_id)}

    @app.delete("/api/connections/{connection_id}/key")
    def forget_key(connection_id: str) -> dict[str, Any]:
        forgotten = keys.forget(connection_id)
        return {"connection_id": connection_id, "key_set": not forgotten}

    @app.get("/api/roles")
    def get_roles() -> dict[str, Any]:
        return store.roles().as_dict()

    @app.post("/api/roles")
    async def set_roles(request: Request) -> dict[str, Any]:
        body = await request.json()
        roles, problems = store.set_roles(body)
        if problems:
            raise HTTPException(status_code=400, detail="; ".join(problems))
        return roles.as_dict()

    @app.post("/api/connections/{connection_id}/test")
    async def test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        """List models from the endpoint. No spend, unless explicitly confirmed."""
        from medeval_web.probe import probe_connection

        connection = store.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail=f"unknown connection {connection_id!r}")
        body = await request.json() if (await request.body()).strip() else {}
        result = probe_connection(
            connection,
            api_key=keys.get(connection_id),
            confirm_one_token_call=bool(body.get("confirm_one_token_call")),
            timeout_s=20.0,
        )
        return result

    # ---------------------------------------------------------------- #
    # Papers library (data/literature.csv + git-ignored local notes)
    # ---------------------------------------------------------------- #
    @app.get("/api/papers")
    def list_papers() -> list[dict[str, Any]]:
        return papers.rows()

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: str) -> dict[str, Any]:
        row = papers.get(paper_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown paper {paper_id!r}")
        return {**row, "cited_by": papers.cited_by(paper_id)}

    @app.post("/api/papers/fetch")
    async def fetch_paper_metadata(request: Request) -> dict[str, Any]:
        """User-initiated outbound metadata fetch (Crossref or arXiv).

        The response names the URL that was contacted so the call is never
        silent; nothing is stored until the caller posts /api/papers.
        """
        body = await request.json()
        identifier = str(body.get("identifier") or "").strip()
        if not identifier:
            raise HTTPException(status_code=400, detail="identifier (DOI or arXiv id) is required")
        base_override = body.get("base_override") or getattr(app.state, "metadata_base", None) or None
        try:
            metadata = fetch_metadata(identifier, base_override=base_override)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=502, detail=f"metadata fetch failed: {exc}") from exc
        return {"fetched_from": metadata.pop("fetched_url", ""), "metadata": metadata}

    @app.post("/api/papers")
    async def add_paper(request: Request) -> JSONResponse:
        body = await request.json()
        row = {column: str(body.get(column, "") or "") for column in (
            "id", "doi", "title", "year", "task", "reference_standard",
            "metric_proposed", "how_we_operationalize", "limitations",
        )}
        row["verified"] = "no"  # only scripts/verify_literature.py may set yes
        if not row["id"] or not row["title"]:
            raise HTTPException(status_code=400, detail="id and title are required")
        try:
            papers.append(row)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(papers.get(row["id"]), status_code=201)

    @app.post("/api/papers/{paper_id}/status")
    async def set_paper_status(paper_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        try:
            return papers.set_status(paper_id, str(body.get("status") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/papers/{paper_id}/notes")
    async def set_paper_notes(paper_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return papers.set_notes(paper_id, str(body.get("notes") or ""))

    # ---------------------------------------------------------------- #
    # Test center: allow-listed commands only
    # ---------------------------------------------------------------- #
    @app.get("/api/tests/commands")
    def list_test_commands() -> list[dict[str, Any]]:
        return tests.commands()

    @app.get("/api/tests/results")
    def test_results() -> list[dict[str, Any]]:
        return tests.as_list()

    @app.post("/api/tests/run")
    async def run_test_command(request: Request) -> dict[str, Any]:
        body = await request.json()
        key = str(body.get("command") or "")
        allowed = {row["key"] for row in tests.commands()}
        if key not in allowed:
            raise HTTPException(status_code=400, detail=f"command {key!r} is not allow-listed")
        return tests.start(key)

    # ---------------------------------------------------------------- #
    # Workspaces and sessions
    # ---------------------------------------------------------------- #
    @app.get("/api/workspaces")
    def list_workspaces() -> list[dict[str, Any]]:
        return spaces.list_workspaces()

    @app.post("/api/workspaces")
    async def create_workspace(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            workspace = spaces.create_workspace(str(body.get("name") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(workspace, status_code=201)

    @app.post("/api/workspaces/{workspace_id}/sessions")
    async def create_session(workspace_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        session = spaces.create_session(
            workspace_id,
            str(body.get("title") or ""),
            hypothesis=str(body.get("hypothesis") or ""),
        )
        if body.get("hypothesis"):
            spaces.append_message(
                session["id"], "user", "note",
                {"text": body["hypothesis"]},
            )
        return JSONResponse(spaces.get_session(session["id"]), status_code=201)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = spaces.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}")
        return session

    @app.post("/api/sessions/{session_id}/messages")
    async def append_session_message(session_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        try:
            message = spaces.append_message(
                session_id,
                str(body.get("role") or "user"),
                str(body.get("kind") or "note"),
                dict(body.get("payload") or {}),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown session") from exc
        return JSONResponse(message, status_code=201)

    @app.post("/api/sessions/{session_id}/run")
    async def run_session(session_id: str, request: Request) -> dict[str, Any]:
        """Execute the session's plan: /run or /dryrun from the composer."""
        session = spaces.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown session")
        body = await request.json()
        dry_run = bool(body.get("dry_run", True))
        if runs.active_run():
            raise HTTPException(status_code=409, detail="a run is already active; stop it first")
        config = _config_from_composer(body, session)
        try:
            new_run_id = runs.start(config, _server_settings())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        spaces.link_run(session_id, new_run_id)
        spaces.append_message(session_id, "system", "plan", {
            "text": f"{'Dry run' if dry_run else 'Live run'} of {config.experiment} started as {new_run_id}",
            "run_id": new_run_id,
            "dry_run": dry_run,
        })
        return JSONResponse({"run_id": new_run_id, "session_id": session_id}, status_code=202)

    def _config_from_composer(body: dict[str, Any], session: dict[str, Any]) -> StudyConfig:
        """Build a StudyConfig from composer fields (mode, judges, spend policy)."""
        mode = str(body.get("mode") or "dry_run")
        is_dry = mode == "dry_run"
        experiment = str(body.get("experiment") or "exp003")
        n_items = int(body.get("n_items") or (140 if experiment == "exp003" else 60))
        config_kwargs: dict[str, Any] = {
            "experiment": "003_frontier_judges" if experiment == "exp003" else "002_judge_families",
            "dataset": str(body.get("dataset") or "medqa_4options"),
            "split": "dev",
            "n_items": n_items,
            "seed": int(body.get("seed") or (20261118 if experiment == "exp003" else 20260101)),
            "repeats": int(body.get("repeats") or (1 if experiment == "exp003" else 3)),
            "max_api_calls": int(body.get("max_api_calls") or 500),
            "dry_run": mode == "dry_run",
            "run_id": None,
            "resume": False,
        }
        if experiment == "exp003":
            judge_bindings, screening_binding = _bindings_from_roles()
            config_kwargs.update({
                "pilot_items": 40,
                "pilot_min_disagreements": 5,
                "screening_size": int(body.get("screening_size") or 600),
                "connection_judges": judge_bindings,
                "screening_binding": screening_binding,
                "stage": str(body.get("stage") or "screening"),
            })
            # A dry run needs no connections at all: it uses labelled mock judges
            # and a mock screener, which is the whole point of a free pipeline test.
            if is_dry and not config_kwargs["connection_judges"]:
                config_kwargs["connection_judges"] = dry_run_judge_bindings(1)
            if not is_dry and not config_kwargs["connection_judges"]:
                raise HTTPException(
                    status_code=400,
                    detail="Experiment 003 needs judges: add a provider connection on the Models page and "
                    "assign it to a judge role (the key stays in this app's memory)",
                )
        else:
            # Experiment 002 uses the connection judges assigned on the Models
            # page, so the same in-memory keys drive it. There is no fallback to a
            # provider baked into the code: with no connection there is no judge.
            judge_bindings, _screening = _bindings_from_roles()
            config_kwargs["judges"] = ()
            if judge_bindings:
                config_kwargs["connection_judges"] = judge_bindings
            elif is_dry:
                config_kwargs["connection_judges"] = dry_run_judge_bindings(config_kwargs["repeats"])
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Experiment 002 needs judges: add a provider connection on the Models page and "
                    "assign it to a judge role. Nothing is preconfigured and no model is chosen for you.",
                )
        return StudyConfig(**config_kwargs)

    def _bindings_from_roles() -> tuple[tuple[dict[str, Any], ...], dict[str, Any] | None]:
        """Build judge bindings from saved roles; keys come from memory only.

        Returns ``(judge_bindings, screening_binding)`` — the screening model is
        deliberately separate so it can never be treated as a judge.
        """
        roles = store.roles()
        bindings: list[dict[str, Any]] = []
        for role, connection_id in roles.judge_ids():
            connection = store.get(connection_id)
            if connection is None:
                continue
            bindings.append({
                "judge_id": f"{role}_{connection.name}".replace(" ", "-")[:40],
                "connection_id": connection.id,
                "family_label": connection.family_label,
                "backend": "api",
                "base_url": connection.base_url,
                "api_style": connection.api_style,
                "model": connection.model,
                "api_key": keys.get(connection.id),
                "temperature": connection.temperature,
                "repeats": 1,
                "max_tokens": connection.max_tokens,
                "call_cap": connection.per_connection_call_cap,
                "usd_cap": connection.per_connection_usd_cap,
                "price_in_per_mtok": connection.price_in_per_mtok,
                "price_out_per_mtok": connection.price_out_per_mtok,
            })
        screening_id = roles.screening
        screening_binding = None
        screening = store.get(screening_id) if screening_id else None
        if screening:
            screening_binding = {
                "connection_id": screening.id,
                "family_label": screening.family_label,
                "base_url": screening.base_url,
                "model": screening.model,
                "api_key": keys.get(screening.id),
                "max_tokens": screening.max_tokens,
                "call_cap": screening.per_connection_call_cap,
                "usd_cap": screening.per_connection_usd_cap,
            }
        return tuple(bindings), screening_binding

    def _connection_worst_case_usd(
        bindings: Any, *, n_items: int, repeats: int, estimated_input_tokens: int = 1200
    ) -> float | None:
        """Upper-bound spend from the prices the owner declared on each connection.

        Returns None when any connection declares no price, because inventing a
        price would make the estimate a guess: the call cap is then the binding
        limit and the page says so.
        """
        rows = list(bindings or ())
        if not rows:
            return None
        total = 0.0
        for binding in rows:
            price_in = binding.get("price_in_per_mtok")
            price_out = binding.get("price_out_per_mtok")
            if price_in is None and price_out is None:
                return None
            calls = max(1, n_items) * max(1, repeats) * max(1, int(binding.get("repeats") or 1))
            max_tokens = max(1, int(binding.get("max_tokens") or 1024))
            total += calls * (
                estimated_input_tokens * float(price_in or 0.0) + max_tokens * float(price_out or 0.0)
            ) / 1_000_000
        return round(total, 6)

    @app.get("/api/settings")
    def settings_payload() -> dict[str, Any]:
        settings = _settings()
        try:
            selection = select_backend(settings).as_dict()
        except Exception as exc:
            selection = {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "version": __version__,
            "repo_root": str(repo_root()),
            "data_dir": str(data_dir()),
            "results_dir": str(results_dir()),
            "run_root": str(run_root()),
            "backend": selection,
            # There is no built-in provider status to report: judges come from the
            # connections the owner creates. This is a count of local metadata, not
            # a model or provider named by the code (v0.6.0).
            "connections": {
                "saved": len(store.list()),
                "judges_assigned": len(store.roles().judge_ids()),
                "screening_assigned": bool(store.roles().screening),
                "keys_in_process_memory_only": True,
            },
            "caps": {"max_api_calls": settings.max_calls, "max_usd": settings.max_usd},
            "prices": {
                "verified": settings.input_usd_per_million is not None and settings.output_usd_per_million is not None,
                "input_per_million": settings.input_usd_per_million,
                "output_per_million": settings.output_usd_per_million,
                "source": settings.price_source,
                "source_url": settings.price_source_url,
                "source_date": settings.price_source_date,
            },
            "datasets": [
                {
                    "key": licence.key,
                    "hf_repo": licence.hf_repo,
                    "licence": licence.licence,
                    "redistribute": licence.redistribute,
                }
                for licence in DATASET_LICENCES.values()
            ],
            "env_files_loaded": list(settings.env_files_loaded),
        }

    @app.get("/api/runs")
    def get_runs() -> list[dict[str, Any]]:
        return [{**summary.as_dict(), "active": runs.is_active(summary.run_id)} for summary in list_runs()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        payload = load_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        return {**payload, "active": runs.is_active(run_id)}

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str) -> str:
        text = load_report(run_id)
        if text is None:
            raise HTTPException(status_code=404, detail="no report for this run yet")
        return text

    @app.get("/api/runs/{run_id}/items")
    def get_items(run_id: str) -> list[dict[str, Any]]:
        if not (run_root() / run_id).is_dir():
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        return item_table(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str) -> StreamingResponse:
        base = run_root() / run_id
        if not base.is_dir():
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")

        async def generator() -> Any:
            seen = 0
            for _ in range(14_400):  # ~4 hours at 1s
                state = load_json(base / "run.json") or {}
                rows = _count_jsonl(base / "judgements.jsonl")
                plan = state.get("plan") or {}
                snapshot = {
                    "run_id": run_id,
                    "status": state.get("status", "unknown"),
                    "done": len(rows),
                    "total": plan.get("total_calls"),
                    "calls_made": (state.get("usage") or {}).get("calls_made"),
                    "cache_hits": (state.get("usage") or {}).get("cache_hits"),
                    "active": runs.is_active(run_id),
                }
                yield _sse("snapshot", snapshot)
                for row in rows[seen:]:
                    yield _sse(
                        "judgement",
                        {
                            "item_id": row.get("item_id"),
                            "judge_id": row.get("judge_id"),
                            "repeat": row.get("repeat"),
                            "verdict": row.get("verdict"),
                            "confidence": row.get("confidence"),
                            "error": row.get("error"),
                        },
                    )
                seen = len(rows)
                if snapshot["status"] in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(1.0)
            yield _sse("closed", {"run_id": run_id})

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs")
    async def start_run(request: Request) -> JSONResponse:
        body = await request.json()
        dry_run = bool(body.get("dry_run", True))
        if not dry_run and not body.get("confirm_real_calls"):
            raise HTTPException(
                status_code=400,
                detail="a run with real judges must set confirm_real_calls=true",
            )
        # Judges come from the connections the owner assigned; nothing is
        # preconfigured. A dry run substitutes labelled mock judges.
        judge_bindings, _screening = _bindings_from_roles()
        if judge_bindings:
            connection_judges: tuple[dict[str, Any], ...] = judge_bindings
        elif dry_run:
            connection_judges = dry_run_judge_bindings(int(body.get("repeats", 3)))
        elif body.get("judges"):
            # The archived Experiment 002 ids may still be named explicitly.
            connection_judges = ()
        else:
            raise HTTPException(
                status_code=400,
                detail="Experiment 002 needs judges: add a provider connection on the Models page and "
                "assign it to a judge role. Nothing is preconfigured and no model is chosen for you.",
            )
        config = StudyConfig(
            experiment="002_judge_families",
            dataset=str(body.get("dataset", "medqa_4options")),
            split=str(body.get("split", "dev")),
            n_items=int(body.get("n_items", 60)),
            seed=int(body.get("seed", 20260101)),
            repeats=int(body.get("repeats", 3)),
            temperature=float(body.get("temperature", 0.3)),
            max_api_calls=int(body.get("max_api_calls", 500)),
            dry_run=dry_run,
            judges=() if connection_judges else tuple(body.get("judges") or ()),
            connection_judges=connection_judges,
            run_id=body.get("run_id") or None,
        )
        if config.n_items < 2:
            raise HTTPException(status_code=400, detail="n_items must be at least 2")
        if config.repeats < 1:
            raise HTTPException(status_code=400, detail="repeats must be at least 1")
        try:
            run_id = runs.start(config, _settings())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"run_id": run_id, "status": "queued"}, status_code=202)

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str) -> dict[str, Any]:
        if runs.stop(run_id):
            return {"run_id": run_id, "stopping": True}
        raise HTTPException(
            status_code=409,
            detail="this run is not active in the app (it may have been started from the terminal)",
        )

    # ---------------------------------------------------------------- #
    # Staged Experiment 003: plan, advance, resume
    # ---------------------------------------------------------------- #
    @app.get("/api/runs/{run_id}/stages")
    def get_stages(run_id: str) -> dict[str, Any]:
        """The stage plan with per-stage worst-case cost and status."""
        payload = load_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        study = _staged_study(run_id)
        if study is None:
            raise HTTPException(status_code=400, detail="this run is not a staged Experiment 003 run")
        study.state = {**(payload.get("run") or {})}
        return {
            "run_id": run_id,
            "status": study.state.get("status"),
            "next_stage": study.next_stage(),
            "stages": study.stage_plan(),
            "gate": study.state.get("pilot_gate"),
            "screening": study.state.get("screening"),
            # A blocked run explains itself on the card, in the same words the
            # run page uses.
            "blocked_reason": study.state.get("blocked_reason"),
            "active": runs.is_active(run_id),
        }

    @app.post("/api/runs/{run_id}/advance")
    async def advance_stage(run_id: str, request: Request) -> JSONResponse:
        """Run one confirmed stage of an Experiment 003 run.

        Keys live in memory only, so a stage advanced after an app restart is
        refused with a clear message until the owner re-enters the key on the
        Models page. The config lock still protects the design.
        """
        body = await request.json()
        if not body.get("confirm"):
            raise HTTPException(status_code=400, detail="advancing a stage requires confirm=true")
        if runs.active_run():
            raise HTTPException(status_code=409, detail=f"run {runs.active_run()} is already active; stop it first")
        payload = load_run(run_id) or {}
        stored = (payload.get("run") or {}).get("config") or {}
        study = _staged_study(run_id)
        if study is None:
            raise HTTPException(status_code=400, detail="this run is not a staged Experiment 003 run")
        study.state = {**(payload.get("run") or {})}
        wanted = str(body.get("stage") or "").strip() or study.next_stage()
        if wanted not in EXP003_STAGES:
            raise HTTPException(status_code=400, detail=f"unknown stage {wanted!r}; expected one of {list(EXP003_STAGES)}")
        # Strict rebuild next: a stage that would spend money must have its keys.
        _config_from_stored(stored, run_id)
        if study.next_stage() is None:
            return JSONResponse({"run_id": run_id, "status": "finished", "detail": "no stage remains"}, status_code=200)
        config = replace(study.config, run_id=run_id, resume=True, stage=wanted)
        study.config = config
        try:
            new_thread_id = runs.start(
                config,
                _settings(),
                run_factory=lambda cfg, st, flag: _study_for_config(cfg, st, flag),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        for session in spaces.list_sessions():
            if session.get("run_id") == run_id:
                spaces.append_message(
                    session["id"], "system", "plan",
                    {"text": f"Stage '{wanted}' confirmed and started.", "run_id": run_id, "stage": wanted},
                )
        return JSONResponse({"run_id": new_thread_id, "stage": wanted, "status": "queued"}, status_code=202)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> JSONResponse:
        """Resume an interrupted run (same design; completed calls are never repeated)."""
        if runs.active_run():
            raise HTTPException(status_code=409, detail=f"run {runs.active_run()} is already active; stop it first")
        payload = load_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        study = _staged_study(run_id)
        if study is not None:
            study.state = {**(payload.get("run") or {})}
            wanted = study.next_stage() or "remaining"
            config = replace(study.config, run_id=run_id, resume=True, stage=wanted)
        else:
            stored = (payload.get("run") or {}).get("config") or {}
            if not stored:
                raise HTTPException(status_code=400, detail="this run has no stored config to resume from")
            config = _config_from_stored(stored, run_id)
        try:
            runs.start(config, _settings())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"run_id": run_id, "status": "queued"}, status_code=202)

    def _staged_study(run_id: str) -> Any:
        """Rebuild an Exp003Study for a stored run, with keys from memory if any."""
        from medeval_study.exp003 import Exp003Study

        payload = load_run(run_id)
        if payload is None:
            return None
        state = payload.get("run") or {}
        stored = state.get("config") or {}
        if stored.get("experiment") != "003_frontier_judges":
            return None
        try:
            config = _config_from_stored(stored, run_id, tolerate_missing_keys=True)
        except HTTPException:
            config = _config_from_stored(stored, run_id, tolerate_missing_keys=True)
        config = replace(config, resume=True, stage=state.get("stage") or "screening")
        study = Exp003Study(config, _settings(), run_root=run_root())
        study.state = dict(state)
        return study

    def _config_from_stored(stored: dict[str, Any], run_id: str, *, tolerate_missing_keys: bool = False) -> StudyConfig:
        """Rebuild a StudyConfig from run.json, re-attaching keys held in memory."""
        judge_bindings, screening_binding = _bindings_from_roles()
        stored_judges = stored.get("connection_judges") or []
        if stored_judges and not tolerate_missing_keys:
            wanted = {binding.get("connection_id"): binding for binding in stored_judges}
            missing = [cid for cid in wanted if cid not in {b["connection_id"] for b in judge_bindings}]
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "the app no longer holds a key for connection(s) "
                        f"{', '.join(str(cid) for cid in missing)}: re-enter them on the Models page and "
                        "assign the same roles, then advance the stage again"
                    ),
                )
        if stored_judges:
            wanted = {binding.get("connection_id"): binding for binding in stored_judges}
            known = {binding["connection_id"] for binding in judge_bindings}
            merged = []
            for binding in judge_bindings:
                if binding["connection_id"] not in wanted:
                    continue
                override = {
                    key: value for key, value in wanted[binding["connection_id"]].items()
                    if key in {"judge_id", "repeats", "max_tokens", "temperature", "call_cap", "usd_cap",
                               "price_in_per_mtok", "price_out_per_mtok"}
                }
                merged.append({**binding, **override})
            if tolerate_missing_keys:
                for connection_id, binding in wanted.items():
                    if connection_id not in known:
                        merged.append({**binding, "backend": "api"})
            judge_bindings = tuple(merged)
        stored_screening = stored.get("screening_binding")
        if stored_screening and screening_binding is None:
            if not tolerate_missing_keys:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "the app no longer holds the screening model's key: re-enter it on the Models page "
                        "(Screening role) and advance the stage again"
                    ),
                )
            screening_binding = {**stored_screening, "backend": "api"}
        return StudyConfig(
            experiment=str(stored.get("experiment") or "003_frontier_judges"),
            dataset=str(stored.get("dataset") or "medqa_4options"),
            split=str(stored.get("split") or "dev"),
            n_items=int(stored.get("n_items") or 140),
            seed=int(stored.get("seed") or 20261118),
            repeats=int(stored.get("repeats") or 1),
            temperature=float(stored.get("temperature") or 0.3),
            max_api_calls=int(stored.get("max_api_calls") or 500),
            allow_download=bool(stored.get("allow_download", True)),
            dry_run=bool(stored.get("dry_run", False)),
            judges=tuple(stored.get("judges") or ()),
            connection_judges=judge_bindings,
            consistency_subsample=int(stored.get("consistency_subsample") or 0),
            pilot_items=int(stored.get("pilot_items") or 40),
            pilot_min_disagreements=int(stored.get("pilot_min_disagreements") or 5),
            screening_size=int(stored.get("screening_size") or 600),
            screening_binding=screening_binding,
            run_id=run_id,
            resume=True,
            stage=str(stored.get("stage") or "all"),
        )

    def _study_for_config(config: StudyConfig, settings: Settings, stop_flag: Any) -> Any:
        from medeval_study.exp003 import Exp003Study

        if config.experiment == "003_frontier_judges":
            return Exp003Study(config, settings, stop_flag=stop_flag)
        return StudyRun(config, settings, stop_flag=stop_flag)

    @app.post("/api/runs/{run_id}/export")
    def export_run(run_id: str, include_notes: bool = False) -> FileResponse:
        """Zip report bundle: ids and aggregates only (notes only if asked)."""
        from medeval_web.export import build_export_bundle

        try:
            path = build_export_bundle(
                run_id,
                include_notes=include_notes,
                review_notes_path=(local_data or data_dir()) / "review_notes.json",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # UnsafExport and anything else: refuse, never ship text
            raise HTTPException(status_code=409, detail=f"export refused: {exc}") from exc
        return FileResponse(path, media_type="application/zip", filename=path.name)

    # ---------------------------------------------------------------- #
    # Review queue: one reviewer's labels and notes (local, git-ignored)
    # ---------------------------------------------------------------- #
    @app.get("/api/runs/{run_id}/review")
    def get_review_queue(run_id: str) -> dict[str, Any]:
        payload = load_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        ids = _review_item_ids(run_id, payload)
        return review.queue(run_id, ids)

    @app.post("/api/runs/{run_id}/review")
    async def set_review_label(run_id: str, request: Request) -> dict[str, Any]:
        if load_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        body = await request.json()
        item_id = str(body.get("item_id") or "").strip()
        if not item_id:
            raise HTTPException(status_code=400, detail="item_id is required")
        try:
            return review.set_label(
                run_id, item_id, str(body.get("label") or ""), str(body.get("note") or "")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _review_item_ids(run_id: str, payload: dict[str, Any]) -> list[str]:
        """Flagged ids when the experiment has a queue, else every judged id."""
        from medeval_study.registry import item_table

        metrics = payload.get("metrics") or {}
        flagged = (metrics.get("review_queue") or {}).get("flagged_item_ids") or []
        if flagged:
            return list(flagged)
        try:
            rows = item_table(run_id)
        except Exception:
            rows = []
        ids = [str(row.get("item_id")) for row in rows if row.get("item_id")]
        if ids:
            return ids
        # Fall back to the judgement log (item ids only), so a run whose item
        # table is unavailable can still be reviewed by id.
        path = run_root() / run_id / "judgements.jsonl"
        seen: list[str] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item_id = str(json.loads(line).get("item_id") or "")
                if item_id and item_id not in seen:
                    seen.append(item_id)
        return seen

    # ---------------------------------------------------------------- #
    # Local preferences (first-run card dismissal)
    # ---------------------------------------------------------------- #
    @app.get("/api/preferences")
    def get_preferences() -> dict[str, Any]:
        return _prefs()

    @app.post("/api/preferences")
    async def set_preferences(request: Request) -> dict[str, Any]:
        body = await request.json()
        allowed = {"first_run_dismissed", "show_pipeline_test_only", "theme"}
        current = _prefs()
        for key, value in body.items():
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"unknown preference {key!r}")
            current[key] = value
        path = (local_data or data_dir()) / "preferences.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current

    def _prefs() -> dict[str, Any]:
        path = (local_data or data_dir()) / "preferences.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"first_run_dismissed": False}

    @app.get("/api/estimate")
    def estimate(
        n_items: int = 60,
        repeats: int = 3,
        seed: int = 20260101,
        temperature: float = 0.3,
        dry_run: bool = True,
        max_api_calls: int = 500,
    ) -> dict[str, Any]:
        settings = _settings()
        judge_bindings, _screening = _bindings_from_roles()
        if not judge_bindings and dry_run:
            judge_bindings = dry_run_judge_bindings(max(1, repeats))
        config = StudyConfig(
            n_items=max(1, n_items),
            repeats=max(1, repeats),
            seed=seed,
            temperature=temperature,
            max_api_calls=max_api_calls,
            dry_run=dry_run,
            judges=(),
            connection_judges=judge_bindings,
        )
        try:
            selection = select_backend(settings)
            run = StudyRun(config, settings, selection=selection)
            judges, detail = build_judges(config, settings, selection=selection)
            bundle, sampling, _responses = run.load()
            plan = run.plan(sampling, judges)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        worst_case_usd = _connection_worst_case_usd(
            judge_bindings, n_items=max(1, n_items), repeats=max(1, repeats)
        )
        return {
            "plan": plan,
            "judges": [judge.as_dict() for judge in judges],
            "resolution": detail,
            "dataset": bundle.as_dict(),
            "sampling": sampling.as_dict(),
            "dry_run": dry_run,
            "connection_plan": {
                "n_connections": len(judge_bindings),
                "worst_case_usd": worst_case_usd,
                "prices_declared": worst_case_usd is not None,
                "note": (
                    "upper bound from the prices declared on your connections"
                    if worst_case_usd is not None
                    else "cost unavailable, call cap is binding (no connection declares prices)"
                ),
            },
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.svg")
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
