"""Tests for the memory layer: cache backends and the ContextBuilder."""

import pytest

from sreagent.context import ContextManager
from sreagent.memory import ContextBuilder, InMemoryCache, get_cache


def test_inmemory_kv_roundtrip_and_ttl():
    cache = InMemoryCache()
    cache.set("k", "v")
    assert cache.get("k") == "v"
    cache.set("gone", "v", ttl_s=-1)  # already expired
    assert cache.get("gone") is None
    cache.delete("k")
    assert cache.get("k") is None
    assert cache.ping() is True


def test_inmemory_list_semantics():
    cache = InMemoryCache()
    cache.lpush("l", "a")
    cache.lpush("l", "b")
    cache.lpush("l", "c")
    assert cache.lrange("l", 0, -1) == ["c", "b", "a"]
    assert cache.lrange("l", 0, 1) == ["c", "b"]
    cache.ltrim("l", 2)
    assert cache.lrange("l", 0, -1) == ["c", "b"]


def test_get_cache_prefers_memory_on_request():
    assert isinstance(get_cache(prefer="memory"), InMemoryCache)


def test_get_cache_auto_falls_back_without_redis(monkeypatch):
    # No redis package/server here: auto mode must degrade gracefully.
    monkeypatch.setenv("SREAGENT_CACHE", "auto")
    assert isinstance(get_cache(), InMemoryCache)


def test_get_cache_forced_redis_raises_helpfully():
    with pytest.raises(RuntimeError, match="redis"):
        get_cache(prefer="redis")


def test_builder_records_and_caps_turns():
    b = ContextBuilder(cache=InMemoryCache(), max_turns=3)
    for i in range(5):
        b.record_turn("user", f"msg {i}")
    turns = b.recent_turns()
    assert [t["content"] for t in turns] == ["msg 2", "msg 3", "msg 4"]
    assert all(t["role"] == "user" for t in turns)


def test_builder_incident_state_and_facts():
    b = ContextBuilder(cache=InMemoryCache())
    b.set_incident_state("SEV2-1042", {"status": "triaging"})
    state = b.update_incident_state("SEV2-1042", root_cause="OOM")
    assert state == {"status": "triaging", "root_cause": "OOM"}
    assert b.get_incident_state("SEV2-1042")["root_cause"] == "OOM"

    b.pin_fact("team", "payments")
    b.pin_fact("oncall", "ming")
    assert b.get_facts() == {"team": "payments", "oncall": "ming"}
    b.unpin_fact("team")
    assert b.get_facts() == {"oncall": "ming"}


def test_builder_sections_priority_order():
    b = ContextBuilder(cache=InMemoryCache())
    b.record_turn("user", "hello")
    b.pin_fact("team", "payments")
    b.set_incident_state("SEV2-1", {"status": "open"})
    names = [name for name, _, _ in b.build_sections(ticket_id="SEV2-1")]
    assert names == ["incident state", "pinned facts", "recent conversation"]


def test_builder_apply_to_context_manager():
    b = ContextBuilder(cache=InMemoryCache())
    b.record_turn("user", "worker OOM again")
    b.pin_fact("service", "checkout")
    ctx = ContextManager(max_tokens=6000)
    ctx.set_system("you are an SRE")
    b.apply_to(ctx, ticket_id="SEV2-1")  # no incident state stored: skipped silently
    messages = ctx.build_messages()
    roles = [m["role"] for m in messages]
    assert roles[0] == "system"
    joined = " ".join(m["content"] for m in messages)
    assert "worker OOM again" in joined
    assert "checkout" in joined
    assert ctx.stats()["sections"] == 2


def test_builder_stats():
    b = ContextBuilder(cache=InMemoryCache())
    b.record_turn("user", "hi")
    stats = b.stats()
    assert stats["backend"] == "InMemoryCache"
    assert stats["turns"] == 1
