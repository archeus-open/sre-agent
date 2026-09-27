"""Host inventory + policy-gated command execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from .backends import CommandResult, SimulatedBackend
from .policy import CommandPolicy


@dataclass
class Host:
    name: str
    role: str = ""
    service: str = ""
    address: str = ""
    labels: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "role": self.role, "service": self.service,
                "address": self.address or self.name, "labels": self.labels}


class HostInventory:
    def __init__(self, backend=None, policy: CommandPolicy | None = None) -> None:
        self.backend = backend or SimulatedBackend()
        self.policy = policy or CommandPolicy()
        self.hosts: dict[str, Host] = {}

    def add(self, host: Host) -> None:
        self.hosts[host.name] = host

    def list(self, service: str | None = None, role: str | None = None) -> list[Host]:
        hosts = list(self.hosts.values())
        if service:
            hosts = [h for h in hosts if h.service == service]
        if role:
            hosts = [h for h in hosts if h.role == role]
        return hosts

    def run_command(self, host: str, command: str) -> CommandResult:
        """Policy-gate, then execute on the backend."""
        if host not in self.hosts:
            raise KeyError(f"Unknown host: {host}")
        allowed, reason = self.policy.check(command)
        if not allowed:
            return CommandResult(host=host, command=command, stdout="",
                                 stderr=f"blocked by read-only policy: {reason}",
                                 exit_code=126)
        return self.backend.run(host, command)

    @classmethod
    def demo(cls, backend=None, policy: CommandPolicy | None = None) -> "HostInventory":
        inv = cls(backend=backend, policy=policy)
        for i in (1, 2, 3):
            inv.add(Host(name=f"checkout-api-0{i}", role="api", service="checkout-api"))
        inv.add(Host(name="db-replica-01", role="db", service="postgres"))
        inv.add(Host(name="db-replica-02", role="db", service="postgres"))
        return inv
