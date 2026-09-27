"""Tests for host inventory + simulated backend."""

import pytest

from sreagent.hosts import HostInventory


def test_demo_inventory_hosts():
    inv = HostInventory.demo()
    assert {h.name for h in inv.list(service="checkout-api")} == {
        "checkout-api-01", "checkout-api-02", "checkout-api-03"}
    assert len(inv.list(role="db")) == 2


def test_culprit_host_shows_oom():
    inv = HostInventory.demo()
    res = inv.run_command("checkout-api-02",
                          "journalctl -u checkout-api --since '1 hour ago' | tail -40")
    assert res.exit_code == 0
    assert "Out of memory" in res.stdout
    assert "MemoryError" in res.stdout


def test_culprit_host_health_500():
    inv = HostInventory.demo()
    res = inv.run_command(
        "checkout-api-02",
        "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health")
    assert res.stdout.strip() == "500"


def test_healthy_peer_health_200():
    inv = HostInventory.demo()
    res = inv.run_command(
        "checkout-api-01",
        "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health")
    assert res.stdout.strip() == "200"


def test_disk_pressure_host():
    inv = HostInventory.demo()
    res = inv.run_command("db-replica-02", "df -h")
    assert "87%" in res.stdout
    assert "pg_wal" in inv.run_command(
        "db-replica-02", "du -sh /var/lib/postgresql/*").stdout


def test_blocked_command_never_reaches_backend():
    inv = HostInventory.demo()
    res = inv.run_command("checkout-api-02", "systemctl restart checkout-api")
    assert res.exit_code == 126
    assert "blocked by read-only policy" in res.stderr


def test_unknown_host():
    inv = HostInventory.demo()
    with pytest.raises(KeyError):
        inv.run_command("nope-01", "uptime")
