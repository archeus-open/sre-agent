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
    from sreagent.servers.logs_mcp import SERVER_VERSION as v
    from sreagent.servers.logs_mcp import mcp

    m = export_manifest(mcp, "logs", v)
    assert m["manifest_version"] == 1
    assert m["server"] == "logs"
    assert m["version"] == v
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


async def test_client_prefers_static_manifest_without_subprocess():
    # bogus command: must still resolve from the static manifest.json
    mgr = MCPClientManager({"servers": {"logs": {"command": "/nonexistent/python",
                                                 "args": ["-m", "nope"]}}})
    m = await mgr.get_manifest("logs")
    assert m["server"] == "logs"
    assert m["version"] == "0.1.0"


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


def test_static_manifests_match_code_and_versions():
    """Each static manifest.json exists, stamps the server's SERVER_VERSION,
    and matches what the code generates."""
    import importlib

    from sreagent.servers.manifest import SERVERS, export_manifest, load_manifest

    assert set(SERVERS) == {"tickets", "hosts", "logs", "repo"}
    for name, module_name in SERVERS.items():
        module = importlib.import_module(module_name)
        static = load_manifest(name)
        assert static, f"missing static manifest for {name}"
        assert static["version"] == module.SERVER_VERSION, f"stale manifest for {name}"
        live = export_manifest(module.mcp, name, module.SERVER_VERSION)
        assert [t["name"] for t in static["tools"]] == [t["name"] for t in live["tools"]]
        for s_tool, l_tool in zip(static["tools"], live["tools"]):
            assert s_tool["input_schema"] == l_tool["input_schema"], name


def test_load_manifest_unknown_server_is_empty():
    from sreagent.servers.manifest import load_manifest

    assert load_manifest("nope") == {}


def test_refresh_rewrites_manifests(tmp_path, monkeypatch):
    import sreagent.servers.manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "MANIFESTS_DIR", tmp_path)
    written = manifest_mod.refresh_all()
    assert len(written) == 4
    assert all(str(tmp_path) in w for w in written)
