# sre-agent — SRE incident-response AI prototype

An agentic AI prototype that handles **SEV2 tickets** end to end:

1. **Reads the SEV2 ticket** (severity filter, full timeline)
2. **Matches a runbook** (RAG over `examples/runbooks` + keyword triggers) and follows its diagnostic steps
3. **Logs in to the affected hosts and runs read-only commands** — gated by a default-deny `CommandPolicy`; writes, restarts, kills, installs and shell tricks are blocked by construction
4. **Accesses the code repository** — clones the implicated service into a **local sandbox**, searches code for suspects, reads files
5. **Runs the repro in the sandbox** to confirm the hypothesis
6. **Updates the SEV2 ticket** — timeline entries with evidence, root cause, remediation, status → `INVESTIGATING`

Everything is simulated locally (ticket store, host fleet, sample service), so the whole loop runs on a laptop. Swap in the real `SSHBackend`, a real ticket API, and a real repo URL when graduating to production.

## Quickstart

```bash
make install        # creates .venv, installs sreagent (+ dev/test)
make test           # pytest
make demo           # direct in-process walkthrough (no server, no MCP)
```

Full agentic demo (inference server + 3 MCP servers + staged pipeline):

```bash
# terminal 1: inference server (echo backend = no model needed)
SREAGENT_BACKEND=echo .venv/bin/python examples/run_server.py

# terminal 2: staged SEV2 incident response over MCP
.venv/bin/python examples/run_demo.py
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
python -m pytest
python examples\triage_direct.py
```

## Architecture

```
tickets ─┐
hosts ───┼─► MCP servers (stdio) ─► MCPClientManager ─┐
repo ────┘                                             │
runbooks/*.md ─► RAGPipeline ─► runbook context ───────┤
                                                      ▼
SREAgent.run_incident_response(ticket_id)   # staged pipeline
SREAgent.run_react(ticket_id)               # ReAct tool loop
   │
   ▼
InferenceClient ─► FastAPI server (echo | openai_compatible)
```

- `src/sreagent/tickets/` — `Ticket` model (severity, status, timeline, root cause), in-memory `TicketStore` seeded with `SEV2-1042` (5xx on checkout-api) and `SEV2-1038` (disk pressure).
- `src/sreagent/runbooks/` — markdown runbooks with `triggers:`, `## Diagnostic commands` (`$ …` lines the agent runs), and `## Suspect code patterns` (regexes the agent searches for in the repo).
- `src/sreagent/hosts/` — `CommandPolicy` (default-deny read-only allowlist), `HostInventory`, `SimulatedBackend` (deterministic scripted fleet) and `SSHBackend` (real SSH via paramiko, `pip install sreagent[ssh]`; still policy-gated).
- `src/sreagent/sandbox/` — `SandboxManager.clone()` copies a local dir or `git clone`s a URL; `Sandbox.search/read_file/run` with `shell=False`, timeouts, and a destructive-token denylist.
- `src/sreagent/servers/` — `tickets_mcp.py`, `hosts_mcp.py`, `repo_mcp.py`.
- `src/sreagent/agent/` — `SREAgent` staged pipeline + ReAct loop, prompts.
- Reused from the finagent/oms-agent lineage: OpenAI-compatible inference server, priority/token-budget context manager, multi-server MCP client, RAG pipeline.

## The demo incident (SEV2-1042)

`checkout-api-02` shows load 14+, a worker at 187% CPU, 7.9/8 GB RAM used, `Out of memory: Killed process` + `MemoryError` in the journal, and HTTP 500 on `/health` — while its peers are healthy. The runbook's suspect patterns lead the agent to `ORDER_CACHE` in `checkout.py:19` (never evicted); `repro_leak.py` in the sandbox confirms unbounded growth. The ticket gets timeline entries, root cause, and remediation.

## Safety notes

- Host access is **read-only by construction**: `CommandPolicy` is default-deny; the MCP tool cannot be talked into running anything else.
- The sandbox runs local commands with `shell=False` and blocks destructive tokens — prototype scope, not a security boundary.
- `SSHBackend` takes credentials from ssh-agent/key files at runtime; never commit credentials.
- Host outputs in this prototype are **simulated** — the agent says so in its report.

## Production roadmap

Real ticket API (PagerDuty/Jira) behind the tickets MCP server · real SSH fleet with per-host credentials from a vault · runbook execution approvals for anything beyond read-only · repo access via GitHub App tokens · sandbox in containers (gVisor/Firecracker) · evals on historical incidents.
