"""Fixed parameterized SQL queries for the claims tools."""

from __future__ import annotations

import sqlite3
from typing import Any


def get_claim(connection: sqlite3.Connection, claim_id: str) -> dict[str, Any] | None:
    """Join one claim to its provider network status and member plan."""
    row = connection.execute(
        "SELECT c.claim_id, c.member_id, m.plan_id, c.provider_id, "
        "p.network_status AS provider_network_status, c.procedure_code, "
        "c.diagnosis_code, c.service_date, c.submitted_on, c.billed_amount, "
        "c.allowed_amount, c.paid_amount, c.status "
        "FROM claims c "
        "JOIN members m ON m.member_id = c.member_id "
        "JOIN providers p ON p.provider_id = c.provider_id "
        "WHERE c.claim_id = ?",
        (claim_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_member_claims(
    connection: sqlite3.Connection,
    member_id: str,
    status: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any] | None:
    """Count and paginate a member's claims ordered newest service date first."""
    member = connection.execute(
        "SELECT 1 FROM members WHERE member_id = ?", (member_id,)
    ).fetchone()
    if member is None:
        return None
    count_row = connection.execute(
        "SELECT COUNT(*) AS total FROM claims "
        "WHERE member_id = ? AND (? IS NULL OR status = ?)",
        (member_id, status, status),
    ).fetchone()
    rows = connection.execute(
        "SELECT claim_id, service_date, procedure_code, billed_amount, status "
        "FROM claims WHERE member_id = ? AND (? IS NULL OR status = ?) "
        "ORDER BY service_date DESC, claim_id DESC LIMIT ? OFFSET ?",
        (member_id, status, status, limit, offset),
    ).fetchall()
    return {
        "total": count_row["total"],
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


def get_coverage_rule(
    connection: sqlite3.Connection, plan_id: str, procedure_code: str
) -> dict[str, Any] | None:
    """Fetch one plan/procedure coverage rule."""
    row = connection.execute(
        "SELECT plan_id, procedure_code, covered, requires_preauth, max_allowed "
        "FROM coverage_rules WHERE plan_id = ? AND procedure_code = ?",
        (plan_id, procedure_code),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["covered"] = bool(result["covered"])
    result["requires_preauth"] = bool(result["requires_preauth"])
    return result


def get_member_ytd_summary(
    connection: sqlite3.Connection, member_id: str, year: int
) -> dict[str, Any] | None:
    """Group the member's year-to-date claims by status and calculate remaining limit."""
    member = connection.execute(
        "SELECT m.plan_id, p.annual_limit, p.deductible "
        "FROM members m JOIN plans p ON p.plan_id = m.plan_id "
        "WHERE m.member_id = ?",
        (member_id,),
    ).fetchone()
    if member is None:
        return None
    grouped = connection.execute(
        "SELECT status, COUNT(*) AS claim_count, "
        "COALESCE(SUM(billed_amount), 0) AS total_billed, "
        "COALESCE(SUM(paid_amount), 0) AS total_paid "
        "FROM claims WHERE member_id = ? AND substr(service_date, 1, 4) = ? "
        "GROUP BY status ORDER BY status",
        (member_id, str(year)),
    ).fetchall()
    by_status = [dict(row) for row in grouped]
    paid_total = sum(row["total_paid"] for row in by_status if row["status"] == "PAID")
    annual_limit = member["annual_limit"]
    return {
        "member_id": member_id,
        "year": year,
        "plan_id": member["plan_id"],
        "annual_limit": annual_limit,
        "deductible": member["deductible"],
        "by_status": by_status,
        "remaining_annual_limit": max(0.0, annual_limit - paid_total),
    }


def get_claim_notes(
    connection: sqlite3.Connection, claim_id: str, limit: int
) -> list[dict[str, Any]] | None:
    """Fetch newest notes for an existing claim; keep their text untrusted."""
    claim = connection.execute(
        "SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if claim is None:
        return None
    rows = connection.execute(
        "SELECT note_id, created_on, author_type, note_text "
        "FROM claim_notes WHERE claim_id = ? "
        "ORDER BY created_on DESC, note_id DESC LIMIT ?",
        (claim_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]
