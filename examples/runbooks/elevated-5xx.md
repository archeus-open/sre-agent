# Runbook: Elevated 5xx error rate

triggers: 5xx, 500, error rate, elevated errors, http 500, latency p99

## Symptoms
- HTTP 5xx rate above 1% for 5+ minutes
- Often accompanied by p99 latency spikes on the same endpoints

## Diagnosis steps
1. Confirm scope: which service, which endpoints, when it started.
2. Check per-host health — is it fleet-wide or one bad host?
3. Read recent service logs for tracebacks and OOM-killer lines.
4. Check resource saturation (CPU, memory) on the affected hosts.
5. Correlate with recent deploys or config changes.

## Diagnostic commands
$ uptime
$ ps aux --sort=-%cpu | head -15
$ journalctl -u checkout-api --since '1 hour ago' | tail -40
$ curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health
$ df -h

## Suspect code patterns
`CACHE`
`cache\[`
`MemoryError`

## Common causes
- Worker OOM-killed after unbounded in-memory growth, then crash-looping
- Bad deploy (new exception on the hot path)
- Saturated downstream dependency timing out

## Remediation
- If OOM: bound the cache (LRU/TTL eviction), roll the fix, add a memory alert
- If bad deploy: roll back to the last known-good version
- If downstream: shed load / fail fast, then fix the dependency
