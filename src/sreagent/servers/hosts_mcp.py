"""Hosts MCP server: inventory + read-only command execution.

Every command is gated by CommandPolicy (default-deny read-only allowlist)
before it reaches the backend. Demo backend is simulated; swap in
SSHBackend for real hosts.
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from sreagent.hosts import HostInventory

mcp = FastMCP("hosts")
_inventory = HostInventory.demo()


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


@mcp.tool()
def list_hosts(service: str | None = None, role: str | None = None) -> str:
    """List hosts, optionally filtered by service or role."""
    hosts = _inventory.list(service=service, role=role)
    return _ok({"count": len(hosts), "hosts": [h.to_dict() for h in hosts]})


@mcp.tool()
def describe_host(host: str) -> str:
    """Show inventory details for one host."""
    try:
        h = _inventory.hosts[host]
        return _ok({"host": h.to_dict()})
    except KeyError:
        return _err(f"Unknown host: {host}")


@mcp.tool()
def run_command(host: str, command: str) -> str:
    """Run a READ-ONLY diagnostic command on a host.

    The read-only policy is enforced first: anything not on the allowlist
    (writes, restarts, kills, package installs, shell tricks) is blocked
    with exit code 126 and never reaches the host.
    """
    try:
        result = _inventory.run_command(host, command)
        return _ok({"result": result.to_dict()})
    except Exception as e:
        return _err(str(e))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    sys.exit(main())
