"""S13 proof that malformed arguments reach the audited server pipeline."""

import asyncio
import json

from mcp import Client

from claims_mcp.config import Config
from claims_mcp.server import create_server
from tests.fixtures import build_fixture_db


def test_invalid_tool_arguments_are_audited_without_echoing_input(
    tmp_path, capsys
) -> None:
    database_path = tmp_path / "claims.db"
    audit_path = tmp_path / "audit.jsonl"
    build_fixture_db(database_path)
    config = Config(
        database_path=database_path,
        keys_path=tmp_path / "keys.json",
        audit_path=audit_path,
        rate_limit_per_minute=60,
        http_host="127.0.0.1",
        http_port=8765,
    )
    server = create_server(config, transport="stdio")
    requests = [
        ("get_claim", {"unexpected": "missing-secret"}),
        (
            "get_claim",
            {"claim_id": "CLM00000001", "unexpected": "extra-secret"},
        ),
        ("get_claim", {"claim_id": 123456}),
    ]

    async def call_all():
        async with Client(server) as client:
            return [
                await client.call_tool(tool_name, arguments)
                for tool_name, arguments in requests
            ]

    results = asyncio.run(call_all())
    assert len(results) == 3
    for result in results:
        assert result.is_error
        assert result.content[0].text.startswith("INVALID_INPUT:")
        assert "missing-secret" not in result.content[0].text
        assert "extra-secret" not in result.content[0].text
        assert "123456" not in result.content[0].text

    audit_records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(audit_records) == 3
    assert all(item["args"] is None for item in audit_records)
    assert all(item["invalid_fields"] for item in audit_records)
    stderr_records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(stderr_records) == 3
    assert all(
        item["guardrail_status"] == "BLOCKED_VALIDATION" for item in stderr_records
    )
