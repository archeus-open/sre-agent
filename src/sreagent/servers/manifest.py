"""manifest.json support for MCP servers.

Each server ships a **static** ``manifest.json``
(``src/sreagent/servers/manifests/<server>.json``) describing its tools —
the contract the MCP client uses for tool calling (argument validation,
catalog rendering) without a live server round-trip.

Versioning workflow: each server module carries ``SERVER_VERSION``. When a
server's tools change, bump its ``SERVER_VERSION`` and regenerate::

    python -m sreagent.servers.manifest refresh

``refresh`` rewrites every static manifest from the code and stamps the
current server version, so the file and the code can never silently drift.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.resources
import json
import sys
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1

#: server name -> module holding ``mcp`` and ``SERVER_VERSION``
SERVERS: dict[str, str] = {
    "tickets": "sreagent.servers.tickets_mcp",
    "hosts": "sreagent.servers.hosts_mcp",
    "logs": "sreagent.servers.logs_mcp",
    "repo": "sreagent.servers.repo_mcp",
}

MANIFESTS_DIR = Path(__file__).resolve().parent / "manifests"


def export_manifest(mcp: Any, server_name: str, version: str = "0.1.0") -> dict[str, Any]:
    """Build ``{"server", "version", "tools": [{name, description, input_schema}]}``."""
    tools = _collect_tools(mcp)
    return {
        "manifest_version": MANIFEST_VERSION,
        "server": server_name,
        "version": version,
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


def run_main(mcp: Any, server_name: str, version: str = "0.1.0") -> None:
    """``main()`` replacement: ``--manifest`` prints manifest.json,
    otherwise the MCP server runs as before."""
    if "--manifest" in sys.argv:
        print(json.dumps(export_manifest(mcp, server_name, version), indent=2))
        return
    mcp.run()


def manifest_path(server_name: str) -> Path:
    return MANIFESTS_DIR / f"{server_name}.json"


def load_manifest(server_name: str) -> dict[str, Any]:
    """Read the static ``manifest.json`` for a server.

    Prefers the installed package resources; falls back to the source
    tree so it also works from a plain checkout.
    """
    resource = importlib.resources.files("sreagent.servers.manifests").joinpath(
        f"{server_name}.json"
    )
    try:
        return json.loads(resource.read_text())
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError):
        path = manifest_path(server_name)
        if path.exists():
            return json.loads(path.read_text())
    return {}


def refresh_all() -> list[str]:
    """Regenerate every static manifest from the server code, stamping each
    server's current ``SERVER_VERSION``. Run after changing any server's
    tools (and bumping its version). Returns the files written."""
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, module_name in SERVERS.items():
        module = importlib.import_module(module_name)
        version = getattr(module, "SERVER_VERSION", "0.1.0")
        manifest = export_manifest(module.mcp, name, version)
        path = manifest_path(name)
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        written.append(str(path))
    return written


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if argv == ["refresh"]:
        for path in refresh_all():
            print(f"wrote {path}")
    else:
        print("usage: python -m sreagent.servers.manifest refresh", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()