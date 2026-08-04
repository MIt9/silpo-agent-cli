import time

from silpo_agent.auth import MCPClient, TokenStore


class FakeTokenStore:
    def __init__(self, token=None):
        self._token = token

    def load(self):
        return self._token

    def save(self, token):
        self._token = token


def fail_login():
    raise AssertionError("login should not be called when a valid token is stored")


def fail_refresh(refresh_token):
    raise AssertionError("refresh should not be called when token is still valid")


def test_warm_start_reuses_stored_token_without_login_or_refresh():
    token_store = FakeTokenStore(
        {"access_token": "warm-token", "refresh_token": "r1", "expires_at": time.time() + 3600}
    )
    calls = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        calls.append((server_url, tool, args, access_token))
        return {"ok": True}

    client = MCPClient(
        server_url="https://mcp.silpo.ua/mcp",
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fail_login,
        refresh=fail_refresh,
        now=time.time,
    )

    result = client.call("silpo_get_my_online_orders", {"last": 5})

    assert result == {"ok": True}
    assert calls == [
        ("https://mcp.silpo.ua/mcp", "silpo_get_my_online_orders", {"last": 5}, "warm-token")
    ]


def test_expired_token_triggers_refresh_before_call():
    expired = {"access_token": "old-token", "refresh_token": "r1", "expires_at": 1000.0}
    token_store = FakeTokenStore(expired)
    refreshed = {"access_token": "new-token", "refresh_token": "r2", "expires_at": 9999999999.0}

    def fake_refresh(refresh_token):
        assert refresh_token == "r1"
        return refreshed

    used_tokens = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        used_tokens.append(access_token)
        return {"ok": True}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fail_login,
        refresh=fake_refresh,
        now=lambda: 2000.0,
    )

    client.call("silpo_get_my_shopping_cart", {})

    assert used_tokens == ["new-token"]
    assert token_store.load() == refreshed


def test_first_run_with_no_stored_token_triggers_browser_login():
    token_store = FakeTokenStore(None)
    fresh_token = {"access_token": "first-token", "refresh_token": "r1", "expires_at": 9999999999.0}

    def fake_login():
        return fresh_token

    used_tokens = []

    def fake_call_tool_http(server_url, tool, args, access_token):
        used_tokens.append(access_token)
        return {"ok": True}

    client = MCPClient(
        token_store=token_store,
        call_tool_http=fake_call_tool_http,
        login=fake_login,
        refresh=fail_refresh,
        now=time.time,
    )

    client.call("silpo_get_my_delivery_addresses", {})

    assert used_tokens == ["first-token"]
    assert token_store.load() == fresh_token


def test_token_store_round_trips_through_keyring(monkeypatch):
    saved = {}

    monkeypatch.setattr(
        "silpo_agent.auth.keyring.get_password", lambda s, u: saved.get((s, u))
    )
    monkeypatch.setattr(
        "silpo_agent.auth.keyring.set_password",
        lambda s, u, v: saved.__setitem__((s, u), v),
    )

    store = TokenStore(service="test-service", username="test-user")
    assert store.load() is None

    store.save({"access_token": "abc", "refresh_token": "r", "expires_at": 123.0})

    assert store.load() == {"access_token": "abc", "refresh_token": "r", "expires_at": 123.0}


def test_token_store_load_returns_none_for_corrupt_value(monkeypatch):
    monkeypatch.setattr("silpo_agent.auth.keyring.get_password", lambda s, u: "not-json")

    store = TokenStore(service="test-service", username="test-user")

    assert store.load() is None
