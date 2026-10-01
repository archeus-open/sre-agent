"""Tests for manifest.json support: server export, client fetch, and
manifest-driven argument validation for tool calling."""

import sys

import pytest

from sreagent.mcp_client import MCPClientManager
from sreagent.servers import manifest as manifest_mod
from sreagent.servers.manifest import export_manifest, validate_args


def _sample_manifest() -> dict:
    return {
        "manifest_version": 1,
        "server": "logs",
        "version": "0.1.0",
        "tools": [
            {
                "name": "search_logs",
                "description": "search",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["host", "query"],
                },
            }
        ],
    }


def test_export_manifest_shape():
    from sreagent.servers.logs_mcp import mcp

    m = export_manifest(mcp, "logs")
    assert m["manifest_version"] == 1
    assert m["server"] == "logs"
    tool = next(t for t in m["tools"] if t["name"] == "search_logs")
    assert tool["description"]
    assert tool["input_schema"]["type"] == "object"


def test_validate_args_ok_and_missing():
    m = _sample_manifest()
    validate_args(m, "search_logs", {"host": "h", "query": "q", "limit": 5})  # no raise
    with pytest.raises(ValueError, match="missing required.*query"):
        validate_args(m, "search_logs", {"host": "h"})


def test_validate_args_type_mismatch():
    m = _sample_manifest()
    with pytest.raises(ValueError, match="should be integer"):
        validate_args(m, "search_logs", {"host": "h", "query": "q", "limit": "many"})


def test_validate_args_unknown_tool_is_graceful():
    validate_args(_sample_manifest(), "nope", {})  # no raise
    validate_args({}, "search_logs", {})  # empty manifest: no raise


async def _manager():
    config = {"servers": {
        "logs": {"command": sys.executable,
                 "args": ["-m", "sreagent.servers.logs_mcp"]},
    }}
    return MCPClientManager(config)


async def test_client_fetch_manifest_from_server():
    mgr = await _manager()
    m1 = await mgr.get_manifest("logs")
    assert m1["server"] == "logs"
    assert {t["name"] for t in m1["tools"]} == {"search_logs", "tail_logs", "log_error_summary"}
    m2 = await mgr.get_manifest("logs")
    assert m2 is m1  # cached


async def test_client_fetch_manifest_unknown_server_is_empty():
    mgr = await _manager()
    assert await mgr.get_manifest("nope") == {}


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool, arguments):
        self.calls.append((tool, arguments))

        class _Content:
            text = '{"ok": true}'

        class _Result:
            content = [_Content()]

        return _Result()


async def test_call_tool_validates_against_manifest():
    mgr = await _manager()
    mgr._sessions["logs"] = _FakeSession()
    with pytest.raises(ValueError, match="missing required"):
        await mgr.call_tool("logs", "search_logs", {"host": "h"})
    result = await mgr.call_tool("logs", "search_logs", {"host": "h", "query": "q"})
    assert result == {"ok": True}
    assert mgr._sessions["logs"].calls == [("search_logs", {"host": "h", "query": "q"})]


async def test_call_tool_unknown_server():
    mgr = await _manager()
    with pytest.raises(KeyError, match="not connected"):
        await mgr.call_tool("nope", "search_logs", {})
