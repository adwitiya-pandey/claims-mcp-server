"""Fail-closed JSONL and structured stderr audit logging."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claims_mcp.security import hash_key

_WRITE_LOCK = threading.Lock()
GUARDRAIL_STATUSES = frozenset(
    {"PASS", "FLAGGED_ADVERSARIAL", "BLOCKED_AUTH", "BLOCKED_VALIDATION"}
)
STRUCTURED_FIELDS = frozenset(
    {
        "timestamp",
        "correlation_id",
        "tool_name",
        "caller_key_hash",
        "claim_id",
        "guardrail_status",
        "latency_ms",
        "error",
    }
)


class AuditWriteError(RuntimeError):
    """Audit output could not be durably written."""


def new_correlation_id() -> str:
    """Create a per-event UUID4 correlation identifier."""
    return str(uuid.uuid4())


def key_hash_prefix(key: str | None) -> str | None:
    """Return a short digest prefix without retaining or exposing the key."""
    return hash_key(key)[:12] if isinstance(key, str) and key else None


def utc_timestamp() -> str:
    """Return a millisecond-resolution ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_audit(path: Path | str, record: dict[str, Any]) -> None:
    """Append one JSONL audit record and flush it to the filesystem."""
    audit_path = Path(path)
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n"
        with _WRITE_LOCK, audit_path.open("a", encoding="utf-8") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise AuditWriteError("Audit record could not be written") from error


def write_structured_stderr(record: dict[str, Any]) -> None:
    """Write one structured security event only to stderr."""
    if set(record) != STRUCTURED_FIELDS:
        raise ValueError("Structured security event has an invalid field set")
    if record["guardrail_status"] not in GUARDRAIL_STATUSES:
        raise ValueError("Structured security event has an invalid guardrail status")
    encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    try:
        sys.stderr.write(encoded + "\n")
        sys.stderr.flush()
    except OSError as error:
        raise AuditWriteError(
            "Structured security event could not be written"
        ) from error


def make_structured_record(
    *,
    tool_name: str | None,
    caller_key: str | None,
    caller_key_hash: str | None = None,
    claim_id: str | None,
    guardrail_status: str,
    latency_ms: float,
    error: str | None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build the required redacted stderr event fields."""
    if guardrail_status not in GUARDRAIL_STATUSES:
        raise ValueError("Invalid guardrail status")
    return {
        "timestamp": utc_timestamp(),
        "correlation_id": correlation_id or new_correlation_id(),
        "tool_name": tool_name,
        "caller_key_hash": caller_key_hash or key_hash_prefix(caller_key),
        "claim_id": claim_id,
        "guardrail_status": guardrail_status,
        "latency_ms": max(0.0, round(latency_ms, 3)),
        "error": error,
    }


def audit_then_return(
    audit_path: Path | str,
    audit_record: dict[str, Any],
    structured_record: dict[str, Any],
    result: Any,
) -> Any:
    """Return data only after both audit sinks have accepted their records."""
    try:
        write_audit(audit_path, audit_record)
        write_structured_stderr(structured_record)
    except AuditWriteError:
        raise
    except OSError as error:
        raise AuditWriteError("Audit record could not be written") from error
    return result
