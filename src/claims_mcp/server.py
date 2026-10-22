"""MCP server and authenticated, audited tool pipeline."""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from claims_mcp.audit import (
    AuditWriteError,
    audit_then_return,
    make_structured_record,
    new_correlation_id,
    utc_timestamp,
    write_structured_stderr,
)
from claims_mcp.config import PROJECT_ROOT, Config
from claims_mcp.db import open_readonly
from claims_mcp.http_auth import current_key
from claims_mcp.models import (
    ErrorCode,
    GetClaimInput,
    GetClaimNotesInput,
    GetCoverageRuleInput,
    GetMemberYtdSummaryInput,
    ListMemberClaimsInput,
    SafeError,
)
from claims_mcp.queries import (
    get_claim,
    get_claim_notes,
    get_coverage_rule,
    get_member_ytd_summary,
    list_member_claims,
)
from claims_mcp.security import (
    FixedWindowRateLimiter,
    sanitize_untrusted_text,
)

INPUT_MODELS = {
    "get_claim": GetClaimInput,
    "list_member_claims": ListMemberClaimsInput,
    "get_coverage_rule": GetCoverageRuleInput,
    "get_member_ytd_summary": GetMemberYtdSummaryInput,
    "get_claim_notes": GetClaimNotesInput,
}
ADVERSARIAL_PATTERN = re.compile(
    r"ignore (?:all |the )?(?:previous|prior|above)|<system|"
    r'"tool"\s*:|https?://|(?:select|drop|delete|update)\s+.+\s+from|'
    r"bypass authentication|reveal (?:the )?(?:hidden|database)",
    re.IGNORECASE,
)
NOTE_OPEN = "<untrusted_claim_note>"
NOTE_CLOSE = "</untrusted_claim_note>"


def _mask_identifier(value: str) -> str:
    if len(value) <= 5:
        return value[0] + "*" * (len(value) - 1)
    return value[:3] + "*" * (len(value) - 5) + value[-2:]


def _audit_arguments(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _mask_identifier(value) if key.endswith("_id") else value
        for key, value in values.items()
        if value is not None
    }


def _safe_validation_fields(model: type, error: ValidationError) -> list[str]:
    known = model.model_fields
    fields = {
        str(item["loc"][0])
        if item["loc"] and str(item["loc"][0]) in known
        else "arguments"
        for item in error.errors()
    }
    return sorted(fields)


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)], is_error=True
    )


