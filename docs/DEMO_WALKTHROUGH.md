# sre-agent demo walkthrough: memory → multi-model orchestration → API gateway

A hands-on tour of the three new sre-agent layers. **Nothing here needs
credentials, a Redis server, or network access** — the whole tour runs on
the in-memory cache, the mock LLM, and faked gateway upstreams. Each
section ends with the "go real" step for production.

```
┌─────────────┐     ┌──────────────────┐     ┌───────────────────┐
│   clients   │────▶│   API gateway    │────▶│  model workers    │
│ (curl, SDK) │     │ auth · rate limit│     │ (inference svcs)  │
└─────────────┘     │ load balancing   │     └───────────────────┘
                    └──────────────────┘
                              │  ▲
                    ┌─────────▼──┴────────┐
                    │  LLM orchestrator   │  mock | openai/codex | claude
                    │  (SREAGENT_LLM)     │  user-provided API keys
                    └─────────┬───────────┘
                              │ prompt sections
                    ┌─────────▼───────────┐
                    │  Context builder    │  turns · incident state · facts
                    │  (Redis ⟷ fallback) │
                    └─────────────────────┘
```

## 0. Setup

```bash
cd sre-agent
make install          # .venv + editable install (+ tests)
make test             # 90 tests, all offline
```

## 1. Guided demo (2 minutes, zero setup)

```bash
.venv/bin/python examples/demo_layers.py
```

Watch for three acts:

**Act 1 — context builder.** Turns, incident state, and pinned facts are
recorded into the cache, then assembled into prioritized prompt sections
(incident state > facts > recent conversation) and fed to a
`ContextManager`, so the existing token-budget/truncation logic still
applies unchanged.

**Act 2 — LLM orchestration.** `LLMOrchestrator()` answers with the mock
provider (deterministic, keyword-driven — no key, no cost, no network).
Then it tries `openai` with no key set: instead of crashing, it logs a
warning and **falls back to mock**, reporting `fallback: true`.

**Act 3 — API gateway.** A live FastAPI gateway (TestClient, no port
needed) fronts two fake workers:
- no `X-API-Key` → `401`
- `demo-viewer-key` POSTs → `403` (viewers are read-only)
- `demo-operator-key` POSTs → `200`, alternating `worker-1` / `worker-2`
  (round-robin load balancing)
- 3rd chat inside the minute → `429` with a `Retry-After` header
- `demo-admin-key` inspects the upstream pool and its request counters

## 2. Try it yourself (curl against a real gateway port)

Terminal 1 — start the gateway in front of two echo workers. (The echo
inference server speaks the OpenAI-compatible chat API, so it doubles as
a fake model worker.)

```bash
# worker A and B (echo backend = canned deterministic replies)
SREAGENT_BACKEND=echo SREAGENT_PORT=8081 .venv/bin/python examples/run_server.py &
SREAGENT_BACKEND=echo SREAGENT_PORT=8082 .venv/bin/python examples/run_server.py &

# gateway: 60 req/min/key, round-robin over both workers
SREAGENT_GATEWAY_UPSTREAMS=http://127.0.0.1:8081,http://127.0.0.1:8082 \
.venv/bin/python -m uvicorn sreagent.gateway.app:create_app --factory --port 8090
```

Terminal 2:

```bash
# 401 — no key
curl -s -X POST localhost:8090/v1/chat -H 'Content-Type: application/json' \
  -d '{"model":"m","messages":[{"role":"user","content":"triage SEV2-1042"}]}'

# 403 — viewer role is read-only
curl -s -X POST localhost:8090/v1/chat -H 'X-API-Key: demo-viewer-key' \
  -H 'Content-Type: application/json' -d '{"model":"m","messages":[]}'

# 200 — operator, watch the upstream alternate each call
for i in 1 2; do curl -s -X POST localhost:8090/v1/chat \
  -H 'X-API-Key: demo-operator-key' -H 'Content-Type: application/json' \
  -d '{"model":"m","messages":[{"role":"user","content":"hi"}]}' | head -c 120; echo; done

# admin pool view
curl -s localhost:8090/admin/upstreams -H 'X-API-Key: demo-admin-key'

# 429 — hammer it (60/min default; lower SREAGENT_GATEWAY_RATE_LIMIT to see it fast)
for i in $(seq 1 65); do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8090/v1/chat \
    -H 'X-API-Key: demo-operator-key' -H 'Content-Type: application/json' \
    -d '{"model":"m","messages":[]}'
done | sort | uniq -c
```

