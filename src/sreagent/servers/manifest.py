"""manifest.json support for MCP servers.

Every sreagent MCP server can emit a machine-readable tool manifest::

    python -m sreagent.servers.logs_mcp --manifest

The manifest is the contract the MCP client uses for tool calling: it
drives argument validation and the agent's tool catalogue without a live
server round-trip.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

MANIFEST_VERSION = 1
SERVER_VERSION = "0.1.0"


def export_manifest(mcp: Any, server_name: str) -> dict[str, Any]:
    """Build ``{"server", "tools": [{name, description, input_schema}]}``."""
    tools = _collect_tools(mcp)
    return {
        "manifest_version": MANIFEST_VERSION,
        "server": server_name,
        "version": SERVER_VERSION,
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ],
    }


def _collect_tools(mcp: Any) -> list[dict[str, Any]]:
    try:
        listed = asyncio.run(mcp.list_tools())
        out = []
        for t in listed:
            out.append(
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {},
                }
            )
        return out
    except RuntimeError:
        pass  # already inside a loop: fall back to the tool manager
    manager = getattr(mcp, "_tool_manager", None)
    raw = getattr(manager, "_tools", {}) or {}
    out = []
    for tool in raw.values():
        params = getattr(tool, "parameters", None) or {}
        out.append(
            {
                "name": getattr(tool, "name", "?"),
                "description": getattr(tool, "description", "") or "",
                "input_schema": params,
            }
        )
    return out


def validate_args(manifest: dict[str, Any], tool: str, arguments: dict[str, Any]) -> None:
    """Fail fast when arguments don't match the manifest's input schema.

    Raises ``ValueError`` naming what's missing or mistyped; unknown tools
    and manifests without schemas are left alone (graceful, not strict).
    """
    specs = {t["name"]: t for t in manifest.get("tools", [])}
    spec = specs.get(tool)
    if spec is None:
        return
    schema = spec.get("input_schema") or {}
    if not isinstance(arguments, dict):
        raise ValueError(f"tool {tool!r}: arguments must be an object")
    required = schema.get("required", [])
    missing = [p for p in required if p not in arguments]
    if missing:
        raise ValueError(
            f"tool {tool!r}: missing required arguments: {', '.join(missing)} "
            f"(schema: {list(schema.get('properties', {}))})"
        )
    props = schema.get("properties", {})
    for key, value in arguments.items():
        expected = (props.get(key) or {}).get("type")
        if expected and not _type_ok(value, expected):
            raise ValueError(
                f"tool {tool!r}: argument {key!r} should be {expected}, "
                f"got {type(value).__name__}"
            )


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True  # unknown / union types: don't block


def run_main(mcp: Any, server_name: str) -> None:
    """``main()`` replacement: ``--manifest`` prints manifest.json,
    otherwise the MCP server runs as before."""
    if "--manifest" in sys.argv:
        print(json.dumps(export_manifest(mcp, server_name), indent=2))
        return
    mcp.run()