"""Authentication, authorization, rate limiting, and text sanitization."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TOOL_NAMES = frozenset(
    {
        "get_claim",
        "list_member_claims",
        "get_coverage_rule",
        "get_member_ytd_summary",
        "get_claim_notes",
    }
)


class SecurityConfigurationError(RuntimeError):
    """The API-key file is missing or malformed."""


@dataclass(frozen=True)
class KeyRecord:
    key_id: str
    sha256: str
    allowed_tools: frozenset[str]


def hash_key(key: str) -> str:
    """Return the hexadecimal SHA-256 digest of a plaintext API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_keys(path: Path | str) -> list[KeyRecord]:
    """Load and validate hashed keys and their tool allowlists."""
    try:
        contents = json.loads(Path(path).read_text(encoding="utf-8"))
        records = contents["keys"]
        if not isinstance(records, list):
            raise TypeError("keys must be a list")
        result = []
        seen_ids: set[str] = set()
        for item in records:
            key_id = item["key_id"]
            digest = item["sha256"]
            allowed = item["allowed_tools"]
            if (
                not isinstance(key_id, str)
                or not key_id
                or key_id in seen_ids
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not isinstance(allowed, list)
                or any(tool not in TOOL_NAMES for tool in allowed)
            ):
                raise ValueError("invalid key record")
            seen_ids.add(key_id)
            result.append(KeyRecord(key_id, digest, frozenset(allowed)))
        return result
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SecurityConfigurationError("API-key configuration is invalid") from error


def authenticate_key(key: str | None, keys_path: Path | str) -> KeyRecord | None:
    """Find a matching key using constant-time digest comparisons."""
    if not isinstance(key, str) or not key:
        return None
    digest = hash_key(key)
    matched: KeyRecord | None = None
    for record in load_keys(keys_path):
        if hmac.compare_digest(digest, record.sha256):
            matched = record
    return matched


def append_key(path: Path | str, key_id: str, allowed_tools: list[str]) -> str:
    """Append one hashed key record and return its one-time plaintext key."""
    if not key_id or any(tool not in TOOL_NAMES for tool in allowed_tools):
        raise ValueError("key ID and tool allowlist must be valid")
    key = secrets.token_urlsafe(32)
    key_path = Path(path)
    if key_path.exists():
        contents = json.loads(key_path.read_text(encoding="utf-8"))
    else:
        contents = {"keys": []}
    if any(entry.get("key_id") == key_id for entry in contents["keys"]):
        raise ValueError("key ID already exists")
    contents["keys"].append(
        {
            "key_id": key_id,
            "sha256": hash_key(key),
            "allowed_tools": allowed_tools,
        }
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(contents, indent=2) + "\n", encoding="utf-8")
    return key


class FixedWindowRateLimiter:
    """Thread-safe per-key fixed-window rate limiter."""

    def __init__(
        self,
        limit: int = 60,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("rate limit and window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key_id: str) -> bool:
        """Consume one call if the key remains within its current time window."""
        now = self.clock()
        with self._lock:
            started, count = self._windows.get(key_id, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            if count >= self.limit:
                self._windows[key_id] = (started, count)
                return False
            self._windows[key_id] = (started, count + 1)
            return True


def sanitize_untrusted_text(text: str, max_length: int = 1000) -> tuple[str, bool]:
    """Remove unsafe controls and format characters, then cap note length."""
    sanitized = "".join(
        character
        for character in text
        if (unicodedata.category(character) not in {"Cc", "Cf"}) or character in "\n\t"
    )
    was_truncated = len(sanitized) > max_length
    return sanitized[:max_length], was_truncated
