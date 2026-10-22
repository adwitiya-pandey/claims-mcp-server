"""HTTP bearer-key authentication middleware for Streamable HTTP."""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from typing import Any

from claims_mcp.audit import (
    AuditWriteError,
    make_structured_record,
    write_structured_stderr,
)
from claims_mcp.security import KeyRecord, SecurityConfigurationError, authenticate_key

_CURRENT_KEY: ContextVar[KeyRecord | None] = ContextVar("claims_api_key", default=None)


def current_key() -> KeyRecord | None:
    """Return the authenticated key record for this HTTP request context."""
    return _CURRENT_KEY.get()


class BearerKeyMiddleware:
    """Authenticate every HTTP request to the MCP endpoint before dispatch."""

    def __init__(self, app: Any, keys_path: str) -> None:
        self.app = app
        self.keys_path = keys_path

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/mcp":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ]
        supplied_key: str | None = None
        if len(values) == 1 and values[0].startswith("Bearer "):
            supplied_key = values[0][len("Bearer ") :]

        try:
            identity = authenticate_key(supplied_key, self.keys_path)
        except SecurityConfigurationError:
            identity = None

        authenticated = identity is not None
        try:
            write_structured_stderr(
                make_structured_record(
                    tool_name=None,
                    caller_key=supplied_key,
                    claim_id=None,
                    guardrail_status="PASS" if authenticated else "BLOCKED_AUTH",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=None if authenticated else "UNAUTHENTICATED",
                )
            )
        except AuditWriteError:
            await self._respond(send, 500)
            return

        if identity is None:
            await self._respond(send, 401)
            return

        request_token: Token[KeyRecord | None] = _CURRENT_KEY.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            _CURRENT_KEY.reset(request_token)

    @staticmethod
    async def _respond(send: Any, status_code: int) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})
