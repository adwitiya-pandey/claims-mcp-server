"""Hostile input and adversarial-note regression tests."""

import asyncio
import json
import sqlite3

import pytest
from mcp import Client

from claims_mcp.config import Config
from claims_mcp.server import create_server
from scripts.seed_db import seed_database
from tests.fixtures import build_fixture_db

ID_FIELDS = (
    ("get_claim", "claim_id", {"claim_id": "CLM00000001"}),
    ("list_member_claims", "member_id", {"member_id": "M000001"}),
    (
        "get_coverage_rule",
        "plan_id",
        {"plan_id": "PLN001", "procedure_code": "PX0001"},
    ),
    (
        "get_coverage_rule",
        "procedure_code",
        {"plan_id": "PLN001", "procedure_code": "PX0001"},
    ),
    (
        "get_member_ytd_summary",
        "member_id",
        {"member_id": "M000001", "year": 2025},
    ),
    ("get_claim_notes", "claim_id", {"claim_id": "CLM00000001"}),
)
INVALID_ID_VALUES = (
    "SQL injection' OR 1=1 --",
    "CLM00000001\n",
    "CLM１２３４５６７８",
)
ID_CASES = [
    (tool, field, {**arguments, field: invalid})
    for tool, field, arguments in ID_FIELDS
    for invalid in INVALID_ID_VALUES
]
EXTRA_CASES = [
    (name, arguments | {"attacker_controlled_extra": "extra-secret"})
    for name, arguments in (
        ("get_claim", {"claim_id": "CLM00000001"}),
        ("list_member_claims", {"member_id": "M000001"}),
        (
            "get_coverage_rule",
            {"plan_id": "PLN001", "procedure_code": "PX0001"},
        ),
        ("get_member_ytd_summary", {"member_id": "M000001", "year": 2025}),
        ("get_claim_notes", {"claim_id": "CLM00000001"}),
    )
]
BOUND_CASES = [
    ("list_member_claims", {"member_id": "M000001", "limit": -1}, "limit"),
    ("list_member_claims", {"member_id": "M000001", "limit": 51}, "limit"),
    ("list_member_claims", {"member_id": "M000001", "limit": "2"}, "limit"),
    ("list_member_claims", {"member_id": "M000001", "offset": -1}, "offset"),
    ("list_member_claims", {"member_id": "M000001", "offset": 10001}, "offset"),
    ("get_claim_notes", {"claim_id": "CLM00000001", "limit": 21}, "limit"),
    ("get_member_ytd_summary", {"member_id": "M000001", "year": 1999}, "year"),
    ("get_member_ytd_summary", {"member_id": "M000001", "year": 2101}, "year"),
    ("get_member_ytd_summary", {"member_id": "M000001", "year": "2025"}, "year"),
]


def _config(database_path, audit_path, rate_limit=60) -> Config:
    return Config(
        database_path=database_path,
        keys_path=database_path.parent / "keys.json",
        audit_path=audit_path,
        rate_limit_per_minute=rate_limit,
        http_host="127.0.0.1",
        http_port=8765,
    )


def _invoke(config: Config, name: str, arguments: dict):
    server = create_server(config, transport="stdio")

    async def call():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


@pytest.mark.parametrize(("tool_name", "invalid_field", "arguments"), ID_CASES)
def test_sql_unicode_newline_and_oversized_ids_are_safe(
    tmp_path, capsys, tool_name, invalid_field, arguments
) -> None:
    database_path = tmp_path / "claims.db"
    audit_path = tmp_path / "audit.jsonl"
    build_fixture_db(database_path)
    result = _invoke(_config(database_path, audit_path), tool_name, arguments)
    assert result.is_error
    assert result.content[0].text == f"INVALID_INPUT: {invalid_field} is invalid"
    audit_records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(audit_records) == 1
    assert audit_records[0]["args"] is None
    assert invalid_field in audit_records[0]["invalid_fields"]
    assert "SQL injection" not in audit_path.read_text()
    stderr = capsys.readouterr().err
    assert arguments[invalid_field] not in stderr
    assert "SQL injection" not in result.content[0].text


