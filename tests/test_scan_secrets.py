from __future__ import annotations

import importlib.util
import sys

from tests.support import ROOT


def _scanner():
    path = ROOT / "scripts" / "scan_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_secrets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fake_key_is_detected_but_never_returned_or_printed() -> None:
    scanner = _scanner()
    fake = ("test-" + "key-" + "abcdefghijklmnop").encode()
    hits = scanner.scan_bytes(b"prefix " + fake + b" suffix", (fake,))
    assert "actual-key" in hits
    assert fake.decode() not in repr(hits)


def test_scan_detects_provider_key_shapes() -> None:
    scanner = _scanner()
    # Assembled at runtime so this source file never contains an intact key
    # shape (the scanner scans its own tests, and these lines carry no marker).
    parts = [
        ["key: ", "sk-or-v1-", "0123456789", "abcdefghijklmnop"],
        ["Auth", "orization: Be", "arer abcdef1234567890", "abcdef"],
        ["github_token: ", "ghp_", "0123456789", "abcdefghijklmnopqrstuv"],
        ["google: ", "AIza", "0123456789ABC", "DEFGHIJKLMNOPQRSTUVW"],
        ["slack: ", "xox", "b-0123456789-", "abcdef"],
    ]
    for pieces in parts:
        line = "".join(pieces).encode()
        assert scanner.scan_bytes(line, ()), line


def test_allow_list_covers_obvious_fixtures() -> None:
    scanner = _scanner()
    line = b'api_key="sk-fake-key-for-redaction-tests-4242"'
    assert all(
        not pattern.search(line) or scanner.ALLOWED_LINE_MARKERS.search(line)
        for pattern in scanner.PATTERNS
    )


def test_actual_configured_key_is_flagged_even_on_a_marked_line() -> None:
    scanner = _scanner()
    key = b"sk-re" b"al-configured-key-0001"
    data = b"fake example with the real one inside: " + key
    assert "actual-key" in scanner.scan_bytes(data, (key,))


def test_known_fixture_strings_are_excluded() -> None:
    scanner = _scanner()
    assert scanner.KNOWN_FIXTURE_STRINGS, "the exclusion list documents the pre-v0.3 fixtures"
    for known in scanner.KNOWN_FIXTURE_STRINGS:
        line = b'binding("j", "c", "http://x", api_key="' + known + b'")'
        assert scanner.scan_bytes(line, ()) == set(), known
