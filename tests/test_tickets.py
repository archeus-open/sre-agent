"""Tests for the ticket store."""

import pytest

from sreagent.tickets import Severity, TicketStatus, TicketStore


@pytest.fixture()
def store(tmp_path):
    return TicketStore.seed_demo(tmp_path / "tickets.json")


def test_seed_has_three_sev2_tickets(store):
    sev2 = store.list(severity="SEV2")
    assert len(sev2) == 3
    assert all(t.severity == Severity.SEV2 for t in sev2)
    # one of them is a resolved predecessor for find_similar demos
    assert store.get("SEV2-1019").status == TicketStatus.RESOLVED


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
    assert len(loaded.list(severity="SEV2")) == 3
    assert loaded.get("SEV2-1038").title.startswith("Disk pressure")


def test_status_filter(store):
    assert len(store.list(status="OPEN")) == 2
    assert [t.ticket_id for t in store.list(status="RESOLVED")] == ["SEV2-1019"]


def test_unknown_ticket(store):
    with pytest.raises(KeyError):
        store.get("SEV2-9999")


def test_failure_signature_extraction(store):
    sigs = TicketStore.failure_signature(store.get("SEV2-1042"))
    assert "http-5xx" in sigs
    assert "latency/timeout" in sigs
    assert "memory-pressure/oom" in TicketStore.failure_signature(store.get("SEV2-1019"))


def test_find_similar_same_host_and_failure(store):
    similar = store.find_similar("SEV2-1042")
    assert len(similar) == 1
    top = similar[0]
    assert top["ticket_id"] == "SEV2-1019"
    assert top["status"] == "RESOLVED"
    assert "checkout-api-01" in top["matched_hosts"]
    assert "http-5xx" in top["matched_signatures"]
    assert "ORDER_CACHE" in (top["root_cause"] or "")


def test_find_similar_excludes_self_and_unrelated(store):
    # SEV2-1038 (disk pressure on db-replica-02) shares neither host nor
    # failure signature with the checkout-api 5xx incident.
    assert store.find_similar("SEV2-1038") == []
    ids = [s["ticket_id"] for s in store.find_similar("SEV2-1042")]
    assert "SEV2-1042" not in ids


def test_find_similar_respects_limit(store):
    assert store.find_similar("SEV2-1042", limit=0) == []
