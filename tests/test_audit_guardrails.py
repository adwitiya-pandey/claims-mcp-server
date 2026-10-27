"""Per-path audit-guardrail coverage tests (Step 3 of the polish pass).

Each test asserts:
- exactly the expected number of audit lines is written to the JSONL file
  (tool-pipeline paths write one line per call; HTTP middleware auth rejections
  write zero JSONL lines – they only emit a stderr event before the pipeline
  is reached);
- the structured stderr event has the correct guardrail_status;
- the raw API key never appears in any audit or stderr output;
- note text never appears in any audit or stderr output.

Transport notes
---------------
stdio: no bearer-key authentication; all callers are trusted as 'stdio-local'.
       BLOCKED_AUTH only fires at the tool-pipeline level for unknown/forbidden
       tools.
http:  BearerKeyMiddleware rejects missing/wrong keys with HTTP 401 before the
       pipeline runs.  Those rejections emit one stderr event (tool_name=
       'http/auth') but no JSONL audit line.  Per-tool FORBIDDEN_TOOL
       enforcement happens inside the pipeline and does write a JSONL line.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from claims_mcp.config import Config
from claims_mcp.http_auth import BearerKeyMiddleware
from claims_mcp.security import append_key
from claims_mcp.server import create_server
from tests.fixtures import build_fixture_db

ALL_TOOLS = [
    "get_claim",
    "list_member_claims",
    "get_coverage_rule",
    "get_member_ytd_summary",
    "get_claim_notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(tmp_path, keys_path=None, rate_limit: int = 60) -> Config:
    database_path = tmp_path / "claims.db"
    build_fixture_db(database_path)
    return Config(
        database_path=database_path,
        keys_path=keys_path or tmp_path / "keys.json",
        audit_path=tmp_path / "audit.jsonl",
        rate_limit_per_minute=rate_limit,
        http_host="127.0.0.1",
        http_port=8766,
    )


def _stdio_call(config: Config, tool: str, args: dict[str, Any]) -> Any:
    server = create_server(config, transport="stdio")

    async def _run():
        async with Client(server) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(_run())


def _audit_lines(config: Config) -> list[dict[str, Any]]:
    if not config.audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in config.audit_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _stderr_events(capsys) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]


# ---------------------------------------------------------------------------
# Scenario: malformed claim_id → BLOCKED_VALIDATION
# ---------------------------------------------------------------------------


def test_malformed_claim_id_produces_one_blocked_validation_audit_line(
    tmp_path, capsys
) -> None:
    config = _config(tmp_path)
    result = _stdio_call(config, "get_claim", {"claim_id": "NOT-VALID!"})

    assert result.is_error
    lines = _audit_lines(config)
    assert len(lines) == 1, f"expected 1 JSONL line, got {len(lines)}"
    assert lines[0]["error_code"] == "INVALID_INPUT"

    events = _stderr_events(capsys)
    assert len(events) == 1
    assert events[0]["guardrail_status"] == "BLOCKED_VALIDATION"
    assert events[0]["tool_name"] == "get_claim"


# ---------------------------------------------------------------------------
# Scenario: out-of-range pagination → BLOCKED_VALIDATION
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("list_member_claims", {"member_id": "M000001", "limit": -1}),
        ("list_member_claims", {"member_id": "M000001", "limit": 51}),
        ("list_member_claims", {"member_id": "M000001", "offset": -1}),
        ("list_member_claims", {"member_id": "M000001", "offset": 10001}),
        ("get_claim_notes", {"claim_id": "CLM00000001", "limit": 21}),
        ("get_member_ytd_summary", {"member_id": "M000001", "year": 1999}),
        ("get_member_ytd_summary", {"member_id": "M000001", "year": 2101}),
    ],
)
def test_out_of_range_pagination_produces_one_blocked_validation_line(
    tmp_path, capsys, tool, args
) -> None:
    config = _config(tmp_path)
    result = _stdio_call(config, tool, args)

    assert result.is_error
    lines = _audit_lines(config)
    assert len(lines) == 1, f"expected 1 JSONL line, got {len(lines)}"

    events = _stderr_events(capsys)
    assert len(events) == 1
    assert events[0]["guardrail_status"] == "BLOCKED_VALIDATION"


# ---------------------------------------------------------------------------
# Scenario: adversarial note → FLAGGED_ADVERSARIAL
# ---------------------------------------------------------------------------


def test_adversarial_note_produces_one_flagged_adversarial_line(
    tmp_path, capsys
) -> None:
    """FLAGGED_ADVERSARIAL: one JSONL line, one stderr event, note text absent."""
    import sqlite3

    from scripts.seed_db import seed_database

    # Use the full seed DB so adversarial note IDs are populated.
    db_path = tmp_path / "seed.db"
    seed_database(db_path)

    config = Config(
        database_path=db_path,
        keys_path=tmp_path / "keys.json",
        audit_path=tmp_path / "audit.jsonl",
        rate_limit_per_minute=60,
        http_host="127.0.0.1",
        http_port=8766,
    )

    # Find a claim that has an adversarial note (note_id in 1-15).
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT claim_id, note_text FROM claim_notes WHERE note_id = 1"
        ).fetchone()
    claim_id, raw_note_text = row

    result = _stdio_call(config, "get_claim_notes", {"claim_id": claim_id, "limit": 20})

    assert not result.is_error
    lines = _audit_lines(config)
    assert len(lines) == 1, f"expected 1 JSONL line, got {len(lines)}"

    events = _stderr_events(capsys)
    assert len(events) == 1
    assert events[0]["guardrail_status"] == "FLAGGED_ADVERSARIAL"

    # Note text must not appear in either audit sink.
    all_output = json.dumps(lines) + json.dumps(events)
    assert raw_note_text not in all_output, "raw note text leaked into audit output"


# ---------------------------------------------------------------------------
# Scenario: rate-limit hit → BLOCKED_VALIDATION (documented as RATE_LIMITED)
# ---------------------------------------------------------------------------


def test_rate_limit_produces_one_blocked_validation_line_per_call(
    tmp_path, capsys
) -> None:
    config = _config(tmp_path, rate_limit=1)

    async def _two_calls():
        server = create_server(config, transport="stdio")
        async with Client(server) as client:
            first = await client.call_tool("get_claim", {"claim_id": "CLM00000001"})
            second = await client.call_tool("get_claim", {"claim_id": "CLM00000001"})
        return first, second

    first, second = asyncio.run(_two_calls())

    assert not first.is_error
    assert second.is_error
    assert second.content[0].text == "RATE_LIMITED: try again later"

    lines = _audit_lines(config)
    assert len(lines) == 2, f"expected 2 JSONL lines, got {len(lines)}"

    events = _stderr_events(capsys)
    assert len(events) == 2
    assert events[0]["guardrail_status"] == "PASS"
    assert events[1]["guardrail_status"] == "BLOCKED_VALIDATION"
    assert events[1]["error"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# Scenario: key without tool permission → BLOCKED_AUTH (via HTTP pipeline)
# ---------------------------------------------------------------------------


def test_key_without_tool_permission_produces_one_blocked_auth_line(
    tmp_path, capsys
) -> None:
    """Per-tool BLOCKED_AUTH fires inside the pipeline → writes one JSONL line."""
    keys_path = tmp_path / "keys.json"
    limited_key = append_key(keys_path, "limited", ["get_claim"])
    config = _config(tmp_path, keys_path=keys_path)
    server = create_server(config, transport="http")
    mcp_app = server.streamable_http_app(host=config.http_host)
    app = BearerKeyMiddleware(mcp_app, str(keys_path))

    async def _run():
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8766",
                headers={"Authorization": f"Bearer {limited_key}"},
            ) as transport,
            Client(
                streamable_http_client(
                    "http://127.0.0.1:8766/mcp", http_client=transport
                )
            ) as client,
        ):
            return await client.call_tool(
                "get_claim_notes", {"claim_id": "CLM00000001"}
            )

    result = asyncio.run(_run())

    assert result.is_error
    assert result.content[0].text == "FORBIDDEN_TOOL"

    # Exactly one JSONL line (pipeline-level rejection).
    lines = _audit_lines(config)
    assert len(lines) == 1, f"expected 1 JSONL line, got {len(lines)}"
    assert lines[0]["error_code"] == "FORBIDDEN_TOOL"

    events = _stderr_events(capsys)
    # Two stderr events: one from http/auth (PASS) + one from tool pipeline (BLOCKED_AUTH).
    tool_events = [e for e in events if e["tool_name"] not in (None, "http/auth")]
    assert len(tool_events) == 1
    assert tool_events[0]["guardrail_status"] == "BLOCKED_AUTH"

    # The raw key must never appear in any audit output.
    all_audit_text = config.audit_path.read_text(encoding="utf-8")
    assert limited_key not in all_audit_text, "raw key leaked into JSONL audit"
    assert limited_key not in json.dumps(events), "raw key leaked into stderr events"


# ---------------------------------------------------------------------------
# Scenario: missing key → BLOCKED_AUTH (HTTP middleware, no JSONL line)
# ---------------------------------------------------------------------------


def test_missing_key_produces_stderr_blocked_auth_and_no_jsonl_line(
    tmp_path, capsys
) -> None:
    """Missing bearer key: 401 before pipeline → one stderr event, zero JSONL lines."""
    keys_path = tmp_path / "keys.json"
    append_key(keys_path, "analyst", ALL_TOOLS)
    config = _config(tmp_path, keys_path=keys_path)
    server = create_server(config, transport="http")
    mcp_app = server.streamable_http_app(host=config.http_host)
    app = BearerKeyMiddleware(mcp_app, str(keys_path))

    async def _run():
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8766",
            ) as client,
        ):
            resp = await client.post("/mcp", json={})
            return resp.status_code

    status = asyncio.run(_run())
    assert status == 401

    # No JSONL file written (pipeline was never reached).
    assert not config.audit_path.exists() or _audit_lines(config) == []

    events = _stderr_events(capsys)
    assert len(events) == 1
    assert events[0]["tool_name"] == "http/auth"
    assert events[0]["guardrail_status"] == "BLOCKED_AUTH"
    assert events[0]["caller_key_hash"] is None


# ---------------------------------------------------------------------------
# Scenario: wrong (invalid) key → BLOCKED_AUTH (HTTP middleware)
# ---------------------------------------------------------------------------


def test_wrong_key_produces_stderr_blocked_auth_and_no_jsonl_line(
    tmp_path, capsys
) -> None:
    """Wrong bearer key: 401 before pipeline → one stderr event, zero JSONL lines."""
    keys_path = tmp_path / "keys.json"
    append_key(keys_path, "analyst", ALL_TOOLS)
    config = _config(tmp_path, keys_path=keys_path)
    server = create_server(config, transport="http")
    mcp_app = server.streamable_http_app(host=config.http_host)
    app = BearerKeyMiddleware(mcp_app, str(keys_path))

    wrong_key = "this-key-does-not-exist-in-keys-json"

    async def _run():
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8766",
                headers={"Authorization": f"Bearer {wrong_key}"},
            ) as client,
        ):
            resp = await client.post("/mcp", json={})
            return resp.status_code

    status = asyncio.run(_run())
    assert status == 401

    assert not config.audit_path.exists() or _audit_lines(config) == []

    events = _stderr_events(capsys)
    assert len(events) == 1
    assert events[0]["tool_name"] == "http/auth"
    assert events[0]["guardrail_status"] == "BLOCKED_AUTH"

    # The raw wrong key must not appear in the stderr event.
    assert wrong_key not in json.dumps(events), "wrong raw key leaked into stderr"


# ---------------------------------------------------------------------------
# Cross-cutting: raw key and note text never appear in any audit output
# ---------------------------------------------------------------------------


def test_raw_key_never_appears_in_jsonl_audit_on_successful_call(
    tmp_path, capsys
) -> None:
    """Happy path: the raw bearer key must not be written to the JSONL audit file."""
    keys_path = tmp_path / "keys.json"
    plaintext_key = append_key(keys_path, "analyst", ALL_TOOLS)
    config = _config(tmp_path, keys_path=keys_path)
    server = create_server(config, transport="http")
    mcp_app = server.streamable_http_app(host=config.http_host)
    app = BearerKeyMiddleware(mcp_app, str(keys_path))

    async def _run():
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8766",
                headers={"Authorization": f"Bearer {plaintext_key}"},
            ) as transport,
            Client(
                streamable_http_client(
                    "http://127.0.0.1:8766/mcp", http_client=transport
                )
            ) as client,
        ):
            return await client.call_tool("get_claim", {"claim_id": "CLM00000001"})

    result = asyncio.run(_run())
    assert not result.is_error

    all_audit_text = config.audit_path.read_text(encoding="utf-8")
    events = _stderr_events(capsys)
    assert plaintext_key not in all_audit_text, "raw key leaked into JSONL audit"
    assert plaintext_key not in json.dumps(events), "raw key leaked into stderr events"
