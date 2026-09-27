"""checkout-api: sample order-checkout service (for the sre-agent demo).

THE BUG: ``ORDER_CACHE`` grows without bound — every processed order is
stored and nothing is ever evicted. In production this exhausts worker
memory until the OOM-killer takes the process down, which surfaces as
HTTP 500s during the crash/restart cycle (see SEV2-1042).
"""

from __future__ import annotations

import time

# (user_id, item_id) -> order dict. No TTL, no LRU, no eviction. Leaks.
ORDER_CACHE: dict[tuple[int, int], dict] = {}


def process_order(user_id: int, item_id: int, amount: float) -> dict:
    """Process one checkout and memoize the result (never evicted)."""
    order = {
        "user_id": user_id,
        "item_id": item_id,
        "amount": amount,
        "ts": time.time(),
        # verbose per-order history makes the leak visible faster
        "history": [f"event-{n}" for n in range(50)],
    }
    ORDER_CACHE[(user_id, item_id)] = order
    return order


def get_cached_order(user_id: int, item_id: int) -> dict | None:
    return ORDER_CACHE.get((user_id, item_id))


def cache_size() -> int:
    return len(ORDER_CACHE)
