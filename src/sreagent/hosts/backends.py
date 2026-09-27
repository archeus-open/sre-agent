"""Host command backends.

- ``SimulatedBackend``: deterministic scripted outputs per host — what the
  demo and tests use. No real SSH.
- ``SSHBackend``: real SSH via paramiko (optional extra ``ssh``). Every
  command still goes through ``CommandPolicy`` first.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass
class CommandResult:
    host: str
    command: str
    stdout: str
    stderr: str = ""
    exit_code: int = 0
    simulated: bool = True

    def to_dict(self) -> dict:
        return {"host": self.host, "command": self.command, "stdout": self.stdout,
                "stderr": self.stderr, "exit_code": self.exit_code,
                "simulated": self.simulated}


def _scripted() -> dict[str, list[tuple[str, str]]]:
    """(glob pattern, output) per host. First match wins."""
    oom_journal = """\
-- Logs begin at Fri 2026-09-25 09:12:44 UTC --
Sep 26 21:02:11 checkout-api-02 kernel: Out of memory: Killed process 1791 (checkout-api) total-vm:8234560kB
Sep 26 21:02:12 checkout-api-02 systemd[1]: checkout-api.service: Main process exited, code=killed, status=9/KILL
Sep 26 21:02:12 checkout-api-02 systemd[1]: checkout-api.service: Failed with result 'signal'.
Sep 26 21:02:13 checkout-api-02 systemd[1]: checkout-api.service: Service RestartSec=5s expired, scheduling restart.
Sep 26 21:02:18 checkout-api-02 checkout-api[1842]: Traceback (most recent call last):
Sep 26 21:02:18 checkout-api-02 checkout-api[1842]:   File "/opt/checkout-api/checkout.py", line 41, in process_order
Sep 26 21:02:18 checkout-api-02 checkout-api[1842]:     ORDER_CACHE[(user_id, item_id)] = order
Sep 26 21:02:18 checkout-api-02 checkout-api[1842]: MemoryError: unable to allocate 3.1 GiB
Sep 26 21:03:02 checkout-api-02 checkout-api[1842]: ERROR 500 /checkout user_id=99182 latency_ms=8120
Sep 26 21:04:44 checkout-api-02 checkout-api[1842]: ERROR 500 /checkout user_id=103311 latency_ms=7904"""

    return {
        "checkout-api-02": [
            ("uptime", "21:14:03 up 12 days,  3:41,  1 user,  load average: 14.22, 12.08, 8.44"),
            ("ps aux*", "USER  PID %CPU %MEM  VSZ   RSS TTY  STAT START  TIME COMMAND\n"
                        "svc  1842 187.3 92.1 8234560 7550000 ? Sl 21:02  41:12 python3 /opt/checkout-api/worker.py\n"
                        "svc   901  0.4  1.2  412000  98000 ? Ssl 09:12   2:11 python3 /opt/checkout-api/api.py"),
            ("free -m", "              total  used  free shared buff/cache available\n"
                        "Mem:           8192  7901   102     12        189       96\n"
                        "Swap:          2048  1890   158"),
            ("journalctl*", oom_journal),
            ("curl*health*", "500"),
            ("df*", "Filesystem  Size Used Avail Use% Mounted on\n/dev/sda1  100G   42G   58G  42% /"),
            ("ss*", "LISTEN 0 128 *:8080 *:* users:(\"checkout-api\",pid=1842,fd=9)"),
            ("systemctl status*", "● checkout-api.service - Checkout API\n   Active: active (running) since Sat 2026-09-26 21:02:18 UTC; 12min ago\n Main PID: 1842 (worker.py)"),
        ],
        "checkout-api-0[13]": [
            ("uptime", "21:14:03 up 30 days,  6:02,  1 user,  load average: 0.82, 0.71, 0.66"),
            ("ps aux*", "USER  PID %CPU %MEM  VSZ   RSS TTY  STAT START  TIME COMMAND\n"
                        "svc   712  4.2  6.8  612000 540000 ? Ssl 08:00  12:44 python3 /opt/checkout-api/api.py"),
            ("free -m", "              total  used  free shared buff/cache available\n"
                        "Mem:           8192  1210  6102     12        880     6680\n"
                        "Swap:          2048     0  2048"),
            ("journalctl*", "-- No entries --"),
            ("curl*health*", "200"),
            ("df*", "Filesystem  Size Used Avail Use% Mounted on\n/dev/sda1  100G   40G   60G  40% /"),
        ],
        "db-replica-02": [
            ("df*", "Filesystem  Size Used Avail Use% Mounted on\n/dev/sdb1  500G  437G   63G  87% /var/lib/postgresql"),
            ("du -sh*", "180G\t/var/lib/postgresql/pg_wal\n240G\t/var/lib/postgresql/base"),
            ("uptime", "21:14:03 up 90 days,  1:15,  1 user,  load average: 1.10, 1.02, 0.98"),
            ("journalctl*", "Sep 26 18:40:01 db-replica-02 postgres[2210]: WARNING: pg_wal growing faster than archival; 180 GB retained"),
        ],
    }


class SimulatedBackend:
    """Deterministic fake fleet. Unknown commands get a generic healthy reply."""

    def __init__(self) -> None:
        self._scripted = _scripted()

    def run(self, host: str, command: str) -> CommandResult:
        for host_pattern, scripts in self._scripted.items():
            if not fnmatch.fnmatch(host, host_pattern):
                continue
            for pattern, output in scripts:
                if fnmatch.fnmatch(command, pattern):
                    return CommandResult(host=host, command=command, stdout=output)
        # generic fallbacks
        if command == "hostname":
            return CommandResult(host=host, command=command, stdout=host)
        if command == "whoami":
            return CommandResult(host=host, command=command, stdout="sre-agent")
        return CommandResult(host=host, command=command,
                             stdout=f"(simulated) no scripted output for {command!r} on {host}")


class SSHBackend:
    """Real SSH backend (requires the ``ssh`` extra: paramiko).

    Credentials must come from the operator (ssh-agent / key file), never
    hardcoded. The read-only policy is enforced before any command runs.
    """

    def __init__(self, username: str, key_filename: str | None = None,
                 timeout: int = 15) -> None:
        try:
            import paramiko  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("SSHBackend needs the 'ssh' extra: pip install sreagent[ssh]") from exc
        self._paramiko = paramiko
        self.username = username
        self.key_filename = key_filename
        self.timeout = timeout

    def run(self, host: str, command: str) -> CommandResult:
        client = self._paramiko.SSHClient()
        client.set_missing_host_key_policy(self._paramiko.RejectPolicy())
        client.connect(host, username=self.username, key_filename=self.key_filename,
                       timeout=self.timeout, allow_agent=True)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=self.timeout)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            code = stdout.channel.recv_exit_status()
        finally:
            client.close()
        return CommandResult(host=host, command=command, stdout=out,
                             stderr=err, exit_code=code, simulated=False)
