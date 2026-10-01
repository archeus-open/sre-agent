"""Logs MCP server: search / tail / summarize synthetic fleet logs.

Read-only by construction — there is nothing to write to. Backed by
``sreagent.logs.LogStore``; swap in a real log backend (Loki,
Elasticsearch, CloudWatch) behind the same three tools when graduating
to production.
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from sreagent.logs import LogStore
from sreagent.servers.manifest import run_main

mcp = FastMCP("logs")
_store = LogStore()


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


@mcp.tool()
def search_logs(
    host: str,
    query: str,
    level: str | None = None,
    since_minutes: float = 60,
    limit: int = 50,
) -> str:
    """Case-insensitive substring search over one host's logs, newest first.

    Args:
        host: e.g. "checkout-api-02".
        query: substring to match, e.g. "Out of memory" or "500".
        level: minimum level (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        since_minutes: lookback window.
        limit: max lines returned.
    """
    if host not in _store.hosts:
        return _err(f"Unknown host: {host}. Known: {_store.hosts}")
    hits = _store.search(host, query, level=level, since_minutes=since_minutes, limit=limit)
    return _ok({"count": len(hits), "lines": [h.to_dict() for h in hits]})


@mcp.tool()
def tail_logs(host: str, lines: int = 40, level: str | None = None) -> str:
    """Last N log lines for a host, oldest first."""
    if host not in _store.hosts:
        return _err(f"Unknown host: {host}. Known: {_store.hosts}")
    out = _store.tail(host, n=lines, level=level)
    return _ok({"count": len(out), "lines": [h.to_dict() for h in out]})


@mcp.tool()
def log_error_summary(host: str, since_minutes: float = 60) -> str:
    """Error/critical counts by level plus the top repeated message
    signatures for a host — the fastest way to spot what's actually
    failing."""
    if host not in _store.hosts:
        return _err(f"Unknown host: {host}. Known: {_store.hosts}")
    return _ok(_store.error_summary(host, since_minutes=since_minutes))


def main() -> None:
    run_main(mcp, "logs")


if __name__ == "__main__":
    sys.exit(main())