class ClaimsMCPServer(MCPServer):
    """MCPServer subclass that validates raw arguments inside the audited pipeline."""

    def __init__(self, config: Config, transport: str = "stdio") -> None:
        super().__init__(name="claims-mcp-server", version="0.1.0")
        self.config = config
        self.transport = transport
        self.rate_limiter = FixedWindowRateLimiter(config.rate_limit_per_minute)
        annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False)
        for name, handler in (
            ("get_claim", self._schema_get_claim),
            ("list_member_claims", self._schema_list_member_claims),
            ("get_coverage_rule", self._schema_get_coverage_rule),
            ("get_member_ytd_summary", self._schema_get_member_ytd_summary),
            ("get_claim_notes", self._schema_get_claim_notes),
        ):
            self.add_tool(
                handler,
                name=name,
                description=f"Read synthetic claims data with {name}.",
                annotations=annotations,
            )

    def _schema_get_claim(self, claim_id: str) -> dict[str, Any]:
        """Return a claim and its plan/provider summary."""
        return {"claim_id": claim_id}

    def _schema_list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List a member's claims with optional status and bounded pagination."""
        return {
            "member_id": member_id,
            "status": status,
            "limit": limit,
            "offset": offset,
        }

    def _schema_get_coverage_rule(
        self, plan_id: str, procedure_code: str
    ) -> dict[str, Any]:
        """Read the coverage rule for one plan and procedure."""
        return {"plan_id": plan_id, "procedure_code": procedure_code}

    def _schema_get_member_ytd_summary(
        self, member_id: str, year: int
    ) -> dict[str, Any]:
        """Summarize one member's claims for a calendar year."""
        return {"member_id": member_id, "year": year}

    def _schema_get_claim_notes(self, claim_id: str, limit: int = 10) -> dict[str, Any]:
        """Read sanitized, explicitly untrusted notes for a claim."""
        return {"claim_id": claim_id, "limit": limit}

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ) -> CallToolResult:
        """Intercept unvalidated MCP arguments before the SDK tool manager runs."""
        del context
        return self._invoke(name, arguments)

    def _invoke(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        correlation_id = new_correlation_id()
        started = time.perf_counter()
        identity = current_key() if self.transport == "http" else None
        caller = identity.key_id if identity else "stdio-local"
        caller_key_hash = identity.sha256[:12] if identity else None
        model = INPUT_MODELS.get(name)
        invalid_fields: list[str] | None = None
        validated_values: dict[str, Any] | None = None
        claim_id: str | None = None
        error_code: ErrorCode | None = None
        outcome = "ok"
        rows = 0
        guardrail_status = "PASS"
        result: dict[str, Any] | list[Any] | None = None

        try:
            if self.transport == "http" and identity is None:
                guardrail_status = "BLOCKED_AUTH"
                raise SafeError(ErrorCode.UNAUTHENTICATED)
            if not self.rate_limiter.allow(caller):
                guardrail_status = "BLOCKED_VALIDATION"
                raise SafeError(ErrorCode.RATE_LIMITED, "try again later")
            if name not in INPUT_MODELS:
                guardrail_status = "BLOCKED_AUTH"
                raise SafeError(ErrorCode.FORBIDDEN_TOOL)
            if identity is not None and name not in identity.allowed_tools:
                guardrail_status = "BLOCKED_AUTH"
                raise SafeError(ErrorCode.FORBIDDEN_TOOL)

            assert model is not None
            if not isinstance(arguments, dict):
                guardrail_status = "BLOCKED_VALIDATION"
                invalid_fields = ["arguments"]
                raise SafeError(ErrorCode.INVALID_INPUT, "arguments is invalid")
            try:
                validated = model.model_validate(arguments)
            except ValidationError as validation_error:
                guardrail_status = "BLOCKED_VALIDATION"
                invalid_fields = _safe_validation_fields(model, validation_error)
                field = invalid_fields[0] if invalid_fields else "arguments"
                raise SafeError(
                    ErrorCode.INVALID_INPUT, f"{field} is invalid"
                ) from None

            validated_values = validated.model_dump()
            claim_id_value = validated_values.get("claim_id")
            claim_id = claim_id_value if isinstance(claim_id_value, str) else None
            with open_readonly(self.config.database_path) as connection:
                result = self._run_query(name, validated_values, connection)
            if result is None:
                outcome = "not_found"
                raise SafeError(ErrorCode.NOT_FOUND)
            if name == "get_claim_notes":
                result, flagged = self._sanitize_notes(claim_id or "", result)
                if flagged:
                    guardrail_status = "FLAGGED_ADVERSARIAL"
            rows = self._row_count(name, result)
        except SafeError as error:
            error_code = error.code
            if error.code == ErrorCode.INVALID_INPUT:
                outcome = "invalid"
            elif error.code == ErrorCode.FORBIDDEN_TOOL:
                outcome = "denied"
            elif error.code == ErrorCode.RATE_LIMITED:
                outcome = "rate_limited"
            elif error.code == ErrorCode.NOT_FOUND:
                outcome = "not_found"
            elif error.code == ErrorCode.UNAUTHENTICATED:
                outcome = "denied"
            response = _error_result(str(error))
        # The tool boundary must map all unexpected failures to a safe wire error.
        except Exception:  # noqa: BLE001
            error_code = ErrorCode.INTERNAL
            outcome = "error"
            response = _error_result(f"INTERNAL: reference {correlation_id}")
        else:
            response = CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            result, separators=(",", ":"), ensure_ascii=True
                        ),
                    )
                ],
                structured_content=result
                if isinstance(result, dict)
                else {"result": result},
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        audit_record: dict[str, Any] = {
            "ts": utc_timestamp(),
            "request_id": correlation_id,
            "caller": caller,
            "transport": self.transport,
            "tool": name,
            "args": _audit_arguments(validated_values)
            if validated_values is not None
            else None,
            "outcome": outcome,
            "rows": rows,
            "duration_ms": round(elapsed_ms, 3),
            "error_code": error_code.value if error_code else None,
        }
        if invalid_fields is not None:
            audit_record["invalid_fields"] = invalid_fields

        structured_record = make_structured_record(
            tool_name=name,
            caller_key=None,
            caller_key_hash=caller_key_hash,
            claim_id=_mask_identifier(claim_id) if claim_id else None,
            guardrail_status=guardrail_status,
            latency_ms=elapsed_ms,
            error=error_code.value if error_code else None,
            correlation_id=correlation_id,
        )
        try:
            return audit_then_return(
                self.config.audit_path,
                audit_record,
                structured_record,
                response,
            )
        except AuditWriteError:
            failed_log = make_structured_record(
                tool_name=name,
                caller_key=None,
                caller_key_hash=caller_key_hash,
                claim_id=_mask_identifier(claim_id) if claim_id else None,
                guardrail_status="BLOCKED_VALIDATION",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=ErrorCode.INTERNAL.value,
                correlation_id=correlation_id,
            )
            try:
                write_structured_stderr(failed_log)
            except AuditWriteError:
                pass
            return _error_result(f"INTERNAL: reference {correlation_id}")

    @staticmethod
    def _run_query(
        name: str, values: dict[str, Any], connection
    ) -> dict[str, Any] | list[Any] | None:
        if name == "get_claim":
            return get_claim(connection, values["claim_id"])
        if name == "list_member_claims":
            return list_member_claims(
                connection,
                values["member_id"],
                values["status"],
                values["limit"],
                values["offset"],
            )
        if name == "get_coverage_rule":
            return get_coverage_rule(
                connection, values["plan_id"], values["procedure_code"]
            )
        if name == "get_member_ytd_summary":
            return get_member_ytd_summary(
                connection, values["member_id"], values["year"]
            )
        if name == "get_claim_notes":
            return get_claim_notes(connection, values["claim_id"], values["limit"])
        return None

    @staticmethod
    def _row_count(name: str, result: dict[str, Any] | list[Any]) -> int:
        if name == "list_member_claims" and isinstance(result, dict):
            return len(result["items"])
        if name == "get_claim_notes" and isinstance(result, list):
            return len(result)
        return 1

    @staticmethod
    def _sanitize_notes(
        claim_id: str, notes: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        sidecar = PROJECT_ROOT / "data" / "adversarial_note_ids.txt"
        adversarial_ids = {
            int(line)
            for line in sidecar.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        flagged = False
        safe_notes = []
        body_limit = 1000 - len(NOTE_OPEN) - len(NOTE_CLOSE)
        for note in notes:
            raw_text = note["note_text"]
            if note["note_id"] in adversarial_ids or ADVERSARIAL_PATTERN.search(
                raw_text
            ):
                flagged = True
            sanitized, was_truncated = sanitize_untrusted_text(raw_text, body_limit)
            escaped = html.escape(sanitized, quote=False)
            if len(escaped) > body_limit:
                escaped = escaped[:body_limit]
                was_truncated = True
            safe_notes.append(
                {
                    "note_id": note["note_id"],
                    "created_on": note["created_on"],
                    "author_type": note["author_type"],
                    "text": f"{NOTE_OPEN}{escaped}{NOTE_CLOSE}",
                    "truncated": was_truncated,
                }
            )
        return {"claim_id": claim_id, "untrusted": True, "notes": safe_notes}, flagged


def create_server(config: Config, transport: str = "stdio") -> ClaimsMCPServer:
    """Create an MCP server with the requested local or HTTP trust boundary."""
    return ClaimsMCPServer(config, transport=transport)
