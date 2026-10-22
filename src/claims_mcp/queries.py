"""Fixed parameterized SQL queries for the claims tools."""

from __future__ import annotations

import sqlite3
from typing import Any


def get_claim(connection: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    """Join one claim to its provider network status and member plan."""
    raise NotImplementedError


def list_member_claims(
    connection: sqlite3.Connection,
    member_id: str,
    status: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any] | None:
    """Count and paginate a member's claims ordered newest service date first."""
    raise NotImplementedError


def get_coverage_rule(
    connection: sqlite3.Connection, plan_id: str, procedure_code: str
) -> dict[str, Any] | None:
    """Fetch one plan/procedure coverage rule."""
    raise NotImplementedError


def get_member_ytd_summary(
    connection: sqlite3.Connection, member_id: str, year: int
) -> dict[str, Any] | None:
    """Group the member's year-to-date claims by status and calculate remaining limit."""
    raise NotImplementedError


def get_claim_notes(
    connection: sqlite3.Connection, claim_id: str, limit: int
) -> list[dict[str, Any]] | None:
    """Fetch newest notes for an existing claim; keep their text untrusted."""
    raise NotImplementedError
