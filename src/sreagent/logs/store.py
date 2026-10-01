"""Deterministic synthetic log store for the demo fleet.

Mirrors ``HostInventory.demo()``: checkout-api-01/02/03 + db-replica-01/02.
``checkout-api-02`` is the sick host (OOM-killer, MemoryError tracebacks,
HTTP 500s, worker restarts); its peers log normal 200 traffic. Timestamps
are anchored at store construction so every run tells the same story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class LogLine:
    ts: datetime
    host: str
    level: str  # DEBUG < INFO < WARNING < ERROR < CRITICAL
    service: str
    msg: str

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "host": self.host,
            "level": self.level,
            "service": self.service,
            "msg": self.msg,
        }


_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


class LogStore:
    def __init__(self, base: datetime | None = None) -> None:
        self.base = base or datetime.now()
        self._lines: list[LogLine] = []
        self._build()

    # -- generation -----------------------------------------------------
    def _add(self, minutes_ago: float, host: str, level: str, service: str, msg: str) -> None:
        self._lines.append(
            LogLine(self.base - timedelta(minutes=minutes_ago), host, level, service, msg)
        )

    def _healthy_api_traffic(self, host: str) -> None:
        for i in range(30):
            mins = 58 - i * 2
            self._add(mins, host, "INFO", "checkout-api",
                      f'GET /health 200 {3 + i % 5}ms req_id={1000 + i}')
        self._add(61, host, "INFO", "checkout-api", "worker heartbeat ok rss=412MB")
        self._add(90, host, "INFO", "checkout-api", "worker heartbeat ok rss=408MB")

    def _sick_api_traffic(self, host: str) -> None:
        # The SEV2-1042 story, newest last.
        self._add(95, host, "INFO", "checkout-api", "worker heartbeat ok rss=401MB")
        self._add(80, host, "WARNING", "checkout-api", "worker rss=1180MB approaching limit 1536MB")
        self._add(72, host, "ERROR", "checkout-api",
                  "POST /orders 500 812ms req_id=4471 upstream=checkout-api")
        self._add(70, host, "ERROR", "checkout-api",
                  "Traceback (most recent call last): MemoryError: unable to allocate array")
        self._add(68, host, "CRITICAL", "kernel",
                  "Out of memory: Killed process 2291 (gunicorn) total-vm:8123456kB")
        self._add(67, host, "WARNING", "checkout-api", "worker restarted by supervisor (exit 137)")
        self._add(60, host, "INFO", "checkout-api", "worker heartbeat ok rss=395MB")
        self._add(45, host, "WARNING", "checkout-api", "worker rss=1290MB approaching limit 1536MB")
        self._add(38, host, "ERROR", "checkout-api",
                  "POST /orders 500 940ms req_id=4519 upstream=checkout-api")
        self._add(36, host, "ERROR", "checkout-api",
                  "Traceback (most recent call last): MemoryError: unable to allocate array")
        self._add(35, host, "CRITICAL", "kernel",
                  "Out of memory: Killed process 2317 (gunicorn) total-vm:8234011kB")
        self._add(34, host, "WARNING", "checkout-api", "worker restarted by supervisor (exit 137)")
        self._add(20, host, "INFO", "checkout-api", "worker heartbeat ok rss=402MB")
        self._add(12, host, "WARNING", "checkout-api", "worker rss=980MB approaching limit 1536MB")
        for i in range(8):
            self._add(10 - i, host, "INFO", "checkout-api",
                      f'GET /health 200 {4 + i % 3}ms req_id={2000 + i}')

    def _db_traffic(self, host: str) -> None:
        for i in range(12):
            self._add(55 - i * 5, host, "INFO", "postgres",
                      f"checkpoint complete: wrote 42 buffers ({0.4 + i * 0.01:.2f}s)")
        self._add(70, host, "WARNING", "postgres",
                  "slow query: 812ms SELECT * FROM orders WHERE status='PENDING'")

    def _build(self) -> None:
        self._healthy_api_traffic("checkout-api-01")
        self._sick_api_traffic("checkout-api-02")
        self._healthy_api_traffic("checkout-api-03")
        self._db_traffic("db-replica-01")
        self._db_traffic("db-replica-02")
        self._lines.sort(key=lambda l: l.ts)

    # -- queries ----------------------------------------------------------
    @property
    def hosts(self) -> list[str]:
        return sorted({l.host for l in self._lines})

    def _in_window(self, host: str, since_minutes: float) -> list[LogLine]:
        cutoff = self.base - timedelta(minutes=since_minutes)
        return [l for l in self._lines if l.host == host and l.ts >= cutoff]

    def search(
        self,
        host: str,
        query: str,
        level: str | None = None,
        since_minutes: float = 60,
        limit: int = 50,
    ) -> list[LogLine]:
        """Case-insensitive substring search, newest first."""
        q = query.lower()
        min_level = _LEVEL_ORDER.get((level or "DEBUG").upper(), 0)
        hits = [
            l for l in self._in_window(host, since_minutes)
            if q in l.msg.lower() and _LEVEL_ORDER[l.level] >= min_level
        ]
        hits.sort(key=lambda l: l.ts, reverse=True)
        return hits[:limit]

    def tail(self, host: str, n: int = 40, level: str | None = None) -> list[LogLine]:
        min_level = _LEVEL_ORDER.get((level or "DEBUG").upper(), 0)
        lines = [l for l in self._lines if l.host == host and _LEVEL_ORDER[l.level] >= min_level]
        lines.sort(key=lambda l: l.ts)
        return lines[-n:]

    def error_summary(self, host: str, since_minutes: float = 60) -> dict:
        lines = [l for l in self._in_window(host, since_minutes) if _LEVEL_ORDER[l.level] >= 3]
        by_level: dict[str, int] = {}
        signatures: dict[str, int] = {}
        for l in lines:
            by_level[l.level] = by_level.get(l.level, 0) + 1
            sig = l.msg[:80]
            signatures[sig] = signatures.get(sig, 0) + 1
        top = sorted(signatures.items(), key=lambda kv: kv[1], reverse=True)[:5]
        return {
            "host": host,
            "window_minutes": since_minutes,
            "error_lines": len(lines),
            "by_level": by_level,
            "top_signatures": [{"signature": s, "count": c} for s, c in top],
        }
