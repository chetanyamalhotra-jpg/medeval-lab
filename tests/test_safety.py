from __future__ import annotations

import pytest

from medeval_core.safety import PriceCard, ResponseCache, SpendGuard, redact_value


def test_spend_guard_stops_at_call_cap() -> None:
    guard = SpendGuard(max_calls=1, max_usd=5.0)
    guard.check_before_call(1, 1)
    guard.record(1, 1)
    with pytest.raises(RuntimeError, match="call cap"):
        guard.check_before_call(1, 1)


def test_spend_guard_stops_before_usd_cap() -> None:
    guard = SpendGuard(max_calls=10, max_usd=0.001, prices=PriceCard(1000.0, 1000.0, source="test"))
    with pytest.raises(RuntimeError, match="USD cap"):
        guard.check_before_call(100, 100)


def test_response_cache_keys_include_required_dimensions(tmp_path) -> None:
    cache = ResponseCache(tmp_path / "cache.json")
    one = cache.key("m", "h", 0.0, 1)
    two = cache.key("m", "h", 0.0, 2)
    cache.put(one, {"text": "ok"})
    assert cache.get(one) == {"text": "ok"}
    assert cache.get(two) is None


def test_recursive_redaction() -> None:
    assert "KEY" not in str(redact_value({"x": ["KEY"]}, ("KEY",)))
