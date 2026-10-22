"""Package import smoke test."""

from claims_mcp import __doc__


def test_package_imports() -> None:
    assert __doc__
