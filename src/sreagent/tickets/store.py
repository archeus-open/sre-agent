"""Ticket store: in-memory with optional JSON-file persistence.

Persistence matters because each MCP server process gets its own store
instance — without a shared file, ticket updates would vanish between
sessions. Path comes from ``SREAGENT_TICKET_DB`` (default
``~/.sreagent/tickets.json``).
"""

from __future__ import annotations

import json
import os
import re
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

    # -- similarity -----------------------------------------------------
    #: regex -> failure-signature label, matched against title + symptoms.
    _FAILURE_SIGNATURES = (
        (r"oom|out of memory|memoryerror|killed process", "memory-pressure/oom"),
        (r"\b5xx\b|\b500\b|internal server error", "http-5xx"),
        (r"disk|/var.*full|no space|inode", "disk-pressure"),
        (r"p99|latency|slow|timeout|timed out", "latency/timeout"),
        (r"cpu|load average", "cpu-pressure"),
        (r"connection refused|conn.*reset|econn", "connection-failure"),
        (r"replica lag|replication", "replication-lag"),
        (r"deploy|rollout|release", "deploy-related"),
    )

    @classmethod
    def failure_signature(cls, ticket: Ticket) -> set[str]:
        """Coarse failure labels for a ticket, from title + symptoms."""
        text = f"{ticket.title} {ticket.symptoms}".lower()
        return {label for rx, label in cls._FAILURE_SIGNATURES
                if re.search(rx, text)}

    def find_similar(self, ticket_id: str, limit: int = 3) -> list[dict]:
        """Previous incidents on the same host(s) with the same failure.

        A ticket counts as similar when it shares at least one affected
        host AND at least one failure signature with the reference ticket.
        Ranked by host overlap, signature overlap, then recency; resolved
        tickets (which carry a recorded root cause) sort first on ties so
        their RCA is what the agent learns from.
        """
        ref = self.get(ticket_id)
        ref_hosts = set(ref.affected_hosts)
        ref_sigs = self.failure_signature(ref)
        if not ref_hosts or not ref_sigs:
            return []

        scored = []
        for t in self.tickets.values():
            if t.ticket_id == ticket_id:
                continue
            hosts = ref_hosts & set(t.affected_hosts)
            sigs = ref_sigs & self.failure_signature(t)
            if not hosts or not sigs:
                continue
            score = (2 * len(hosts) + 3 * len(sigs)
                     + (1 if t.severity == ref.severity else 0))
            scored.append((score, t.status == TicketStatus.RESOLVED,
                           t.created_at, t, sorted(hosts), sorted(sigs)))
        scored.sort(key=lambda s: (-s[0], -s[1], -s[2]))
        return [{
            "ticket_id": t.ticket_id,
            "title": t.title,
            "severity": t.severity.value,
            "status": t.status.value,
            "service": t.service,
            "matched_hosts": hosts,
            "matched_signatures": sigs,
            "root_cause": t.root_cause,
            "remediation": t.remediation,
            "created_at": t.created_at,
        } for _, _, _, t, hosts, sigs in scored[:limit]]

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

        # Previous *resolved* incident on the same host with the same
        # failure mode — gives find_similar() something to learn from.
        t3 = Ticket(
            ticket_id="SEV2-1019", severity=Severity.SEV2,
            status=TicketStatus.RESOLVED,
            title="Worker OOM-killed on checkout-api-01, 5xx spike",
            service="checkout-api",
            affected_hosts=["checkout-api-01"],
            symptoms=("Worker process OOM-killed at 03:12 UTC; 5xx rate spiked "
                      "to 3.1% on /checkout until the pod restarted. "
                      "Alert: CheckoutAPIHigh5xx."),
            repo="examples/sample-service",
            repro_cmd="python3 repro_leak.py",
            assignee="sre-agent",
            root_cause=("Unbounded ORDER_CACHE growth in the checkout worker; "
                        "RSS grew until the OOM-killer fired, and restarts "
                        "surfaced as 5xx on /checkout."),
            remediation=("Bounded ORDER_CACHE with LRU eviction (max 10k entries), "
                         "deployed, and added a worker-RSS alert below the OOM threshold."),
        )
        t3.add_entry("alertmanager", "Firing: CheckoutAPIHigh5xx (5xx > 1% for 5m).")
        t3.add_entry("sre-agent", "Assessment complete. Root cause: unbounded ORDER_CACHE growth.")
        store.add(t3)
        store.save()
        return store
