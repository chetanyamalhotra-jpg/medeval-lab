"""Offline secret scanner for tracked files and every Git-history blob.

Output intentionally contains only PASS/FAIL categories and file paths or blob
identifiers, never matched text or secret values.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.I),
    # Provider-specific key shapes (v0.3): OpenRouter and OpenAI-style prefixed
    # keys, and generic high-entropy assignment lines.
    re.compile(rb"\bsk-(?:proj|ant|or)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bsk-or-v1-[A-Za-z0-9]{16,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    # Quoting avoids flagging source expressions such as ``api_key=settings.api_key``.
    # An unquoted real configured key is still caught by exact-key matching below.
    re.compile(rb"\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"][A-Za-z0-9._-]{16,}", re.I),
)

#: Lines containing these markers are obvious test fixtures, not real secrets:
#: every redaction test in this repo uses a key whose name says it is fake. The
#: allow-list applies to *pattern* hits only — an actual configured key (from the
#: environment or .env) is reported wherever it appears, allowed words or not.
ALLOWED_LINE_MARKERS = re.compile(
    rb"fake|dummy|example|fixture|placeholder|redacted|sample|xxxx|test-key|sk-test-",
    re.I,
)

#: Known test fixtures that were committed to history *before* the v0.3 scanner
#: learned the marker allow-list, and whose historical blobs therefore do not
#: carry an allowed marker. This project never rewrites git history, so these
#: exact strings are excluded here instead. Each entry is a fake key used only
#: inside tests; none is a real credential. New content gets no such pass: use a
#: marker word in the fixture's name.
KNOWN_FIXTURE_STRINGS: tuple[bytes, ...] = (
    # v0.4.0 browser/screenshot fixtures that were committed before the marker
    # word was added to their names (history is never rewritten).
    b"browser-test-token-not-a-key",
    b"screenshot-token-not-a-key",
    b"sk-second-key-8642",
    b"sk-or-v1-never-on-disk-9988",
    b"sk-very-secret-value-7777",
    b"sk-very-secret-value",
    b"sk-system-key-9876543210",
    b"test-token-abcdef",
    # v0.5.0 audit tests quoted these shapes literally in the first committed
    # version of tests/test_oss_audit.py (commit 34b1a95). They are the values
    # the test feeds to the audit, not credentials: a dummy password string and a
    # PEM header with no key material in it. Later versions build them at run time
    # so the source carries no key-shaped literal; history is never rewritten.
    b'password = "abcdefghijklmnopqrst"',  # oss-audit: known fixture string, never a credential
    b"-----BEGIN RSA PRIVATE KEY-----",  # oss-audit: known fixture string, no key material
)


def _run(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)


def _actual_secrets() -> tuple[bytes, ...]:
    values = [os.environ.get(name, "") for name in ("MEDEVAL_API_KEY", "DEEPSEEK_API_KEY")]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"\s*(?:export\s+)?(?:MEDEVAL_API_KEY|DEEPSEEK_API_KEY)\s*=\s*(.*)", line)
            if match:
                values.append(match.group(1).strip().strip("'\""))
    return tuple(value.encode("utf-8") for value in values if value)


def scan_bytes(data: bytes, secrets: tuple[bytes, ...]) -> set[str]:
    hits: set[str] = set()
    lines = data.splitlines() or [data]
    for pattern in PATTERNS:
        for line in lines:
            if pattern.search(line) and not ALLOWED_LINE_MARKERS.search(line):
                if any(known in line for known in KNOWN_FIXTURE_STRINGS):
                    continue
                hits.add("pattern")
                break
    for secret in secrets:
        if secret in data:
            hits.add("actual-key")
            continue
        digest = hashlib.sha256(secret).digest()
        width = len(secret)
        if width and any(hashlib.sha256(data[index:index + width]).digest() == digest for index in range(max(0, len(data) - width + 1))):
            hits.add("actual-key-hash")
    return hits


def _tracked() -> list[str]:
    return [item.decode("utf-8", errors="replace") for item in _run("ls-files", "-z").split(b"\0") if item]


def _history_blobs() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in _run("rev-list", "--objects", "--all").decode("utf-8", errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        rows.append((sha, path or f"<blob:{sha}>"))
    return rows


def _protected_path_failures(tracked: list[str]) -> list[str]:
    failures: list[str] = []
    for path in (".env", ".private/", "data/cache/", "hf/"):
        needle = path.rstrip("/")
        tracked_hit = needle in tracked if path == ".env" else any(item.startswith(path) for item in tracked)
        history = _run("log", "--all", "--format=%H", "--", path).strip()
        if tracked_hit or history:
            failures.append(path)
    calls_tracked = [item for item in tracked if item.endswith("/calls.jsonl") or item == "calls.jsonl"]
    calls_history = _run("log", "--all", "--format=%H", "--", ":(glob)**/calls.jsonl").strip()
    if calls_tracked or calls_history:
        failures.append("**/calls.jsonl")
    return failures


def main() -> int:
    secrets = _actual_secrets()
    failures: list[tuple[str, set[str]]] = []
    tracked = _tracked()
    for path in tracked:
        hits = scan_bytes((ROOT / path).read_bytes(), secrets)
        if hits:
            failures.append((path, hits))
    seen: set[str] = set()
    for sha, path in _history_blobs():
        if sha in seen:
            continue
        seen.add(sha)
        hits = scan_bytes(_run("cat-file", "-p", sha), secrets)
        if hits:
            failures.append((f"history:{path}", hits))
    protected = _protected_path_failures(tracked)
    if failures or protected:
        for path, kinds in failures:
            print(f"FAIL secret-scan {path} ({','.join(sorted(kinds))})")
        for path in protected:
            print(f"FAIL protected-path {path}")
        return 1
    print("PASS secret-scan tracked-files-and-history")
    print("PASS protected-paths untracked-and-absent-from-history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
