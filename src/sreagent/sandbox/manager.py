"""Local sandbox: clone a repo, inspect code, run repro commands.

The sandbox is a plain directory (default under the system temp dir).
``run`` executes commands with ``shell=False`` (no shell injection), a
timeout, and a denylist of destructive tokens. Prototype scope: local use.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_RUN_DENY = [re.compile(p) for p in [
    r"\brm\b", r"\bmv\b", r"\bdd\b", r"\bmkfs\b", r":\(\)\s*\{",
    r"\bshutdown\b", r"\breboot\b", r"\bsudo\b",
    r"\.\./",  # no path escapes
]]


@dataclass
class RunResult:
    command: str
    stdout: str
    stderr: str
    returncode: int

    def to_dict(self) -> dict:
        return {"command": self.command, "stdout": self.stdout,
                "stderr": self.stderr, "returncode": self.returncode}


class Sandbox:
    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name

    def list_files(self, subdir: str = "") -> list[str]:
        base = (self.path / subdir) if subdir else self.path
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and ".git/" not in str(p):
                out.append(str(p.relative_to(self.path)))
        return out

    def read_file(self, relpath: str, max_chars: int = 20000) -> str:
        target = (self.path / relpath).resolve()
        if not str(target).startswith(str(self.path.resolve())):
            raise ValueError("path escapes sandbox")
        text = target.read_text(errors="replace")
        return text[:max_chars] + ("…[truncated]" if len(text) > max_chars else "")

    def search(self, pattern: str, glob: str = "*.py") -> list[dict]:
        rx = re.compile(pattern)
        hits = []
        for p in self.path.rglob(glob):
            if ".git/" in str(p):
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": str(p.relative_to(self.path)),
                                 "line": i, "text": line.strip()[:200]})
                    if len(hits) >= 50:
                        return hits
        return hits

    def run(self, command: str, timeout: int = 120) -> RunResult:
        for rx in _RUN_DENY:
            if rx.search(command):
                return RunResult(command, "", f"blocked by sandbox policy ({rx.pattern!r})", 126)
        args = shlex.split(command)
        if not args:
            return RunResult(command, "", "empty command", 1)
        try:
            proc = subprocess.run(args, cwd=self.path, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunResult(command, "", f"timed out after {timeout}s", 124)
        except FileNotFoundError as exc:
            return RunResult(command, "", str(exc), 127)
        return RunResult(command, proc.stdout[-8000:], proc.stderr[-8000:], proc.returncode)


class SandboxManager:
    def __init__(self, workdir: str | Path | None = None) -> None:
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="sreagent-sandboxes-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.sandboxes: dict[str, Sandbox] = {}

    def clone(self, source: str, name: str = "sandbox-1") -> Sandbox:
        """Clone a repo. ``source`` is a git URL or a local directory."""
        dest = self.workdir / name
        if dest.exists():
            shutil.rmtree(dest)
        src = Path(source)
        if src.is_dir():
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
        else:
            proc = subprocess.run(["git", "clone", "--depth", "1", source, str(dest)],
                                  capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise RuntimeError(f"git clone failed: {proc.stderr[-500:]}")
        sb = Sandbox(dest, name)
        self.sandboxes[name] = sb
        return sb

    def get(self, name: str) -> Sandbox:
        try:
            return self.sandboxes[name]
        except KeyError:
            raise KeyError(f"Unknown sandbox: {name}") from None
