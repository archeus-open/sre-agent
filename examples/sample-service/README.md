# checkout-api (sample service)

Tiny order-checkout service used by the sre-agent incident-response demo.

- `checkout.py` — order processing. Contains an **intentional memory leak**:
  `ORDER_CACHE` is never evicted.
- `worker.py` — worker entrypoint sketch.
- `repro_leak.py` — reproduces the leak: `python3 repro_leak.py`.

SEV2-1042 ("Elevated 5xx error rate on checkout-api") is caused by this leak:
the worker's RSS grows until the OOM-killer terminates it, and requests
fail with HTTP 500 during the crash/restart cycle.
