"""Tests for the ticket store."""

import pytest

from sreagent.tickets import Severity, TicketStatus, TicketStore


@pytest.fixture()
def store(tmp_path):
    return TicketStore.seed_demo(tmp_path / "tickets.json")


def test_seed_has_two_sev2_tickets(store):
    sev2 = store.list(severity="SEV2")
    assert len(sev2) == 2
    assert all(t.severity == Severity.SEV2 for t in sev2)


def test_ticket_lifecycle(store):
    t = store.get("SEV2-1042")
    assert t.status == TicketStatus.OPEN
    assert t.affected_hosts == ["checkout-api-01", "checkout-api-02", "checkout-api-03"]

    t.status = TicketStatus.INVESTIGATING
    t.add_entry("sre-agent", "started investigation")
    t.root_cause = "unbounded cache"
    t.remediation = "bound the cache"
    store.save()

    again = TicketStore.load(store.path).get("SEV2-1042")
    assert again.status == TicketStatus.INVESTIGATING
    assert again.timeline[-1].text == "started investigation"
    assert again.root_cause == "unbounded cache"


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "tickets.json"
    TicketStore.seed_demo(path)
    loaded = TicketStore.load(path)
    assert len(loaded.list(severity="SEV2")) == 2
    assert loaded.get("SEV2-1038").title.startswith("Disk pressure")


def test_status_filter(store):
    assert len(store.list(status="OPEN")) == 2
    assert store.list(status="RESOLVED") == []


def test_unknown_ticket(store):
    with pytest.raises(KeyError):
        store.get("SEV2-9999")
