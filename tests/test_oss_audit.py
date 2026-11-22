"""Unit tests for the release audit (``scripts/oss_audit.py``).

Every fake credential here is invented and carries a marker word where the rule
allows one, so the tests exercise both the failure and the fixture allowance.
Nothing in this file touches a real `.env`; the integration test builds a
throwaway git repository inside ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from .support import ROOT


def _load_audit() -> ModuleType:
    """Import ``scripts/oss_audit.py`` by path: ``scripts/`` is not a package."""
    path = ROOT / "scripts" / "oss_audit.py"
    spec = importlib.util.spec_from_file_location("oss_audit_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module  # dataclasses resolves cls.__module__ here
    spec.loader.exec_module(module)
    return module


oss_audit = _load_audit()
MAX_TRACKED_BYTES = oss_audit.MAX_TRACKED_BYTES
Finding = oss_audit.Finding
audit = oss_audit.audit
is_allowed = oss_audit.is_allowed
load_allowlist = oss_audit.load_allowlist
scan_path = oss_audit.scan_path
scan_text = oss_audit.scan_text

#: Fixture strings are assembled at run time on purpose: the *source* of this test
#: file must not itself contain a key-shaped, personal-path or email string, or the
#: release audit (which scans this file too) would fail on its own tests. The
#: strings exist only inside the test process.
def joined(*parts: bytes) -> bytes:
    return b"".join(parts)


#: A key-shaped string with no fixture marker: this is what must fail.
FAKE_OPENAI_KEY = joined(b"sk-", b"abcdefghijklmnop1234567890")


def test_key_shapes_are_flagged_and_fixtures_are_not() -> None:
    assert scan_text(FAKE_OPENAI_KEY, path="x.py")
    assert scan_text(joined(b"github_token = 'ghp_", b"a" * 40, b"'"), path="x.py")
    assert scan_text(joined(b'password = "', b"a" * 20, b'"'), path="x.py")
    assert scan_text(joined(b"-----BEGIN RSA ", b"PRIVATE KEY-----"), path="x.py")
    # A fake key whose name says it is fake is allowed by the fixture marker.
    assert scan_text(joined(b"sk-test-", b"abcdefghijklmnop1234"), path="tests/test_x.py") == []
    # An explicit inline marker documents a reviewed exception for that line.
    assert scan_text(FAKE_OPENAI_KEY + b"  # oss-audit: known historical fixture", path="x.py") == []


def test_personal_paths_are_flagged_but_placeholders_are_not() -> None:
    windows = joined(b"cd C:", b"\\Users\\alice\\medeval-lab")
    assert any(f.rule == "personal-path" for f in scan_text(windows, path="README.md"))
    placeholder = joined(b"cd C:", b"\\Users\\<name>\\medeval-lab and %USERPROFILE%\\x")
    assert scan_text(placeholder, path="README.md") == []
    unix = joined(b"cp -r /home/", b"alice/project .")
    assert any(f.rule == "personal-path" for f in scan_text(unix, path="README.md"))
    assert scan_text(joined(b"cp -r /home/", b"<name>/project ."), path="README.md") == []


def test_email_addresses_need_the_allow_list() -> None:
    address = joined(b"alice@", b"hospital.org").decode()
    found = scan_text(joined(b"contact: ", address.encode()), path="x.md")
    assert any(f.rule == "email-address" for f in found)
    # Non-routable example domains are always fine.
    assert scan_text(joined(b"contact: alice@", b"example.com"), path="x.md") == []
    # A listed address is intentional.
    assert scan_text(joined(b"contact: ", address.encode()), path="x.md", allowed_emails={address}) == []


def test_tracked_env_and_run_artefact_rules(tmp_path: Path) -> None:
    for name, rule in ((".env", "tracked-env-file"), (".env.local", "tracked-env-file")):
        (tmp_path / name).write_text("X=1\n", encoding="utf-8")
        assert any(f.rule == rule for f in scan_path(tmp_path, name)), name
    (tmp_path / ".env.example").write_text("X=\n", encoding="utf-8")
    assert scan_path(tmp_path, ".env.example") == []

    for name in ("calls.jsonl", "cache.json", "items.jsonl", "candidates.jsonl", "review_notes.md"):
        target = tmp_path / "run"
        target.mkdir(exist_ok=True)
        (target / name).write_text("{}", encoding="utf-8")
        assert any(f.rule == "tracked-run-artefact" for f in scan_path(tmp_path, f"run/{name}")), name
    # Verdicts are evidence and stay allowed.
    (tmp_path / "run" / "verdicts.jsonl").write_text("{}\n", encoding="utf-8")
    assert scan_path(tmp_path, "run/verdicts.jsonl") == []


def test_large_tracked_file_is_flagged(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * (MAX_TRACKED_BYTES + 1))
    assert any(f.rule == "large-tracked-file" for f in scan_path(tmp_path, "big.bin"))


def test_deny_list_strings_are_flagged(tmp_path: Path) -> None:
    deny_file = tmp_path / "deny.txt"
    deny_file.write_text("# comment\npersonal-machine-name\n", encoding="utf-8")
    _deny_strings = oss_audit._deny_strings

    needles = _deny_strings(deny_file)
    assert needles == (b"personal-machine-name",)
    assert scan_text(b"a personal-machine-name appears here", path="x.md", deny_strings=needles)
    assert scan_text(b"nothing to see", path="x.md", deny_strings=needles) == []


def test_allowlist_matches_rule_and_path_pattern(tmp_path: Path) -> None:
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text(
        "# comment\nresults/audit/*|personal-path|kept byte-identical\nscripts/x.py|*|any rule\n",
        encoding="utf-8",
    )
    entries = load_allowlist(tmp_path, allow_file)
    assert len(entries) == 2
    assert is_allowed(Finding("personal-path", "results/audit/a.json", 3), entries)
    assert not is_allowed(Finding("openai-style-key", "results/audit/a.json", 3), entries)
    assert is_allowed(Finding("deny-list", "scripts/x.py", None), entries)
    assert not is_allowed(Finding("personal-path", "docs/README.md", 1), entries)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "audit@example.invalid")
    _git(root, "config", "user.name", "audit-test")


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "test commit")


def test_audit_only_looks_at_tracked_content(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "good.py").write_text("print('hello')\n", encoding="utf-8")
    _commit(tmp_path)
    # An untracked key is not published by a clone, so it is not an audit finding.
    (tmp_path / "notes.txt").write_bytes(FAKE_OPENAI_KEY)
    result = audit(tmp_path)
    assert result.ok, [f.render() for f in result.findings]
    assert result.checked_files == 1


def test_audit_finds_and_reports_what_a_clone_would_receive(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "leak.py").write_bytes(FAKE_OPENAI_KEY + b"\n")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        joined(b"built in C:", b"\\Users\\alice\\repo\n").decode(), encoding="utf-8"
    )
    _commit(tmp_path)
    result = audit(tmp_path)
    rules = {finding.rule for finding in result.findings}
    assert {"openai-style-key", "tracked-env-file", "personal-path"} <= rules
    # Rendered findings never contain the matched secret.
    rendered = "\n".join(finding.render() for finding in result.findings)
    assert "abcdefghijklmnop" not in rendered


def test_history_scan_reports_old_blobs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    leaked = joined(b"built in C:", b"\\Users\\alice\\repo\n").decode()
    (tmp_path / "doc.md").write_text(leaked, encoding="utf-8")
    _commit(tmp_path)
    (tmp_path / "doc.md").write_text("neutral text\n", encoding="utf-8")
    _commit(tmp_path)
    # Fixed at HEAD: the tree scan is clean but history still has the old line.
    assert audit(tmp_path).ok
    with_history = audit(tmp_path, history=True)
    assert any(finding.rule == "personal-path" and finding.path.startswith("history:") for finding in with_history.findings)


def test_audit_without_a_git_checkout_explains_instead_of_a_traceback(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        audit(tmp_path)
    message = str(excinfo.value)
    # The tmp dir is usually inside the test repository, so the message may be
    # either "not a repository" or "not the root of one"; both say what to do.
    assert "git" in message
    assert "repository" in message or "checkout" in message


def test_cli_exit_codes_and_json(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    main = oss_audit.main

    _init_repo(tmp_path)
    (tmp_path / "clean.py").write_text("print(1)\n", encoding="utf-8")
    _commit(tmp_path)
    assert main(["--root", str(tmp_path)]) == 0
    assert "PASS oss-audit" in capsys.readouterr().out

    (tmp_path / "leak.py").write_bytes(FAKE_OPENAI_KEY)
    _commit(tmp_path)
    assert main(["--root", str(tmp_path)]) == 1
    capsys.readouterr()  # discard the plain-text run so the JSON below parses
    assert main(["--root", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["findings"]
