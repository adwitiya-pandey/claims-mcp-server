"""Input model validation regression tests."""

import pytest
from pydantic import ValidationError

from claims_mcp.models import (
    GetClaimInput,
    GetClaimNotesInput,
    GetCoverageRuleInput,
    GetMemberYtdSummaryInput,
    ListMemberClaimsInput,
    invalid_field_names,
)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("CLM00000001", True),
        ("CLM12345678", True),
        ("CLM00000001\n", False),
        ("CLM１２３４５６７８", False),
        ("", False),
        (None, False),
        (1, False),
        ("CLM000000010", False),
        ("CLM00000001' OR 1=1 --", False),
    ],
)
def test_claim_id_validation(value, valid) -> None:
    if valid:
        assert GetClaimInput.model_validate({"claim_id": value}).claim_id == value
    else:
        with pytest.raises(ValidationError):
            GetClaimInput.model_validate({"claim_id": value})


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("M000001", True),
        ("M１２３４５６", False),
        ("M000001\n", False),
        ("", False),
        (None, False),
        (True, False),
        ("M000001; DROP TABLE members", False),
    ],
)
def test_member_id_validation(value, valid) -> None:
    if valid:
        assert ListMemberClaimsInput.model_validate({"member_id": value}).member_id == value
    else:
        with pytest.raises(ValidationError):
            ListMemberClaimsInput.model_validate({"member_id": value})


@pytest.mark.parametrize(
    ("plan_id", "procedure_code", "valid"),
    [
        ("PLN001", "PX0001", True),
        ("PLN01", "PX0001", False),
        ("PLN001\n", "PX0001", False),
        ("PLN001", "PX１２３４", False),
        ("PLN001", "PX0001' OR 1=1", False),
        (None, "PX0001", False),
    ],
)
def test_coverage_id_validation(plan_id, procedure_code, valid) -> None:
    values = {"plan_id": plan_id, "procedure_code": procedure_code}
    if valid:
        assert GetCoverageRuleInput.model_validate(values).plan_id == plan_id
    else:
        with pytest.raises(ValidationError):
            GetCoverageRuleInput.model_validate(values)


@pytest.mark.parametrize(
    ("year", "valid"),
    [(2000, True), (2100, True), (1999, False), (2101, False), ("2025", False), (True, False), (None, False)],
)
def test_year_is_strict_and_bounded(year, valid) -> None:
    values = {"member_id": "M000001", "year": year}
    if valid:
        assert GetMemberYtdSummaryInput.model_validate(values).year == year
    else:
        with pytest.raises(ValidationError):
            GetMemberYtdSummaryInput.model_validate(values)


@pytest.mark.parametrize(
    ("model", "values", "valid"),
    [
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": 1, "offset": 0}, True),
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": 50, "offset": 10000}, True),
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": 0}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": 51}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "offset": -1}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "offset": 10001}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": "2"}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "limit": True}, False),
        (GetClaimNotesInput, {"claim_id": "CLM00000001", "limit": 20}, True),
        (GetClaimNotesInput, {"claim_id": "CLM00000001", "limit": 0}, False),
        (ListMemberClaimsInput, {"member_id": "M000001", "unexpected": "x"}, False),
    ],
)
def test_pagination_bounds_and_extra_fields(model, values, valid) -> None:
    if valid:
        model.model_validate(values)
    else:
        with pytest.raises(ValidationError) as error:
            model.model_validate(values)
        assert invalid_field_names(error.value)


def test_status_enum_rejects_unknown_and_accepts_none() -> None:
    assert ListMemberClaimsInput.model_validate({"member_id": "M000001", "status": None})
    with pytest.raises(ValidationError):
        ListMemberClaimsInput.model_validate({"member_id": "M000001", "status": "PAID;--"})
