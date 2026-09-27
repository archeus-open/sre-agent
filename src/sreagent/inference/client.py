"""Thin client for the sreagent inference server."""

from __future__ import annotations

from typing import Any

import httpx


class InferenceClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout_s: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # Never route localhost inference traffic through a proxy.
        self._client_kwargs: dict = {"trust_env": False}

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=10, **self._client_kwargs) as client:
            return client.get(f"{self.base_url}/health").json()

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "research-brief-v1",
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with httpx.Client(timeout=self.timeout_s, **self._client_kwargs) as client:
            resp = client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def summarize(self, text: str, max_tokens: int = 300) -> str:
        with httpx.Client(timeout=self.timeout_s, **self._client_kwargs) as client:
            resp = client.post(
                f"{self.base_url}/v1/summarize",
                json={"text": text, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            return resp.json()["summary"]
