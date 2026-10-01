"""Persistent agent memory: cache backends for conversation turns, incident
state, and pinned facts.

Two backends share one small interface:
- ``InMemoryCache``: process-local dict + lists. Zero dependencies; the
  default for tests, CI, and demos.
- ``RedisCache``: backed by a real Redis server. Needs the ``redis`` package
  (``pip install -e ".[redis]"``) and ``SREAGENT_REDIS_URL`` (default
  ``redis://localhost:6379/0``).

Use ``get_cache()`` to pick: Redis when the package *and* a reachable
server are available, otherwise the in-memory fallback.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Protocol


class CacheBackend(Protocol):
    """Minimal cache surface the context builder needs."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_s: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...
    def lpush(self, key: str, value: str) -> None: ...
    def ltrim(self, key: str, max_len: int) -> None: ...
    def lrange(self, key: str, start: int, stop: int) -> list[str]: ...
    def ping(self) -> bool: ...


class InMemoryCache:
    """Thread-safe process-local cache. No server, no dependencies."""

    def __init__(self) -> None:
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._lists: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._kv.get(key)
            if item is None:
                return None
            value, expires = item
            if expires is not None and expires < time.time():
                del self._kv[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        with self._lock:
            expires = time.time() + ttl_s if ttl_s else None
            self._kv[key] = (value, expires)

    def delete(self, key: str) -> None:
        with self._lock:
            self._kv.pop(key, None)
            self._lists.pop(key, None)

    def lpush(self, key: str, value: str) -> None:
        with self._lock:
            self._lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key: str, max_len: int) -> None:
        with self._lock:
            self._lists[key] = self._lists.get(key, [])[:max_len]

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        with self._lock:
            items = self._lists.get(key, [])
            # Redis LRANGE stop is inclusive; -1 means the end.
            end = None if stop == -1 else stop + 1
            return items[start:end]

    def ping(self) -> bool:
        return True


class RedisCache:
    """Redis-backed cache. Raises a clear error if the ``redis`` package
    is missing or the server is unreachable."""

    def __init__(self, url: str | None = None) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "RedisCache needs the 'redis' package: pip install -e \".[redis]\""
            ) from exc
        self._url = url or os.getenv("SREAGENT_REDIS_URL", "redis://localhost:6379/0")
        self._client = redis.Redis.from_url(self._url, decode_responses=True)
        try:
            self._client.ping()
        except Exception as exc:
            raise RuntimeError(f"Cannot reach Redis at {self._url}: {exc}") from exc

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        self._client.set(key, value, ex=ttl_s)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def lpush(self, key: str, value: str) -> None:
        self._client.lpush(key, value)

    def ltrim(self, key: str, max_len: int) -> None:
        self._client.ltrim(key, 0, max_len - 1)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        return list(self._client.lrange(key, start, stop))

    def ping(self) -> bool:
        return bool(self._client.ping())


def get_cache(prefer: str | None = None) -> CacheBackend:
    """Pick a cache backend.

    ``prefer="redis"`` forces Redis (raises if unavailable);
    ``prefer="memory"`` forces the in-memory fallback; otherwise Redis is
    tried first and the in-memory cache is used as a graceful fallback.
    """
    prefer = (prefer or os.getenv("SREAGENT_CACHE", "auto")).lower()
    if prefer == "memory":
        return InMemoryCache()
    if prefer == "redis":
        return RedisCache()
    try:
        return RedisCache()
    except RuntimeError:
        return InMemoryCache()


def _json(value: Any) -> str:
    return json.dumps(value)


def _unjson(raw: str | None) -> Any:
    return json.loads(raw) if raw is not None else None