Kill a worker mid-loop and re-run: the gateway marks it unhealthy
(30 s cooldown) and fails over to the survivor — visible in
`/admin/upstreams` as `"healthy": false`.

## 3. Go real: Redis

```bash
pip install -e ".[redis]"
docker run -d -p 6379:6379 redis:7
export SREAGENT_REDIS_URL=redis://localhost:6379/0   # or SREAGENT_CACHE=redis
```

`get_cache()` now returns `RedisCache`; everything else — `ContextBuilder`,
tests, demos — works unchanged. Without the package or a reachable
server it degrades to the in-memory cache automatically.

## 4. Go real: your own model keys

Keys come **only** from the environment (or explicit constructor args) —
never hardcoded, never logged:

```bash
export SREAGENT_LLM=openai              # or: claude | codex | mock
export OPENAI_API_KEY=sk-...           # only when you opt in
export ANTHROPIC_API_KEY=sk-ant-...    # only when you opt in
# optional model pins:
export SREAGENT_OPENAI_MODEL=gpt-4o            # Codex: codex-mini-latest
export SREAGENT_ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

```python
from sreagent.llm import LLMOrchestrator
orch = LLMOrchestrator()  # reads SREAGENT_LLM
print(orch.complete([{"role": "user", "content": "triage SEV2-1042"}]))
print(orch.last_call)  # {"provider": ..., "model": ..., "fallback": ...}
```

Billing guardrails: `mock` is the default, missing keys raise a clear
error *before* any HTTP, and `fallback_to_mock=True` keeps demos alive
when a key is wrong or a provider is down.

## 5. Go real: gateway hardening checklist

The gateway ships as a **reference** implementation. Before real traffic:

- [ ] Replace `SREAGENT_GATEWAY_KEYS` demo keys with a real identity
      provider (the `authenticate` dependency is the single seam).
- [ ] Swap the in-memory token buckets for Redis-backed limits so they
      survive restarts and scale past one process.
- [ ] Add TLS termination and put the gateway behind it.
- [ ] Point `SREAGENT_GATEWAY_UPSTREAMS` at real inference replicas;
      the 30 s unhealthy cooldown is tunable in `gateway/app.py`.

## Environment reference

| Variable | Default | Effect |
|---|---|---|
| `SREAGENT_CACHE` | `auto` | `redis` / `memory` / `auto` (Redis if reachable) |
| `SREAGENT_REDIS_URL` | `redis://localhost:6379/0` | Redis server for `RedisCache` |
| `SREAGENT_LLM` | `mock` | `mock` / `openai` / `codex` / `claude` / `anthropic` |
| `OPENAI_API_KEY` | — | User-provided OpenAI key (Codex models via `SREAGENT_OPENAI_MODEL`) |
| `ANTHROPIC_API_KEY` | — | User-provided Anthropic key |
| `SREAGENT_OPENAI_MODEL` | `gpt-4o-mini` | Model name sent to OpenAI |
| `SREAGENT_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model name sent to Anthropic |
| `SREAGENT_GATEWAY_KEYS` | demo keys | `key:role,...` (`viewer`/`operator`/`admin`) |
| `SREAGENT_GATEWAY_RATE_LIMIT` | `60` | Requests/minute per API key |
| `SREAGENT_GATEWAY_UPSTREAMS` | — | Comma-separated upstream base URLs |
