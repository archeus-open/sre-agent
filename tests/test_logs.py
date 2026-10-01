"""Tests for the logs layer: LogStore queries and the logs MCP server."""

import json

from sreagent.logs import LogStore
from sreagent.servers import manifest as manifest_mod
from sreagent.servers.logs_mcp import log_error_summary, search_logs, tail_logs


def _payload(raw: str) -> dict:
    return json.loads(raw)


def test_search_finds_oom_on_sick_host():
    store = LogStore()
    hits = store.search("checkout-api-02", "Out of memory", since_minutes=120)
    assert len(hits) == 2
    assert all("Out of memory" in h.msg for h in hits)
    # newest first
    assert hits[0].ts >= hits[1].ts


def test_search_is_case_insensitive_and_host_scoped():
    store = LogStore()
    assert len(store.search("checkout-api-02", "out of memory", since_minutes=120)) == 2
    assert store.search("checkout-api-01", "Out of memory", since_minutes=120) == []
    assert store.search("checkout-api-02", "no-such-string-xyz") == []


def test_search_level_filter_and_limit():
    store = LogStore()
    errors = store.search("checkout-api-02", "Traceback", level="ERROR", since_minutes=120)
    assert len(errors) == 2 and all(h.level == "ERROR" for h in errors)
    warns = store.search("checkout-api-02", "worker", level="WARNING", since_minutes=120)
    assert warns and all(h.level in ("WARNING", "ERROR", "CRITICAL") for h in warns)
    assert len(store.search("checkout-api-02", "GET", limit=3)) == 3


def test_tail_ordering_and_unknown_host():
    store = LogStore()
    lines = store.tail("db-replica-01", n=5)
    assert len(lines) == 5
    assert [l.ts for l in lines] == sorted(l.ts for l in lines)  # oldest first
    assert store.tail("nope", n=5) == []
    assert store.search("nope", "x") == []


def test_error_summary_counts():
    store = LogStore()
    summary = store.error_summary("checkout-api-02", since_minutes=120)
    assert summary["error_lines"] == 6  # 4 ERROR + 2 CRITICAL in window
    assert summary["by_level"] == {"ERROR": 4, "CRITICAL": 2}
    assert summary["top_signatures"]
    healthy = store.error_summary("checkout-api-01", since_minutes=120)
    assert healthy["error_lines"] == 0


def test_mcp_search_logs_tool():
    data = _payload(search_logs(host="checkout-api-02", query="MemoryError",
                                since_minutes=120, limit=10))
    assert data["ok"] is True
    assert data["count"] == 2
    assert "MemoryError" in data["lines"][0]["msg"]


def test_mcp_tools_reject_unknown_host():
    assert _payload(search_logs(host="nope", query="x"))["ok"] is False
    assert _payload(tail_logs(host="nope"))["ok"] is False
    assert _payload(log_error_summary(host="nope"))["ok"] is False


def test_mcp_tail_logs_tool():
    data = _payload(tail_logs(host="checkout-api-02", lines=4))
    assert data["ok"] is True and data["count"] == 4


def test_logs_manifest_lists_three_tools():
    from sreagent.servers.logs_mcp import mcp

    manifest = manifest_mod.export_manifest(mcp, "logs", "0.1.0")
    assert manifest["server"] == "logs"
    assert manifest["version"] == "0.1.0"
    names = [t["name"] for t in manifest["tools"]]
    assert names == ["search_logs", "tail_logs", "log_error_summary"]
    schema = next(t["input_schema"] for t in manifest["tools"] if t["name"] == "search_logs")
    assert set(schema["required"]) == {"host", "query"}
