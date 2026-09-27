"""Direct (no server, no MCP) incident-response walkthrough.

Shows every stage in-process: ticket -> runbook -> read-only host commands
-> sandbox clone/code search/repro -> findings. Deterministic.
"""

from __future__ import annotations

import asyncio
import sys

from sreagent.hosts import HostInventory
from sreagent.runbooks import RunbookLoader
from sreagent.sandbox import SandboxManager
from sreagent.tickets import TicketStore


async def main() -> None:
    store = TicketStore.seed_demo()
    ticket = store.get("SEV2-1042")
    print(f"== {ticket.ticket_id}: {ticket.title} ==")

    loader = RunbookLoader("examples/runbooks")
    rb = loader.match(f"{ticket.title} {ticket.symptoms}")
    print(f"matched runbook: {rb.name}")

    inv = HostInventory.demo()
    for host in ticket.affected_hosts:
        for cmd in rb.diagnostic_commands()[:4]:
            res = inv.run_command(host, cmd)
            out = (res.stdout or res.stderr).splitlines()
            print(f"\n$ [{host}] {cmd}\n  " + "\n  ".join(out[:6]))

    # policy demo: a destructive command must be blocked
    blocked = inv.run_command("checkout-api-02", "systemctl restart checkout-api")
    print(f"\n$ [checkout-api-02] systemctl restart checkout-api\n  -> exit={blocked.exit_code} {blocked.stderr[:80]}")

    sbm = SandboxManager()
    sb = sbm.clone(ticket.repo)
    print(f"\ncloned {ticket.repo} -> {len(sb.list_files())} files")
    for pattern in rb.code_patterns():
        for hit in sb.search(pattern)[:3]:
            print(f"  suspect {hit['file']}:{hit['line']}: {hit['text']}")
    if ticket.repro_cmd:
        rr = sb.run(ticket.repro_cmd, timeout=120)
        print(f"\n$ {ticket.repro_cmd}\n{rr.stdout.strip()}\n(exit={rr.returncode})")

    # ticket update with findings
    ticket.status = ticket.status.__class__.INVESTIGATING
    ticket.add_entry("sre-agent", "Evidence: checkout-api-02 OOM-killed (MemoryError in checkout.py:41); "
                                  "peers healthy. Sandbox repro confirms unbounded ORDER_CACHE growth.")
    ticket.root_cause = "Unbounded ORDER_CACHE in checkout.py -> worker OOM -> 5xx during restart cycle"
    ticket.remediation = "Bound the cache (LRU/TTL eviction) and add memory alerting."
    print(f"\nticket updated: status={ticket.status.value}, root_cause set, "
          f"timeline entries={len(ticket.timeline)}")

    # optional: run the full MCP-backed staged pipeline too
    print("\n(direct walkthrough complete)")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
