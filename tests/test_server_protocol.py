"""MCP protocol and HTTP authentication tests."""

import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from claims_mcp.config import Config
from claims_mcp.http_auth import BearerKeyMiddleware
from claims_mcp.security import append_key
from claims_mcp.server import create_server
from tests.fixtures import build_fixture_db


def _config(tmp_path, keys_path=None) -> Config:
    database_path = tmp_path / "claims.db"
    build_fixture_db(database_path)
    return Config(
        database_path=database_path,
        keys_path=keys_path or tmp_path / "keys.json",
        audit_path=tmp_path / "audit.jsonl",
        rate_limit_per_minute=60,
        http_host="127.0.0.1",
        http_port=8765,
    )


def _text(result) -> str:
    return result.content[0].text


def test_in_memory_protocol_lists_five_tools_and_serves_all_contracts(tmp_path) -> None:
    config = _config(tmp_path)
    server = create_server(config, transport="stdio")

    async def exercise():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "get_claim",
                "list_member_claims",
                "get_coverage_rule",
                "get_member_ytd_summary",
                "get_claim_notes",
            }
            claim = await client.call_tool("get_claim", {"claim_id": "CLM00000001"})
            member_claims = await client.call_tool(
                "list_member_claims", {"member_id": "M000001", "limit": 1}
            )
            coverage = await client.call_tool(
                "get_coverage_rule", {"plan_id": "PLN001", "procedure_code": "PX0002"}
            )
            summary = await client.call_tool(
                "get_member_ytd_summary", {"member_id": "M000001", "year": 2025}
            )
            notes = await client.call_tool(
                "get_claim_notes", {"claim_id": "CLM00000001"}
            )
            missing = await client.call_tool("get_claim", {"claim_id": "CLM99999999"})
            invalid = await client.call_tool("get_claim", {"claim_id": "bad"})
        return claim, member_claims, coverage, summary, notes, missing, invalid

    claim, member_claims, coverage, summary, notes, missing, invalid = asyncio.run(
        exercise()
    )
    assert not claim.is_error and claim.structured_content["claim_id"] == "CLM00000001"
    assert member_claims.structured_content["total"] == 4
    assert coverage.structured_content["requires_preauth"] is True
    assert summary.structured_content["remaining_annual_limit"] == 560.0
    assert notes.structured_content["untrusted"] is True
    assert "<untrusted_claim_note>" in notes.structured_content["notes"][0]["text"]
    assert missing.is_error and _text(missing) == "NOT_FOUND"
    assert invalid.is_error and _text(invalid) == "INVALID_INPUT: claim_id is invalid"


def test_unexpected_database_failure_returns_safe_internal_error(tmp_path) -> None:
    config = _config(tmp_path)
    config.database_path.unlink()
    server = create_server(config, transport="stdio")

    async def call():
        async with Client(server) as client:
            return await client.call_tool("get_claim", {"claim_id": "CLM00000001"})

    result = asyncio.run(call())
    assert result.is_error
    assert result.content[0].text.startswith("INTERNAL: reference ")
    assert str(config.database_path) not in result.content[0].text
    record = json.loads(config.audit_path.read_text(encoding="utf-8"))
    assert record["error_code"] == "INTERNAL"


def test_http_auth_and_per_tool_authorization(tmp_path, capsys) -> None:
    keys_path = tmp_path / "keys.json"
    all_key = append_key(
        keys_path,
        "analyst-all",
        [
            "get_claim",
            "list_member_claims",
            "get_coverage_rule",
            "get_member_ytd_summary",
            "get_claim_notes",
        ],
    )
    limited_key = append_key(keys_path, "analyst-limited", ["get_claim"])
    config = _config(tmp_path, keys_path)
    server = create_server(config, transport="http")
    mcp_app = server.streamable_http_app(host=config.http_host)
    app = BearerKeyMiddleware(mcp_app, str(keys_path))

    async def exercise_http():
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8765",
            ) as unauthenticated:
                rejected = await unauthenticated.post("/mcp", json={})
                assert rejected.status_code == 401
                assert rejected.content == b""
                rejected_invalid = await unauthenticated.post(
                    "/mcp", json={}, headers={"Authorization": "Bearer invalid-key"}
                )
                assert rejected_invalid.status_code == 401
                assert rejected_invalid.content == b""

            async with (
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8765",
                    headers={"Authorization": f"Bearer {all_key}"},
                ) as transport,
                Client(
                    streamable_http_client(
                        "http://127.0.0.1:8765/mcp", http_client=transport
                    )
                ) as client,
            ):
                result = await client.call_tool(
                    "get_claim", {"claim_id": "CLM00000001"}
                )
                assert result.structured_content["claim_id"] == "CLM00000001"

            async with (
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8765",
                    headers={"Authorization": f"Bearer {limited_key}"},
                ) as transport,
                Client(
                    streamable_http_client(
                        "http://127.0.0.1:8765/mcp", http_client=transport
                    )
                ) as client,
            ):
                forbidden = await client.call_tool(
                    "get_claim_notes", {"claim_id": "CLM00000001"}
                )
        assert forbidden.is_error and _text(forbidden) == "FORBIDDEN_TOOL"

    asyncio.run(exercise_http())
    stderr_records = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line
    ]
    auth_events = [item for item in stderr_records if item["tool_name"] is None]
    blocked_auth = [
        item for item in auth_events if item["guardrail_status"] == "BLOCKED_AUTH"
    ]
    assert len(blocked_auth) == 2
    assert all(item["error"] == "UNAUTHENTICATED" for item in blocked_auth)
    assert any(item["caller_key_hash"] is None for item in blocked_auth)
    assert any(item["caller_key_hash"] is not None for item in blocked_auth)
    tool_events = [item for item in stderr_records if item["tool_name"] is not None]
    assert len(tool_events) == 2
    assert {item["guardrail_status"] for item in tool_events} == {
        "PASS",
        "BLOCKED_AUTH",
    }
