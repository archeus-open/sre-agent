"""Reproduce the SEV2-1042 memory leak in the sandbox.

Runs process_order in a loop and shows ORDER_CACHE growing without bound.
Portable (stdlib only).
"""

import sys

from checkout import ORDER_CACHE, process_order


def main() -> int:
    for i in range(20_000):
        process_order(user_id=i, item_id=i % 500, amount=9.99)

    sample = next(iter(ORDER_CACHE.values()))
    estimated_mb = len(ORDER_CACHE) * (sys.getsizeof(sample) + 200) / 1e6
    print(f"cached orders: {len(ORDER_CACHE)}")
    print(f"estimated cache size: ~{estimated_mb:.1f} MB and still growing")
    if len(ORDER_CACHE) == 20_000:
        print("LEAK CONFIRMED: ORDER_CACHE is unbounded (no eviction)")
        return 0
    print("no leak detected")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
