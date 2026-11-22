"""Workspaces and sessions: the harness-style organisation of lab activity.

A **workspace** is a study (for example "Experiment 003"); a **session** is one
run or one working conversation inside it. Old runs from v0.2 appear
automatically as sessions in an "Experiment 002" workspace, read from the run
directories on disk — nothing is copied.

The model is stored in a git-ignored JSON file: it is local organisation, not
project content. No key, token or secret is ever part of a session record.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC
from pathlib import Path
from typing import Any

from medeval_core.calllog import utc_now_iso
from medeval_study.registry import list_runs

__all__ = ["WorkspaceStore"]


class WorkspaceStore:
    """Workspaces containing sessions, persisted without secrets."""

    def __init__(self, path: Path, runs_root: Path | None = None) -> None:
        self.path = path
        self._runs_root = runs_root
        self._lock = threading.Lock()
        self._load()

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        self.workspaces: list[dict[str, Any]] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.workspaces = payload.get("workspaces") or []
        for session in payload.get("sessions") or []:
            self.sessions[session["id"]] = session

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "workspaces": self.workspaces,
            "sessions": list(self.sessions.values()),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- workspaces ------------------------------------------------------- #
    def ensure_workspace(self, name: str) -> dict[str, Any]:
        with self._lock:
            for workspace in self.workspaces:
                if workspace["name"] == name:
                    return workspace
            workspace = {
                "id": f"ws-{uuid.uuid4().hex[:8]}",
                "name": name,
                "created_utc": utc_now_iso(),
            }
            self.workspaces.append(workspace)
            self._save()
            return workspace

    def create_workspace(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("workspace name is required")
        with self._lock:
            if any(workspace["name"] == name for workspace in self.workspaces):
                raise ValueError(f"workspace {name!r} already exists")
        return self.ensure_workspace(name)

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._lock:
            workspaces = [dict(workspace) for workspace in self.workspaces]
        for workspace in workspaces:
            workspace["sessions"] = self.list_sessions(workspace["id"])
        # The Ungrouped folder exists even when empty.
        if not any(workspace["name"] == "Ungrouped" for workspace in workspaces):
            workspaces.append({"id": "ws-ungrouped", "name": "Ungrouped", "created_utc": None, "sessions": []})
        return workspaces

    # -- sessions --------------------------------------------------------- #
    def create_session(
        self,
        workspace_id: str,
        title: str,
        *,
        kind: str = "conversation",
        hypothesis: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip() or "Untitled session"
        session = {
            "id": f"s-{uuid.uuid4().hex[:10]}",
            "workspace_id": workspace_id,
            "title": title,
            "kind": kind,  # conversation | run
            "hypothesis": hypothesis,
            "run_id": run_id,
            "created_utc": utc_now_iso(),
            "messages": [],
        }
        with self._lock:
            self.sessions[session["id"]] = session
            self._save()
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self.sessions.get(session_id)
            return dict(session) if session else None

    def append_message(self, session_id: str, role: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            message = {
                "role": role,  # user | system | card
                "kind": kind,  # note | plan | progress | results | flagged | report | error
                "payload": payload,
                "utc": utc_now_iso(),
            }
            session.setdefault("messages", []).append(message)
            self._save()
            return message

    def link_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            session["run_id"] = run_id
            session["kind"] = "run"
            self._save()
            return session

    def list_sessions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sessions = [dict(session) for session in self.sessions.values()]
        if workspace_id:
            sessions = [session for session in sessions if session["workspace_id"] == workspace_id]
        # Surface run-backed sessions with their live status.
        runs = {run.run_id: run for run in list_runs()}
        for session in sessions:
            run_id = session.get("run_id")
            if run_id and run_id in runs:
                run = runs[run_id]
                session["run_status"] = run.status
                session["pipeline_test_only"] = run.pipeline_test_only
            session["relative_time"] = _relative(session.get("created_utc"))
        sessions.sort(key=lambda row: row.get("created_utc") or "", reverse=True)
        return sessions

    def import_run_as_session(self, run_id: str, workspace_name: str) -> dict[str, Any] | None:
        """Make an existing run visible as a session (used for v0.2 runs)."""
        runs = {run.run_id: run for run in list_runs()}
        if run_id not in runs:
            return None
        workspace = self.ensure_workspace(workspace_name)
        for session in self.list_sessions(workspace["id"]):
            if session.get("run_id") == run_id:
                return session
        return self.create_session(
            workspace["id"],
            run_id,
            kind="run",
            run_id=run_id,
        )

    def import_all_runs(self, default_workspace: str = "Experiment 002") -> list[str]:
        imported: list[str] = []
        for run in list_runs():
            session = self.import_run_as_session(run.run_id, default_workspace)
            if session:
                imported.append(run.run_id)
        return imported


def _relative(iso: str | None) -> str:
    if not iso:
        return ""
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    delta = datetime.now(UTC) - moment
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"
