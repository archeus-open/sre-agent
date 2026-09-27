"""Context management: token budgeting, sectioned context, summarization.

The agent builds its prompt from named *sections* (system, web context, RAG
docs, tool outputs, conversation). Each section has a priority; when the
assembled prompt exceeds ``max_tokens``, low-priority sections are truncated
or summarized first so the most important context survives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token). Swap for tiktoken if installed."""
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


@dataclass(order=True)
class Section:
    priority: int
    name: str = field(compare=False)
    content: str = field(compare=False)
    summarizable: bool = field(default=True, compare=False)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def truncated(self, max_tokens: int) -> "Section":
        approx_chars = max_tokens * 4
        content = self.content[:approx_chars].rstrip() + "\n…[truncated]"
        return Section(self.priority, self.name, content, self.summarizable)


class ContextManager:
    def __init__(
        self,
        max_tokens: int = 6000,
        reserve_for_answer: int = 1500,
        summarizer: Callable[[str], str] | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.reserve_for_answer = reserve_for_answer
        self.summarizer = summarizer
        self._sections: dict[str, Section] = {}
        self.system_prompt: str = ""

    # -- sections -----------------------------------------------------
    def set_system(self, prompt: str) -> None:
        self.system_prompt = prompt

    def add_section(self, name: str, content: str, priority: int, summarizable: bool = True) -> None:
        content = content.strip()
        if content:
            self._sections[name] = Section(priority, name, content, summarizable)

    def remove_section(self, name: str) -> None:
        self._sections.pop(name, None)

    # -- assembly -----------------------------------------------------
    @property
    def budget(self) -> int:
        return max(512, self.max_tokens - self.reserve_for_answer)

    def _ordered(self) -> list[Section]:
        return sorted(self._sections.values(), reverse=True)

    def total_tokens(self) -> int:
        total = estimate_tokens(self.system_prompt)
        return total + sum(s.tokens for s in self._sections.values())

    def stats(self) -> dict:
        """Lightweight context snapshot for reports/tests."""
        return {
            "sections": len(self._sections),
            "total_tokens": self.total_tokens(),
            "budget": self.budget,
        }

    def ensure_budget(self) -> list[str]:
        """Shrink sections until the prompt fits. Returns notes on what changed."""
        notes: list[str] = []
        if self.total_tokens() <= self.budget:
            return notes
        # Pass 1: summarize low-priority summarizable sections (highest impact first).
        for section in sorted(self._sections.values()):
            if self.total_tokens() <= self.budget:
                break
            if section.summarizable and self.summarizer and section.tokens > 400:
                summary = self.summarizer(section.content)
                self._sections[section.name] = Section(
                    section.priority, section.name, f"[summary] {summary}", False
                )
                notes.append(f"summarized section '{section.name}'")
        # Pass 2: truncate remaining low-priority sections.
        for section in sorted(self._sections.values()):
            if self.total_tokens() <= self.budget:
                break
            if section.name == "system":
                continue
            current = self.total_tokens()
            over = current - self.budget
            # shrink this section by (over + 10% headroom), but keep at least 100 tokens
            new_tokens = max(100, section.tokens - over - current // 10)
            self._sections[section.name] = section.truncated(new_tokens)
            notes.append(f"truncated section '{section.name}' to ~{new_tokens} tokens")
        return notes

    def build_messages(self) -> list[dict[str, str]]:
        notes = self.ensure_budget()
        messages: list[dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        for section in self._ordered():
            messages.append(
                {
                    "role": "user",
                    "content": f"### {section.name}\n{section.content}",
                }
            )
        if notes:
            messages.append(
                {
                    "role": "system",
                    "content": "Note: some context was compressed to fit the window: " + "; ".join(notes),
                }
            )
        return messages

    def stats(self) -> dict[str, int | str]:
        return {
            "sections": len(self._sections),
            "total_tokens": self.total_tokens(),
            "budget": self.budget,
            **{f"tokens:{s.name}": s.tokens for s in self._sections.values()},
        }
