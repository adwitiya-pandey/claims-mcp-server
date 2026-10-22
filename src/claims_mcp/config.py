"""Project configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from claims_mcp.security import load_keys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    """Startup configuration is invalid or incomplete."""


@dataclass(frozen=True)
class Config:
    database_path: Path
    keys_path: Path
    audit_path: Path
    rate_limit_per_minute: int
    http_host: str
    http_port: int


def _configured_path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


def load_config() -> Config:
    """Load typed runtime settings from environment variables."""
    try:
        rate_limit = int(os.environ.get("CLAIMS_RATE_LIMIT_PER_MIN", "60"))
        port = int(os.environ.get("CLAIMS_HTTP_PORT", "8765"))
    except ValueError as error:
        raise ConfigurationError(
            "Numeric configuration values must be integers"
        ) from error
    if rate_limit < 1:
        raise ConfigurationError("Rate limit must be positive")
    if not 1 <= port <= 65535:
        raise ConfigurationError("HTTP port is outside the allowed range")
    return Config(
        database_path=_configured_path("CLAIMS_DB_PATH", "data/claims.db"),
        keys_path=_configured_path("CLAIMS_KEYS_FILE", "keys.json"),
        audit_path=_configured_path("CLAIMS_AUDIT_PATH", "audit/audit.jsonl"),
        rate_limit_per_minute=rate_limit,
        http_host=os.environ.get("CLAIMS_HTTP_HOST", "127.0.0.1"),
        http_port=port,
    )


def validate_config(config: Config, transport: str) -> None:
    """Fail startup early when the database or HTTP credentials are unavailable."""
    if transport not in {"stdio", "http"}:
        raise ConfigurationError("Transport must be stdio or http")
    if not config.database_path.is_file():
        raise ConfigurationError("Claims database file is required")
    if transport == "http":
        if config.http_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigurationError("HTTP must bind to a loopback address")
        try:
            records = load_keys(config.keys_path)
        except RuntimeError as error:
            raise ConfigurationError("HTTP mode requires a valid keys file") from error
        if not records:
            raise ConfigurationError("HTTP mode requires at least one API key")
