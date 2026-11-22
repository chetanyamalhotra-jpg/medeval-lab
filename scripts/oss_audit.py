"""Open-source release audit: what a stranger would receive if this repo were cloned.

The audit looks only at **tracked** content (plus, with ``--history``, every blob
reachable from any ref), because untracked files are not published by a clone.
It fails the build when it finds:

* a key-like string (``sk-...``, ``ghp_...``, ``AKIA...``, a bearer token, a
  private-key header, or a quoted ``api_key = ...`` assignment);
* a tracked ``.env``-style file other than ``.env.example``;
* a personal path: a drive-letter user directory, ``/home/<name>`` or
  ``/Users/<name>`` (documented placeholders such as ``<name>`` are allowed);
* an email address whose domain is neither on the allow-list file nor an
  obviously non-routable example domain;
* a tracked file over 5 MB;
* a tracked run artefact that must never be published (``calls.jsonl``,
  ``cache.json``, ``items.jsonl``, ``candidates.jsonl``, reviewer notes);
* any string from the optional, git-ignored personal deny-list file.

The output never contains a matched secret: finding lines carry the path, the
line number and the rule name only. Personal paths and emails are the exception,
because those are what the owner has to fix and they are not credentials.

Run it from anywhere:

    python scripts/oss_audit.py
    python scripts/oss_audit.py --history
    python scripts/oss_audit.py --json

Exit code 0 means clean, 1 means at least one finding. ``--history`` is slow on a
large repository and is meant for release preparation, not for every commit.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The repository root is the parent of ``scripts/``; ``--root`` overrides it.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

#: Key-like shapes. Keep in step with ``scripts/scan_secrets.py``: that scanner
#: additionally knows the *actual* configured key, this one knows only shapes.
KEY_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("openai-style-key", re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("prefixed-key", re.compile(rb"\bsk-(?:proj|ant|or)-[A-Za-z0-9_-]{16,}\b")),
    ("openrouter-key", re.compile(rb"\bsk-or-v1-[A-Za-z0-9]{16,}\b")),
    ("bearer-token", re.compile(rb"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.I)),
    ("slack-token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("huggingface-token", re.compile(rb"\bhf_[A-Za-z0-9]{30,}\b")),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Quoting avoids flagging source expressions such as ``api_key=settings.api_key``.
    (
        "quoted-credential",
        re.compile(rb"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*['\"][A-Za-z0-9._-]{16,}", re.I),
    ),
)

#: A line carrying one of these words is a documented fixture, not a credential.
#: Tests must name their fake keys ``fake-...``/``dummy-...`` so this allow-list
#: never has to grow; a real key pasted next to the word "example" is still a
#: problem, which is why release checks also run ``scripts/scan_secrets.py``
#: against the actual key values.
FIXTURE_LINE = re.compile(
    rb"fake|dummy|example|fixture|placeholder|redacted|sample|xxxx|test-key|test-token|sk-test-|not-a-key|should-never",
    re.I,
)

#: A line carrying this marker is a documented, reviewed exception: it disables
#: the key-shape rules for that line only (never the personal-path, email or
#: deny-list rules). Used where a file has to quote a historical fake key.
INLINE_ALLOW = b"oss-audit:"

#: Filenames that must never be tracked, whatever their directory.
FORBIDDEN_ARTEFACT_NAMES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^calls\.jsonl$"),
    re.compile(r"^cache\.json$"),
    re.compile(r"^items\.jsonl$"),
    re.compile(r"^candidates\.jsonl$"),
    re.compile(r"^screening_cache\.json$"),
    re.compile(r"review[-_]?notes", re.I),
    re.compile(r"reviewer[-_]?notes", re.I),
    re.compile(r"^notes\.local\.", re.I),
)

#: ``.env``-style files: only ``.env.example`` may be tracked.
ENV_FILE = re.compile(r"^\.env(?:$|\..*)")
ALLOWED_ENV_FILES = frozenset({".env.example"})

#: Path segments that are placeholders, not personal names.
PLACEHOLDER_SEGMENTS = frozenset(
    {"<name>", "<user>", "<you>", "<username>", "%userprofile%", "$home", "~", "user", "username", "you", "me", "public", "default"}
)

#: Drive-letter user directories: ``C:\Users\<name>``, ``C:/Users/<name>``.
WINDOWS_USER_DIR = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'`<>|:]+)", re.I)
#: POSIX home directories: ``/home/<name>``, ``/Users/<name>``.
POSIX_USER_DIR = re.compile(r"(?<![A-Za-z0-9._-])/(?:home|Users)/([^/\s\"'`<>|:]+)")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

#: Example domains are never routable and never identify a person. RFC 2606
#: reserves the names themselves and every subdomain of them, so a test URL such
#: as ``https://user:pass@api.example.com/v1`` is not a personal email address.
EXAMPLE_DOMAINS = frozenset({"example.com", "example.org", "example.net", "example.invalid", "invalid"})
EXAMPLE_DOMAIN_SUFFIXES = (
    ".example.com",
    ".example.org",
    ".example.net",
    ".example",
    ".invalid",
    ".test",
    ".localhost",
)


def is_example_domain(domain: str) -> bool:
    """True for a domain that cannot leave the local machine or reach a person."""
    lowered = domain.casefold()
    return lowered in EXAMPLE_DOMAINS or lowered.endswith(EXAMPLE_DOMAIN_SUFFIXES)

#: Files larger than this must not be tracked (5 MB).
MAX_TRACKED_BYTES = 5 * 1024 * 1024

#: Directories the audit never walks (they are git-ignored anyway).
SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tmp", ".ruff_cache", ".pytest_cache", "hf", ".hfhub", "data/cache"})


@dataclass
class Finding:
    """One problem, with no secret value in it."""

    rule: str
    path: str
    line: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "detail": self.detail}

    def render(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        suffix = f" ({self.detail})" if self.detail else ""
        return f"FAIL {self.rule} {where}{suffix}"


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    checked_files: int = 0
    checked_blobs: int = 0
    skipped_binary: int = 0
    suppressed: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checked_files": self.checked_files,
            "checked_blobs": self.checked_blobs,
            "suppressed": self.suppressed,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def tracked_files(root: Path) -> list[str]:
    """Paths of every tracked file, relative to ``root``, in git order.

    The audit is about what a clone would receive, so it needs the git index. A
    source zip has no ``.git`` directory, and a plain "not a git repository"
    traceback is a bad first experience there: say what to do instead.
    """
    try:
        top = _git(root, "rev-parse", "--show-toplevel").decode("utf-8", errors="replace").strip()
    except FileNotFoundError as exc:
        raise SystemExit(
            "oss-audit needs a git checkout: git was not found on PATH. "
            "Clone the repository (or run this inside one) and try again."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"oss-audit covers tracked content, but {root} has no git index "
            "(is this an extracted zip?). Clone the repository and run it there."
        ) from exc
    if Path(top).resolve() != root.resolve():
        # git walks up to the enclosing repository; the audit must not silently
        # report on a different project's files.
        raise SystemExit(
            f"oss-audit must run at the root of a git checkout: {root} is a directory inside "
            f"{top}, not its own repository."
        )
    raw = _git(root, "ls-files", "-z")
    return [item.decode("utf-8", errors="replace") for item in raw.split(b"\0") if item]


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:4096]


def _allow_emails(root: Path, allow_file: Path | None) -> set[str]:
    path = allow_file if allow_file is not None else root / ".github" / "allowed-emails.txt"
    addresses: set[str] = set()
    if not path.is_file():
        return addresses
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and "@" in line:
            addresses.add(line.casefold())
    return addresses


def _deny_strings(deny_list: Path | None) -> tuple[bytes, ...]:
    """Literal personal strings the owner wants flagged, from a git-ignored file."""
    if deny_list is None or not deny_list.is_file():
        return ()
    rows: list[bytes] = []
    for raw in deny_list.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            rows.append(line.encode("utf-8"))
    return tuple(rows)


def load_allowlist(root: Path, path: Path | None = None) -> list[tuple[str, str, str]]:
    """Documented exceptions: ``path-glob|rule|reason``, one per line.

    An exception is a visible, reviewable decision (for example "this v0.1 audit
    record is kept byte-identical and is known to contain the build machine's
    path"), never a silent pass. The audit prints how many findings it suppressed.
    """
    target = path if path is not None else root / ".github" / "oss_audit_allowlist.txt"
    rows: list[tuple[str, str, str]] = []
    if not target.is_file():
        return rows
    for raw in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def is_allowed(finding: Finding, allowlist: list[tuple[str, str, str]]) -> bool:
    """True when a documented exception covers this finding."""
    tree_path = finding.path.removeprefix("history:")
    for pattern, rule, _reason in allowlist:
        if rule not in {finding.rule, "*"}:
            continue
        if fnmatch.fnmatchcase(tree_path, pattern) or fnmatch.fnmatchcase(finding.path, pattern):
            return True
    return False


def scan_text(
    data: bytes,
    *,
    path: str,
    allowed_emails: frozenset[str] | set[str] = frozenset(),
    deny_strings: tuple[bytes, ...] = (),
) -> list[Finding]:
    """Scan one file's bytes. Returns findings; never returns matched text."""
    findings: list[Finding] = []
    for number, raw_line in enumerate(data.splitlines(), start=1):
        if INLINE_ALLOW not in raw_line:
            for rule, pattern in KEY_PATTERNS:
                if pattern.search(raw_line) and not FIXTURE_LINE.search(raw_line):
                    findings.append(Finding(rule, path, number))
                    break
        line = raw_line.decode("utf-8", errors="replace")
        if path not in {".env.example"}:
            for match in WINDOWS_USER_DIR.finditer(line):
                segment = match.group(1).casefold()
                if segment not in PLACEHOLDER_SEGMENTS:
                    findings.append(Finding("personal-path", path, number, "drive-letter user directory"))
                    break
            if any(match.group(1).casefold() not in PLACEHOLDER_SEGMENTS for match in POSIX_USER_DIR.finditer(line)):
                findings.append(Finding("personal-path", path, number, "home directory"))
        for match in EMAIL.finditer(line):
            domain = match.group(1).casefold()
            whole = match.group(0).casefold()
            if is_example_domain(domain) or whole in allowed_emails:
                continue
            findings.append(Finding("email-address", path, number, f"@{domain}; add it to .github/allowed-emails.txt if it is public"))
        for needle in deny_strings:
            if needle in raw_line:
                findings.append(Finding("deny-list", path, number))
                break
    return findings


def scan_path(root: Path, path: str) -> list[Finding]:
    """Path-level rules: forbidden artefacts, tracked ``.env`` files, size."""
    findings: list[Finding] = []
    name = path.rsplit("/", 1)[-1]
    if ENV_FILE.match(name) and name not in ALLOWED_ENV_FILES:
        findings.append(Finding("tracked-env-file", path))
    for pattern in FORBIDDEN_ARTEFACT_NAMES:
        if pattern.search(name):
            findings.append(Finding("tracked-run-artefact", path))
            break
    target = root / path
    try:
        size = target.stat().st_size
    except OSError:
        return findings
    if size > MAX_TRACKED_BYTES:
        findings.append(Finding("large-tracked-file", path, None, f"{size / (1024 * 1024):.1f} MB"))
    return findings


def audit(
    root: Path | None = None,
    *,
    allow_emails_file: Path | None = None,
    deny_list: Path | None = None,
    allow_list: Path | None = None,
    history: bool = False,
) -> AuditResult:
    """Run every audit rule over the tracked tree (and optionally all history)."""
    root = (root or DEFAULT_ROOT).resolve()
    result = AuditResult()
    allowed = frozenset(_allow_emails(root, allow_emails_file))
    denies = _deny_strings(deny_list)
    exceptions = load_allowlist(root, allow_list)
    for path in tracked_files(root):
        result.checked_files += 1
        result.findings.extend(scan_path(root, path))
        target = root / path
        try:
            data = target.read_bytes()
        except OSError:
            continue
        if is_binary(data):
            result.skipped_binary += 1
            continue
        result.findings.extend(scan_text(data, path=path, allowed_emails=allowed, deny_strings=denies))
    if history:
        result.findings.extend(_scan_history(root, allowed=allowed, denies=denies, result=result))
    kept: list[Finding] = []
    for finding in result.findings:
        if is_allowed(finding, exceptions):
            result.suppressed += 1
        else:
            kept.append(finding)
    result.findings = kept
    return result


def _scan_history(root: Path, *, allowed: frozenset[str], denies: tuple[bytes, ...], result: AuditResult) -> list[Finding]:
    """Scan every blob reachable from any ref, reporting ``history:<sha>:<path>``."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for line in _git(root, "rev-list", "--objects", "--all").decode("utf-8", errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        try:
            kind = _git(root, "cat-file", "-t", sha).strip()
        except subprocess.CalledProcessError:
            continue
        if kind != b"blob":
            continue
        try:
            data = _git(root, "cat-file", "-p", sha)
        except subprocess.CalledProcessError:
            continue
        result.checked_blobs += 1
        if is_binary(data) or len(data) > MAX_TRACKED_BYTES:
            continue
        label = f"history:{path or sha}"
        findings.extend(scan_text(data, path=label, allowed_emails=allowed, deny_strings=denies))
        if path:
            findings.extend(scan_path(root, path) if (root / path).exists() else [])
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oss_audit", description="audit the repository for release blockers")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--allow-emails", type=Path, default=None, help="email allow-list (default .github/allowed-emails.txt)")
    parser.add_argument(
        "--deny-list",
        type=Path,
        default=None,
        help="git-ignored file of personal strings to flag (default .private/oss_deny_list.txt when present)",
    )
    parser.add_argument("--history", action="store_true", help="also scan every blob reachable from any ref (slow)")
    parser.add_argument("--allow-list", type=Path, default=None, help="documented exceptions (default .github/oss_audit_allowlist.txt)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--max-printed", type=int, default=200)
    parser.add_argument("--quiet", action="store_true", help="print only the verdict line")
    return parser


def _default_deny_list(root: Path) -> Path | None:
    candidate = root / ".private" / "oss_deny_list.txt"
    return candidate if candidate.is_file() else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    deny_list = args.deny_list or _default_deny_list(args.root)
    result = audit(
        args.root,
        allow_emails_file=args.allow_emails,
        deny_list=deny_list,
        allow_list=args.allow_list,
        history=args.history,
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    elif args.quiet:
        print(f"{'PASS' if result.ok else 'FAIL'} oss-audit {len(result.findings)} finding(s)")
    else:
        for finding in result.findings[: args.max_printed]:
            print(finding.render())
        if len(result.findings) > args.max_printed:
            print(f"... {len(result.findings) - args.max_printed} more finding(s) not printed")
        scope = f"{result.checked_files} files"
        if result.checked_blobs:
            scope += f", {result.checked_blobs} history blobs"
        if result.suppressed:
            scope += f", {result.suppressed} documented exception(s)"
        print(f"{'PASS' if result.ok else 'FAIL'} oss-audit {len(result.findings)} finding(s) in {scope}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
