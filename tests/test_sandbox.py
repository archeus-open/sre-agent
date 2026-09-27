"""Tests for the code sandbox."""

import pytest

from sreagent.sandbox import SandboxManager


@pytest.fixture()
def sandbox():
    mgr = SandboxManager()
    return mgr.clone("examples/sample-service")


def test_clone_lists_files(sandbox):
    files = sandbox.list_files()
    assert "checkout.py" in files
    assert "repro_leak.py" in files


def test_search_finds_leak(sandbox):
    hits = sandbox.search("ORDER_CACHE")
    assert hits
    assert any(h["file"] == "checkout.py" for h in hits)


def test_read_file(sandbox):
    content = sandbox.read_file("checkout.py")
    assert "never evicted" in content.lower() or "NEVER EVICTED" in content


def test_run_repro_confirms_leak(sandbox):
    res = sandbox.run("python3 repro_leak.py", timeout=120)
    assert res.returncode == 0, res.stderr
    assert "LEAK CONFIRMED" in res.stdout


def test_destructive_command_blocked(sandbox):
    res = sandbox.run("rm -rf /tmp/whatever")
    assert res.returncode == 126
    assert "blocked" in res.stderr


def test_path_escape_blocked(sandbox):
    with pytest.raises(ValueError):
        sandbox.read_file("../outside.txt")
