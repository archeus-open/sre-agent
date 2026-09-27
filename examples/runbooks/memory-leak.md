# Runbook: Memory leak / OOM

triggers: memory, oom, out of memory, memoryerror, killed process

## Symptoms
- `Out of memory: Killed process` in kernel logs
- `MemoryError` tracebacks in application logs
- Swap exhaustion, rising RSS over hours/days

## Diagnosis steps
1. Confirm OOM-killer activity in kernel logs.
2. Find which process grows without bound.
3. Search the code for unbounded caches, lists, or global accumulators.

## Diagnostic commands
$ free -m
$ ps aux --sort=-%mem | head -15
$ journalctl -u checkout-api --since '1 hour ago' | tail -40
$ dmesg | tail -20

## Suspect code patterns
`CACHE`
`\.append\(`
`global `

## Common causes
- Unbounded in-memory cache with no eviction
- Accumulating request/response history in globals
- Unclosed resources (connections, file handles)

## Remediation
- Bound the structure (LRU/TTL), deploy, and watch RSS stabilize
- Add memory-usage alerting below the OOM threshold
