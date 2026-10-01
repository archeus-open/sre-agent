"""MCP client manager: connect to many MCP servers, discover + call tools.

Config format (see configs/mcp_servers.yaml)::

    servers:
      finance:
        transport: stdio
        command: python
        args: ["-m", "sreagent.servers.finance_mcp"]
        env: {}
      marketdata:
        transport: sse
        url: "http://localhost:9000/sse"

Usage::

    async with MCPClientManager("configs/mcp_servers.yaml") as mgr:
        tools = await mgr.list_all_tools()
        result = await mgr.call_tool("finance", "get_quote", {"ticker": "AAPL"})
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class MCPTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.name}"


class MCPClientManager:
    def __init__(self, config: str | dict[str, Any]) -> None:
        if isinstance(config, str):
            with open(config) as fh:
                config = yaml.safe_load(fh)
        self.server_configs: dict[str, dict[str, Any]] = config.get("servers", {})
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._manifests: dict[str, dict[str, Any]] = {}

    async def __aenter__(self) -> "MCPClientManager":
        try:
            from mcp import ClientSession  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "The 'mcp' package is not installed. Install it with: pip install 'finagent[mcp]'"
            ) from exc
        self._ClientSession = ClientSession
        for name, cfg in self.server_configs.items():
            await self._connect(name, cfg)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()
        self._sessions.clear()

    async def _connect(self, name: str, cfg: dict[str, Any]) -> None:
        transport = cfg.get("transport", "stdio")
        if transport == "stdio":
            from mcp import StdioServerParameters  # type: ignore
            from mcp.client.stdio import stdio_client  # type: ignore

            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env={**os.environ, **cfg.get("env", {})},
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif transport in ("sse", "http"):
            url = cfg["url"]
            if transport == "sse":
                from mcp.client.sse import sse_client  # type: ignore

                read, write = await self._stack.enter_async_context(sse_client(url))
            else:
                from mcp.client.streamablehttp import streamablehttp_client  # type: ignore

                read, write, _ = await self._stack.enter_async_context(streamablehttp_client(url))
        else:
            raise ValueError(f"unknown MCP transport '{transport}' for server '{name}'")
        session = await self._stack.enter_async_context(self._ClientSession(read, write))
        await session.initialize()
        self._sessions[name] = session

    @property
    def servers(self) -> list[str]:
        return list(self._sessions)

    async def list_all_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []
        for server, session in self._sessions.items():
            result = await session.list_tools()
            for t in result.tools:
                schema = (
                    getattr(t, "input_schema", None)
                    or getattr(t, "inputSchema", None)  # mcp 1.x
                    or {}
                )
                tools.append(
                    MCPTool(
                        server=server,
                        name=t.name,
                        description=t.description or "",
                        input_schema=schema,
                    )
                )
        return tools

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        """Call a tool; returns the decoded payload (JSON if possible).

        When the server's manifest.json is available, arguments are
        validated against the tool's input schema first — bad calls fail
        fast locally instead of round-tripping to the server.
        """
        session = self._sessions.get(server)
        if session is None:
            raise KeyError(f"MCP server '{server}' is not connected")
        manifest = await self.get_manifest(server)
        if manifest.get("tools"):
            from sreagent.servers.manifest import validate_args

            validate_args(manifest, tool, arguments)
        async with self._lock:
            result = await session.call_tool(tool, arguments)
        return _decode_result(result)

    async def get_manifest(self, server: str) -> dict[str, Any]:
        """Fetch (and cache) the server's manifest.json.

        Prefers the static ``manifest.json`` bundled with each server
        (``sreagent/servers/manifests/<server>.json``) — no subprocess, no
        generation at runtime. Falls back to running the server's own
        command with ``--manifest`` for third-party servers, then ``{}``.
        """
        if server in self._manifests:
            return self._manifests[server]
        from sreagent.servers.manifest import load_manifest

        manifest = load_manifest(server)
        if not manifest.get("tools"):
            manifest = await self._manifest_via_subprocess(server)
        self._manifests[server] = manifest
        return manifest

    async def _manifest_via_subprocess(self, server: str) -> dict[str, Any]:
        """Legacy path: ask the server process itself for its manifest."""
        manifest: dict[str, Any] = {}
        cfg = self.server_configs.get(server, {})
        try:
            if cfg.get("transport", "stdio") == "stdio" and cfg.get("command"):
                cmd = [cfg["command"], *(cfg.get("args", []))]
                # resolve bare "python" the same way the manager would
                if cmd[0] == "python":
                    cmd[0] = sys.executable
                env = {**os.environ, **cfg.get("env", {})}
                proc = await asyncio.create_subprocess_exec(
                    *cmd, "--manifest",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0 and out:
                    manifest = json.loads(out.decode())
        except Exception:  # noqa: BLE001
            manifest = {}
        return manifest

    async def tools_prompt_block(self) -> str:
        """Render tool catalogue for the agent's system prompt."""
        lines = ["Available MCP tools (call via the ACT action):"]
        for tool in await self.list_all_tools():
            lines.append(f"- {tool.qualified_name}: {tool.description}")
        return "\n".join(lines)


def _decode_result(result: Any) -> Any:
    import json

    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    payload = "\n".join(texts)
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001
        return payload
