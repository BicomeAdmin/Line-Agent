"""Project Echo MCP server entry point (stdio transport).

OpenClaw / Claude Desktop / any MCP-aware client launches this process and
talks to it over stdin/stdout. Tools defined in app/mcp/project_echo_server.py.
"""

from __future__ import annotations

import asyncio
import sys

import _bootstrap  # noqa: F401

from app.mcp.project_echo_server import serve_stdio


def main() -> int:
    try:
        asyncio.run(serve_stdio())
    except KeyboardInterrupt:
        print("[mcp] shutting down", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
