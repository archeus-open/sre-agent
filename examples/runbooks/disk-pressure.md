# Runbook: Disk pressure

triggers: disk, disk pressure, full, pg_wal, node disk

## Symptoms
- Node disk usage above 85% (warning) / 90% (critical)
- Database WAL directory growing faster than archival

## Diagnosis steps
1. Confirm which mount is filling and how fast.
2. Find the largest consumers under the mount.
3. Check logs for archival / cleanup failures.

## Diagnostic commands
$ df -h
$ du -sh /var/lib/postgresql/*
$ journalctl --since '1 hour ago' | tail -30
$ uptime

## Suspect code patterns
`pg_wal`
`archive_command`

## Common causes
- WAL archival lagging (archiver down or slow destination)
- Log rotation misconfigured
- Runaway temp files from a bad query

## Remediation
- Clear or move oldest WAL segments only after confirming archival
- Fix the archiver, then verify usage drops below 80%
- Add a disk-usage alert with a runbook link
