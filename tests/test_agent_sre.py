"""End-to-end staged incident response over real MCP servers.

Inference is monkeypatched to a canned report so the test is deterministic;
everything else (tickets/hosts/repo MCP, runbook matching, sandbox repro)
runs for real. The ticket DB lives in a tmp file shared by all server
processes in the test.
"""

import json
import sys

import pytest

from sreagent.agent import SREAgent
from sreagent.context import ContextManager
from sreagent.inference import InferenceClient
from sreagent.mcp_client import MCPClientManager
from sreagent.runbooks import RunbookLoader


@pytest.fixture()
def mcp_config(tmp_path):
    db = str(tmp_path / "tickets.json")
    exe = sys.executable
    return {"servers": {
        "tickets": {"command": exe,
                    "args": ["-m", "sreagent.servers.tickets_mcp"],
                    "env": {"SREAGENT_TICKET_DB": db}},
        "hosts": {"command": exe, "args": ["-m", "sreagent.servers.hosts_mcp"]},
        "repo": {"command": exe, "args": ["-m", "sreagent.servers.repo_mcp"]},
    }}


async def test_staged_incident_response_updates_ticket(monkeypatch, mcp_config):
    inference = InferenceClient(base_url="http://127.0.0.1:1")  # never called
    monkeypatch.setattr(
        InferenceClient, "chat",
        lambda self, messages, **kw: "# Incident report\n\nSimulated host evidence "
                                    "points to an OOM-killed worker; sandbox repro "
                                    "confirms the unbounded ORDER_CACHE leak.",
    )

    async with MCPClientManager(mcp_config) as mcp:
        assert set(mcp.servers) == {"tickets", "hosts", "repo"}
        agent = SREAgent(
            inference=inference,
            context=ContextManager(max_tokens=12000),
            rag=None,
            mcp=mcp,
            runbooks=RunbookLoader("examples/runbooks"),
        )
        report = await agent.run_incident_response("SEV2-1042")

        # verify ticket state in a FRESH session (persistence across processes)
        async with MCPClientManager(mcp_config) as mcp2:
            data = await mcp2.call_tool("tickets", "get_ticket",
                                        {"ticket_id": "SEV2-1042"})
        ticket = json.loads(data)["ticket"] if isinstance(data, str) else data["ticket"]

    assert report.ticket_id == "SEV2-1042"
    assert "ORDER_CACHE" in report.report
    assert report.timeline_entries >= 3
    assert report.sources  # ticket, runbook, hosts, repo, ticket-update

    assert ticket["status"] == "INVESTIGATING"
    assert ticket["assignee"] == "sre-agent"
    assert "ORDER_CACHE" in (ticket["root_cause"] or "")
    assert ticket["remediation"]
    assert len(ticket["timeline"]) >= 5  # 2 seeded + evidence + code + repro + assessment


async def test_staged_incident_response_unknown_ticket(mcp_config):
    inference = InferenceClient(base_url="http://127.0.0.1:1")
    async with MCPClientManager(mcp_config) as mcp:
        agent = SREAgent(
            inference=inference,
            context=ContextManager(max_tokens=8000),
            rag=None,
            mcp=mcp,
            runbooks=RunbookLoader("examples/runbooks"),
        )
        try:
            await agent.run_incident_response("SEV2-9999")
        except ValueError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("expected ValueError")
