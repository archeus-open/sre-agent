"""Context builder: loads agent memory/state from the cache and turns it
into prioritized prompt sections for the LLM.

What lives in the cache (per namespace, default ``"sreagent"``):
- ``{ns}:turns``            — recent conversation turns (capped list)
- ``{ns}:incident:<id>``    — mutable incident state dict (status, findings…)
- ``{ns}:facts``            — pinned long-lived facts (JSON dict)

``ContextBuilder.build_sections()`` returns ``(name, content, priority)``
triples; ``apply_to()`` loads them straight into the existing
``ContextManager`` so token budgeting / truncation keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from ..context.manager import ContextManager
from .cache import CacheBackend, _json, _unjson, get_cache

# Priorities: higher wins when the ContextManager squeezes the budget.
PRIO_INCIDENT_STATE = 90
PRIO_FACTS = 70
PRIO_RECENT_TURNS = 50


class ContextBuilder:
    def __init__(
        self,
        cache: CacheBackend | None = None,
        namespace: str = "sreagent",
        max_turns: int = 20,
    ) -> None:
        self.cache = cache or get_cache()
        self.ns = namespace
        self.max_turns = max_turns

    # -- keys ---------------------------------------------------------
    def _k(self, *parts: str) -> str:
        return ":".join([self.ns, *parts])

    # -- writes -------------------------------------------------------
    def record_turn(self, role: str, content: str) -> None:
        key = self._k("turns")
        self.cache.lpush(key, _json({"role": role, "content": content}))
        self.cache.ltrim(key, self.max_turns)

    def set_incident_state(self, ticket_id: str, state: dict[str, Any]) -> None:
        self.cache.set(self._k("incident", ticket_id), _json(state))

    def update_incident_state(self, ticket_id: str, **fields: Any) -> dict[str, Any]:
        state = self.get_incident_state(ticket_id)
        state.update(fields)
        self.set_incident_state(ticket_id, state)
        return state

    def pin_fact(self, key: str, value: str) -> None:
        facts = self.get_facts()
        facts[key] = value
        self.cache.set(self._k("facts"), _json(facts))

    def unpin_fact(self, key: str) -> None:
        facts = self.get_facts()
        facts.pop(key, None)
        self.cache.set(self._k("facts"), _json(facts))

    def clear_turns(self) -> None:
        self.cache.delete(self._k("turns"))

    # -- reads --------------------------------------------------------
    def recent_turns(self, limit: int | None = None) -> list[dict[str, str]]:
        raw = self.cache.lrange(self._k("turns"), 0, (limit or self.max_turns) - 1)
        turns = [_unjson(r) for r in raw]
        # lpush stores newest-first; return chronological.
        return [t for t in reversed(turns) if isinstance(t, dict)]

    def get_incident_state(self, ticket_id: str) -> dict[str, Any]:
        return _unjson(self.cache.get(self._k("incident", ticket_id))) or {}

    def get_facts(self) -> dict[str, str]:
        return _unjson(self.cache.get(self._k("facts"))) or {}

    # -- prompt sections ----------------------------------------------
    def build_sections(self, ticket_id: str | None = None) -> list[tuple[str, str, int]]:
        """Return ``(name, content, priority)`` triples, highest priority first."""
        sections: list[tuple[str, str, int]] = []

        if ticket_id:
            state = self.get_incident_state(ticket_id)
            if state:
                lines = [f"- {k}: {v}" for k, v in state.items()]
                sections.append(
                    ("incident state", f"Current incident state for {ticket_id}:\n" + "\n".join(lines), PRIO_INCIDENT_STATE)
                )

        facts = self.get_facts()
        if facts:
            lines = [f"- {k}: {v}" for k, v in facts.items()]
            sections.append(("pinned facts", "Pinned facts (long-lived):\n" + "\n".join(lines), PRIO_FACTS))

        turns = self.recent_turns()
        if turns:
            lines = [f"{t['role']}: {t['content']}" for t in turns]
            sections.append(("recent conversation", "Recent conversation:\n" + "\n".join(lines), PRIO_RECENT_TURNS))

        sections.sort(key=lambda s: s[2], reverse=True)
        return sections

    def apply_to(self, ctx: ContextManager, ticket_id: str | None = None) -> ContextManager:
        """Load memory sections into a ContextManager. Returns it for chaining."""
        for name, content, priority in self.build_sections(ticket_id):
            ctx.add_section(name, content, priority, summarizable=(name == "recent conversation"))
        return ctx

    def stats(self) -> dict[str, Any]:
        return {
            "backend": type(self.cache).__name__,
            "turns": len(self.recent_turns()),
            "facts": len(self.get_facts()),
        }
