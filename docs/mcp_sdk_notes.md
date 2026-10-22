# MCP Python SDK notes

Pre-filled on 17 Oct 2026 and verified on 19 Oct 2026 by running `mcp` 2.3.0 in the uv project environment. The probes were re-run on Python 3.12.15 with `mcp` 2.3.0 on 22 Oct 2026 and confirmed.

## Verified imports
```python
from mcp.server import (
    MCPServer,
)  # also: from mcp.server.mcpserver import MCPServer, Context
from mcp import Client
from mcp.types import ToolAnnotations, CallToolResult, TextContent
from mcp.server.mcpserver.exceptions import ToolError  # also UnexpectedToolError
from mcp.client.streamable_http import streamable_http_client
```
`from mcp.server.fastmcp import FastMCP` raises `ModuleNotFoundError` (v1 API, gone in 2.x).

## Verified behaviour
| Topic | Result |
|---|---|
| Server | `MCPServer(name, ..., token_verifier=None, auth_server_provider=None)`; `@server.tool(annotations=...)`; `server.run(transport="stdio" or "streamable-http", host=..., port=...)` |
| Annotations | `ToolAnnotations(read_only_hint=True, destructive_hint=False)` works; the client sees them in `list_tools()` |
| In-memory test client | `async with Client(server) as c:` with an `MCPServer` instance; `c.list_tools()`, `c.call_tool(name, args)` |
| Success result | `.is_error` False; `.content` text; `.structured_content` was `None` for a plain `dict` return (do not rely on it without checking) |
| Handler raises `ValueError("NOT_FOUND")` | client gets `is_error=True`, text `Error executing tool <name>` (message lost); the server logs a full traceback |
| Handler raises `ToolError("NOT_FOUND")` | text `Error executing tool <name>: NOT_FOUND` |
| Handler returns `CallToolResult(is_error=True, content=[TextContent(type="text", text="INVALID_INPUT: claim_id is invalid")])` | client gets exactly that text with `is_error=True`. **Use this** (SPEC 8 wire format) |
| Missing or extra required argument on a typed parameter | SDK rejects before the handler runs; `is_error=True`; text contains `Field required [type=missing, input_value={'wrong': 1}, ...]`: **raw input echoed** (SPEC S13) |
| Parameter typed `object` with a default | handler is called with whatever was sent (`int` arrived as `int`); an extra unknown argument is silently ignored and the parameter stays `None` |
| HTTP client with a header | `Client(streamable_http_client(url, http_client=<AsyncClient(headers={"Authorization": "Bearer ..."})>))`; `Client("http://127.0.0.1:PORT/mcp")` also works. The SDK's client library imports as `httpx2` |
| HTTP server | `server.run(transport="streamable-http", host="127.0.0.1", port=8791)`; endpoint `/mcp` |

The Python 3.12.15 probe reproduced every result above. In particular, an SDK-validated missing argument produces a tool result that echoes the supplied argument object, while an `object = None` handler receives wrong scalar types but the SDK silently discards unknown extra keys. This is why the server must intercept raw tool arguments before the tool manager validates them.

## Decisions verified for T8/T9

1. **S13:** `ClaimsMCPServer.call_tool` intercepts the raw argument dictionary before the SDK tool manager validates typed parameters. The five registered tool schemas remain typed and useful; validation is performed by the Pydantic request models. The missing, extra, and wrong-type calls in `tests/test_server_s13.py` each reach the pipeline, leave one audit file line, and return safe errors without echoing values.
2. **HTTP bearer auth:** the official authorization guide documents `TokenVerifier` together with `AuthSettings` as an OAuth resource-server integration. Since this project uses static API keys and has no OAuth issuer, `BearerKeyMiddleware` protects `/mcp` before request parsing. The SDK continues to provide the Streamable HTTP transport. See [docs/decisions.md](./decisions.md).
3. **Structured output:** the server returns both compact JSON text and `structured_content`; the protocol suite checks the shape for all five tools.
4. **Python 3.12:** the import/error/schema probes and project tests ran on Python 3.12.15 with `mcp` 2.3.0.

Official pages consulted: [Authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/index.md), [Testing](https://py.sdk.modelcontextprotocol.io/get-started/testing/index.md), and [Middleware](https://py.sdk.modelcontextprotocol.io/advanced/middleware/index.md).

## Probe snippets (re-run these)
```python
import asyncio
from mcp.server import MCPServer
from mcp import Client
from mcp.types import ToolAnnotations, CallToolResult, TextContent
from mcp.server.mcpserver.exceptions import ToolError

srv = MCPServer("probe")


@srv.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def typed(claim_id: str) -> dict:
    if claim_id == "boom":
        raise ValueError("NOT_FOUND")
    return {"ok": True}


@srv.tool()
def returns_error(x: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="INVALID_INPUT: claim_id is invalid")],
        is_error=True,
    )


@srv.tool()
def loose(claim_id: object = None) -> dict:
    return {"got": type(claim_id).__name__}


async def main():
    async with Client(srv) as c:
        for name, args in [
            ("typed", {"claim_id": "boom"}),
            ("typed", {"wrong": 1}),
            ("returns_error", {"x": "a"}),
            ("loose", {"claim_id": 123}),
            ("loose", {"extra": 1}),
        ]:
            r = await c.call_tool(name, args)
            print(
                name,
                args,
                "->",
                r.is_error,
                [getattr(x, "text", x) for x in r.content][:1],
            )


asyncio.run(main())
```
