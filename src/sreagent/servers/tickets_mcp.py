"""Tickets MCP server: list / get / update SEV2 incident tickets.

State is a JSON file (``SREAGENT_TICKET_DB``, default ``~/.sreagent/tickets.json``)
seeded with demo incidents, so updates survive across server processes.
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from sreagent.servers.manifest import run_main

from sreagent.tickets import TicketStatus, TicketStore

mcp = FastMCP("tickets")

SERVER_VERSION = "0.2.0"
_store = TicketStore.seed_demo()


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


@mcp.tool()
def list_tickets(severity: str | None = None, status: str | None = None) -> str:
    """List incident tickets, optionally filtered by severity (e.g. SEV2) and status."""
    try:
        tickets = _store.list(severity=severity, status=status)
        return _ok({"count": len(tickets), "tickets": [t.to_dict() for t in tickets]})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def get_ticket(ticket_id: str) -> str:
    """Fetch one ticket with its full timeline."""
    try:
        return _ok({"ticket": _store.get(ticket_id).to_dict()})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def find_similar_incidents(ticket_id: str, limit: int = 3) -> str:
    """Find previous incidents on the same host(s) with the same failure
    signature (e.g. OOM, http-5xx, disk-pressure). Resolved incidents sort
    first — their recorded root cause / remediation is what the responder
    should learn from."""
    try:
        return _ok({"similar": _store.find_similar(ticket_id, limit=limit)})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def add_timeline_entry(ticket_id: str, author: str, text: str) -> str:
    """Append a timestamped entry to the ticket timeline (findings, evidence)."""
    try:
        entry = _store.get(ticket_id).add_entry(author, text)
        _store.save()
        return _ok({"entry": entry.to_dict()})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def update_status(ticket_id: str, status: str) -> str:
    """Move a ticket: OPEN -> INVESTIGATING -> MITIGATED -> RESOLVED."""
    try:
        ticket = _store.get(ticket_id)
        ticket.status = TicketStatus(status.upper())
        _store.save()
        return _ok({"ticket_id": ticket_id, "status": ticket.status.value})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def assign(ticket_id: str, assignee: str) -> str:
    """Assign the ticket to someone (e.g. 'sre-agent')."""
    try:
        _store.get(ticket_id).assignee = assignee
        _store.save()
        return _ok({"ticket_id": ticket_id, "assignee": assignee})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def set_root_cause(ticket_id: str, root_cause: str, remediation: str = "") -> str:
    """Record the confirmed root cause and recommended remediation."""
    try:
        ticket = _store.get(ticket_id)
        ticket.root_cause = root_cause
        ticket.remediation = remediation
        _store.save()
        return _ok({"ticket_id": ticket_id, "root_cause": root_cause})
    except Exception as e:
        return _err(str(e))


def main() -> None:
    run_main(mcp, "tickets", SERVER_VERSION)


if __name__ == "__main__":
    sys.exit(main())
