"""Seed and fixture database acceptance tests."""

import sqlite3

from scripts.seed_db import seed_database
from tests.fixtures import build_fixture_db


def test_seed_has_expected_counts_and_is_deterministic(tmp_path) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    seed_database(first)
    seed_database(second)

    with sqlite3.connect(first) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "plans",
                "members",
                "providers",
                "coverage_rules",
                "claims",
                "claim_notes",
            )
        }
        assert counts == {
            "plans": 3,
            "members": 200,
            "providers": 25,
            "coverage_rules": 36,
            "claims": 1500,
            "claim_notes": 400,
        }
        with open(
            connection.execute("PRAGMA database_list").fetchone()[2], "rb"
        ) as db_file:
            first_bytes = db_file.read()
    assert first_bytes == second.read_bytes()


def test_fixture_builds_fixed_small_database(tmp_path) -> None:
    path = tmp_path / "fixture.db"
    build_fixture_db(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 8
        assert connection.execute("SELECT COUNT(*) FROM claim_notes").fetchone()[0] == 4
