"""Read-only command policy for host access.

Default-deny: a command runs only if (a) it contains no deny-token and
(b) every pipe segment fully matches the read-only allowlist. This is what
keeps the SRE agent's host access non-destructive by construction.
"""

from __future__ import annotations

import re

# Anything matching these anywhere in the command is rejected outright.
_DENY_TOKENS = [
    r";", r"&&", r"\|\|", r"`", r"\$\(", r"\$\{",
    r">", r"<",  # no redirections (prevents writes/truncation)
    r"\bsudo\b", r"\bsu\b", r"\bdoas\b",
    r"\brm\b", r"\bmv\b", r"\bcp\b", r"\bmkdir\b", r"\btouch\b", r"\btee\b",
    r"\bkill\b", r"\bpkill\b", r"\bkillall\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r"\bsystemctl\s+(restart|stop|start|reload|enable|disable|mask)\b",
    r"\bservice\s+\w+\s+(restart|stop|start|reload)\b",
    r"\bchmod\b", r"\bchown\b", r"\bchgrp\b",
    r"\bmkfs\b", r"\bdd\b", r"\bfdisk\b",
    r"\bpasswd\b", r"\buseradd\b", r"\buserdel\b", r"\busermod\b",
    r"\biptables\b", r"\bnft\b", r"\bfirewall-cmd\b",
    r"\bmount\b", r"\bumount\b",
    r"\bapt\b", r"\byum\b", r"\bdnf\b", r"\bpip\s+install\b",
    r"\bwget\s+(?!.*localhost|.*127\.0\.0\.1)",  # filled in below; placeholder
    r":\(\)\s*\{",  # fork bomb
    r"(?:^|[\s;|&])(?:sh|bash)(?:\s|$)", r"\bpython3?\b.*-c\b",
]
_DENY_RES = [re.compile(p) for p in _DENY_TOKENS if "localhost" not in p]

# Per pipe-segment allowlist (fullmatch against the stripped segment).
_ALLOW_RES = [re.compile(p) for p in [
    r"uptime",
    r"hostname",
    r"whoami",
    r"id",
    r"date( -u)?",
    r"df( -[hi])?( /[\w./-]*)?",
    r"du -sh /[\w./*-]+",
    r"free -(m|g|h)",
    r"vmstat( \d+( \d+)?)?",
    r"iostat( -x)?( \d+( \d+)?)?",
    r"mpstat( \d+( \d+)?)?",
    r"ps aux( --sort=-?%(cpu|mem))?",
    r"top -b -n ?\d+",
    r"ss -tlnp?",
    r"netstat -tlnp?",
    r"ip (addr|route) show",
    r"systemctl (status|is-active|is-enabled) [\w@.:-]+",
    r"journalctl( -u [\w@.:-]+)?( --since '[^']+')?( --until '[^']+')?( -n \d+)?",
    r"dmesg",
    r"cat /var/log/[\w./-]+",
    r"cat /proc/[\w/.-]+",
    r"cat /etc/[\w./-]+",
    r"ls( -[lahtr]+)?( /[\w./-]+)?",
    r"tail( -n \d+| -\d+)?( /var/log/[\w./-]+)?",
    r"head( -n \d+| -\d+)?( /var/log/[\w./-]+)?",
    r"wc -l /var/log/[\w./-]+",
    r"lsof( -i( :\d+)?)?",
    r"curl -s( -o /dev/null)?( -w '[^']*')? https?://(localhost|127\.0\.0\.1)(:\d+)?(/[\w./?=&%-]*)?",
    r"getent hosts [\w.-]+",
    r"nslookup [\w.-]+",
    r"ping -c \d+ [\w.-]+",
]]


class CommandPolicy:
    """Gate every host command through the read-only policy."""

    def __init__(self, allow: list[re.Pattern] | None = None,
                 deny: list[re.Pattern] | None = None) -> None:
        self.allow = allow or _ALLOW_RES
        self.deny = deny or _DENY_RES

    def check(self, command: str) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        cmd = command.strip()
        if not cmd:
            return False, "empty command"
        for rx in self.deny:
            if rx.search(cmd):
                return False, f"denied by policy (matched {rx.pattern!r})"
        # wget/curl to non-local hosts is denied
        if re.search(r"\bwget\b", cmd) and not re.search(
                r"localhost|127\.0\.0\.1", cmd):
            return False, "denied: downloads only allowed from localhost"
        for segment in cmd.split("|"):
            seg = segment.strip()
            if not any(rx.fullmatch(seg) for rx in self.allow):
                return False, f"denied: pipe segment not on read-only allowlist: {seg!r}"
        return True, "allowed (read-only)"

    def is_allowed(self, command: str) -> bool:
        return self.check(command)[0]
