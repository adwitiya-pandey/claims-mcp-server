"""Read-only SQLite connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class DatabaseAccessError(RuntimeError):
    """A database path could not be opened safely."""


def schema_path() -> Path:
    """Return the project schema path independent of the current directory."""
    return Path(__file__).resolve().parents[2] / "schema.sql"


def _authorize_read_only(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def open_readonly(path: Path | str) -> sqlite3.Connection:
    """Open an existing SQLite database with independent read-only defenses."""
    database_path = Path(path)
    try:
        resolved_path = database_path.resolve(strict=True)
        if not resolved_path.is_file():
            raise DatabaseAccessError("Database path is not a file")
        connection = sqlite3.connect(
            f"{resolved_path.as_uri()}?mode=ro",
            uri=True,
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.set_authorizer(_authorize_read_only)
        return connection
    except (OSError, sqlite3.Error) as error:
        raise DatabaseAccessError("Database could not be opened safely") from error
