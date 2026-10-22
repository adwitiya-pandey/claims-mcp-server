"""Startup configuration safety tests."""

import pytest

from claims_mcp.config import Config, ConfigurationError, validate_config


def _config(database_path, keys_path) -> Config:
    return Config(
        database_path=database_path,
        keys_path=keys_path,
        audit_path=database_path.parent / "audit.jsonl",
        rate_limit_per_minute=60,
        http_host="127.0.0.1",
        http_port=8765,
    )


def test_startup_refuses_missing_database(tmp_path) -> None:
    config = _config(tmp_path / "missing.db", tmp_path / "keys.json")
    with pytest.raises(ConfigurationError, match="database"):
        validate_config(config, "stdio")


def test_http_startup_refuses_missing_or_empty_keys(tmp_path) -> None:
    database_path = tmp_path / "claims.db"
    database_path.touch()
    config = _config(database_path, tmp_path / "keys.json")
    with pytest.raises(ConfigurationError, match="keys"):
        validate_config(config, "http")
    config.keys_path.write_text('{"keys": []}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="API key"):
        validate_config(config, "http")


def test_http_startup_refuses_non_loopback_bind(tmp_path) -> None:
    database_path = tmp_path / "claims.db"
    database_path.touch()
    keys_path = tmp_path / "keys.json"
    keys_path.write_text('{"keys": []}', encoding="utf-8")
    config = _config(database_path, keys_path)
    config = Config(**{**config.__dict__, "http_host": "0.0.0.0"})
    with pytest.raises(ConfigurationError, match="loopback"):
        validate_config(config, "http")
