"""Tests for the multi-model LLM orchestration layer.

No test touches the network: the mock provider is deterministic, and the
real providers raise a clear error before any HTTP when the key is missing.
"""

import pytest

from sreagent.llm import (
    ClaudeProvider,
    LLMOrchestrator,
    LLMProvider,
    MockLLM,
    OpenAIProvider,
)


def _msgs(text="the worker hit OOM"):
    return [{"role": "user", "content": text}]


def test_mock_llm_is_keyword_driven_and_deterministic():
    llm = MockLLM()
    a = llm.complete(_msgs("OOM killer fired on worker-3"))
    b = llm.complete(_msgs("OOM killer fired on worker-3"))
    assert a == b
    assert "memory" in a.lower()
    assert "disk" in llm.complete(_msgs("disk full on /var")).lower()
    assert llm.describe() == {"provider": "mock", "model": "mock-demo-v1"}


def test_orchestrator_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("SREAGENT_LLM", raising=False)
    orch = LLMOrchestrator()
    text = orch.complete(_msgs())
    assert text.startswith("[mock]")
    assert orch.last_call["provider"] == "mock"
    assert orch.last_call["fallback"] is False


def test_orchestrator_env_selects_provider(monkeypatch):
    monkeypatch.setenv("SREAGENT_LLM", "claude")
    orch = LLMOrchestrator()
    assert isinstance(orch.get_provider(), ClaudeProvider)
    assert orch.get_provider("openai").name == "openai"


def test_orchestrator_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        LLMOrchestrator().get_provider("gemini-x")


def test_openai_requires_key_before_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIProvider().complete(_msgs())


def test_claude_requires_key_before_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider().complete(_msgs())


def test_orchestrator_falls_back_to_mock_on_failure(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orch = LLMOrchestrator(default="openai", fallback_to_mock=True)
    text = orch.complete(_msgs("disk full"))
    assert text.startswith("[mock]")
    assert orch.last_call["fallback"] is True
    assert "OPENAI_API_KEY" in orch.last_call["error"]


def test_orchestrator_no_fallback_reraises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orch = LLMOrchestrator(default="openai", fallback_to_mock=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        orch.complete(_msgs())


def test_orchestrator_registers_custom_provider():
    class LocalLLM(LLMProvider):
        name = "local"

        def __init__(self, model: str = "local-v1") -> None:
            super().__init__(model)

        def complete(self, messages, **kwargs):
            return "local answer"

    orch = LLMOrchestrator()
    orch.register("local", LocalLLM)
    assert "local" in LLMOrchestrator.available()
    assert orch.complete(_msgs(), provider="local") == "local answer"
    # don't leak the test double into other tests' registries
    from sreagent.llm import orchestrator as orch_mod

    del orch_mod._REGISTRY["local"]


def test_orchestrator_describe():
    orch = LLMOrchestrator(default="mock")
    info = orch.describe()
    assert info["default"] == "mock"
    assert set(info["available"]) >= {"mock", "openai", "claude"}
