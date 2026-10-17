"""Multi-model LLM orchestration: mock (free demo), OpenAI/Codex,
Claude (Anthropic), and OpenRouter (free-tier gateway) providers behind
one ``LLMOrchestrator``. Keys come from the environment
(``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``OPENROUTER_API_KEY``) —
never hardcode them.
"""

from .orchestrator import LLMOrchestrator
from .providers import (
    ClaudeProvider,
    LLMProvider,
    MockLLM,
    OpenAIProvider,
    OpenRouterProvider,
)

__all__ = [
    "LLMOrchestrator",
    "LLMProvider",
    "MockLLM",
    "OpenAIProvider",
    "ClaudeProvider",
    "OpenRouterProvider",
]
