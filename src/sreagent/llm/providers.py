"""LLM providers: one interface, several models.

- ``MockLLM``: deterministic, offline demo model. Default everywhere.
- ``OpenAIProvider``: OpenAI API (gpt-*, o-*, and Codex models selectable
  via the model name). Key from ``OPENAI_API_KEY`` or the constructor.
- ``ClaudeProvider``: Anthropic Messages API. Key from ``ANTHROPIC_API_KEY``
  or the constructor.

API keys are *never* hardcoded or logged — they come from the environment
or explicit constructor arguments. Anything that would spend money raises
a clear error when the key is missing instead of failing deep in a call.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

_CLIENT_KWARGS: dict[str, Any] = {"trust_env": False}  # never proxy model traffic implicitly


class LLMProvider:
    """Common interface every model provider implements."""

    name = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise NotImplementedError

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "model": self.model}


class MockLLM(LLMProvider):
    """Deterministic demo model: no network, no key, no cost.

    Replies are keyword-driven so demos and tests get plausible,
    reproducible incident-response chatter.
    """

    name = "mock"

    def __init__(self, model: str = "mock-demo-v1") -> None:
        super().__init__(model)

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        user_text = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        text = user_text.lower()
        if re.search(r"oom|out of memory|memory", text):
            return (
                "[mock] Likely memory pressure: correlate OOM-killer lines in dmesg "
                "with the process RSS growth, then run the sandbox repro to confirm "
                "the leak before restarting the worker."
            )
        if re.search(r"500|http|latency|slow", text):
            return (
                "[mock] Suggests an app-tier issue: check error rate by endpoint, "
                "then pull thread dumps / slow-query logs for the worst route."
            )
        if re.search(r"disk|space|full", text):
            return "[mock] Disk pressure: rank directories with du, rotate or ship the largest logs, then re-check."
        return (
            "[mock] Acknowledged. Next step: gather host evidence with the "
            "read-only diagnostics, match a runbook, and update the ticket timeline."
        )


class _HTTPChatProvider(LLMProvider):
    """Shared httpx plumbing for real API providers."""

    api_key_env: str = ""

    def _require_key(self, api_key: str | None) -> str:
        key = api_key or os.getenv(self.api_key_env, "")
        if not key:
            raise RuntimeError(
                f"{self.name} needs an API key: set the {self.api_key_env} "
                "environment variable or pass api_key= explicitly. "
                "Falling back to the mock provider is free: SREAGENT_LLM=mock."
            )
        return key

    @staticmethod
    def _redacted_headers(api_key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        # The key travels only in the Authorization header; never log it.
        headers = {"Authorization": f"Bearer {api_key}"}
        if extra:
            headers.update(extra)
        return headers


class OpenAIProvider(_HTTPChatProvider):
    """OpenAI chat-completions API. ``model`` picks the model — gpt-4o-mini
    by default; Codex models (e.g. ``codex-mini-latest``) work the same way.
    """

    name = "openai"
    api_key_env = "OPENAI_API_KEY"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__(model or os.getenv("SREAGENT_OPENAI_MODEL", "gpt-4o-mini"))
        self.api_key = api_key  # resolved lazily so import-time env is not required
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        key = self._require_key(self.api_key)
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.2),
        }
        if kwargs.get("max_tokens"):
            payload["max_tokens"] = kwargs["max_tokens"]
        with httpx.Client(timeout=self.timeout_s, **_CLIENT_KWARGS) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._redacted_headers(key),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


class ClaudeProvider(_HTTPChatProvider):
    """Anthropic Messages API (Claude)."""

    name = "claude"
    api_key_env = "ANTHROPIC_API_KEY"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com/v1",
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__(model or os.getenv("SREAGENT_ANTHROPIC_MODEL", "claude-sonnet-4-20250514"))
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        key = self._require_key(self.api_key)
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        convo = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "max_tokens": kwargs.get("max_tokens", 1024),
            "messages": convo,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        headers = self._redacted_headers(key, {"anthropic-version": "2023-06-01"})
        # Anthropic uses x-api-key, not Bearer.
        headers["x-api-key"] = headers.pop("Authorization").replace("Bearer ", "")
        with httpx.Client(timeout=self.timeout_s, **_CLIENT_KWARGS) as client:
            resp = client.post(f"{self.base_url}/messages", json=payload, headers=headers)
            resp.raise_for_status()
            blocks = resp.json()["content"]
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
