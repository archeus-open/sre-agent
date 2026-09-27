"""Inference backends for the sreagent inference server.

Two backends ship with the prototype:
- ``echo``: deterministic mock, no model needed. Great for tests, CI, and
  exercising the agent plumbing end to end.
- ``openai_compatible``: forwards to any OpenAI-compatible chat-completions
  endpoint (Ollama, vLLM, llama.cpp server, ...).
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class EchoBackend:
    """Deterministic mock backend: summarises the request as its answer."""

    name = "echo"

    def chat(self, messages: list[dict[str, Any]], model: str, **kwargs: Any) -> dict[str, Any]:
        user_text = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        preview = str(user_text)[:600]
        content = (
            "[echo backend — no model loaded]\n"
            f"model={model} messages={len(messages)}\n"
            "Last user message preview:\n" + preview
        )
        return {
            "id": "echo-1",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


class OpenAICompatBackend:
    """Forwards chat requests to an OpenAI-compatible endpoint."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_COMPAT_API_KEY", "not-needed")
        self.default_model = model or os.getenv("OPENAI_COMPAT_MODEL", "qwen2.5:7b")
        self.timeout_s = timeout_s

    def chat(self, messages: list[dict[str, Any]], model: str, **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.2),
        }
        if kwargs.get("max_tokens"):
            payload["max_tokens"] = kwargs["max_tokens"]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # trust_env=False: never send model traffic through a proxy implicitly.
        with httpx.Client(timeout=self.timeout_s, trust_env=False) as client:
            resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()


def get_backend(name: str | None = None) -> EchoBackend | OpenAICompatBackend:
    name = (name or os.getenv("SREAGENT_BACKEND", "echo")).lower()
    if name == "openai_compatible":
        return OpenAICompatBackend()
    return EchoBackend()
