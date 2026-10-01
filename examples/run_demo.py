"""Full staged incident-response demo through the inference server + MCP.

1. Starts from this repo root (needs the venv + installed sreagent).
2. Ingests runbooks into RAG, connects the 4 MCP servers over stdio
   (tickets, hosts, logs, repo).
3. Runs SREAgent.run_incident_response("SEV2-1042") and prints the
   investigation steps, the report, and the updated ticket state.

The inference server runs in echo mode by default (no model needed);
set SREAGENT_BACKEND=openai_compatible + SREAGENT_MODEL for a real model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from sreagent.agent import SREAgent
from sreagent.context import ContextManager
from sreagent.inference import InferenceClient
from sreagent.mcp_client import MCPClientManager
from sreagent.rag import RAGPipeline
from sreagent.runbooks import RunbookLoader


def mcp_config() -> dict:
    exe = sys.executable
    db = os.environ.get("SREAGENT_TICKET_DB", "/tmp/sreagent-demo-tickets.json")
    # fresh DB for the demo so ticket updates are visible end to end
    if os.path.exists(db):
        os.remove(db)
    return {"servers": {
        "tickets": {"command": exe, "args": ["-m", "sreagent.servers.tickets_mcp"],
                    "env": {"SREAGENT_TICKET_DB": db}},
        "hosts": {"command": exe, "args": ["-m", "sreagent.servers.hosts_mcp"]},
        "logs": {"command": exe, "args": ["-m", "sreagent.servers.logs_mcp"]},
        "repo": {"command": exe, "args": ["-m", "sreagent.servers.repo_mcp"]},
    }}


# Human-readable labels for the investigation trace, in pipeline order.
STEP_LABELS = [
    ("[mcp] tickets.get_ticket", "Ticket fetched and claimed"),
    ("[runbook]", "Runbook matched (RAG + keyword)"),
    ("[mcp] logs.search_logs", "Log search on affected hosts — error signatures"),
    ("[mcp] hosts.run_command", "Host lookup — read-only diagnostics"),
    ("[mcp] repo.clone_repo", "Code repository downloaded to sandbox"),
    ("[mcp] repo.search_code", "Suspect code search in sandbox"),
    ("[mcp] repo.run_in_sandbox", "Sandbox repro run"),
    ("[mcp] tickets.add_timeline_entry + set_root_cause", "RCA — ticket updated with root cause"),
]


def print_investigation_steps(sources: list[str]) -> None:
    print("\n================ INVESTIGATION STEPS ================")
    shown = 0
    for marker, label in STEP_LABELS:
        if any(marker in s for s in sources):
            shown += 1
            detail = next((s for s in sources if marker in s), "")
            extra = f"  ({detail})" if detail != marker and "(" not in marker else ""
            print(f"  step {shown}. {label}{extra}")
    if not shown:
        print("  (no steps recorded)")


async def main() -> None:
    base_url = os.environ.get("SREAGENT_URL", "http://127.0.0.1:8080")
    inference = InferenceClient(base_url=base_url)
    print("inference server:", inference.health())

    rag = RAGPipeline()
    n = rag.ingest_dir("examples/runbooks")
    print(f"ingested {n} runbook chunks")

    config = mcp_config()  # built once: second session must NOT reset the DB
    async with MCPClientManager(config) as mcp:
        print("mcp servers:", mcp.servers)
        agent = SREAgent(
            inference=inference,
            context=ContextManager(max_tokens=12000),
            rag=rag,
            mcp=mcp,
            runbooks=RunbookLoader("examples/runbooks"),
        )
        report = await agent.run_incident_response("SEV2-1042")

    print("\n================ INCIDENT REPORT ================\n")
    print(report.report)
    print("\n================ TICKET STATE ================")
    async with MCPClientManager(config) as mcp:
        data = await mcp.call_tool("tickets", "get_ticket", {"ticket_id": "SEV2-1042"})
    ticket = json.loads(data)["ticket"] if isinstance(data, str) else data["ticket"]
    print(f"status: {ticket['status']}  assignee: {ticket['assignee']}")
    print(f"root_cause: {ticket['root_cause']}")
    print(f"remediation: {ticket['remediation']}")
    print(f"timeline entries: {len(ticket['timeline'])}")
    print(f"sources: {report.sources}")
    print(f"context: {report.context_stats}")
    print_investigation_steps(report.sources)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
