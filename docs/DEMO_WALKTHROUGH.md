# sre-agent demo walkthrough: memory → multi-model orchestration → API gateway

A hands-on tour of the sre-agent layers. **Nothing here needs
credentials, a Redis server, or network access** — the whole tour runs on
the in-memory cache, the mock LLM, and faked gateway upstreams.

The incident loop itself is covered by `make agent-demo`
(`examples/run_demo.py`): ticket → runbook → **log search** → host
lookup → repo download → sandbox repro → RCA, with each step printed.
The sections below tour the supporting layers.

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
make test             # 108 tests, all offline
```

## 0b. Incident loop with log search (needs the inference server)

```bash
# terminal 1: echo backend = no model needed
SREAGENT_BACKEND=echo .venv/bin/python examples/run_server.py

# terminal 2: staged SEV2 response over 4 MCP servers
.venv/bin/python examples/run_demo.py
```

Watch the `INVESTIGATION STEPS` section print the pipeline in order:

```
step 1. Ticket fetched and claimed
step 2. Runbook matched (RAG + keyword)
step 3. Log search on affected hosts — error signatures
step 4. Previous similar incidents — prior RCA pulled in
step 5. Host lookup — read-only diagnostics
step 6. Code repository downloaded to sandbox
step 7. Suspect code search in sandbox
step 8. Sandbox repro run
step 9. RCA — ticket updated with root cause
```

The LLM reasons through the runbook, the logs, **and** previous incidents:
step 3 searches `checkout-api-02` for the runbook's error signatures
(`Out of memory`, ` 500 `, `Traceback`), and whatever it finds lands in the
prompt context (`log evidence` section); step 4 pulls previous SEV2s on the
same host with the same failure signature — a resolved predecessor's
recorded root cause (`previous similar incidents` section) becomes the
leading hypothesis. In `run_react` mode
each round's observation is likewise fed back into context (`round-N`
sections, last 6 kept) so the next action builds on accumulated evidence.

## 0c. Tool manifests (static manifest.json per server)

Each MCP server ships a versioned, static `manifest.json`
(`src/sreagent/servers/manifests/<server>.json`) — the tool contract the
client uses for calling: argument validation and catalog rendering with
no live server round-trip and nothing generated at runtime.

```bash
cat src/sreagent/servers/manifests/logs.json   # tools, input schemas, version
```

When a server's tools change: bump its `SERVER_VERSION` in the server
module, then regenerate:

```bash
.venv/bin/python -m sreagent.servers.manifest refresh
```

`refresh` rewrites all four manifests from the code and stamps each
server's current version, so file and code can't silently drift. The
client prefers the bundled static file and only falls back to
`--manifest` subprocess probing for third-party servers:

```python
await mcp.get_manifest("logs")          # static file, cached
await mcp.call_tool("logs", "search_logs", {"host": "h"})  # ValueError: missing 'query'
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
