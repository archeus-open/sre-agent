"""Agent orchestration over multiple LLM providers.

``LLMOrchestrator`` picks a provider per call (default from ``SREAGENT_LLM``,
``"mock"`` unless you opt into a paid model), routes the request, and can
fall back to the free mock provider if the primary errors — so a bad key
or an outage degrades the demo instead of killing it.

Example:
    orch = LLMOrchestrator()                      # mock by default
    orch = LLMOrchestrator(default="claude")       # needs ANTHROPIC_API_KEY
    text = orch.complete([{"role": "user", "content": "triage SEV2-1042"}])
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .providers import ClaudeProvider, LLMProvider, MockLLM, OpenAIProvider

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[LLMProvider]] = {
    "mock": MockLLM,
    "openai": OpenAIProvider,
    "codex": OpenAIProvider,  # Codex models ride the OpenAI API; pick via model name
    "anthropic": ClaudeProvider,
    "claude": ClaudeProvider,
}


class LLMOrchestrator:
    def __init__(
        self,
        default: str | None = None,
        fallback_to_mock: bool = True,
        **provider_kwargs: Any,
    ) -> None:
        """``default``: provider name (env ``SREAGENT_LLM`` wins when set).
        Extra kwargs are forwarded to every constructed provider."""
        self.default_name = (default or os.getenv("SREAGENT_LLM", "mock")).lower()
        self.fallback_to_mock = fallback_to_mock
        self._provider_kwargs = provider_kwargs
        self._providers: dict[str, LLMProvider] = {}
        self.last_call: dict[str, Any] = {}

    # -- provider management ------------------------------------------
    @classmethod
    def available(cls) -> list[str]:
        return sorted(_REGISTRY)

    def get_provider(self, name: str | None = None) -> LLMProvider:
        name = (name or self.default_name).lower()
        if name not in _REGISTRY:
            raise ValueError(f"Unknown LLM provider {name!r}. Available: {self.available()}")
        if name not in self._providers:
            self._providers[name] = _REGISTRY[name](**self._provider_kwargs)
        return self._providers[name]

    def register(self, name: str, provider_cls: type[LLMProvider]) -> None:
        """Plug in a custom provider class (e.g. a local vLLM wrapper)."""
        _REGISTRY[name.lower()] = provider_cls

    # -- completion ----------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        provider: str | None = None,
        **kwargs: Any,
    ) -> str:
        chosen = (provider or self.default_name).lower()
        try:
            llm = self.get_provider(chosen)
            text = llm.complete(messages, **kwargs)
            self.last_call = {"provider": llm.name, "model": llm.model, "fallback": False}
            return text
        except Exception as exc:
            if self.fallback_to_mock and chosen != "mock":
                log.warning("LLM provider %r failed (%s); falling back to mock", chosen, exc)
                llm = self.get_provider("mock")
                text = llm.complete(messages, **kwargs)
                self.last_call = {
                    "provider": llm.name,
                    "model": llm.model,
                    "fallback": True,
                    "error": str(exc),
                }
                return text
            raise

    def describe(self) -> dict[str, Any]:
        return {
            "default": self.default_name,
            "available": self.available(),
            "fallback_to_mock": self.fallback_to_mock,
            "last_call": self.last_call,
        }
