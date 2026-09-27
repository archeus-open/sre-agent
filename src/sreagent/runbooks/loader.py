"""Runbook loading and ticket→runbook matching.

Runbooks are markdown files. Convention:
- a `triggers:` line lists comma-separated keywords, e.g.
  `triggers: 5xx, 500, error rate`
- a `## Diagnostic commands` section lists `$ <command>` lines the agent
  may run on affected hosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Runbook:
    name: str
    path: Path
    triggers: list[str]
    text: str

    def diagnostic_commands(self) -> list[str]:
        return self._section_lines("## diagnostic commands", prefix="$ ")

    def code_patterns(self) -> list[str]:
        """Regex patterns to search for in the service code."""
        return self._section_lines("## suspect code patterns", prefix="`")

    def _section_lines(self, heading: str, prefix: str) -> list[str]:
        items: list[str] = []
        in_section = False
        for line in self.text.splitlines():
            if line.strip().lower().startswith(heading):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                s = line.strip()
                if s.startswith(prefix):
                    s = s[len(prefix):].strip().rstrip("`").strip()
                    if s:
                        items.append(s)
        return items


class RunbookLoader:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.runbooks: list[Runbook] = []
        for md in sorted(self.directory.glob("*.md")):
            triggers: list[str] = []
            for line in md.read_text().splitlines():
                if line.strip().lower().startswith("triggers:"):
                    triggers = [t.strip().lower()
                                for t in line.split(":", 1)[1].split(",")]
                    break
            self.runbooks.append(Runbook(name=md.stem, path=md,
                                         triggers=triggers, text=md.read_text()))

    def match(self, ticket_text: str) -> Runbook | None:
        """Keyword-score runbooks against ticket title + symptoms."""
        text = ticket_text.lower()
        best: Runbook | None = None
        best_score = 0
        for rb in self.runbooks:
            score = sum(1 for t in rb.triggers if t and t in text)
            if score > best_score:
                best, best_score = rb, score
        return best
