"""Tests for runbook loading and ticket matching."""

from sreagent.runbooks import RunbookLoader


def _loader():
    return RunbookLoader("examples/runbooks")


def test_match_5xx_ticket():
    rb = _loader().match("Elevated 5xx error rate on checkout-api; 5xx rate spiked, http 500")
    assert rb is not None
    assert rb.name == "elevated-5xx"


def test_match_disk_ticket():
    rb = _loader().match("Disk pressure on db-replica-02 (87% full); pg_wal growing")
    assert rb is not None
    assert rb.name == "disk-pressure"


def test_diagnostic_commands_extracted():
    rb = _loader().match("5xx spike")
    cmds = rb.diagnostic_commands()
    assert cmds
    assert any(c.startswith("journalctl") for c in cmds)
    assert all(not c.startswith("$") for c in cmds)


def test_code_patterns_extracted():
    rb = _loader().match("5xx spike")
    patterns = rb.code_patterns()
    assert "CACHE" in patterns
