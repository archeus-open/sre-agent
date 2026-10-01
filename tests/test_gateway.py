"""Tests for the API gateway: mock auth/authz, rate limiting, load balancing.

Upstream HTTP is faked by monkeypatching ``_post_json`` — no network.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import sreagent.gateway.app as gw
from sreagent.gateway import create_app

VIEWER = {"X-API-Key": "demo-viewer-key"}
OPERATOR = {"X-API-Key": "demo-operator-key"}
ADMIN = {"X-API-Key": "demo-admin-key"}
CHAT_BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture()
def app():
    return create_app(upstreams=["http://w1", "http://w2"], rate_limit_per_minute=1000)


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def fake_upstream(monkeypatch):
    calls: list[str] = []

    def _fake(url, payload, timeout_s=60.0):
        calls.append(url)
        return {"ok": True, "via": url}

    monkeypatch.setattr(gw, "_post_json", _fake)
    return calls


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["upstreams"] == 2


def test_missing_or_bad_key_is_401(client):
    assert client.post("/v1/chat", json=CHAT_BODY).status_code == 401
    assert client.post("/v1/chat", json=CHAT_BODY, headers={"X-API-Key": "nope"}).status_code == 401


def test_viewer_cannot_post_chat(client, fake_upstream):
    r = client.post("/v1/chat", json=CHAT_BODY, headers=VIEWER)
    assert r.status_code == 403
    assert fake_upstream == []  # never reached an upstream


def test_operator_can_chat_and_admin_can_list(client, fake_upstream):
    r = client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert r.status_code == 200
    assert r.json()["via"].startswith("http://w")

    r = client.get("/admin/upstreams", headers=ADMIN)
    assert r.status_code == 200
    assert len(r.json()["upstreams"]) == 2


def test_operator_cannot_touch_admin(client):
    r = client.get("/admin/upstreams", headers=OPERATOR)
    assert r.status_code == 403


def test_admin_can_add_upstream(client):
    r = client.post("/admin/upstreams", json={"url": "http://w3"}, headers=ADMIN)
    assert r.status_code == 200
    assert len(r.json()["upstreams"]) == 3


def test_rate_limit_returns_429_with_retry_after(fake_upstream):
    app = create_app(upstreams=["http://w1"], rate_limit_per_minute=2)
    client = TestClient(app)
    assert client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR).status_code == 200
    assert client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR).status_code == 200
    r = client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert r.json()["error"] == "Rate limit exceeded"


def test_round_robin_across_upstreams(client, fake_upstream):
    client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert fake_upstream == ["http://w1/v1/chat/completions", "http://w2/v1/chat/completions"]


def test_failed_upstream_is_skipped(client, monkeypatch):
    calls: list[str] = []

    def _flaky(url, payload, timeout_s=60.0):
        calls.append(url)
        if url.startswith("http://w1"):
            raise httpx.ConnectError("down")
        return {"ok": True}

    monkeypatch.setattr(gw, "_post_json", _flaky)
    r = client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert r.status_code == 200  # failed over to w2
    assert calls[0].startswith("http://w1")
    # w1 is now cooling down: next request goes straight to w2
    calls.clear()
    client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert calls == ["http://w2/v1/chat/completions"]
    # admin view reflects the outage
    snap = client.get("/admin/upstreams", headers=ADMIN).json()["upstreams"]
    health = {u["url"]: u["healthy"] for u in snap}
    assert health == {"http://w1": False, "http://w2": True}


def test_no_upstreams_is_503(fake_upstream):
    app = create_app(upstreams=[], rate_limit_per_minute=1000)
    client = TestClient(app)
    r = client.post("/v1/chat", json=CHAT_BODY, headers=OPERATOR)
    assert r.status_code == 503


def test_custom_keys_from_env(monkeypatch):
    monkeypatch.setenv("SREAGENT_GATEWAY_KEYS", "sekret:admin")
    app = create_app(upstreams=[])
    client = TestClient(app)
    assert client.get("/admin/upstreams", headers={"X-API-Key": "sekret"}).status_code == 200
    assert client.get("/admin/upstreams", headers=ADMIN).status_code == 401
