"""Repository-secret and dependency-lock invariants."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_secret_and_runtime_artifact_paths_are_gitignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore.splitlines()
    assert "keys.json" in ignore.splitlines()
    assert "*.db" in ignore.splitlines()
    assert "audit/" in ignore.splitlines()


def test_mcp_dependency_is_pinned_and_lockfile_exists() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == ["mcp==2.3.0"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    mcp_package = next(
        package for package in lock["package"] if package["name"] == "mcp"
    )
    assert mcp_package["version"] == "2.3.0"
