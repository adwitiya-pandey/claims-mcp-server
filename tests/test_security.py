"""Security helper tests."""

import json

from claims_mcp import security
from claims_mcp.security import (
    FixedWindowRateLimiter,
    append_key,
    authenticate_key,
    hash_key,
    load_keys,
    sanitize_untrusted_text,
)


def test_missing_wrong_and_allowed_keys(tmp_path) -> None:
    path = tmp_path / "keys.json"
    plaintext = append_key(path, "analyst-1", ["get_claim"])
    assert authenticate_key(None, path) is None
    assert authenticate_key("", path) is None
    assert authenticate_key("wrong", path) is None
    record = authenticate_key(plaintext, path)
    assert record is not None and record.key_id == "analyst-1"
    assert record.allowed_tools == {"get_claim"}
    assert plaintext not in path.read_text(encoding="utf-8")


def test_key_storage_contains_only_digest_and_allowlist(tmp_path) -> None:
    path = tmp_path / "nested" / "keys.json"
    key = append_key(path, "analyst-2", ["get_claim", "get_claim_notes"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["keys"][0]["sha256"] == hash_key(key)
    assert set(raw["keys"][0]["allowed_tools"]) == {"get_claim", "get_claim_notes"}
    assert load_keys(path)[0].key_id == "analyst-2"


def test_rate_limiter_enforces_fixed_window_with_injected_clock() -> None:
    now = [10.0]
    limiter = FixedWindowRateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow("analyst")
    assert limiter.allow("analyst")
    assert not limiter.allow("analyst")
    now[0] = 70.0
    assert limiter.allow("analyst")
    assert limiter.allow("other-key")


def test_sanitizer_removes_zero_width_bidi_controls_and_crlf() -> None:
    cleaned, truncated = sanitize_untrusted_text("A\u200bB\u202eC\r\nD\x00\tE")
    assert cleaned == "ABC\nD\tE"
    assert not truncated


def test_sanitizer_caps_twenty_thousand_characters_and_handles_empty() -> None:
    cleaned, truncated = sanitize_untrusted_text("x" * 20000)
    assert len(cleaned) == 1000
    assert truncated
    assert sanitize_untrusted_text("")[0] == ""


def test_key_comparison_uses_constant_time_primitive(monkeypatch, tmp_path) -> None:
    path = tmp_path / "keys.json"
    plaintext = append_key(path, "analyst", ["get_claim"])
    calls = []
    original = security.hmac.compare_digest

    def tracked(left: str, right: str) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(security.hmac, "compare_digest", tracked)
    assert authenticate_key(plaintext, path) is not None
    assert len(calls) == 1
