"""Ticket store: in-memory with optional JSON-file persistence.

Persistence matters because each MCP server process gets its own store
instance — without a shared file, ticket updates would vanish between
sessions. Path comes from ``SREAGENT_TICKET_DB`` (default
``~/.sreagent/tickets.json``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Severity, Ticket, TicketStatus

DEFAULT_DB = Path(os.environ.get("SREAGENT_TICKET_DB",
                                 Path.home() / ".sreagent" / "tickets.json"))


class TicketStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB
        self.tickets: dict[str, Ticket] = {}

    def add(self, ticket: Ticket) -> Ticket:
        self.tickets[ticket.ticket_id] = ticket
        return ticket

    def get(self, ticket_id: str) -> Ticket:
        try:
            return self.tickets[ticket_id]
        except KeyError:
            raise KeyError(f"Unknown ticket: {ticket_id}") from None

    def list(self, severity: Severity | str | None = None,
             status: TicketStatus | str | None = None) -> list[Ticket]:
        tickets = list(self.tickets.values())
        if severity is not None:
            severity = Severity(severity.upper()) if isinstance(severity, str) else severity
            tickets = [t for t in tickets if t.severity == severity]
        if status is not None:
            status = TicketStatus(status.upper()) if isinstance(status, str) else status
            tickets = [t for t in tickets if t.status == status]
        return sorted(tickets, key=lambda t: t.created_at)

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([t.to_dict() for t in self.tickets.values()],
                                  indent=1))
        tmp.replace(self.path)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TicketStore":
        store = cls(path)
        if store.path.exists():
            data = json.loads(store.path.read_text())
            for d in data:
                store.add(Ticket.from_dict(d))
        return store

    @classmethod
    def seed_demo(cls, path: str | Path | None = None,
                  reset: bool = False) -> "TicketStore":
        """Seeded store; persists to ``path`` unless it already exists."""
        store = cls(path)
        if store.path.exists() and not reset:
            return cls.load(store.path)
        t1 = Ticket(
            ticket_id="SEV2-1042", severity=Severity.SEV2,
            title="Elevated 5xx error rate on checkout-api",
            service="checkout-api",
            affected_hosts=["checkout-api-01", "checkout-api-02", "checkout-api-03"],
            symptoms=("5xx rate spiked from 0.02% to 4.7% at 21:02 UTC; p99 latency "
                      "up 8x on /checkout. Alerts: CheckoutAPIHigh5xx, CheckoutAPILatencyP99."),
            repo="examples/sample-service",
            repro_cmd="python3 repro_leak.py",
        )
        t1.add_entry("alertmanager", "Firing: CheckoutAPIHigh5xx (5xx > 1% for 5m).")
        t1.add_entry("alertmanager", "Firing: CheckoutAPILatencyP99 (p99 > 2s for 10m).")
        store.add(t1)

        t2 = Ticket(
            ticket_id="SEV2-1038", severity=Severity.SEV2,
            title="Disk pressure on db-replica-02 (87% full)",
            service="postgres",
            affected_hosts=["db-replica-02"],
            symptoms=("Node disk usage crossed 85% warning at 18:40 UTC, now 87%. "
                      "pg_wal growing faster than archival. Alert: NodeDiskPressure."),
            repo="examples/sample-service",
            repro_cmd=None,
        )
        t2.add_entry("alertmanager", "Firing: NodeDiskPressure on db-replica-02.")
        store.add(t2)
        store.save()
        return store
