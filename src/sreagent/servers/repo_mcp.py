"""Repo MCP server: clone code into a local sandbox, inspect, and run it.

Backs the "access the code repository, clone and run local sandbox" step of
incident response: the agent can clone the implicated service, search the
code for suspects, read files, and run repro/diagnostic commands.
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from sreagent.servers.manifest import run_main

from sreagent.sandbox import SandboxManager

mcp = FastMCP("repo")
_manager = SandboxManager()


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


@mcp.tool()
def clone_repo(source: str, name: str = "sandbox-1") -> str:
    """Clone a git URL (or copy a local directory) into a sandbox."""
    try:
        sb = _manager.clone(source, name)
        return _ok({"sandbox": name, "path": str(sb.path),
                    "files": len(sb.list_files())})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def list_files(sandbox: str = "sandbox-1", subdir: str = "") -> str:
    """List files in the sandbox."""
    try:
        return _ok({"files": _manager.get(sandbox).list_files(subdir)})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def read_file(sandbox: str, path: str) -> str:
    """Read a file from the sandbox (capped at 20k chars)."""
    try:
        return _ok({"path": path, "content": _manager.get(sandbox).read_file(path)})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def search_code(sandbox: str, pattern: str, glob: str = "*.py") -> str:
    """Regex-search the sandbox code; returns file/line/match hits."""
    try:
        hits = _manager.get(sandbox).search(pattern, glob)
        return _ok({"count": len(hits), "hits": hits})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def run_in_sandbox(sandbox: str, command: str, timeout: int = 120) -> str:
    """Run a command inside the sandbox (no shell; destructive tokens blocked).

    Use for repro scripts and diagnostics, e.g. "python3 repro_leak.py".
    """
    try:
        result = _manager.get(sandbox).run(command, timeout=timeout)
        return _ok({"result": result.to_dict()})
    except Exception as e:
        return _err(str(e))


def main() -> None:
    run_main(mcp, "repo")


if __name__ == "__main__":
    sys.exit(main())
