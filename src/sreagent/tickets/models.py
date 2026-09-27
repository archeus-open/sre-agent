"""Incident ticket models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class TicketStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"


@dataclass
class TimelineEntry:
    timestamp: float
    author: str
    text: str

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "author": self.author, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineEntry":
        return cls(timestamp=d["timestamp"], author=d["author"], text=d["text"])


@dataclass
class Ticket:
    ticket_id: str
    severity: Severity
    title: str
    status: TicketStatus = TicketStatus.OPEN
    service: str = ""
    affected_hosts: list[str] = field(default_factory=list)
    symptoms: str = ""
    repo: str = ""            # local path or git URL of the service repo
    repro_cmd: str | None = None  # command to reproduce in the sandbox, e.g. "python3 repro_leak.py"
    assignee: str | None = None
    root_cause: str | None = None
    remediation: str | None = None
    timeline: list[TimelineEntry] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_entry(self, author: str, text: str) -> TimelineEntry:
        entry = TimelineEntry(timestamp=time.time(), author=author, text=text)
        self.timeline.append(entry)
        self.updated_at = time.time()
        return entry

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id, "severity": self.severity.value,
            "title": self.title, "status": self.status.value,
            "service": self.service, "affected_hosts": self.affected_hosts,
            "symptoms": self.symptoms, "repo": self.repo, "repro_cmd": self.repro_cmd,
            "assignee": self.assignee, "root_cause": self.root_cause,
            "remediation": self.remediation,
            "timeline": [e.to_dict() for e in self.timeline],
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Ticket":
        return cls(
            ticket_id=d["ticket_id"],
            severity=Severity(d["severity"]),
            title=d["title"],
            status=TicketStatus(d.get("status", "OPEN")),
            service=d.get("service", ""),
            affected_hosts=d.get("affected_hosts", []),
            symptoms=d.get("symptoms", ""),
            repo=d.get("repo", ""),
            repro_cmd=d.get("repro_cmd"),
            assignee=d.get("assignee"),
            root_cause=d.get("root_cause"),
            remediation=d.get("remediation"),
            timeline=[TimelineEntry.from_dict(e) for e in d.get("timeline", [])],
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
        )
