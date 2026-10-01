"""SRE agent orchestrator.

Two modes:
- ``run_incident_response``: deterministic staged pipeline — fetch the SEV2
  ticket -> match runbook (RAG + keyword) -> gather host evidence with
  read-only commands -> clone the service repo into a sandbox, search code,
  run the repro -> synthesize findings -> update the ticket (timeline,
  root cause, status). Works with the echo backend; ideal for demos/tests.
- ``run_react``: agentic ReAct loop. The model drives
  (mcp.tickets.*, mcp.hosts.*, mcp.repo.*, rag_search) until it emits a
  final report. Needs a real model behind the inference server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..context.manager import ContextManager
from ..inference.client import InferenceClient
from ..runbooks import RunbookLoader
from . import prompts

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_EVIDENCE_RES = [
    (re.compile(r"out of memory|killed process|memoryerror", re.I), "memory pressure / OOM"),
    (re.compile(r"\b500\b"), "HTTP 500 responses"),
    (re.compile(r"load average: (\d+\.\d+)", re.I), "high load average"),
    (re.compile(r"(\d+)% /"), "disk usage"),
    (re.compile(r"traceback", re.I), "application traceback"),
]


@dataclass
class IncidentReport:
    ticket_id: str
    report: str
    sources: list[str] = field(default_factory=list)
    rounds: int = 0
    timeline_entries: int = 0
    context_stats: dict = field(default_factory=dict)


class SREAgent:
    def __init__(
        self,
        inference: InferenceClient,
        context: ContextManager,
        rag: Any | None = None,
        mcp: Any | None = None,
        runbooks: RunbookLoader | None = None,
        model: str = "sre-responder-v1",
        temperature: float = 0.2,
        max_tokens: int = 1500,
        rag_top_k: int = 3,
        react_max_rounds: int = 10,
        max_diag_commands: int = 6,
    ) -> None:
        self.inference = inference
        self.context = context
        self.rag = rag
        self.mcp = mcp
        self.runbooks = runbooks
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.rag_top_k = rag_top_k
        self.react_max_rounds = react_max_rounds
        self.max_diag_commands = max_diag_commands

    # -- MCP helper -----------------------------------------------------
    async def _tool(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.mcp is None:
            raise RuntimeError("No MCP client configured.")
        data = await self.mcp.call_tool(server, tool, args)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return {"raw": data}
        return data if isinstance(data, dict) else {"raw": data}

    @staticmethod
    def _log_queries(ticket: dict, runbook) -> list[str]:
        """Error-signature queries for the logs MCP, derived from the
        ticket symptoms and the matched runbook name."""
        text = f"{ticket.get('title', '')} {ticket.get('symptoms', '')} "
        text += runbook.name if runbook else ""
        text = text.lower()
        queries: list[str] = []
        if any(k in text for k in ("oom", "out of memory", "memory")):
            queries.append("Out of memory")
        if "500" in text or "5xx" in text:
            queries.append(" 500 ")
        if any(k in text for k in ("exception", "traceback", "error")):
            queries.append("Traceback")
        queries.append("ERROR")
        # de-dupe, keep order
        return list(dict.fromkeys(queries))

    # -- staged pipeline ------------------------------------------------
    async def run_incident_response(self, ticket_id: str) -> IncidentReport:
        sources: list[str] = []
        self.context.set_system(prompts.SYSTEM_RESPONSE)

        # 1. Fetch + claim the ticket.
        ticket = (await self._tool("tickets", "get_ticket", {"ticket_id": ticket_id})).get("ticket", {})
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found.")
        await self._tool("tickets", "assign", {"ticket_id": ticket_id, "assignee": "sre-agent"})
        await self._tool("tickets", "update_status",
                         {"ticket_id": ticket_id, "status": "INVESTIGATING"})
        self.context.add_section(
            "ticket",
            f"[{ticket.get('severity')}] {ticket.get('ticket_id')}: {ticket.get('title')}\n"
            f"service={ticket.get('service')} hosts={', '.join(ticket.get('affected_hosts', []))}\n"
            f"symptoms: {ticket.get('symptoms')}\n"
            f"existing timeline: {len(ticket.get('timeline', []))} entries",
            priority=100,
        )
        sources.append("[mcp] tickets.get_ticket")

        # 2. Runbook: RAG lookup + keyword match for diagnostic commands.
        query = f"{ticket.get('title')} {ticket.get('symptoms')}"
        runbook = self.runbooks.match(query) if self.runbooks else None
        if self.rag is not None:
            docs_md = self.rag.query_context(f"runbook {query}", top_k=self.rag_top_k)
            if docs_md:
                self.context.add_section("runbook", docs_md, priority=90)
                sources.append("[rag] runbook corpus")
        diag_commands: list[str] = runbook.diagnostic_commands()[:self.max_diag_commands] if runbook else []
        code_patterns: list[str] = runbook.code_patterns() if runbook else []
        if runbook:
            self.context.add_section(
                "runbook-selected",
                f"Matched runbook: {runbook.name}\n"
                f"diagnostic commands: {diag_commands}\n"
                f"code patterns: {code_patterns}",
                priority=92,
            )
            sources.append(f"[runbook] {runbook.name}")

        # 2b. Log search on affected hosts, guided by runbook + symptoms.
        # The logs server is optional: skip gracefully when not connected.
        log_lines: list[str] = []
        anomalies: list[str] = []
        try:
            for host in ticket.get("affected_hosts", []):
                for query in self._log_queries(ticket, runbook)[:3]:
                    hits = (await self._tool("logs", "search_logs", {
                        "host": host, "query": query, "limit": 15,
                    })).get("lines", [])
                    for h in hits[:8]:
                        log_lines.append(f"[{h.get('ts')}] [{host}] {h.get('level')}: {h.get('msg')}")
                        for rx, label in _EVIDENCE_RES:
                            if rx.search(h.get("msg", "")):
                                anomalies.append(f"{host}: {label} (via log search `{query}`)")
                summary = (await self._tool("logs", "log_error_summary",
                                           {"host": host})).get("top_signatures", [])
                for sig in summary[:3]:
                    log_lines.append(f"[{host}] recurring: {sig.get('signature')} x{sig.get('count')}")
            if log_lines:
                self.context.add_section("log evidence", "\n".join(log_lines[:40]), priority=87)
                sources.append("[mcp] logs.search_logs")
        except Exception as exc:  # noqa: BLE001 — logs MCP not connected; host evidence still applies
            log.warning("Skipping log search step: %s", exc)

        # 3. Host evidence (read-only commands on affected hosts).
        evidence: list[str] = []
        for host in ticket.get("affected_hosts", []):
            for cmd in diag_commands:
                res = (await self._tool("hosts", "run_command",
                                        {"host": host, "command": cmd})).get("result", {})
                out = res.get("stdout", "") or res.get("stderr", "")
                evidence.append(f"$ [{host}] {cmd}\n{out[:1500]}")
                for rx, label in _EVIDENCE_RES:
                    if rx.search(out):
                        anomalies.append(f"{host}: {label} (via `{cmd}`)")
        if evidence:
            self.context.add_section("evidence", "\n\n".join(evidence), priority=85)
            sources.append("[mcp] hosts.run_command")

        # 4. Code: clone repo, search suspect patterns, read hits, run repro.
        code_notes: list[str] = []
        repro_out = ""
        if ticket.get("repo"):
            cloned = await self._tool("repo", "clone_repo", {"source": ticket["repo"]})
            sources.append("[mcp] repo.clone_repo")
            code_notes.append(f"cloned {ticket['repo']} "
                              f"({cloned.get('files', '?')} files)")
            seen_files: list[str] = []
            for pattern in code_patterns:
                hits = (await self._tool("repo", "search_code",
                                         {"sandbox": "sandbox-1",
                                          "pattern": pattern})).get("hits", [])
                # prefer real source files over repro scripts in the ranking
                hits = sorted(hits, key=lambda h: (h["file"].startswith("repro"),
                                                   h["file"], h["line"]))
                for h in hits[:5]:
                    code_notes.append(f"suspect: {h['file']}:{h['line']}: {h['text']}")
                    if h["file"] not in seen_files:
                        seen_files.append(h["file"])
            for f in seen_files[:2]:
                content = (await self._tool(
                    "repo", "read_file",
                    {"sandbox": "sandbox-1", "path": f})).get("content", "")
                self.context.add_section(f"code:{f}", content[:6000], priority=80)
            if code_notes:
                sources.append("[mcp] repo.search_code")
            if ticket.get("repro_cmd"):
                rr = (await self._tool("repo", "run_in_sandbox",
                                       {"sandbox": "sandbox-1",
                                        "command": ticket["repro_cmd"],
                                        "timeout": 120})).get("result", {})
                repro_out = (rr.get("stdout", "") or "") + (rr.get("stderr", "") or "")
                self.context.add_section("repro",
                                         f"$ {ticket['repro_cmd']}\n{repro_out[:3000]}",
                                         priority=82)
                sources.append("[mcp] repo.run_in_sandbox")

        # 5. Findings -> inference -> ticket update.
        findings = {
            "anomalies": sorted(set(anomalies)),
            "code_suspects": code_notes[:10],
            "repro": repro_out[:1500],
            "runbook": runbook.name if runbook else None,
        }
        self.context.add_section("findings-draft", json.dumps(findings, indent=1), priority=88)

        messages = self.context.build_messages()
        messages.append({"role": "user",
                         "content": prompts.RESPONSE_USER_TEMPLATE.format(
                             ticket_id=ticket_id, title=ticket.get("title"))})
        report = await asyncio.to_thread(
            self.inference.chat, messages,
            model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
        )

        # 6. Write everything back to the ticket.
        entries = 0
        if findings["anomalies"]:
            await self._tool("tickets", "add_timeline_entry", {
                "ticket_id": ticket_id, "author": "sre-agent",
                "text": "Evidence (log search + read-only host commands):\n- " + "\n- ".join(sorted(set(anomalies)))})
            entries += 1
        if findings["code_suspects"]:
            await self._tool("tickets", "add_timeline_entry", {
                "ticket_id": ticket_id, "author": "sre-agent",
                "text": "Code sandbox findings:\n- " + "\n- ".join(code_notes[:8])})
            entries += 1
        if repro_out.strip():
            await self._tool("tickets", "add_timeline_entry", {
                "ticket_id": ticket_id, "author": "sre-agent",
                "text": f"Repro `{ticket['repro_cmd']}` output:\n{repro_out[:1500]}"})
            entries += 1
        root_cause = self._draft_root_cause(findings)
        remediation = self._draft_remediation(findings, runbook)
        await self._tool("tickets", "set_root_cause", {
            "ticket_id": ticket_id, "root_cause": root_cause, "remediation": remediation})
        await self._tool("tickets", "add_timeline_entry", {
            "ticket_id": ticket_id, "author": "sre-agent",
            "text": f"Assessment complete. Root cause: {root_cause}\n"
                    f"Recommended remediation: {remediation}"})
        entries += 1
        sources.append("[mcp] tickets.add_timeline_entry + set_root_cause")

        return IncidentReport(
            ticket_id=ticket_id, report=report, sources=sources, rounds=1,
            timeline_entries=entries, context_stats=self.context.stats(),
        )

    @staticmethod
    def _draft_root_cause(findings: dict) -> str:
        anomalies = findings["anomalies"]
        suspects = [s for s in findings["code_suspects"] if s.startswith("suspect:")]
        parts = []
        if any("OOM" in a for a in anomalies):
            parts.append("worker OOM-killed under memory pressure")
        if any("HTTP 500" in a for a in anomalies):
            parts.append("elevated HTTP 500s following the OOM/restart cycle")
        if suspects:
            parts.append(f"code suspect: {suspects[0][len('suspect: '):]}")
        return "; ".join(parts) or "undetermined — escalate with gathered evidence"

    @staticmethod
    def _draft_remediation(findings: dict, runbook) -> str:
        if findings["anomalies"] and any("OOM" in a for a in findings["anomalies"]):
            return ("Bound the in-memory cache (LRU/TTL eviction), deploy the fix, "
                    "and add a memory-usage alert below the OOM threshold.")
        return "Follow the matched runbook remediation steps; re-run diagnostics after mitigation."

    # -- ReAct loop ------------------------------------------------------
    def _parse_action(self, text: str) -> dict[str, Any] | None:
        matches = _FENCE_RE.findall(text)
        if not matches:
            return None
        try:
            action = json.loads(matches[-1])
        except json.JSONDecodeError:
            return None
        return action if isinstance(action, dict) and "action" in action else None

    async def _execute_action(self, action: dict[str, Any]) -> str:
        name = action.get("action", "")
        args = action.get("args", {}) or {}
        try:
            if name == "rag_search" and self.rag is not None:
                return self.rag.query_context(args.get("query", ""), top_k=self.rag_top_k) or "(no hits)"
            if name.startswith("mcp.") and self.mcp is not None:
                _, server, tool = name.split(".", 2)
                data = await self._tool(server, tool, args)
                return json.dumps(data, indent=1)[:3000]
            return f"(tool '{name}' unavailable)"
        except Exception as exc:  # noqa: BLE001
            return f"(tool '{name}' error: {exc})"

    def _remember_round(self, rounds: int, action: dict[str, Any], observation: str) -> None:
        """Feed this round's action + observation into the context manager so
        the LLM reasons over accumulated evidence when choosing the next
        action. Keeps only the last 6 rounds to bound context size."""
        self.context.add_section(
            f"round-{rounds}",
            f"action: {action.get('action')} "
            f"args={json.dumps(action.get('args', {}))[:400]}\n"
            f"observation: {observation[:1200]}",
            priority=55,
            summarizable=True,
        )
        for n in range(1, rounds - 5):
            self.context.remove_section(f"round-{n}")

    async def run_react(self, ticket_id: str) -> IncidentReport:
        self.context.set_system(prompts.REACT_SYSTEM)
        if self.mcp is not None:
            try:
                self.context.add_section("mcp-tools",
                                         await self.mcp.tools_prompt_block(), priority=95)
            except Exception:  # noqa: BLE001
                pass

        ticket = (await self._tool("tickets", "get_ticket",
                                   {"ticket_id": ticket_id})).get("ticket", {})
        history: list[dict[str, str]] = [{
            "role": "user",
            "content": f"SEV2 incident: {ticket_id}: {ticket.get('title')}\n"
                       f"symptoms: {ticket.get('symptoms')}\nBegin the response."}]

        sources: list[str] = []
        rounds = 0
        final_answer = ""
        for rounds in range(1, self.react_max_rounds + 1):
            messages = self.context.build_messages() + history
            reply = await asyncio.to_thread(
                self.inference.chat, messages,
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            action = self._parse_action(reply)
            if action is None:
                final_answer = reply
                break
            if action.get("action") == "final":
                final_answer = action.get("answer", "")
                break
            observation = await self._execute_action(action)
            history.append({"role": "assistant", "content": reply})
            history.append({"role": "user",
                            "content": f"Observation:\n{observation}\nContinue with the next json action."})
            self._remember_round(rounds, action, observation)
            sources.append(f"[round {rounds}] {action.get('action')}")
        else:
            final_answer = final_answer or "(max rounds reached without a final report)"

        return IncidentReport(
            ticket_id=ticket_id, report=final_answer, sources=sources,
            rounds=rounds, context_stats=self.context.stats(),
        )