@pytest.mark.parametrize(("tool_name", "arguments"), EXTRA_CASES)
def test_unexpected_tool_parameters_are_rejected_without_logging_values(
    tmp_path, capsys, tool_name, arguments
) -> None:
    database_path = tmp_path / "claims.db"
    audit_path = tmp_path / "audit.jsonl"
    build_fixture_db(database_path)
    result = _invoke(_config(database_path, audit_path), tool_name, arguments)
    assert result.is_error
    assert result.content[0].text == "INVALID_INPUT: arguments is invalid"
    audit = audit_path.read_text(encoding="utf-8")
    assert len(audit.splitlines()) == 1
    assert '"invalid_fields":["arguments"]' in audit
    assert "attacker_controlled_extra" not in audit
    assert "extra-secret" not in capsys.readouterr().err


@pytest.mark.parametrize(("tool_name", "arguments", "field"), BOUND_CASES)
def test_numeric_bounds_and_strict_integer_types_are_audited(
    tmp_path, capsys, tool_name, arguments, field
) -> None:
    database_path = tmp_path / "claims.db"
    audit_path = tmp_path / "audit.jsonl"
    build_fixture_db(database_path)
    result = _invoke(_config(database_path, audit_path), tool_name, arguments)
    assert result.is_error
    assert result.content[0].text == f"INVALID_INPUT: {field} is invalid"
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1
    event = json.loads(capsys.readouterr().err)
    assert event["guardrail_status"] == "BLOCKED_VALIDATION"


@pytest.fixture(scope="module")
def adversarial_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("adversarial-seed") / "claims.db"
    seed_database(path)
    return path


@pytest.mark.parametrize("note_id", range(1, 16))
def test_seeded_adversarial_notes_are_untrusted_sanitized_capped_and_flagged(
    adversarial_database, note_id, tmp_path, capsys
) -> None:
    with sqlite3.connect(adversarial_database) as connection:
        claim_id, raw_text = connection.execute(
            "SELECT claim_id, note_text FROM claim_notes WHERE note_id = ?",
            (note_id,),
        ).fetchone()
    audit_path = tmp_path / "audit.jsonl"
    config = _config(adversarial_database, audit_path)
    result = _invoke(config, "get_claim_notes", {"claim_id": claim_id, "limit": 20})
    assert not result.is_error
    content = result.structured_content
    assert content["claim_id"] == claim_id
    assert content["untrusted"] is True
    note = next(item for item in content["notes"] if item["note_id"] == note_id)
    assert note["text"].startswith("<untrusted_claim_note>")
    assert note["text"].endswith("</untrusted_claim_note>")
    assert len(note["text"]) <= 1000
    assert "\u200b" not in note["text"]
    assert "\u202e" not in note["text"]
    if note_id == 7:
        assert note["truncated"] is True
    audit_records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(audit_records) == 1
    assert "note_text" not in audit_records[0]
    event = json.loads(capsys.readouterr().err)
    assert event["guardrail_status"] == "FLAGGED_ADVERSARIAL"
    assert raw_text not in json.dumps(audit_records)
    assert raw_text not in json.dumps(event)


def test_rate_limit_denial_leaves_one_audit_line_per_call(tmp_path, capsys) -> None:
    database_path = tmp_path / "claims.db"
    audit_path = tmp_path / "audit.jsonl"
    build_fixture_db(database_path)
    config = _config(database_path, audit_path, rate_limit=1)

    async def call_twice():
        async with Client(create_server(config, transport="stdio")) as client:
            first_result = await client.call_tool(
                "get_claim", {"claim_id": "CLM00000001"}
            )
            second_result = await client.call_tool(
                "get_claim", {"claim_id": "CLM00000001"}
            )
            return first_result, second_result

    first, second = asyncio.run(call_twice())
    assert not first.is_error
    assert second.is_error
    assert second.content[0].text == "RATE_LIMITED: try again later"
    assert len(audit_path.read_text().splitlines()) == 2
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(events) == 2
    assert events[1]["error"] == "RATE_LIMITED"
    assert events[1]["guardrail_status"] == "BLOCKED_VALIDATION"
