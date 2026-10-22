"""Audit file and stderr logger tests."""

import json

import pytest

from claims_mcp import audit
from claims_mcp.audit import (
    AuditWriteError,
    audit_then_return,
    key_hash_prefix,
    make_structured_record,
    write_audit,
    write_structured_stderr,
)


def test_jsonl_audit_record_is_append_only_and_has_contract_fields(tmp_path) -> None:
    path = tmp_path / "audit" / "audit.jsonl"
    record = {
        "ts": "2026-10-22T00:00:00.000Z",
        "request_id": "request-id",
        "caller": "analyst-1",
        "transport": "http",
        "tool": "get_claim",
        "args": {"claim_id": "CLM00000001"},
        "outcome": "ok",
        "rows": 1,
        "duration_ms": 2,
        "error_code": None,
    }
    write_audit(path, record)
    write_audit(path, {**record, "request_id": "request-id-2"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == record
    assert "note_text" not in lines[0]


def test_invalid_record_contains_no_raw_input_or_payload(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    write_audit(
        path,
        {
            "ts": "2026-10-22T00:00:00.000Z",
            "request_id": "request-id",
            "caller": "analyst-1",
            "transport": "http",
            "tool": "get_claim",
            "args": None,
            "outcome": "invalid",
            "rows": 0,
            "duration_ms": 1,
            "error_code": "INVALID_INPUT",
            "invalid_fields": ["claim_id"],
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "raw attacker input" not in text
    assert "invalid_fields" in text


def test_structured_security_log_is_json_stderr_only(capsys) -> None:
    secret = "key-never-logged"
    record = make_structured_record(
        tool_name="get_claim_notes",
        caller_key=secret,
        claim_id="CLM00000001",
        guardrail_status="FLAGGED_ADVERSARIAL",
        latency_ms=1.23456,
        error=None,
    )
    write_structured_stderr(record)
    captured = capsys.readouterr()
    decoded = json.loads(captured.err)
    assert captured.out == ""
    assert set(decoded) == {
        "timestamp",
        "correlation_id",
        "tool_name",
        "caller_key_hash",
        "claim_id",
        "guardrail_status",
        "latency_ms",
        "error",
    }
    assert decoded["caller_key_hash"] == key_hash_prefix(secret)
    assert secret not in captured.err
    assert decoded["guardrail_status"] == "FLAGGED_ADVERSARIAL"


def test_audit_failure_prevents_returning_result(monkeypatch, tmp_path) -> None:
    def fail_write(_path, _record) -> None:
        raise AuditWriteError("disk unavailable")

    monkeypatch.setattr(audit, "write_audit", fail_write)
    with pytest.raises(AuditWriteError):
        audit_then_return(
            tmp_path / "audit.jsonl",
            {},
            make_structured_record(
                tool_name="get_claim",
                caller_key=None,
                claim_id=None,
                guardrail_status="PASS",
                latency_ms=1,
                error=None,
            ),
            {"sensitive": "data"},
        )


def test_structured_stderr_rejects_unapproved_fields(capsys) -> None:
    with pytest.raises(ValueError):
        write_structured_stderr({"raw_input": "not allowed"})
    assert capsys.readouterr().err == ""
