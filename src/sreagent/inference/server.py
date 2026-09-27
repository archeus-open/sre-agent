"""OpenAI-compatible inference server (FastAPI).

Run:  python examples/run_server.py
      (or: uvicorn sreagent.inference.server:app --host 127.0.0.1 --port 8080)

Endpoints:
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions   (OpenAI chat-completions shape)
    POST /v1/summarize         (convenience: {"text": ...} -> {"summary": ...})

The backend is selected with SREAGENT_BACKEND=echo|openai_compatible.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .backends import get_backend

app = FastAPI(title="sreagent inference server", version="0.1.0")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "research-brief-v1"
    messages: list[ChatMessage]
    temperature: float = 0.2
    max_tokens: int | None = None


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1)
    max_tokens: int | None = 300


@app.get("/health")
def health() -> dict[str, Any]:
    backend = get_backend()
    return {"status": "ok", "backend": backend.name, "time": time.time()}


@app.get("/v1/models")
def models() -> dict[str, Any]:
    backend = get_backend()
    model_id = getattr(backend, "default_model", "research-brief-v1")
    return {"object": "list", "data": [{"id": model_id, "object": "model"}]}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest) -> dict[str, Any]:
    backend = get_backend()
    try:
        return backend.chat(
            [m.model_dump() for m in req.messages],
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - surface backend errors as 502
        raise HTTPException(status_code=502, detail=f"backend error: {exc}") from exc


@app.post("/v1/summarize")
def summarize(req: SummarizeRequest) -> dict[str, Any]:
    backend = get_backend()
    messages = [
        {"role": "system", "content": "Summarize the following text concisely, keeping key facts and numbers."},
        {"role": "user", "content": req.text},
    ]
    try:
        completion = backend.chat(messages, model="research-brief-v1", max_tokens=req.max_tokens)
        content = completion["choices"][0]["message"]["content"]
        return {"summary": content}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"backend error: {exc}") from exc


def main() -> None:
    import uvicorn

    host = os.getenv("SREAGENT_HOST", "127.0.0.1")
    port = int(os.getenv("SREAGENT_PORT", "8080"))
    uvicorn.run("sreagent.inference.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
