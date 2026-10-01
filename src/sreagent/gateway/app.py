"""API gateway: mock authentication/authorization, per-key rate limiting,
and round-robin load balancing across upstream model workers.

This is a *reference* gateway for the prototype: the auth is a static
API-key map (no identity provider), the rate limiter is an in-memory
token bucket, and "upstreams" are OpenAI-compatible chat endpoints
(e.g. replicas of the sreagent inference server, or the mock worker in
the demo). Everything is deterministic and testable with FastAPI's
TestClient — no network needed for the demo.

Environment knobs:
- ``SREAGENT_GATEWAY_KEYS``: ``"key1:viewer,key2:operator,key3:admin"``
  (defaults to documented demo keys — replace in any real deployment)
- ``SREAGENT_GATEWAY_RATE_LIMIT``: requests per minute per key (default 60)
- ``SREAGENT_GATEWAY_UPSTREAMS``: comma-separated upstream base URLs
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Mock auth: static key -> role map. Demo keys only; override via env.
# ----------------------------------------------------------------------

DEFAULT_DEMO_KEYS = {
    "demo-viewer-key": "viewer",  # GET only
    "demo-operator-key": "operator",  # + POST /v1/chat
    "demo-admin-key": "admin",  # everything, incl. /admin/*
}

ROLE_SCOPES = {
    "viewer": {"GET"},
    "operator": {"GET", "POST"},
    "admin": {"GET", "POST", "PUT", "DELETE", "PATCH"},
}

ADMIN_PATH_PREFIX = "/admin"


def parse_keys(raw: str | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, role = chunk.split(":", 1)
        keys[key.strip()] = role.strip().lower()
    return keys


def load_api_keys() -> dict[str, str]:
    return parse_keys(os.getenv("SREAGENT_GATEWAY_KEYS")) or dict(DEFAULT_DEMO_KEYS)


class Identity(BaseModel):
    api_key: str
    role: str


def authenticate(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Identity:
    keys = load_api_keys()
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Missing or unknown X-API-Key")
    return Identity(api_key=x_api_key, role=keys[x_api_key])


def authorize(identity: Identity, request: Request) -> Identity:
    role = identity.role
    if request.url.path.startswith(ADMIN_PATH_PREFIX) and role != "admin":
        raise HTTPException(status_code=403, detail=f"Role {role!r} cannot access admin endpoints")
    allowed = ROLE_SCOPES.get(role, set())
    if request.method not in allowed:
        raise HTTPException(
            status_code=403, detail=f"Role {role!r} cannot {request.method} {request.url.path}"
        )
    return identity


# ----------------------------------------------------------------------
# Rate limiting: per-key token bucket, refilled continuously.
# ----------------------------------------------------------------------

class TokenBucket:
    def __init__(self, capacity: int, refill_per_s: float) -> None:
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> tuple[bool, float]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.refill_per_s)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True, 0.0
            deficit = 1 - self._tokens
            return False, deficit / self.refill_per_s


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.per_minute, self.per_minute / 60.0)
                self._buckets[key] = bucket
        return bucket.take()


# ----------------------------------------------------------------------
# Load balancing: round-robin pool with failure cooldown.
# ----------------------------------------------------------------------

UNHEALTHY_COOLDOWN_S = 30.0


class UpstreamPool:
    def __init__(self, urls: list[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._cursor = 0
        self._upstreams: list[dict[str, Any]] = [
            {"url": u.rstrip("/"), "unhealthy_until": 0.0, "requests": 0, "errors": 0}
            for u in (urls or [])
        ]

    def add(self, url: str) -> None:
        with self._lock:
            url = url.rstrip("/")
            if all(u["url"] != url for u in self._upstreams):
                self._upstreams.append({"url": url, "unhealthy_until": 0.0, "requests": 0, "errors": 0})

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            return [
                {
                    "url": u["url"],
                    "healthy": u["unhealthy_until"] <= now,
                    "requests": u["requests"],
                    "errors": u["errors"],
                }
                for u in self._upstreams
            ]

    def pick(self) -> dict[str, Any] | None:
        """Round-robin over healthy upstreams; None when the pool is empty
        or everything is cooling down."""
        with self._lock:
            now = time.monotonic()
            healthy = [u for u in self._upstreams if u["unhealthy_until"] <= now]
            if not healthy:
                return None
            choice = healthy[self._cursor % len(healthy)]
            self._cursor += 1
            choice["requests"] += 1
            return choice

    def mark_failed(self, url: str) -> None:
        with self._lock:
            for u in self._upstreams:
                if u["url"] == url:
                    u["errors"] += 1
                    u["unhealthy_until"] = time.monotonic() + UNHEALTHY_COOLDOWN_S


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------

class ChatRequest(BaseModel):
    model: str = "sre-responder-v1"
    messages: list[dict[str, str]] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: int | None = None


class AddUpstreamRequest(BaseModel):
    url: str


def _post_json(url: str, payload: dict[str, Any], timeout_s: float = 60.0) -> dict[str, Any]:
    """Single place where the gateway talks to upstreams — monkeypatchable in tests."""
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------

def create_app(
    upstreams: list[str] | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    app = FastAPI(title="sreagent-gateway", version="0.1.0")

    if upstreams is None:
        raw = os.getenv("SREAGENT_GATEWAY_UPSTREAMS", "")
        upstreams = [u.strip() for u in raw.split(",") if u.strip()]
    if rate_limit_per_minute is None:
        rate_limit_per_minute = int(os.getenv("SREAGENT_GATEWAY_RATE_LIMIT", "60"))

    app.state.pool = UpstreamPool(upstreams)
    app.state.limiter = RateLimiter(rate_limit_per_minute)

    def guarded(request: Request, identity: Identity = Depends(authenticate)) -> Identity:
        # Rate limit before authorization so brute-forcing keys is expensive.
        allowed, retry_after = app.state.limiter.check(identity.api_key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": f"{retry_after:.1f}"},
            )
        return authorize(identity, request)

    @app.get("/health")
    def health() -> dict[str, Any]:
        pool: UpstreamPool = app.state.pool
        snap = pool.snapshot()
        return {
            "status": "ok",
            "upstreams": len(snap),
            "healthy_upstreams": sum(1 for u in snap if u["healthy"]),
        }

    @app.post("/v1/chat")
    def chat(body: ChatRequest, request: Request, identity: Identity = Depends(guarded)) -> Any:
        pool: UpstreamPool = request.app.state.pool
        payload = body.model_dump(exclude_none=True)
        tried: list[str] = []
        # Try each healthy upstream once, round-robin.
        for _ in range(len(pool.snapshot())):
            upstream = pool.pick()
            if upstream is None:
                break
            tried.append(upstream["url"])
            try:
                return _post_json(f"{upstream['url']}/v1/chat/completions", payload)
            except Exception:
                pool.mark_failed(upstream["url"])
        raise HTTPException(
            status_code=503,
            detail=f"No healthy upstream available (tried: {tried or 'none configured'})",
        )

    @app.get(ADMIN_PATH_PREFIX + "/upstreams")
    def list_upstreams(request: Request, identity: Identity = Depends(guarded)) -> dict[str, Any]:
        return {"upstreams": request.app.state.pool.snapshot()}

    @app.post(ADMIN_PATH_PREFIX + "/upstreams")
    def add_upstream(
        body: AddUpstreamRequest, request: Request, identity: Identity = Depends(guarded)
    ) -> dict[str, Any]:
        request.app.state.pool.add(body.url)
        return {"added": body.url, "upstreams": request.app.state.pool.snapshot()}

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
            headers=exc.headers or {},
        )

    return app
