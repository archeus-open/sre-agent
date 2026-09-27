# Runbook: High CPU

triggers: high cpu, cpu saturation, load average, slow

## Symptoms
- Load average well above core count
- Elevated latency, request queueing

## Diagnosis steps
1. Identify the hot processes per host.
2. Check whether it is user, system, or iowait time.
3. Correlate with deploys or traffic spikes.

## Diagnostic commands
$ uptime
$ ps aux --sort=-%cpu | head -15
$ top -b -n1 | head -20
$ vmstat 1 3

## Suspect code patterns
`while True`
`range\(10\*\*`

## Common causes
- Hot loop / busy retry in application code
- Traffic spike without autoscaling
- Runaway cron or batch job

## Remediation
- Kill the runaway via the normal deploy/restart pipeline (not ad-hoc)
- Add backoff to retry loops; scale out if traffic-driven
