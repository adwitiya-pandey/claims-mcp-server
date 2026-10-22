"""Read-only SQLite boundary tests."""

import sqlite3

import pytest

from claims_mcp.db import DatabaseAccessError, open_readonly
from tests.fixtures import build_fixture_db


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "claims.db"
    build_fixture_db(path)
    return path


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO plans VALUES ('PLN003', 'x', 1, 1, 1)",
        "UPDATE plans SET plan_name = 'x'",
        "DELETE FROM plans",
        "DROP TABLE claims",
        "CREATE TABLE injected (id INTEGER)",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA user_version = 123",
    ],
)
def test_all_write_operations_are_denied(database_path, statement) -> None:
    with (
        open_readonly(database_path) as connection,
        pytest.raises(sqlite3.DatabaseError),
    ):
        connection.execute(statement)


def test_select_join_group_by_and_aggregate_are_allowed(database_path) -> None:
    with open_readonly(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 8
        joined = connection.execute(
            "SELECT c.claim_id, p.network_status FROM claims c "
            "JOIN providers p ON p.provider_id = c.provider_id "
            "WHERE c.claim_id = ?",
            ("CLM00000001",),
        ).fetchone()
        assert joined["network_status"] == "IN_NETWORK"
        grouped = connection.execute(
            "SELECT status, SUM(billed_amount) total FROM claims GROUP BY status"
        ).fetchall()
        assert grouped


def test_missing_database_does_not_create_file(tmp_path) -> None:
    missing_path = tmp_path / "missing.db"
    with pytest.raises(DatabaseAccessError):
        open_readonly(missing_path)
    assert not missing_path.exists()
