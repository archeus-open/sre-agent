"""End-to-end demo of the three new sre-agent layers — no credentials,
no Redis server, no network.

1. ContextBuilder records conversation + incident state into the cache
   (in-memory fallback) and builds prioritized LLM prompt sections.
2. LLMOrchestrator answers through the mock provider (free, deterministic).
3. The API gateway fronts it all: watch 401 -> 403 -> 200 -> 429 and
   round-robin load balancing across two fake upstreams.

Run:  .venv/bin/python examples/demo_layers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import sreagent.gateway.app as gw  # noqa: E402
from sreagent.context import ContextManager  # noqa: E402
from sreagent.gateway import create_app  # noqa: E402
from sreagent.llm import LLMOrchestrator  # noqa: E402
from sreagent.memory import ContextBuilder, InMemoryCache  # noqa: E402


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> None:
    # -- 1. context builder ------------------------------------------------
    section("1. Context builder (cache -> prompt sections)")
    builder = ContextBuilder(cache=InMemoryCache())
    builder.pin_fact("service", "checkout-api")
    builder.pin_fact("team", "payments")
    builder.set_incident_state("SEV2-1042", {"status": "triaging", "sev": "2"})
    builder.record_turn("user", "worker-3 was OOM-killed twice in the last hour")
    builder.record_turn("assistant", "checking dmesg for OOM-killer lines")
    builder.update_incident_state("SEV2-1042", finding="RSS grew 50MB/min before kill")

    ctx = ContextManager(max_tokens=4000)
    ctx.set_system("You are an SRE incident responder. Be concise.")
    builder.apply_to(ctx, ticket_id="SEV2-1042")
    messages = ctx.build_messages()
    for m in messages:
        preview = m["content"].replace("\n", " ")[:100]
        print(f"  [{m['role']}] {preview}...")
    print("  stats:", builder.stats())

    # -- 2. LLM orchestration (mock) ----------------------------------------
    section("2. LLM orchestration (mock provider, no key needed)")
    orch = LLMOrchestrator()  # SREAGENT_LLM unset -> mock
    answer = orch.complete(messages)
    print("  answer:", answer[:160], "...")
    print("  served by:", orch.last_call)

    # what happens with a real provider but no key? -> graceful fallback
    orch2 = LLMOrchestrator(default="openai", fallback_to_mock=True)
    answer2 = orch2.complete([{"role": "user", "content": "disk full on /var"}])
    print("  openai w/o key -> fallback:", answer2[:80], "...")
    print("  served by:", orch2.last_call)

    # -- 3. API gateway ------------------------------------------------------
    section("3. API gateway (auth, rate limit, load balancing)")
    app = create_app(upstreams=["http://worker-1", "http://worker-2"],
                     rate_limit_per_minute=2)

    served_by: list[str] = []

    def fake_post(url: str, payload: dict, timeout_s: float = 60.0) -> dict:
        served_by.append(url)
        return {"answer": "mock upstream reply", "via": url}

    gw._post_json = fake_post  # point the gateway at fake workers
    client = TestClient(app)
    body = {"model": "mock-demo-v1", "messages": messages}

    r = client.post("/v1/chat", json=body)
    print("  no key ->", r.status_code, r.json())
    r = client.post("/v1/chat", json=body, headers={"X-API-Key": "demo-viewer-key"})
    print("  viewer POST /v1/chat ->", r.status_code, r.json())
    for i in range(2):
        r = client.post("/v1/chat", json=body, headers={"X-API-Key": "demo-operator-key"})
        print(f"  operator chat #{i + 1} ->", r.status_code, "via", r.json()["via"])
    print("  round-robin served by:", served_by)
    r = client.post("/v1/chat", json=body, headers={"X-API-Key": "demo-operator-key"})
    print("  3rd chat in the minute ->", r.status_code, r.json(),
          "| Retry-After:", r.headers.get("Retry-After"))
    r = client.get("/admin/upstreams", headers={"X-API-Key": "demo-admin-key"})
    print("  admin upstream pool:", [(u["url"], u["requests"]) for u in r.json()["upstreams"]])

    section("done — see docs/DEMO_WALKTHROUGH.md for the full tour")


if __name__ == "__main__":
    main()
