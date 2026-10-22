"""Validated request and response models for the claims tools."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

ClaimStatus = Literal["SUBMITTED", "PENDED", "APPROVED", "DENIED", "PAID"]
ClaimAuthor = Literal["CLAIMANT", "PROVIDER", "ANALYST", "SYSTEM"]


class ErrorCode(StrEnum):
    """Stable tool error codes."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    RATE_LIMITED = "RATE_LIMITED"
    FORBIDDEN_TOOL = "FORBIDDEN_TOOL"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL = "INTERNAL"


class SafeError(Exception):
    """An anticipated error safe to expose through the MCP tool result."""

    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code.value if detail is None else f"{code.value}: {detail}")


class InputModel(BaseModel):
    """Base class enforcing strict scalar types and closed schemas."""

    model_config = ConfigDict(strict=True, extra="forbid")


class GetClaimInput(InputModel):
    claim_id: StrictStr

    @field_validator("claim_id")
    @classmethod
    def valid_claim_id(cls, value: str) -> str:
        if re.fullmatch(r"CLM[0-9]{8}", value) is None:
            raise ValueError("invalid")
        return value


class ListMemberClaimsInput(InputModel):
    member_id: StrictStr
    status: ClaimStatus | None = None
    limit: StrictInt = Field(default=20, ge=1, le=50)
    offset: StrictInt = Field(default=0, ge=0, le=10000)

    @field_validator("member_id")
    @classmethod
    def valid_member_id(cls, value: str) -> str:
        if re.fullmatch(r"M[0-9]{6}", value) is None:
            raise ValueError("invalid")
        return value


class GetCoverageRuleInput(InputModel):
    plan_id: StrictStr
    procedure_code: StrictStr

    @field_validator("plan_id")
    @classmethod
    def valid_plan_id(cls, value: str) -> str:
        if re.fullmatch(r"PLN[0-9]{3}", value) is None:
            raise ValueError("invalid")
        return value

    @field_validator("procedure_code")
    @classmethod
    def valid_procedure_code(cls, value: str) -> str:
        if re.fullmatch(r"PX[0-9]{4}", value) is None:
            raise ValueError("invalid")
        return value


class GetMemberYtdSummaryInput(InputModel):
    member_id: StrictStr
    year: StrictInt = Field(ge=2000, le=2100)

    @field_validator("member_id")
    @classmethod
    def valid_member_id(cls, value: str) -> str:
        if re.fullmatch(r"M[0-9]{6}", value) is None:
            raise ValueError("invalid")
        return value


class GetClaimNotesInput(InputModel):
    claim_id: StrictStr
    limit: StrictInt = Field(default=10, ge=1, le=20)

    @field_validator("claim_id")
    @classmethod
    def valid_claim_id(cls, value: str) -> str:
        if re.fullmatch(r"CLM[0-9]{8}", value) is None:
            raise ValueError("invalid")
        return value


class ClaimOutput(BaseModel):
    claim_id: str
    member_id: str
    plan_id: str
    provider_id: str
    provider_network_status: str
    procedure_code: str
    diagnosis_code: str
    service_date: str
    submitted_on: str
    billed_amount: float
    allowed_amount: float | None
    paid_amount: float | None
    status: ClaimStatus


class MemberClaimOutput(BaseModel):
    claim_id: str
    service_date: str
    procedure_code: str
    billed_amount: float
    status: ClaimStatus


class CoverageRuleOutput(BaseModel):
    plan_id: str
    procedure_code: str
    covered: bool
    requires_preauth: bool
    max_allowed: float | None


class StatusSummaryOutput(BaseModel):
    status: ClaimStatus
    claim_count: int
    total_billed: float
    total_paid: float


class MemberYtdSummaryOutput(BaseModel):
    member_id: str
    year: int
    plan_id: str
    annual_limit: float
    deductible: float
    by_status: list[StatusSummaryOutput]
    remaining_annual_limit: float


class ClaimNoteOutput(BaseModel):
    note_id: int
    created_on: str
    author_type: ClaimAuthor
    text: str
    truncated: bool


class ClaimNotesOutput(BaseModel):
    claim_id: str
    untrusted: Literal[True] = True
    notes: list[ClaimNoteOutput]


def invalid_field_names(error: ValidationError) -> list[str]:
    """Return stable model field names without exposing invalid values."""
    return sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
