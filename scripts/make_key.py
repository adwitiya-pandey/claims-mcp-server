"""Create a hashed API key and print its plaintext exactly once."""

from __future__ import annotations

import argparse
from pathlib import Path

from claims_mcp.security import TOOL_NAMES, append_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True, dest="key_id")
    parser.add_argument(
        "--tools", required=True, help="all or a comma-separated tool list"
    )
    parser.add_argument("--out", type=Path, default=Path("keys.json"))
    args = parser.parse_args()
    tools = sorted(TOOL_NAMES) if args.tools == "all" else args.tools.split(",")
    key = append_key(args.out, args.key_id, tools)
    print(key)


if __name__ == "__main__":
    main()
