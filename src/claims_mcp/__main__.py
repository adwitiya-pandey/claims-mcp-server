"""Command-line entry point for the MCP server."""

from __future__ import annotations

import argparse

import uvicorn

from claims_mcp.config import ConfigurationError, load_config, validate_config
from claims_mcp.http_auth import BearerKeyMiddleware
from claims_mcp.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only claims MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    args = parser.parse_args()
    config = load_config()
    try:
        validate_config(config, args.transport)
    except ConfigurationError as error:
        parser.error(str(error))

    server = create_server(config, transport=args.transport)
    if args.transport == "stdio":
        server.run(transport="stdio")
        return

    app = BearerKeyMiddleware(
        server.streamable_http_app(host=config.http_host),
        str(config.keys_path),
    )
    uvicorn.run(
        app,
        host=config.http_host,
        port=config.http_port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
