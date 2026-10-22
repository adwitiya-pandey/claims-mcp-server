"""Call all five read-only tools through the authenticated HTTP transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def run_demo(url: str, key: str) -> None:
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        httpx2.AsyncClient(headers=headers) as http_client,
        Client(streamable_http_client(url, http_client=http_client)) as client,
    ):
        calls = (
            ("get_claim", {"claim_id": "CLM00000001"}),
            ("list_member_claims", {"member_id": "M000001", "limit": 2}),
            (
                "get_coverage_rule",
                {"plan_id": "PLN001", "procedure_code": "PX0001"},
            ),
            (
                "get_member_ytd_summary",
                {"member_id": "M000001", "year": 2025},
            ),
            ("get_claim_notes", {"claim_id": "CLM00000001", "limit": 3}),
        )
        for tool_name, arguments in calls:
            result = await client.call_tool(tool_name, arguments)
            if result.is_error:
                output = {"error": result.content[0].text}
            else:
                output = result.structured_content
            print(
                json.dumps(
                    {"tool": tool_name, "result": output},
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key",
        default=os.environ.get("CLAIMS_API_KEY"),
        help="API key (or set CLAIMS_API_KEY in the client environment)",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765/mcp",
        help="Streamable HTTP MCP endpoint",
    )
    args = parser.parse_args()
    if not args.key:
        parser.error("provide --key or set CLAIMS_API_KEY")
    asyncio.run(run_demo(args.url, args.key))


if __name__ == "__main__":
    main()
