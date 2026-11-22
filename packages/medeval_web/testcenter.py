"""Test center: an allow-listed command runner for the web app.

The browser may select an item from the list; it may **never** send a command
string. Every runnable item is defined here, server-side, with a fixed argument
vector, a fixed working directory and no shell. Launch-token protection is
handled by the app middleware.

Kept results: the last 20 runs with counts, ids and duration only — no output
text (output can quote arbitrary repository content), no secrets.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ALLOWED_COMMANDS", "TestCenter"]

#: The complete set of commands the browser can run. Anything not listed here
#: does not exist as far as the API is concerned.
ALLOWED_COMMANDS: dict[str, dict[str, Any]] = {
    "pytest": {
        "label": "pytest (full suite)",
        "argv": [sys.executable, "-m", "pytest"],
    },
    "ruff": {
        "label": "ruff check",
        "argv": [sys.executable, "-m", "ruff", "check", "."],
    },
    "secret-scan": {
        "label": "secret scan (tracked files)",
        "argv": [sys.executable, "scripts/scan_secrets.py"],
    },
    "traceability": {
        "label": "literature traceability check",
        "argv": [sys.executable, "-m", "medeval", "traceability"],
    },
    "key-isolation": {
        "label": "key isolation tests",
        "argv": [sys.executable, "-m", "pytest", "tests/test_connection_run.py", "tests/test_web_security.py"],
    },
    "dry-run-exp002": {
        "label": "Experiment 002 dry run (mock, no network)",
        "argv": [sys.executable, "-m", "medeval", "run", "exp002", "--dry-run", "--n-items", "6"],
    },
    "dry-run-exp003": {
        "label": "Experiment 003 dry run (mock, no network)",
        "argv": [sys.executable, "-m", "medeval", "run", "exp003", "--dry-run", "--n-items", "40", "--screening-size", "40"],
    },
}

import re as _re  # noqa: E402

_PASSED_LINE = _re.compile(r"(\d+) passed")
_FAILED_LINE = _re.compile(r"(\d+) failed")
_SKIPPED_LINE = _re.compile(r"(\d+) skipped")


@dataclass
class TestRun:
    """One finished allow-listed command."""

    id: str
    command_key: str
    label: str
    exit_code: int | None
    duration_s: float
    started_utc: str
    n_passed: int | None = None
    n_failed: int | None = None
    n_skipped: int | None = None
    timed_out: bool = False
    tail: str = ""  # last ~40 lines, redacted of any key-like string

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command_key": self.command_key,
            "label": self.label,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 2),
            "started_utc": self.started_utc,
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "n_skipped": self.n_skipped,
            "timed_out": self.timed_out,
            "tail": self.tail,
        }


def _summarise_pytest(text: str) -> tuple[int | None, int | None, int | None]:
    passed = failed = skipped = None
    match = _PASSED_LINE.search(text)
    if match:
        passed = int(match.group(1))
    match = _FAILED_LINE.search(text)
    if match:
        failed = int(match.group(1))
    match = _SKIPPED_LINE.search(text)
    if match:
        skipped = int(match.group(1))
    return passed, failed, skipped


_KEY_LIKE = _re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")


class TestCenter:
    """Runs allow-listed commands; keeps the last 20 result summaries."""

    def __init__(self, repo_root: Path, *, history_limit: int = 20) -> None:
        self.repo_root = repo_root
        self.history_limit = history_limit
        self._lock = threading.Lock()
        self.history: list[TestRun] = []
        self.running: dict[str, TestRun] = {}
        self._counter = 0

    def commands(self) -> list[dict[str, Any]]:
        return [
            {"key": key, "label": row["label"], "argv": row["argv"]}
            for key, row in ALLOWED_COMMANDS.items()
        ]

    def start(self, command_key: str) -> dict[str, Any]:
        """Run one allow-listed command synchronously and record the summary.

        (The app wraps this in a thread for streaming; the guard rails live
        here: fixed argv, fixed cwd, no shell, timeout.)
        """
        if command_key not in ALLOWED_COMMANDS:
            raise ValueError(f"command {command_key!r} is not allow-listed")
        spec = ALLOWED_COMMANDS[command_key]
        with self._lock:
            self._counter += 1
            run = TestRun(
                id=f"test-{self._counter:04d}",
                command_key=command_key,
                label=spec["label"],
                exit_code=None,
                duration_s=0.0,
                started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self.running[run.id] = run
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                spec["argv"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=1200,
                check=False,
                env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                     "PATH": __import__("os").environ.get("PATH", ""),
                     "PYTHONIOENCODING": "utf-8",
                     "MEDEVAL_SKIP_DATASETS_BUILDER": "1"},
            )
            run.exit_code = completed.returncode
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            if command_key.startswith("pytest"):
                run.n_passed, run.n_failed, run.n_skipped = _summarise_pytest(output)
        except subprocess.TimeoutExpired:
            run.timed_out = True
            output = "timed out after 1200s"
        except OSError as exc:
            run.exit_code = -1
            output = f"{type(exc).__name__}: {exc}"
        run.duration_s = time.perf_counter() - started
        lines = [line for line in output.strip().splitlines() if line.strip()]
        run.tail = _KEY_LIKE.sub("[REDACTED]", "\n".join(lines[-40:]))
        with self._lock:
            self.running.pop(run.id, None)
            self.history.insert(0, run)
            del self.history[self.history_limit:]
        return run.as_dict()

    def start_async(self, command_key: str, on_done: Any = None) -> str:
        """Run in a background thread; the app streams 'running' status."""
        if command_key not in ALLOWED_COMMANDS:
            raise ValueError(f"command {command_key!r} is not allow-listed")
        with self._lock:
            self._counter += 1
            run = TestRun(
                id=f"test-{self._counter:04d}",
                command_key=command_key,
                label=ALLOWED_COMMANDS[command_key]["label"],
                exit_code=None,
                duration_s=0.0,
                started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self.running[run.id] = run

        def worker() -> None:
            result = self.start(command_key)
            if on_done:
                on_done(result)

        threading.Thread(target=worker, daemon=True).start()
        return run.id

    def as_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [row.as_dict() for row in self.history]
